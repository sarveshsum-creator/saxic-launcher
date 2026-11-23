"""XI Launcher - install engine (version files, libraries, assets, Java).

Downloads happen straight from Mojang's public metadata URLs, exactly the
data the official launcher reads. All code here is original to this project.
"""
from __future__ import annotations

import json
import os
import shutil
import threading as _threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from saxic_util import (RUNTIME_MANIFESTS, VERSION_MANIFEST, Cancelled, allowed_args,
                        allowed_by_rules, data_dir, download, extract_natives, http_json,
                        http_json_cached, http_json_first, os_arch, os_name,
                        platform_key, read_json_cache)

MANIFEST_CACHE = os.path.join(data_dir(), "cache", "version_manifest_v2.json")
FABRIC_META = "https://meta.fabricmc.net/v2"


# --------------------------------------------------------------------------
# metadata


def cached_manifest():
    """The last version list we saved, or None. Never touches the network."""
    wrapped = read_json_cache(MANIFEST_CACHE)
    return wrapped.get("data") if wrapped else None


def local_version_json(game_dir: str, ver_id: str):
    """A version JSON stored in the game folder, or None."""
    path = os.path.join(game_dir, "versions", ver_id, ver_id + ".json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _merge_version(parent: dict, child: dict) -> dict:
    """Merge an ``inheritsFrom`` child on top of its parent version."""
    out = dict(parent)
    for key, value in child.items():
        if key in ("libraries", "arguments", "minecraftArguments"):
            continue
        if key == "downloads":
            out["downloads"] = value or parent.get("downloads") or {}
        else:
            out[key] = value

    # libraries: child entries win, then keep the parent's remaining ones
    seen = set()
    merged = []
    for lib in list(child.get("libraries") or []) + list(parent.get("libraries") or []):
        name = lib.get("name")
        if name and name in seen:
            continue
        if name:
            seen.add(name)
        merged.append(lib)
    out["libraries"] = merged

    # arguments: parent first, then the loader's extras
    p_args, c_args = parent.get("arguments"), child.get("arguments")
    if isinstance(p_args, dict) or isinstance(c_args, dict):
        out["arguments"] = {
            "game": list((p_args or {}).get("game") or []) + list((c_args or {}).get("game") or []),
            "jvm": list((p_args or {}).get("jvm") or []) + list((c_args or {}).get("jvm") or []),
        }
    elif c_args or p_args:
        out["arguments"] = list(p_args or []) + list(c_args or [])
    if child.get("minecraftArguments") or parent.get("minecraftArguments"):
        out["minecraftArguments"] = "%s %s" % (
            parent.get("minecraftArguments") or "", child.get("minecraftArguments") or "")
    return out


def resolve_version(ver_json: dict, game_dir: str, manifest: dict | None = None,
                    _depth: int = 0) -> dict:
    """Flatten ``inheritsFrom`` chains (Fabric/Quilt profiles use them).

    The result keeps ``inheritsFrom`` so the UI can still tell which game
    version a loader profile belongs to, plus ``_saxic_resolved`` so we never
    merge the same profile twice.
    """
    if ver_json.get("_saxic_resolved"):
        return ver_json
    parent_id = ver_json.get("inheritsFrom")
    if not parent_id:
        return ver_json
    if _depth > 8:
        raise RuntimeError("version inheritance is too deep")
    parent = local_version_json(game_dir, parent_id)
    if parent is None:
        if manifest is None:
            manifest = fetch_manifest()
        meta = version_meta(manifest, parent_id)
        if not meta:
            raise RuntimeError("missing parent version %s" % parent_id)
        parent = fetch_version_json(meta)
    parent = resolve_version(parent, game_dir, manifest, _depth + 1)
    merged = _merge_version(parent, ver_json)
    merged["inheritsFrom"] = parent_id
    merged["_saxic_resolved"] = True
    return merged


def load_version_json(ver_id: str, meta: dict | None, game_dir: str,
                      manifest: dict | None = None) -> dict:
    """Get a usable version JSON for ``ver_id`` (local first, then Mojang)."""
    vj = local_version_json(game_dir, ver_id)
    if vj is None:
        if not meta or not meta.get("url"):
            raise RuntimeError("version %s is not installed" % ver_id)
        vj = http_json(meta["url"])
    return resolve_version(vj, game_dir, manifest)


def fetch_manifest(progress=None) -> dict:
    """Version manifest, revalidated against the saved copy when possible."""
    data, _state = http_json_cached(VERSION_MANIFEST, MANIFEST_CACHE)
    return data


def fetch_manifest_state():
    """``(manifest, state)`` where state is ``fresh``/``cached``/``stale``."""
    return http_json_cached(VERSION_MANIFEST, MANIFEST_CACHE)


def version_meta(manifest: dict, version_id: str) -> dict | None:
    for v in manifest.get("versions", []):
        if v.get("id") == version_id:
            return v
    return None


def fetch_version_json(meta: dict) -> dict:
    return http_json(meta["url"])


# --------------------------------------------------------------------------
# planning


def natives_classifier(lib: dict) -> str | None:
    n = lib.get("natives") or {}
    key = {"windows": "windows", "osx": "osx", "linux": "linux"}.get(os_name())
    val = n.get(key) if key else None
    if not val:
        return None
    return val.replace("${arch}", "64" if os_arch() == "x86_64" else "32")


def maven_artifact(name: str, base_url: str) -> dict | None:
    """Turn a Maven coordinate into a downloadable artifact.

    Fabric's launcher profile lists libraries as ``group:artifact:version``
    plus a repository URL instead of Mojang-style ``downloads`` entries, so
    those have to be resolved here.
    """
    parts = (name or "").split(":")
    if len(parts) < 3 or not base_url:
        return None
    group, artifact, version = parts[0], parts[1], parts[2]
    classifier = parts[3] if len(parts) > 3 else None
    filename = "%s-%s%s.jar" % (artifact, version,
                                ("-" + classifier) if classifier else "")
    path = "/".join([group.replace(".", "/"), artifact, version, filename])
    base = base_url if base_url.endswith("/") else base_url + "/"
    return {"path": path, "url": base + path, "size": None, "sha1": None}


def library_tasks(ver_json: dict):
    """Return (classpath_libs, native_extracts, total_bytes) for this machine."""
    libs, natives, total = [], [], 0
    for lib in ver_json.get("libraries", []):
        if not allowed_by_rules(lib):
            continue
        dl = lib.get("downloads") or {}
        name = lib.get("name", "")
        if lib.get("natives") and dl.get("classifiers"):
            # legacy layout: natives live in a separate classifier jar, the
            # main jar belongs on the classpath
            cls = natives_classifier(lib)
            art = (dl.get("classifiers") or {}).get(cls) if cls else None
            if art:
                natives.append((art, lib.get("extract") or {}))
                total += art.get("size") or 0
            main = dl.get("artifact")
            if main:
                total += main.get("size") or 0
                libs.append(main)
            continue
        art = dl.get("artifact")
        if not art and lib.get("url") and name:
            art = maven_artifact(name, lib["url"])
        if not art:
            continue
        total += art.get("size") or 0
        # every artifact goes on the classpath. Native jars stay there too:
        # modern LWJGL extracts them itself from the classpath, and dropping
        # them is what causes "Failed to locate library: lwjgl.dll".
        libs.append(art)
        if ":natives-" in name:
            natives.append((art, lib.get("extract") or {}))
    return libs, natives, total


def _native_targets(ver_json: dict, natives_root: str):
    """Work out where the native DLLs must live for this version.

    Older JSONs use ``-Djava.library.path=${natives_directory}``, but newer ones
    append a sub-folder (``/java``) and also point LWJGL, JNA and Netty at
    their own sub-folders. Getting this wrong gives:
    ``UnsatisfiedLinkError: Failed to locate library: lwjgl.dll``.
    Returns ``(library_dir, extra_dirs)``.
    """
    jvm = allowed_args((ver_json.get("arguments") or {}).get("jvm"))
    props = {
        "-Djava.library.path=": (natives_root, False),
        "-Dorg.lwjgl.system.SharedLibraryExtractPath=": (os.path.join(natives_root, "lwjgl"), True),
        "-Djna.tmpdir=": (os.path.join(natives_root, "jna"), True),
        "-Dio.netty.native.workdir=": (os.path.join(natives_root, "netty"), True),
    }
    library_dir = natives_root
    extra = []
    for arg in jvm:
        if not isinstance(arg, str):
            continue
        for prefix, (default, is_extra) in props.items():
            if arg.startswith(prefix):
                value = arg[len(prefix):].replace("${natives_directory}", natives_root)
                if is_extra:
                    extra.append(value)
                else:
                    library_dir = value
    # make sure the dirs we hand to the JVM actually exist and are writable
    os.makedirs(library_dir, exist_ok=True)
    for d in extra:
        os.makedirs(d, exist_ok=True)
    return library_dir, extra


def plan(ver_json: dict) -> dict:
    libs, natives, lib_bytes = library_tasks(ver_json)
    client = (ver_json.get("downloads") or {}).get("client", {})
    ai = ver_json.get("assetIndex") or {}
    return {
        "id": ver_json.get("id"),
        "client_bytes": client.get("size", 0),
        "library_bytes": lib_bytes,
        "library_count": len(libs),
        "native_count": len(natives),
        "asset_index": ai.get("id"),
        "asset_bytes": ai.get("totalSize", 0),
        "java": (ver_json.get("javaVersion") or {}).get("component"),
    }


# --------------------------------------------------------------------------
# installing


def _library_path(game_dir: str, art: dict) -> str:
    return os.path.join(game_dir, "libraries", *(art["path"].split("/")))


READY_MARKER = ".saxic-ready.json"


def _ready_path(game_dir: str, ver_id: str) -> str:
    return os.path.join(game_dir, "versions", ver_id, READY_MARKER)


def is_ready(game_dir: str, ver_id: str, ver_json: dict) -> bool:
    """True when this exact version was fully downloaded and verified before."""
    try:
        with open(_ready_path(game_dir, ver_id), "r", encoding="utf-8") as fh:
            saved = json.load(fh)
    except (OSError, ValueError):
        return False
    client = ((ver_json.get("downloads") or {}).get("client") or {}).get("sha1")
    index = (ver_json.get("assetIndex") or {}).get("id")
    return (saved.get("version") == ver_id
            and saved.get("client_sha1") == client
            and saved.get("asset_index") == index)


def mark_ready(game_dir: str, ver_id: str, ver_json: dict) -> None:
    """Remember that this version is installed, so PLAY can skip re-hashing."""
    client = ((ver_json.get("downloads") or {}).get("client") or {}).get("sha1")
    index = (ver_json.get("assetIndex") or {}).get("id")
    path = _ready_path(game_dir, ver_id)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"version": ver_id, "client_sha1": client,
                       "asset_index": index, "when": int(time.time())}, fh)
        os.replace(tmp, path)
    except OSError:
        pass


