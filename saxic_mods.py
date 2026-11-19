"""SAXIC Launcher - mod library (Modrinth's public API, Fabric mods).

Mods are plain jar files in ``<game folder>/mods``. Installing one also makes
sure Fabric is set up for the selected Minecraft version, because vanilla
cannot load mods.

Everything here is original code for this project. It only reads Modrinth's
public API and downloads the files the mod authors publish.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse

from saxic_util import download, http_json

API = "https://api.modrinth.com/v2"
SITE = "https://modrinth.com/mod/"
MANIFEST = ".saxic-mods.json"
LOADER = "fabric"

SORTS = {
    "Relevance": "relevance",
    "Downloads": "downloads",
    "Newest": "newest",
    "Updated": "updated",
}


# --------------------------------------------------------------------------
# searching


def search(query: str = "", game_version: str = "", offset: int = 0,
           limit: int = 20, sort: str = "relevance"):
    """Return ``(hits, total)`` for Fabric mods matching ``query``."""
    facets = [["project_type:mod"], ["categories:" + LOADER]]
    if game_version:
        facets.append(["versions:" + game_version])
    params = {
        "query": query or "",
        "limit": str(max(1, min(50, int(limit)))),
        "offset": str(max(0, int(offset))),
        "index": sort or "relevance",
        "facets": json.dumps(facets),
    }
    data = http_json(API + "/search?" + urllib.parse.urlencode(params))
    hits = []
    for item in data.get("hits") or []:
        hits.append({
            "project_id": item.get("project_id"),
            "slug": item.get("slug"),
            "title": item.get("title") or item.get("slug"),
            "description": item.get("description") or "",
            "author": item.get("author") or "",
            "downloads": int(item.get("downloads") or 0),
            "follows": int(item.get("follows") or 0),
            "icon_url": item.get("icon_url") or "",
            "categories": item.get("categories") or [],
            "versions": item.get("versions") or [],
        })
    return hits, int(data.get("total_hits") or len(hits))


def project_versions(slug: str, game_version: str = "") -> list:
    """Every Fabric build of a mod, newest first."""
    params = {"loaders": json.dumps([LOADER])}
    if game_version:
        params["game_versions"] = json.dumps([game_version])
    url = "%s/project/%s/version?%s" % (API, urllib.parse.quote(slug), urllib.parse.urlencode(params))
    data = http_json(url)
    out = []
    for v in data if isinstance(data, list) else []:
        files = []
        for f in v.get("files") or []:
            files.append({
                "filename": f.get("filename"),
                "url": f.get("url"),
                "size": int(f.get("size") or 0),
                "primary": bool(f.get("primary")),
                "sha1": (f.get("hashes") or {}).get("sha1"),
            })
        out.append({
            "id": v.get("id"),
            "name": v.get("name") or v.get("version_number"),
            "version_number": v.get("version_number") or "",
            "version_type": v.get("version_type") or "release",
            "published": v.get("date_published") or "",
            "downloads": int(v.get("downloads") or 0),
            "game_versions": v.get("game_versions") or [],
            "loaders": v.get("loaders") or [],
            "files": files,
        })
    out.sort(key=lambda v: v.get("published") or "", reverse=True)
    return out


def primary_file(version: dict) -> dict | None:
    files = [f for f in (version.get("files") or []) if f.get("url") and f.get("filename")]
    if not files:
        return None
    for f in files:
        if f.get("primary"):
            return f
    return files[0]


def pick_version(versions: list, prefer_release: bool = True) -> dict | None:
    """The build to offer first: newest stable, else newest of any type."""
    if not versions:
        return None
    if prefer_release:
        for v in versions:
            if (v.get("version_type") or "") == "release" and primary_file(v):
                return v
    for v in versions:
        if primary_file(v):
            return v
    return versions[0]


# --------------------------------------------------------------------------
# installed mods


def mods_dir(game_dir: str) -> str:
    return os.path.join(game_dir, "mods")


def manifest_path(game_dir: str) -> str:
    return os.path.join(mods_dir(game_dir), MANIFEST)


def _read_manifest(game_dir: str) -> dict:
    try:
        with open(manifest_path(game_dir), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_manifest(game_dir: str, data: dict) -> None:
    path = manifest_path(game_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass


def installed_mods(game_dir: str) -> list:
    """Everything in the mods folder: library installs and manually added jars."""
    root = mods_dir(game_dir)
    out = []
    seen = set()
    for filename, entry in _read_manifest(game_dir).items():
        enabled_path = os.path.join(root, filename)
        disabled_path = enabled_path + ".disabled"
        if os.path.isfile(enabled_path):
            enabled = True
        elif os.path.isfile(disabled_path):
            enabled = False
        else:
            continue
        item = dict(entry)
        item["filename"] = filename
        item["enabled"] = enabled
        out.append(item)
        seen.add(filename)

    try:
        names = os.listdir(root)
    except OSError:
        names = []
    for name in names:
        if name.endswith(".jar.disabled"):
            filename, enabled = name[:-len(".disabled")], False
        elif name.endswith(".jar"):
            filename, enabled = name, True
        else:
            continue
        if filename in seen:
            continue
        out.append({"filename": filename, "title": filename[:-4],
                    "version_number": "manual", "version_type": "manual",
                    "enabled": enabled, "manual": True})
    out.sort(key=lambda m: (m.get("title") or m["filename"]).lower())
    return out


def installed_count(game_dir: str) -> int:
    return len(installed_mods(game_dir))


def install_mod(game_dir: str, version: dict, project: dict) -> dict:
    """Download a mod build into ``mods`` and record it in the manifest."""
    primary = primary_file(version)
    if not primary:
        raise RuntimeError("this build has no downloadable file")
    root = mods_dir(game_dir)
    os.makedirs(root, exist_ok=True)
    filename = os.path.basename(primary["filename"])
    dest = os.path.join(root, filename)
    download(primary["url"], dest, primary.get("sha1"), primary.get("size"))
    # a jar that was disabled under the same name becomes active again
    try:
        os.remove(dest + ".disabled")
    except OSError:
        pass

    data = _read_manifest(game_dir)
    # one build per project: drop older files from the same mod
    project_id = project.get("project_id") or version.get("project_id")
    for old, entry in list(data.items()):
        if old != filename and entry.get("project_id") == project_id:
            _delete_file(root, old)
            data.pop(old, None)

    entry = {
        "project_id": project_id,
        "slug": project.get("slug") or "",
        "title": project.get("title") or version.get("name") or filename,
        "author": project.get("author") or "",
        "description": project.get("description") or "",
        "version_id": version.get("id"),
        "version_number": version.get("version_number") or "",
        "version_type": version.get("version_type") or "release",
        "filename": filename,
        "sha1": primary.get("sha1"),
        "size": primary.get("size") or 0,
        "installed_at": int(time.time()),
    }
    data[filename] = entry
    _write_manifest(game_dir, data)
    return dict(entry, filename=filename, enabled=True)


def _delete_file(root: str, filename: str) -> None:
    for path in (os.path.join(root, filename), os.path.join(root, filename + ".disabled")):
        try:
            os.remove(path)
        except OSError:
            pass


def remove_mod(game_dir: str, filename: str) -> None:
    _delete_file(mods_dir(game_dir), filename)
    data = _read_manifest(game_dir)
    data.pop(filename, None)
    _write_manifest(game_dir, data)


def set_enabled(game_dir: str, filename: str, enabled: bool) -> bool:
    """Rename the jar so the game ignores it (``.jar.disabled``)."""
    root = mods_dir(game_dir)
    active = os.path.join(root, filename)
    parked = active + ".disabled"
    try:
        if enabled and os.path.isfile(parked):
            os.replace(parked, active)
        elif not enabled and os.path.isfile(active):
            os.replace(active, parked)
    except OSError:
        pass
    return os.path.isfile(active)


def remove_all(game_dir: str) -> int:
    items = installed_mods(game_dir)
    root = mods_dir(game_dir)
    for item in items:
        _delete_file(root, item["filename"])
    _write_manifest(game_dir, {})
    return len(items)


def open_folder(game_dir: str) -> str:
    root = mods_dir(game_dir)
    os.makedirs(root, exist_ok=True)
    try:
        os.startfile(root)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        import subprocess
        subprocess.Popen(["explorer", root])
    return root