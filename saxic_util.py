"""SAXIC Launcher - shared helpers (networking, hashing, zip, rules).

Original code written for the SAXIC Launcher project. No third-party
launcher code is used or copied; everything here talks to Mojang's public
metadata/download endpoints the same way any launcher must.

Mojang endpoints used (all public):
  https://piston-meta.mojang.com/mc/game/version_manifest_v2.json
  https://launchermeta.mojang.com/v1/products/java-runtime/.../all.json
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile

VERSION_MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
RUNTIME_MANIFESTS = [
    "https://piston-meta.mojang.com/v1/products/java-runtime/2ec0cc96c44e5a76b9c8b7c39df7210883d12871/all.json",
    "https://launchermeta.mojang.com/v1/products/java-runtime/2ec0cc96c44e5a76b9c8b7c39df7210883d12871/all.json",
]
USER_AGENT = "SAXICLauncher/1.0 (+https://example.invalid/saxic)"


# --------------------------------------------------------------------------
# where files live (source folder or a PyInstaller one-file exe)


def app_dir() -> str:
    """The folder the launcher runs from."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def data_dir() -> str:
    """Writable folder for settings.json and the version-list cache."""
    if getattr(sys, "frozen", False):
        root = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                            "SAXIC Launcher")
        try:
            os.makedirs(root, exist_ok=True)
        except OSError:
            pass
        return root
    return os.path.dirname(os.path.abspath(__file__))


def resource_path(*parts) -> str:
    """Path to a bundled resource (works in a PyInstaller one-file build)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)

# --------------------------------------------------------------------------
# error types


class Cancelled(Exception):
    """Raised when the user aborts a long operation."""


# --------------------------------------------------------------------------
# platform


def os_name() -> str:
    p = sys.platform
    if p.startswith("win"):
        return "windows"
    if p.startswith("darwin"):
        return "osx"
    return "linux"


def os_arch() -> str:
    m = platform.machine().lower()
    if m in ("amd64", "x86_64", "x64"):
        return "x86_64"
    if m in ("i386", "i686", "x86"):
        return "x86"
    if m in ("arm64", "aarch64"):
        return "arm64"
    return m


def platform_key() -> str:
    """Key used by Mojang's java-runtime manifest, e.g. windows-x64."""
    o = os_name()
    a = os_arch()
    if o == "windows":
        return "windows-x64" if a == "x86_64" else ("windows-arm64" if a == "arm64" else "windows-x86")
    if o == "osx":
        return "mac-os-arm64" if a == "arm64" else "mac-os"
    return "linux-i386" if a == "x86" else "linux"