def install_version(ver_id: str, ver_json: dict, game_dir: str, log=print,
                    progress=None, cancel=None, with_assets: bool = True,
                    workers: int = 16, fast: bool = False) -> dict:
    """Make the game files ready and return everything needed to launch.

    ``fast=True`` is the PLAY path: if this exact version was verified before,
    existing files are accepted on name/size instead of being SHA-1 checked
    again. The "Install / verify" button always passes ``fast=False``.
    """
    libs_root = os.path.join(game_dir, "libraries")
    natives_dir = os.path.join(game_dir, "versions", ver_id, "natives")
    version_dir = os.path.join(game_dir, "versions", ver_id)
    os.makedirs(natives_dir, exist_ok=True)
    os.makedirs(libs_root, exist_ok=True)

    if fast and not is_ready(game_dir, ver_id, ver_json):
        fast = False
    if fast:
        log("already verified - checking file sizes only (press Install / verify to re-check hashes)")

    # local copy of the version json (the game and mods expect it here)
    from saxic_util import json_dump_atomic  # local import keeps the module tidy
    json_dump_atomic(ver_json, os.path.join(version_dir, ver_id + ".json"))

    libs, natives, lib_bytes = library_tasks(ver_json)
    client = (ver_json.get("downloads") or {}).get("client")
    ai = ver_json.get("assetIndex") or {}

    total = lib_bytes + (client or {}).get("size", 0)
    if with_assets:
        total += ai.get("totalSize", 0)
    lock = _threading.Lock()
    done = {"n": 0, "bytes": 0}

    def step(label, delta=0):
        with lock:
            done["bytes"] += delta
            current = done["bytes"]
        if progress:
            progress(current, total, label)

    # client jar
    classpath = []
    parent_id = ver_json.get("inheritsFrom")
    parent_jar = (os.path.join(game_dir, "versions", parent_id, parent_id + ".jar")
                  if parent_id else None)
    if client and parent_jar and os.path.isfile(parent_jar):
        # A loader profile inherits the game jar from its parent version. Use
        # that file instead of downloading a second copy.
        log("client jar  using %s" % os.path.basename(parent_jar))
        classpath.append(os.path.normpath(parent_jar))
        step("client jar", 0)
    elif client:
        dest = os.path.join(version_dir, ver_id + ".jar")
        log("client jar  %s" % client.get("url", "").rsplit("/", 1)[-1])
        download(client["url"], dest, client.get("sha1"), client.get("size"),
                 progress=lambda d, t: step("client jar", 0), cancel=cancel, fast=fast)
        step("client jar", client.get("size", 0))
        classpath.append(dest)

    # libraries (several connections in flight; they are big files)
    def grab_library(art):
        dest = _library_path(game_dir, art)
        download(art["url"], dest, art.get("sha1"), art.get("size"), cancel=cancel, fast=fast)
        return dest, art.get("size") or 0

    if libs:
        with ThreadPoolExecutor(max_workers=8) as pool:
            for dest, size in pool.map(grab_library, libs):
                step("library " + os.path.basename(dest), size)
                classpath.append(dest)
    log("%d libraries ready" % len(libs))

    # native jars -> extract into the folder the version json expects
    lib_dir, extra_dirs = _native_targets(ver_json, natives_dir)
    if extra_dirs:
        log("native layout: dlls in %s (+ %d scratch dirs)" % (lib_dir, len(extra_dirs)))
    for art, extract in natives:
        dest = _library_path(game_dir, art)
        download(art["url"], dest, art.get("sha1"), art.get("size"), cancel=cancel, fast=fast)
        exclude = tuple(extract.get("exclude") or ()) + ("META-INF/",)
        extract_natives(dest, lib_dir, exclude=exclude)
        step("natives " + os.path.basename(dest), art.get("size") or 0)
    log("%d native jars extracted" % len(natives))

    assets_root = os.path.join(game_dir, "assets")
    index_id = ai.get("id") or ver_json.get("assets") or "legacy"
    if ai.get("url") and with_assets:
        index_path = os.path.join(assets_root, "indexes", index_id + ".json")
        download(ai["url"], index_path, ai.get("sha1"), ai.get("size"), cancel=cancel, fast=fast)
        index = json.load(open(index_path, "r", encoding="utf-8"))
        objs = index.get("objects") or {}
        log("assets index %s: %d objects" % (index_id, len(objs)))
        _download_assets(assets_root, objs, log=log, step=step, cancel=cancel,
                         workers=int(workers or 16), fast=fast)
        _materialise_virtual(assets_root, index_id, index, log)
    elif index_id and index_id != "legacy":
        log("note: version has no downloadable asset index")

    # logging config
    logging_entry = (ver_json.get("logging") or {}).get("client")
    log_arg = None
    if logging_entry and logging_entry.get("file"):
        f = logging_entry["file"]
        dest = os.path.join(assets_root, "log_configs", f["id"])
        download(f["url"], dest, f.get("sha1"), f.get("size"), cancel=cancel, fast=fast)
        log_arg = (logging_entry.get("argument"), dest)

    mark_ready(game_dir, ver_id, ver_json)

    return {
        "version_id": ver_id,
        "classpath": classpath,
        "natives_dir": natives_dir,
        "assets_root": assets_root,
        "asset_index": index_id,
        "main_class": ver_json.get("mainClass"),
        "logging": log_arg,
        "java_version": ver_json.get("javaVersion") or {},
    }


