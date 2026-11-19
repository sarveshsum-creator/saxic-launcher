"""SAXIC Launcher - offline self-check (no big downloads).

Verifies the metadata pipeline and the launch command builder:

    python saxic_selftest.py [version-id]
"""
from __future__ import annotations

import os
import sys

import saxic_install as install
import saxic_launch as launch


def main(argv):
    want = argv[1] if len(argv) > 1 else None
    man = install.fetch_manifest()
    print("release:", man["latest"]["release"], "| snapshot:", man["latest"]["snapshot"],
          "| total versions:", len(man["versions"]))

    vid = want or man["latest"]["release"]
    meta = install.version_meta(man, vid)
    if not meta:
        print("FAIL: version not found:", vid)
        return 1
    vj = install.fetch_version_json(meta)
    info = install.plan(vj)
    print("chosen:", vid, "type:", meta["type"])
    for k, v in info.items():
        print("  %-15s %s" % (k, v))

    libs, natives, _ = install.library_tasks(vj)
    print("libraries kept for this OS:", len(libs), "| native jars:", len(natives))
    bad = [a for a in libs if not a.get("path") or not a.get("url")]
    print("libraries missing path/url:", len(bad))

    here = os.path.dirname(os.path.abspath(__file__))
    fake_root = os.path.join(here, "_selftest", "game")
    prepared = {
        "version_id": vid,
        "classpath": [os.path.join(fake_root, "libraries", a["path"].replace("/", os.sep)) for a in libs]
                     + [os.path.join(fake_root, "versions", vid, vid + ".jar")],
        "natives_dir": os.path.join(fake_root, "versions", vid, "natives"),
        "assets_root": os.path.join(fake_root, "assets"),
        "asset_index": (vj.get("assetIndex") or {}).get("id", "legacy"),
        "main_class": vj["mainClass"],
        "logging": None,
        "java_version": vj.get("javaVersion") or {},
    }
    settings = {"game_dir": fake_root, "ram_mb": 2048}
    account = launch.offline_account("Tester")
    cmd = launch.build_command(prepared, vj, account, settings, r"C:\fake\bin\javaw.exe")
    print("command parts:", len(cmd))
    checks = {
        "has java": cmd[0].endswith("javaw.exe"),
        "has memory": any(a.startswith("-Xmx") for a in cmd),
        "has classpath": any(a == "-cp" for a in cmd),
        "has main class": vj["mainClass"] in cmd,
        "has username arg": account["name"] in cmd,
        "no unresolved placeholder": not any("${" in a for a in cmd),
        "classpath entries exist as strings": sum(len(a.split(os.pathsep)) for a in cmd if os.pathsep in a) >= len(libs),
    }
    fails = 0
    for name, ok in checks.items():
        print(("PASS " if ok else "FAIL ") + name)
        fails += 0 if ok else 1

    print("\n--- jvm args ---")
    print(" ".join(a for a in cmd[:24] if a.startswith("-")))
    print("--- game args (first 20) ---")
    print(" ".join(cmd[cmd.index(vj["mainClass"]) + 1:][:20]))
    print("\nsystem java found:", install.find_system_java())

    print("\n--- mod library ---")
    import json as _json
    import shutil as _shutil

    import saxic_mods as mods
    here_self = os.path.join(here, "_selftest")
    fake_mods_game = os.path.join(here_self, "modgame")
    _shutil.rmtree(fake_mods_game, ignore_errors=True)
    os.makedirs(mods.mods_dir(fake_mods_game), exist_ok=True)

    art = install.maven_artifact("net.fabricmc:fabric-loader:0.20.1",
                                 "https://maven.fabricmc.net/")
    _json.dump({"id": "child", "inheritsFrom": "parent",
                "mainClass": "example.Knot",
                "libraries": [{"name": "net.fabricmc:fabric-loader:0.20.1",
                               "url": "https://maven.fabricmc.net/"}],
                "arguments": {"game": ["--demo"], "jvm": ["-DFabric=1"]}},
               open(os.path.join(fake_mods_game, "child.json"), "w", encoding="utf-8"))
    os.makedirs(os.path.join(fake_mods_game, "versions", "parent"), exist_ok=True)
    _json.dump({"id": "parent", "mainClass": "net.minecraft.client.main.Main",
                "assetIndex": {"id": "test"},
                "libraries": [{"name": "parent:lib:1", "downloads": {"artifact": {
                    "path": "parent/lib/1/lib-1.jar", "url": "https://example.invalid/lib.jar",
                    "size": 10, "sha1": "00"}}}],
                "arguments": {"game": ["--username", "${auth_player_name}"],
                              "jvm": ["-Dparent=1"]}},
               open(os.path.join(fake_mods_game, "versions", "parent", "parent.json"),
                    "w", encoding="utf-8"))

    # a fake installed mod, then disable it, then remove it
    open(os.path.join(mods.mods_dir(fake_mods_game), "demo.jar"), "wb").write(b"jar")
    mods._write_manifest(fake_mods_game, {"demo.jar": {
        "project_id": "p1", "slug": "demo", "title": "Demo Mod",
        "version_number": "1.0", "version_type": "release", "filename": "demo.jar"}})
    demo_installed = mods.installed_mods(fake_mods_game)
    mods.set_enabled(fake_mods_game, "demo.jar", False)
    demo_disabled = mods.installed_mods(fake_mods_game)
    demo_count = mods.installed_count(fake_mods_game)
    mods.remove_mod(fake_mods_game, "demo.jar")
    demo_removed = mods.installed_mods(fake_mods_game)

    open(os.path.join(mods.mods_dir(fake_mods_game), "manual.jar"), "wb").write(b"jar")
    manual_list = mods.installed_mods(fake_mods_game)
    manual_removed = mods.remove_all(fake_mods_game)
    manual_after = mods.installed_mods(fake_mods_game)

    child = _json.load(open(os.path.join(fake_mods_game, "child.json"), encoding="utf-8"))
    merged = install.resolve_version(child, fake_mods_game)
    lib_names = [lib.get("name") for lib in merged.get("libraries") or []]
    mod_checks = {
        "maven coordinate resolves": bool(art) and art["path"].endswith(
            "net/fabricmc/fabric-loader/0.20.1/fabric-loader-0.20.1.jar"),
        "maven url has repository": bool(art) and art["url"].startswith(
            "https://maven.fabricmc.net/net/fabricmc/"),
        "loader id is readable": install.loader_profile_id("26.3", "0.20.1")
                                == "fabric-loader-0.20.1-26.3",
        "inheritsFrom merges main class": merged.get("mainClass") == "example.Knot",
        "inheritsFrom keeps parent libs": "parent:lib:1" in lib_names,
        "child library comes first": lib_names[:1] == ["net.fabricmc:fabric-loader:0.20.1"],
        "arguments are combined": "--username" in merged["arguments"]["game"]
                                  and "--demo" in merged["arguments"]["game"]
                                  and "-Dparent=1" in merged["arguments"]["jvm"]
                                  and "-DFabric=1" in merged["arguments"]["jvm"],
        "installed mod is listed": len(demo_installed) == 1 and demo_installed[0]["enabled"],
        "disable parks the jar": len(demo_disabled) == 1 and not demo_disabled[0]["enabled"],
        "disabled mod still counted": demo_count == 1,
        "remove cleans the manifest": demo_removed == [],
        "manual jars are listed": len(manual_list) == 1
                                  and bool(manual_list[0].get("manual")),
        "remove all clears manual jars": manual_removed == 1 and manual_after == [],
        "release beats beta": (mods.pick_version(
            [{"version_type": "beta", "files": [{"url": "u", "filename": "b.jar"}]},
             {"version_type": "release", "files": [{"url": "u", "filename": "r.jar"}]}]
        ) or {}).get("version_type") == "release",
        "search sorts are known": set(mods.SORTS.values()) == {
            "relevance", "downloads", "newest", "updated"},
    }
    for name, ok in mod_checks.items():
        print(("PASS " if ok else "FAIL ") + name)
        fails += 0 if ok else 1
    _shutil.rmtree(here_self, ignore_errors=True)

    print("\n--- credits ---")

    def _is_date(value):
        parts = str(value or "").split()
        return len(parts) == 3 and parts[0].isdigit() and parts[2].isdigit()

    try:
        import saxic_launcher as ui
        info = ui.credits_info()
        credit_checks = {
            "created date is set": _is_date(info["created"]),
            "updated date is set": _is_date(info["updated"]),
            "both publisher names": info["publishers"] == ["SARVESH.S", "srxvesh._.11"],
            "inspiration credited": info["inspiration"] == "clumsydev",
        }
    except Exception as exc:  # noqa: BLE001
        credit_checks = {"credits build": False}
        print("credits error:", exc)
    for name, ok in credit_checks.items():
        print(("PASS " if ok else "FAIL ") + name)
        fails += 0 if ok else 1

    print("RESULT:", "ALL CHECKS PASSED" if not fails else ("%d CHECK(S) FAILED" % fails))
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
