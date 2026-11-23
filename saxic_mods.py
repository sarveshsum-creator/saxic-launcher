"""XI Launcher - mod library (Modrinth's public API, Fabric mods).

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
MAX_DEP_DEPTH = 8


SORTS = {
    "Featured": "featured",
    "Relevance": "relevance",
    "Downloads": "downloads",
    "Newest": "newest",
    "Updated": "updated",
}

# Curated Fabric mods organised by category. Every entry here is checked
# against Modrinth when shown, so only mods with a build for the target
# Minecraft version appear in the library. Entries that don't exist or have
# no matching build are simply skipped.
MOD_CATEGORIES = {
    "Performance": [
        "sodium", "lithium", "ferrite-core", "krypton", "badoptimizations",
        "entityculling", "moreculling", "dynamic-fps", "immediatelyfast",
        "phosphor", "existential", "embeddedium", "starlight", "oculus",
        "synthetic-threads", "argon", "entity-distance-fade", "cull-leaves",
        "parallax-chunks", "distant-horizons", "entity-distance-fade",
        "fast-furnace", "fast-bench", "fast-workbench", "torch-master",
        "sleeping-in-cages", "quick-shulker", "indium", "memoryleakfix",
        "fastload", "lazy-dfu", "noisium", "threadtweak",
        "sodium-shadowy-path-blocks",
    ],
    "Visuals & Shaders": [
        "iris", "continuity", "sodium-extra", "reeses-sodium-options",
        "lambdynamiclights", "entitytexturefeatures", "entity-model-features",
        "3dskinlayers", "debugify", "embeddedyouth", "composable-shaders",
        "starlight", "oculus", "entity-rendering", "kerosin",
        "entity-culling-fix", "falling-leaves", "visuality", "particular",
        "effective", "wakes", "first-person-model", "eating-animation",
        "capes", "cit-resewn", "fabricskyboxes", "cameraoverhaul",
        "ok-zoomer", "wi-zoom",
    ],
    "Quality of Life": [
        "modmenu", "appleskin", "jei", "jade", "mouse-tweaks",
        "shulkerboxtooltip", "chat-heads", "no-chat-reports", "betterf3",
        "zoomify", "wthit", "cloth-config", "yacl", "balm", "fancymenu",
        "konkrete", "melody", "tweakeroo", "minihud", "axiom",
        "not-enough-animations", "particle-rain", "roughly-enough-items",
        "item-tooltips", "mouse-wheelie", "control-room", "janitor",
        "keep-inventory", "go-to-bed", "emi", "trashslot",
        "inventory-sorter", "toast-control", "screenshot-to-clipboard",
        "better-ping-display", "tooltipfix", "legendary-tooltips",
        "no-telemetry", "boat-item-view", "elytra-flight-hud",
        "do-a-barrel-roll",
    ],
    "Libraries & API": [
        "fabric-api", "architectury-api", "malilib", "puzzles-lib",
        "geckolib", "collective", "bookshelf-lib", "creativecore",
        "fzzy-config", "creativecore", "fabric-language-kotlin",
        "spell-engine", "ranged-weapon-api",
    ],
    "World & Generation": [
        "terralith", "nullscape", "biomes-o-plenty", "explorers-compass",
        "natures-compass", "waystones", "xaeros-minimap", "xaeros-world-map",
        "oh-the-biomes-youll-go", "present", "worldedit",
        "yungs-better-dungeons", "yungs-better-strongholds",
        "yungs-better-mineshafts", "yungs-better-desert-temples",
        "yungs-better-nether-fortresses", "when-dungeons-arise",
        "dungeons-and-taverns", "structory", "incendium",
        "amplified-nether", "cinderscapes", "traverse",
        "regions-unexplored", "tectonic", "geophilic",
    ],
    "Tools & Building": [
        "litematica", "chunky", "structure-gel", "building-utilities",
        "schematica", "print", "building-gadgets", "construction-wand",
        "effortless-building",
    ],
    "Storage & Inventory": [
        "storagedrawers", "inventory-management", "cooking-for-blockheads",
        "veinminer", "shulkerboxtooltip", "backpacks", "iron-chests",
        "barrels", "inventory-profiles-next", "item-physics",
        "travelers-backpack", "expanded-storage", "trash-cans",
    ],
    "Magic & Adventure": [
        "ars-nouveau", "apotheosis", "better-combat", "epic-fight",
        "alexs-mobs", "nature-aura", "create", "origin",
        "blood-magic", "dank-null", "ender-chest-inventory",
        "wizards", "paladins", "berserker", "simply-swords",
        "aquamirae",
    ],
    "Horror": [
        "the-zone", "liminal", "creeping-dawn", "the-void", "nightmare",
        "scare", "horror-mod", "entity-303", "void-darkness", "creeping",
        "liminal-spaces", "the-backrooms", "from-the-fog", "cave-dweller",
        "the-man-from-the-fog", "siren-head", "sculk-horde", "the-rotting",
    ],
    "Food & Farming": [
        "cooking-for-blockheads", "farmers-delight", "crop-maturity",
        "animal-troughs", "shear-madness", "milkables", "egg-basket",
        "croptopia", "simple-farming", "bakery", "candlelight",
        "beachparty", "vinery", "meadow", "expanded-delight",
        "farmers-respite", "nethers-delight", "ends-delight",
    ],
}


# Live category filters. Category pages are built from Modrinth's own tags,
# filtered to Fabric builds of the selected Minecraft version. That way every
# mod a player sees is actually installable, even right after a new Minecraft
# release when many hand-picked mods have no build yet.
CATEGORY_FILTERS = {
    "Performance": {"tags": ["optimization"]},
    "Visuals & Shaders": {"tags": ["decoration"]},
    "Quality of Life": {"tags": ["utility"]},
    "Libraries & API": {"tags": ["library"]},
    "World & Generation": {"tags": ["worldgen"]},
    "Tools & Building": {"queries": ["building"]},
    "Storage & Inventory": {"tags": ["storage"]},
    "Magic & Adventure": {"tags": ["magic", "adventure"]},
    "Horror": {"queries": ["horror", "scary", "haunted"]},
    "Food & Farming": {"tags": ["food"]},
}


def category_slugs(category: str) -> list:
    """The curated slug list for a category, or every slug when unknown."""
    return MOD_CATEGORIES.get(category, [])


def all_category_slugs() -> list:
    """Every curated slug across all categories, de-duplicated."""
    seen = []
    for slugs in MOD_CATEGORIES.values():
        for slug in slugs:
            if slug not in seen:
                seen.append(slug)
    return seen


def category_names() -> list:
    """Every category name, in display order."""
    return list(MOD_CATEGORIES.keys())


# --------------------------------------------------------------------------
# searching


def search(query: str = "", game_version: str = "", offset: int = 0,
           limit: int = 20, sort: str = "relevance", tags: list | None = None):
    """Return ``(hits, total)`` for Fabric mods matching ``query``.

    ``tags`` is an OR-list of Modrinth category tags (for example
    ``["magic", "adventure"]``); every hit must carry at least one of them.
    """
    facets = [["project_type:mod"], ["categories:" + LOADER]]
    if tags:
        facets.append(["categories:" + str(t).lower() for t in tags if str(t).strip()])
    if game_version:
        facets.append(["versions:" + game_version])
    params = {
        "query": query or "",
        "limit": str(max(1, min(100, int(limit)))),
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
    """Every Fabric build of a mod, newest first.

    When ``game_version`` is given only builds that target that Minecraft
    version are returned, so "newest" really means "newest for this version".
    """
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
            "project_id": v.get("project_id") or "",
            "name": v.get("name") or v.get("version_number"),
            "version_number": v.get("version_number") or "",
            "version_type": v.get("version_type") or "release",
            "published": v.get("date_published") or "",
            "downloads": int(v.get("downloads") or 0),
            "game_versions": v.get("game_versions") or [],
            "loaders": v.get("loaders") or [],
            "files": files,
            "dependencies": v.get("dependencies") or [],
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
# featured mods


def featured_mods(game_version: str = "", limit: int = 60) -> list:
    """Popular Fabric mods that have a build for this version.

    Uses Modrinth's live search, so every hit is installable for
    ``game_version`` even right after a Minecraft release.
    """
    return featured_mods_by_category("", game_version, limit)


def featured_mods_by_category(category: str, game_version: str = "",
                              limit: int = 60) -> list:
    """Popular Fabric mods for a library category that work with this version.

    The category is a live Modrinth query (see ``CATEGORY_FILTERS``) filtered
    to builds for ``game_version``. Every returned mod really has a compatible
    Fabric build, which matters because hand-picked lists go stale whenever
    Minecraft updates.
    """
    limit = max(1, min(100, int(limit)))
    if not category:
        hits, _total = search("", game_version, limit=limit, sort="downloads")
        return hits

    rule = CATEGORY_FILTERS.get(category)
    if not rule:
        return []

    out = []
    seen = set()
    for query in rule.get("queries") or [""]:
        hits, _total = search(query, game_version, limit=limit, sort="downloads",
                              tags=rule.get("tags"))
        for hit in hits:
            slug = hit.get("slug")
            if not slug or slug in seen:
                continue
            seen.add(slug)
            out.append(hit)
    out.sort(key=lambda h: h.get("downloads") or 0, reverse=True)
    return out[:limit]


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


def _dep_project(project_id: str) -> dict | None:
    """Look up a Modrinth project by its numeric id (best effort)."""
    try:
        return http_json(API + "/project/%s" % project_id)
    except Exception:  # noqa: BLE001
        return None


def _dep_entry(project: dict, version: dict, filename: str) -> dict:
    return {
        "project_id": project.get("project_id") or version.get("project_id") or "",
        "slug": project.get("slug") or "",
        "title": project.get("title") or version.get("name") or filename,
        "author": project.get("author") or "",
        "version_id": version.get("id"),
        "version_number": version.get("version_number") or "",
        "version_type": version.get("version_type") or "release",
        "filename": filename,
        "sha1": None,
        "size": 0,
        "installed_at": int(time.time()),
    }


def _install_dep_chain(game_dir: str, version: dict, game_version: str,
                      installed_projects: set, depth: int = 0) -> None:
    """Recursively install all required dependencies of ``version``."""
    key = version.get("id") or ""
    if not key or key in installed_projects or depth > MAX_DEP_DEPTH:
        return
    installed_projects.add(key)
    for dep in version.get("dependencies") or []:
        if dep.get("dependency_type") != "required":
            continue
        dep_id = dep.get("project_id")
        if not dep_id:
            continue
        dep_project = _dep_project(dep_id)
        if not dep_project:
            continue
        dep_versions = project_versions(dep_project.get("slug") or "", game_version)
        dep_version = pick_version(dep_versions, prefer_release=True)
        if not dep_version or not primary_file(dep_version):
            continue
        dp = primary_file(dep_version)
        dn = os.path.basename(dp["filename"])
        dd = os.path.join(mods_dir(game_dir), dn)
        download(dp["url"], dd, dp.get("sha1"), dp.get("size"))
        data = _read_manifest(game_dir)
        # drop older builds of this same project so we never keep two versions
        dep_pid = dep_project.get("project_id") or ""
        if dep_pid:
            for old, oldentry in list(data.items()):
                if old != dn and oldentry.get("project_id") == dep_pid:
                    _delete_file(mods_dir(game_dir), old)
                    data.pop(old, None)
        entry = _dep_entry(dep_project, dep_version, dn)
        entry["sha1"] = dp.get("sha1")
        entry["size"] = dp.get("size") or 0
        data[dn] = entry
        _write_manifest(game_dir, data)
        _install_dep_chain(game_dir, dep_version, game_version, installed_projects, depth + 1)


def install_mod(game_dir: str, version: dict, project: dict,
               install_deps: bool = False, game_version: str = "") -> dict:
    """Download a mod build into ``mods`` and record it in the manifest.

    With ``install_deps=True`` the required library projects are installed
    first (transitively, for the given Minecraft version), so the chosen build
    actually loads in Minecraft.
    """
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

    if install_deps:
        _install_dep_chain(game_dir, version, game_version, set())

    data = _read_manifest(game_dir)
    # one build per project: drop older files from the same mod
    project_id = project.get("project_id") or version.get("project_id") or ""
    if project_id:
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