def _download_assets(assets_root: str, objs: dict, log=print, step=None,
                     cancel=None, workers: int = 16, fast: bool = False) -> None:
    """Fetch the asset objects with a small thread pool.

    There are ~5000 tiny files; doing them one at a time makes the launcher
    wait on round-trip latency instead of bandwidth. Several connections in
    flight is what the official launcher does too.
    """
    base = "https://resources.download.minecraft.net"
    items = list(objs.items())
    total_n = len(items)
    counter = {"n": 0, "last": 0}
    lock = _threading.Lock()

    def one(_name, obj):
        h = obj["hash"]
        dest = os.path.join(assets_root, "objects", h[:2], h)
        download("%s/%s/%s" % (base, h[:2], h), dest, h, obj.get("size"),
                 cancel=cancel, fast=fast)
        return obj.get("size", 0)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(one, name, obj) for name, obj in items]
        for fut in as_completed(futures):
            size = fut.result()  # raises, which cancels the rest
            step and step("assets", size)
            with lock:
                counter["n"] += 1
                n = counter["n"]
            if n - counter["last"] >= 500:
                counter["last"] = n
                log("  %d/%d assets" % (n, total_n))
    log("  %d/%d assets" % (total_n, total_n))


def _materialise_virtual(assets_root: str, index_id: str, index: dict, log=print) -> None:
    """Old versions expect a flat assets/virtual/<id>/ tree of named files."""
    if not (index.get("virtual") or index.get("map_to_resources")):
        return
    target = os.path.join(assets_root, "virtual" if index.get("virtual") else "resources", index_id)
    made = 0
    for name, obj in (index.get("objects") or {}).items():
        src = os.path.join(assets_root, "objects", obj["hash"][:2], obj["hash"])
        dst = os.path.join(target, *(name.split("/")))
        if not os.path.isfile(dst):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            try:
                shutil.copyfile(src, dst)
                made += 1
            except OSError:
                pass
    if made:
        log("virtual assets: %d files linked for %s" % (made, index_id))


