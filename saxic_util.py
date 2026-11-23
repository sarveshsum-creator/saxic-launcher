"""XI Launcher - shared helpers (networking, hashing, zip, rules).

Original code written for the XI Launcher project. No third-party
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
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

VERSION_MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
RUNTIME_MANIFESTS = [
    "https://piston-meta.mojang.com/v1/products/java-runtime/2ec0cc96c44e5a76b9c8b7c39df7210883d12871/all.json",
    "https://launchermeta.mojang.com/v1/products/java-runtime/2ec0cc96c44e5a76b9c8b7c39df7210883d12871/all.json",
]
USER_AGENT = "XILauncher/1.1 (+https://github.com/sarveshsum-creator/xi-launcher)"


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
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
        root = os.path.join(base, "XI Launcher")
        old = os.path.join(base, "SAXIC Launcher")
        if not os.path.isdir(root) and os.path.isdir(old):
            # Keep settings when upgrading from the old product name.
            try:
                shutil.copytree(old, root)
            except OSError:
                return old
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
def memory_status() -> dict:
    """Report RAM and Windows commit headroom, in MB (best effort).

    ``pagefile_mb`` is the extra swap space Windows can commit beyond physical
    RAM. When it is ~0 there is no pagefile, and the Windows commit limit equals
    physical RAM: a big modpack then dies with a *native* out-of-memory error
    (``hs_err_pid*.log``: "insufficient memory ... Chunk::new") during world
    load, no matter how the Java heap is sized.
    """
    out = {"total_mb": 0, "avail_mb": 0, "commit_limit_mb": 0,
           "commit_free_mb": 0, "pagefile_mb": 0}
    try:
        if os_name() != "windows":
            out["total_mb"] = physical_ram_mb()
            out["avail_mb"] = available_ram_mb() or out["total_mb"]
            return out
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
            total = int(status.ullTotalPhys // (1024 * 1024))
            limit = int(status.ullTotalPageFile // (1024 * 1024))
            out["total_mb"] = total
            out["avail_mb"] = int(status.ullAvailPhys // (1024 * 1024))
            out["commit_limit_mb"] = limit
            out["commit_free_mb"] = int(status.ullAvailPageFile // (1024 * 1024))
            out["pagefile_mb"] = max(0, limit - total)
    except Exception:  # noqa: BLE001
        pass
    return out


def available_ram_mb() -> int:
    """Physical RAM free right now, in MB (best effort).

    Returns 0 when it cannot be measured; callers should then treat the
    machine as having ``physical_ram_mb()`` to spend.
    """
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
                return max(0, int(status.ullAvailPhys // (1024 * 1024)))
        elif os_name() == "linux":
            try:
                with open("/proc/meminfo", "r", encoding="utf-8") as fh:
                    for line in fh:
                        if line.startswith("MemAvailable:"):
                            return max(0, int(line.split()[1]) // 1024)
            except (OSError, ValueError, IndexError):
                pass
        elif os_name() == "osx":
            out = subprocess.run(
                ["vm_stat"], capture_output=True, text=True, timeout=10)
            free_pages = 0
            for line in (out.stdout or "").splitlines():
                if "page size of" in line:
                    continue
                if line.startswith("Pages free:") or line.startswith("Pages inactive:"):
                    digits = "".join(c for c in line.split(":")[1] if c.isdigit())
                    free_pages += int(digits or 0)
            if free_pages:
                return max(0, free_pages * 4096 // (1024 * 1024))
    except Exception:  # noqa: BLE001
        pass
    return 0


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


def sha256_file(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
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
             progress=None, cancel=None, retries: int = 3, fast: bool = False,
             sha256: str | None = None) -> str:
    """Download to ``dest``; skip when the hash already matches.

    ``progress(done_bytes, total_bytes)`` is called while streaming and
    ``cancel()`` may raise Cancelled at any time. Either SHA-1 or SHA-256 can
    be supplied for verification.

    ``fast=True`` accepts an existing file when its size matches, without
    re-reading it to compute SHA-1. Used when launching a version that was
    already fully verified, so pressing PLAY does not re-hash ~600 MB.
    """
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    if not sha1 and not sha256 and os.path.isfile(dest) and os.path.getsize(dest) > 0:
        # Maven-style artifacts (Fabric loader libraries) publish no hash in
        # the launcher profile. Once the file is there, keep it.
        return dest
    if (sha1 or sha256) and os.path.isfile(dest) and size is not None and os.path.getsize(dest) == size:
        if fast:
            if progress:
                progress(size, size)
            return dest
        try:
            if sha1 and sha1_file(dest) == sha1:
                if progress:
                    progress(size, size)
                return dest
            if sha256 and sha256_file(dest) == sha256:
                if progress:
                    progress(size, size)
                return dest
        except OSError:
            pass
    elif (sha1 or sha256) and os.path.isfile(dest) and size is None:
        try:
            if sha1 and sha1_file(dest) == sha1:
                return dest
            if sha256 and sha256_file(dest) == sha256:
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
            if sha256 and sha256_file(tmp) != sha256:
                raise IOError("sha256 mismatch for " + url)
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


# --------------------------------------------------------------------------
# time formatting

def format_duration(seconds: float) -> str:
    """Human duration: 45 -> '45s', 3661 -> '1h 1m', 90000 -> '1d 1h'."""
    seconds = int(max(0, seconds))
    if seconds < 60:
        return "%ds" % seconds
    minutes = seconds // 60
    if minutes < 60:
        return "%dm" % minutes
    hours = minutes // 60
    minutes = minutes % 60
    if hours < 24:
        return ("%dh %dm" % (hours, minutes)) if minutes else ("%dh" % hours)
    days = hours // 24
    hours = hours % 24
    return ("%dd %dh" % (days, hours)) if hours else ("%dd" % days)


def relative_time(timestamp: float) -> str:
    """'never' / 'just now' / '5 minutes ago' / '3 days ago' for a timestamp."""
    if not timestamp:
        return "never"
    delta = time.time() - float(timestamp)
    if delta < 45:
        return "just now"
    if delta < 90:
        return "a minute ago"
    minutes = int(delta // 60)
    if minutes < 60:
        return "%d minutes ago" % minutes
    hours = int(delta // 3600)
    if hours < 24:
        return "%d hour%s ago" % (hours, "" if hours == 1 else "s")
    days = int(delta // 86400)
    if days < 30:
        return "%d day%s ago" % (days, "" if days == 1 else "s")
    months = days // 30
    return "%d month%s ago" % (months, "" if months == 1 else "s")


def dir_size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def open_path(path: str) -> None:
    """Open a file or folder in the OS default app / file manager."""
    if not path:
        return
    if os_name() == "windows":
        os.startfile(path)  # type: ignore[attr-defined]
    elif os_name() == "osx":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


# --------------------------------------------------------------------------
# worlds (singleplayer saves) and backups


def worlds_dir(game_dir: str) -> str:
    return os.path.join(game_dir, "saves")


def backups_dir(game_dir: str) -> str:
    return os.path.join(game_dir, "backups")


def scan_worlds(game_dir: str) -> list:
    """Singleplayer worlds as dicts: name, path, size, modified, icon, locked."""
    folder = worlds_dir(game_dir)
    out = []
    try:
        names = os.listdir(folder)
    except OSError:
        return out
    for name in names:
        path = os.path.join(folder, name)
        if not os.path.isdir(path):
            continue
        if not os.path.isfile(os.path.join(path, "level.dat")):
            continue
        try:
            mtime = os.path.getmtime(os.path.join(path, "level.dat"))
        except OSError:
            mtime = 0
        icon = os.path.join(path, "icon.png")
        out.append({
            "name": name,
            "path": path,
            "size": dir_size(path),
            "modified": mtime,
            "icon": icon if os.path.isfile(icon) else "",
        })
    out.sort(key=lambda w: w["modified"], reverse=True)
    return out


def backup_world(game_dir: str, world_name: str, progress=None) -> str:
    """Zip a world into <game_dir>/backups and return the archive path."""
    src = os.path.join(worlds_dir(game_dir), world_name)
    if not os.path.isdir(src):
        raise IOError("world not found: %s" % world_name)
    dest_dir = backups_dir(game_dir)
    os.makedirs(dest_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", world_name).strip("_") or "world"
    dest = os.path.join(dest_dir, "%s-%s.zip" % (safe, stamp))
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(src):
            for fname in files:
                full = os.path.join(root, fname)
                rel = os.path.relpath(full, src)
                zf.write(full, os.path.join(world_name, rel))
                if progress:
                    progress(full)
    return dest


def list_backups(game_dir: str) -> list:
    folder = backups_dir(game_dir)
    out = []
    try:
        names = os.listdir(folder)
    except OSError:
        return out
    for name in names:
        if name.lower().endswith(".zip"):
            p = os.path.join(folder, name)
            try:
                st = os.stat(p)
            except OSError:
                continue
            out.append({"name": name, "path": p, "size": st.st_size, "modified": st.st_mtime})
    out.sort(key=lambda b: b["modified"], reverse=True)
    return out


def restore_world(game_dir: str, zip_path: str) -> str:
    """Restore a world backup without overwriting an existing world.

    Returns the name of the world folder that was created.
    """
    target_root = worlds_dir(game_dir)
    os.makedirs(target_root, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="saxic-restore-")
    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(tmp)
        folders = [e for e in os.listdir(tmp) if os.path.isdir(os.path.join(tmp, e))]
        if not folders:
            raise IOError("this backup has no world folder inside")
        top = sorted(folders)[0]
        dest = os.path.join(target_root, top)
        n = 1
        while os.path.exists(dest):
            dest = os.path.join(target_root, "%s (%d)" % (top, n))
            n += 1
        shutil.move(os.path.join(tmp, top), dest)
        return os.path.basename(dest)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
# screenshots


def screenshots_dir(game_dir: str) -> str:
    return os.path.join(game_dir, "screenshots")


def scan_screenshots(game_dir: str) -> list:
    folder = screenshots_dir(game_dir)
    out = []
    try:
        names = os.listdir(folder)
    except OSError:
        return out
    for name in names:
        if not name.lower().endswith(".png"):
            continue
        p = os.path.join(folder, name)
        try:
            st = os.stat(p)
        except OSError:
            continue
        out.append({"name": name, "path": p, "size": st.st_size, "modified": st.st_mtime})
    out.sort(key=lambda s: s["modified"], reverse=True)
    return out


# --------------------------------------------------------------------------
# java runtimes


def java_version(exe: str) -> str:
    """The '1.8.0_402' / '21.0.1' string reported by ``java -version``."""
    try:
        proc = subprocess.run([exe, "-version"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return ""
    text = (proc.stderr or "") + (proc.stdout or "")
    m = re.search(r'version "([^"]+)"', text)
    return m.group(1) if m else ""


def list_java_runtimes(game_dir: str = "") -> list:
    """Every Java runtime we can find, newest-looking first.

    Returns dicts: path, version ('21.0.1' or ''), label.
    """
    exes = ("javaw.exe", "java.exe") if os_name() == "windows" else ("java",)
    found: list = []

    def add(path):
        if path and os.path.isfile(path):
            found.append(path)

    for name in ("javaw", "java"):
        add(shutil.which(name))
    jh = os.environ.get("JAVA_HOME")
    if jh:
        for name in exes:
            add(os.path.join(jh, "bin", name))
    bases = []
    if os_name() == "windows":
        for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if base and os.path.isdir(base):
                bases.append(base)
    if game_dir:
        # Mojang's runtime layout: <game_dir>/runtime/<component>/<platform>/bin
        rt = os.path.join(game_dir, "runtime")
        if os.path.isdir(rt):
            for comp in sorted(os.listdir(rt)):
                comp_dir = os.path.join(rt, comp)
                if not os.path.isdir(comp_dir):
                    continue
                for plat in sorted(os.listdir(comp_dir)):
                    for exe in exes:
                        add(os.path.join(comp_dir, plat, "bin", exe))
    vendor_dirs = ("Eclipse Adoptium", "Java", "Microsoft", "Amazon Corretto", "Zulu",
                   "BellSoft", "Eclipse Foundation", "Semeru", "OpenJDK", "jdk")
    for base in bases:
        for vendor in vendor_dirs:
            d = os.path.join(base, vendor)
            if not os.path.isdir(d):
                continue
            for name in reversed(sorted(os.listdir(d))):
                for exe in exes:
                    p = os.path.join(d, name, "bin", exe)
                    if os.path.isfile(p):
                        found.append(p)
                        break
        # also a flat ".../jdk-21/bin" layout
        for name in sorted(os.listdir(base), reverse=True):
            if not name.lower().startswith(("jdk", "jre", "temurin", "zulu", "corretto")):
                continue
            for exe in exes:
                p = os.path.join(base, name, "bin", exe)
                if os.path.isfile(p):
                    found.append(p)
                    break

    seen = set()
    out = []
    for path in found:
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            continue
        seen.add(key)
        ver = java_version(path)
        label = (("%s  -  " % ver) if ver else "") + path
        out.append({"path": path, "version": ver, "label": label})

    def major(item):
        v = item["version"]
        if not v:
            return 0
        first = v.split(".")[0]
        if first == "1" and "." in v:  # 1.8.0_x
            parts = v.split(".")
            return int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        return int(first) if first.isdigit() else 0

    out.sort(key=major, reverse=True)
    return out


# --------------------------------------------------------------------------
# low-memory graphics tuning


# Keys in Minecraft's options.txt that most affect memory/CPU. Values are the
# safe ceiling for a small-RAM PC; a lower user setting is never raised.
LOW_MEMORY_OPTIONS = {
    "renderDistance": 8,
    "simulationDistance": 6,
    "mipmapLevels": 2,
    "entityShadows": 0,
    "maxFps": 60,
}


def tune_options_for_memory(game_dir: str, keys: dict | None = None) -> bool:
    """Clamp heavy graphics options in ``options.txt`` for a low-memory PC.

    Only lowers values that are above the safe ceiling, so a player's own
    lighter settings are kept. The untouched file is saved as
    ``options.txt.saxic-backup`` the first time. Returns True when the file
    was changed.

    This matters on PCs without a pagefile: a 16-chunk render distance and
    12-chunk simulation distance can push a modded world past the Windows
    commit limit within a couple of minutes, which is what makes the game
    close itself.
    """
    keys = dict(keys or LOW_MEMORY_OPTIONS)
    path = os.path.join(game_dir, "options.txt")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return False

    changed = False
    seen = set()
    out = []
    for line in lines:
        key, sep, value = line.partition(":")
        if sep and key in keys:
            seen.add(key)
            try:
                current = int(float(value))
            except ValueError:
                current = None
            if current is not None and current > int(keys[key]):
                line = "%s:%s" % (key, keys[key])
                changed = True
        out.append(line)
    for key, value in keys.items():
        if key not in seen:
            out.append("%s:%s" % (key, value))
            changed = True

    if not changed:
        return False

    backup = path + ".saxic-backup"
    try:
        if not os.path.exists(backup):
            with open(backup, "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(out) + "\n")
    except OSError:
        return False
    return True