def physical_ram_mb() -> int:
    """Total physical RAM in MB (best effort; defaults to 8192 MB on failure)."""
    try:
        if os_name() == "windows":
            import ctypes
            class _MemStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            status = _MemStatusEx()
            status.dwLength = ctypes.sizeof(status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return max(2048, int(status.ullTotalPhys // (1024 * 1024)))
        elif os_name() == "linux":
            try:
                with open("/proc/meminfo", "r", encoding="utf-8") as fh:
                    for line in fh:
                        if line.startswith("MemTotal:"):
                            kb = int(line.split()[1])
                            return max(2048, kb // 1024)
            except (OSError, ValueError, IndexError):
                pass
        elif os_name() == "osx":
            out = subprocess.run(
                ["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=10)
            total = (out.stdout or "").strip()
            if total.isdigit():
                return max(2048, int(total) // (1024 * 1024))
    except Exception:  # noqa: BLE001
        pass
    return 8192


# --------------------------------------------------------------------------
# http


def http_json(url: str, timeout: int = 40):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def http_json_first(urls, timeout: int = 40):
    last = None
    for u in urls:
        try:
            return http_json(u, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - try the next mirror
            last = exc
    raise last


# --------------------------------------------------------------------------
# cached json (makes startup instant)


def read_json_cache(path: str):
    """Return the cached payload, or None if it is missing or unreadable."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            wrapped = json.load(fh)
    except (OSError, ValueError):
        return None
    if isinstance(wrapped, dict) and "data" in wrapped:
        return wrapped
    return None


def write_json_cache(path: str, data, etag=None, last_modified=None) -> None:
    """Atomically store a payload plus the validators needed to revalidate it."""
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"data": data, "etag": etag,
                       "last_modified": last_modified, "saved": time.time()},
                      fh)
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass


def http_json_cached(url: str, cache_path: str, timeout: int = 40):
    """Fetch JSON, revalidating an on-disk copy.

    Returns ``(data, state)`` where state is:
      ``fresh``  - downloaded now
      ``cached`` - server said 304 Not Modified
      ``stale``  - network failed, the saved copy was used
    Raises only when there is no usable cache.
    """
    cached = read_json_cache(cache_path) or {}
    headers = {"User-Agent": USER_AGENT}
    if cached.get("etag"):
        headers["If-None-Match"] = cached["etag"]
    if cached.get("last_modified"):
        headers["If-Modified-Since"] = cached["last_modified"]
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
            write_json_cache(cache_path, data,
                             etag=r.headers.get("ETag"),
                             last_modified=r.headers.get("Last-Modified"))
            return data, "fresh"
    except urllib.error.HTTPError as exc:
        if exc.code == 304 and cached.get("data") is not None:
            return cached["data"], "cached"
        if cached.get("data") is not None:
            return cached["data"], "stale"
        raise
    except Exception:  # noqa: BLE001 - offline or timeout
        if cached.get("data") is not None:
            return cached["data"], "stale"
        raise


def sha1_file(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _looks_like_ssl_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return isinstance(exc, ssl.SSLError) or "certificate" in text or "ssl" in text


def _curl_download(url: str, dest: str) -> bool:
    """Fetch with Windows' built-in curl when Python's trust store rejects TLS.

    Some CDNs (Modrinth's, for one) chain to a root that OpenSSL does not ship
    with this Python build, while Windows' own certificate store accepts it.
    The file is still SHA-1 checked afterwards, so this is not a trust bypass.
    """
    curl = shutil.which("curl") or shutil.which("curl.exe")
    if not curl:
        return False
    cmd = [curl, "-sS", "--fail", "--location", "--retry", "2", "--max-time", "900",
           "-A", USER_AGENT, "-o", dest, url]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=960)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and os.path.isfile(dest) and os.path.getsize(dest) > 0


def download(url: str, dest: str, sha1: str | None = None, size: int | None = None,
             progress=None, cancel=None, retries: int = 3, fast: bool = False) -> str:
    """Download to ``dest``; skip when the hash already matches.

    ``progress(done_bytes, total_bytes)`` is called while streaming and
    ``cancel()`` may raise Cancelled at any time.

    ``fast=True`` accepts an existing file when its size matches, without
    re-reading it to compute SHA-1. Used when launching a version that was
    already fully verified, so pressing PLAY does not re-hash ~600 MB.
    """
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    if not sha1 and os.path.isfile(dest) and os.path.getsize(dest) > 0:
        # Maven-style artifacts (Fabric loader libraries) publish no hash in
        # the launcher profile. Once the file is there, keep it.
        return dest
    if sha1 and os.path.isfile(dest) and size is not None and os.path.getsize(dest) == size:
        if fast:
            if progress:
                progress(size, size)
            return dest
        try:
            if sha1_file(dest) == sha1:
                if progress:
                    progress(size, size)
                return dest
        except OSError:
            pass
    elif sha1 and os.path.isfile(dest) and size is None:
        try:
            if sha1_file(dest) == sha1:
                return dest
        except OSError:
            pass

    # unique temp name per process: two launcher windows must never write the
    # same .part file, which would corrupt the download and stall retries
    tmp = "%s.part%d" % (dest, os.getpid())
    last = None
    for attempt in range(retries):
        try:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=60) as r:
                    total = size or int(r.headers.get("Content-Length") or 0)
                    done = 0
                    with open(tmp, "wb") as out:
                        while True:
                            if cancel:
                                cancel()
                            chunk = r.read(1 << 16)
                            if not chunk:
                                break
                            out.write(chunk)
                            done += len(chunk)
                            if progress:
                                progress(done, total)
            except Cancelled:
                raise
            except Exception as exc:  # noqa: BLE001
                # Python's own trust store can be missing a root that Windows
                # has. Retry through the system curl, then hash-check as usual.
                if not _looks_like_ssl_error(exc) or not _curl_download(url, tmp):
                    raise
                if progress:
                    got = os.path.getsize(tmp)
                    progress(got, size or got)
            if sha1 and sha1_file(tmp) != sha1:
                raise IOError("hash mismatch for " + url)
            os.replace(tmp, dest)
            return dest
        except Cancelled:
            _quiet_remove(tmp)
            raise
        except Exception as exc:  # noqa: BLE001 - retry the same file
            last = exc
            _quiet_remove(tmp)
            time.sleep(0.6 * (attempt + 1))
    raise IOError("failed to download %s (%s)" % (url, last))


def _quiet_remove(path: str) -> None:
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


# --------------------------------------------------------------------------
# rule evaluation (per Mojang's launcher spec)


def os_version() -> str:
    """Kernel version, e.g. ``10.0.22631`` on Windows.

    Used for the ``versionRange`` rules in the version JSON.
    """
    v = platform.version() or ""
    if os_name() == "osx":
        v = platform.mac_ver()[0] or v
    return v


def _version_tuple(text: str) -> tuple:
    parts = []
    for chunk in str(text).split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _version_match(spec) -> bool:
    """Mojang encodes an OS version rule as either a regex or a min/max range."""
    current = os_version()
    if isinstance(spec, str):
        try:
            return re.match(spec, current) is not None
        except re.error:
            return False
    if isinstance(spec, dict):
        cur = _version_tuple(current)
        low = spec.get("min")
        high = spec.get("max")
        if low is not None and cur < _version_tuple(low):
            return False
        if high is not None and cur >= _version_tuple(high):
            return False
        return True
    return True


def _rule_applies(rule: dict, features: dict | None = None) -> bool:
    os_rule = rule.get("os") or {}
    if os_rule:
        name = os_rule.get("name")
        if name and name != os_name():
            return False
        arch = os_rule.get("arch")
        if arch and arch != os_arch():
            return False
        if os_rule.get("version") is not None and not _version_match(os_rule["version"]):
            return False
        if os_rule.get("versionRange") is not None and not _version_match(os_rule["versionRange"]):
            return False
    for key, want in (rule.get("features") or {}).items():
        if bool(want) != bool((features or {}).get(key, False)):
            return False
    return True


def rule_matches(rule: dict) -> bool:
    return _rule_applies(rule)


def allowed_by_rules(item: dict, features: dict | None = None) -> bool:
    rules = item.get("rules")
    if not rules:
        return True
    allowed = False
    for rule in rules:
        if _rule_applies(rule, features):
            allowed = rule.get("action") == "allow"
    return allowed


def allowed_args(entries, features=None) -> list:
    """Flatten a modern arguments list, honouring conditional entries."""
    out = []
    for entry in entries or []:
        if isinstance(entry, str):
            out.append(entry)
            continue
        if not allowed_by_rules(entry, features):
            continue
        val = entry.get("value")
        if isinstance(val, list):
            out.extend(val)
        elif isinstance(val, str):
            out.append(val)
    return out


# --------------------------------------------------------------------------
# zip / natives


def extract_natives(jar: str, target_dir: str, exclude=("META-INF/",)) -> list:
    """Extract native libraries from ``jar`` into ``target_dir``."""
    os.makedirs(target_dir, exist_ok=True)
    written = []
    with zipfile.ZipFile(jar) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename
            if any(name.startswith(x) for x in exclude):
                continue
            if name.endswith("/"):
                continue
            dest = os.path.join(target_dir, os.path.basename(name))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with zf.open(info) as src, open(dest, "wb") as out:
                out.write(src.read())
            written.append(dest)
    return written


def sha1_hex(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def offline_uuid(name: str) -> str:
    """Same deterministic UUID scheme vanilla uses for offline players."""
    digest = hashlib.md5(("OfflinePlayer:" + name).encode("utf-8")).digest()
    b = bytearray(digest)
    b[6] = (b[6] & 0x0F) | 0x30
    b[8] = (b[8] & 0x3F) | 0x80
    h = b.hex()
    return "%s-%s-%s-%s-%s" % (h[0:8], h[8:12], h[12:16], h[16:20], h[20:32])


def json_dump_atomic(obj, path: str) -> None:
    """Write JSON without leaving a half-written file behind."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)
    os.replace(tmp, path)


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num) < 1024 or unit == "GB":
            return ("%.0f %s" if unit == "B" else "%.1f %s") % (num, unit)
        num /= 1024.0
    return "%.1f GB" % num