# --------------------------------------------------------------------------
# mod loaders (Fabric)


def fabric_loader_versions(game_version: str) -> list:
    """Loader builds Fabric publishes for a Minecraft version, newest first."""
    data = http_json("%s/versions/loader/%s" % (FABRIC_META, game_version))
    return data if isinstance(data, list) else []


def fabric_profile(game_version: str, loader_version: str) -> dict:
    """The launcher profile Fabric generates for this version pair."""
    return http_json("%s/versions/loader/%s/%s/profile/json" %
                     (FABRIC_META, game_version, loader_version))


def loader_profile_id(game_version: str, loader_version: str) -> str:
    return "fabric-loader-%s-%s" % (loader_version, game_version)


def ensure_loader(game_version: str, loader_version: str, game_dir: str,
                  log=print, progress=None, cancel=None, fast: bool = False):
    """Install Fabric for ``game_version`` and return ``(profile_id, prepared)``.

    The vanilla version must be installed first; when it is missing it is
    downloaded here so the user can go straight from "install mod" to "play".
    """
    profile = fabric_profile(game_version, loader_version)
    if profile.get("inheritsFrom") != game_version:
        raise RuntimeError("Fabric profile does not inherit %s" % game_version)
    profile_id = profile.get("id") or loader_profile_id(game_version, loader_version)

    parent = local_version_json(game_dir, game_version)
    if parent is None:
        man = fetch_manifest()
        meta = version_meta(man, game_version)
        if not meta:
            raise RuntimeError("unknown Minecraft version %s" % game_version)
        log("installing the base game %s first" % game_version)
        parent = fetch_version_json(meta)
        install_version(game_version, parent, game_dir, log=log, progress=progress,
                        cancel=cancel, with_assets=True, fast=False)

    version_dir = os.path.join(game_dir, "versions", profile_id)
    os.makedirs(version_dir, exist_ok=True)
    from saxic_util import json_dump_atomic
    json_dump_atomic(profile, os.path.join(version_dir, profile_id + ".json"))

    merged = resolve_version(profile, game_dir)
    log("Fabric %s for %s: %d libraries" %
        (loader_version, game_version, len(merged.get("libraries") or [])))
    prepared = install_version(profile_id, merged, game_dir, log=log,
                               progress=progress, cancel=cancel,
                               with_assets=False, fast=fast)
    prepared["loader"] = {"name": "fabric", "version": loader_version,
                          "game_version": game_version}
    return profile_id, prepared


# --------------------------------------------------------------------------
# java runtime (Mojang ships one; we fetch the same files the official launcher uses)


def _runtime_entry(runtime_manifest: dict, component: str):
    group = runtime_manifest.get(platform_key()) or {}
    entries = group.get(component) or []
    for e in entries:
        if (e.get("availability") or {}).get("group") in (0, None):
            return e
    return entries[0] if entries else None


def install_java(component: str, game_dir: str, log=print, progress=None, cancel=None) -> str:
    rt = http_json_first(RUNTIME_MANIFESTS)
    entry = _runtime_entry(rt, component)
    if not entry:
        raise RuntimeError("Mojang has no '%s' runtime for %s" % (component, platform_key()))
    man = entry.get("manifest") or {}
    log("java runtime: %s %s (%s)" % (component, entry.get("version", {}).get("name", ""),
                                      man.get("url", "").rsplit("/", 1)[-1]))
    files = http_json(man["url"])
    root = os.path.join(game_dir, "runtime", component, platform_key())
    entries = list((files.get("files") or {}).items())
    tasks = []
    for path, f in entries:
        clean = path.replace("\\", "/")
        if f.get("type") == "directory":
            os.makedirs(os.path.join(root, *(clean.split("/"))), exist_ok=True)
            continue
        if f.get("type") != "file":
            continue
        raw = (f.get("downloads") or {}).get("raw")
        if not raw:
            continue
        tasks.append((clean, raw))
    total = sum(raw.get("size", 0) for _p, raw in tasks)
    lock = _threading.Lock()
    state = {"done": 0}

    def grab(clean, raw):
        dest = os.path.join(root, *(clean.split("/")))
        download(raw["url"], dest, raw.get("sha1"), raw.get("size"), cancel=cancel)
        with lock:
            state["done"] += raw.get("size", 0)
            done = state["done"]
        if progress:
            progress(done, total, "java " + os.path.basename(clean))
        return clean, dest

    exe = None
    with ThreadPoolExecutor(max_workers=8) as pool:
        for clean, dest in pool.map(lambda t: grab(*t), tasks):
            if clean.endswith("bin/javaw.exe") or clean.endswith("bin/java"):
                if not exe:
                    exe = dest
    if os_name() == "windows" and exe and exe.endswith("java.exe"):
        javaw = exe[:-8] + "javaw.exe"
        if os.path.isfile(javaw):
            exe = javaw
    if not exe or not os.path.isfile(exe):
        raise RuntimeError("java runtime installed but launcher binary not found")
    log("java ready: " + exe)
    return exe


def find_system_java() -> str | None:
    if os_name() == "windows":
        candidates = []
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        for base in (pf, pf86):
            for vendor in ("Eclipse Adoptium", "Java", "Microsoft", "Amazon Corretto", "Zulu", "BellSoft"):
                d = os.path.join(base, vendor)
                if not os.path.isdir(d):
                    continue
                for name in sorted(os.listdir(d), reverse=True):
                    for exe in ("javaw.exe", "java.exe"):
                        p = os.path.join(d, name, "bin", exe)
                        if os.path.isfile(p):
                            candidates.append(p)
        if candidates:
            return candidates[0]
    which = shutil.which("javaw") or shutil.which("java")
    return which


def ensure_java(ver_json: dict, game_dir: str, java_override: str | None = None,
                log=print, progress=None, cancel=None) -> str:
    if java_override and os.path.isfile(java_override):
        return java_override
    component = (ver_json.get("javaVersion") or {}).get("component")
    if component:
        try:
            return install_java(component, game_dir, log=log, progress=progress, cancel=cancel)
        except Cancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - fall back to a system JVM
            log("note: could not fetch Mojang runtime (%s); trying system Java" % exc)
    found = find_system_java()
    if found:
        log("using system java: " + found)
        return found
    raise RuntimeError("No Java runtime available. Install Java 21+ or let the launcher fetch "
                       "Mojang's runtime (it needs internet access).")


# --------------------------------------------------------------------------
# small conveniences


def write_launcher_profile(game_dir: str, version_id: str) -> None:
    path = os.path.join(game_dir, "launcher_profiles.json")
    if os.path.isfile(path):
        return
    profile = {
        "profiles": {
            "XI": {
                "name": "XI",
                "type": "custom",
                "lastVersionId": version_id,
                "created": "1970-01-01T00:00:00.000Z",
            }
        },
        "selectedProfile": "XI",
        "clientToken": "xi-launcher",
    }
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(profile, fh, indent=2)
    except OSError:
        pass


def installed_versions(game_dir: str) -> list:
    d = os.path.join(game_dir, "versions")
    if not os.path.isdir(d):
        return []
    out = []
    for name in os.listdir(d):
        if os.path.isfile(os.path.join(d, name, name + ".jar")):
            out.append(name)
    return sorted(out)
