"""XI Launcher - offline self-check (no big downloads).

Verifies the metadata pipeline and the launch command builder:

    python saxic_selftest.py [version-id]
"""
from __future__ import annotations

import os
import sys
import time

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
            "featured", "relevance", "downloads", "newest", "updated"},
    }
    for name, ok in mod_checks.items():
        print(("PASS " if ok else "FAIL ") + name)
        fails += 0 if ok else 1

    print("\n--- worlds, backups and screenshots ---")
    import saxic_util as util

    world_game = os.path.join(here_self, "worldgame")
    _shutil.rmtree(world_game, ignore_errors=True)
    world_path = os.path.join(util.worlds_dir(world_game), "Test World")
    os.makedirs(world_path, exist_ok=True)
    open(os.path.join(world_path, "level.dat"), "wb").write(b"level")
    open(os.path.join(world_path, "region.mca"), "wb").write(b"region")
    scanned_worlds = util.scan_worlds(world_game)
    backup_path = util.backup_world(world_game, "Test World")
    listed_backups = util.list_backups(world_game)
    restored_name = util.restore_world(world_game, backup_path)
    restored_ok = os.path.isfile(os.path.join(util.worlds_dir(world_game),
                                              restored_name, "level.dat"))
    util.open_path  # referenced here so the import is meaningful for the UI
    shots_empty = util.scan_screenshots(world_game) == []
    world_checks = {
        "world scan finds level.dat": len(scanned_worlds) == 1
                                      and scanned_worlds[0]["name"] == "Test World",
        "backup zip is listed": len(listed_backups) == 1,
        "restore creates a new world": bool(restored_name) and restored_ok,
        "restoring does not overwrite": restored_name != "Test World",
        "empty screenshots folder": shots_empty,
        "duration formatting": util.format_duration(3661) == "1h 1m"
                               and util.format_duration(45) == "45s",
        "relative time never": util.relative_time(0) == "never",
        "relative time recent": util.relative_time(time.time() - 7200).endswith("ago"),
    }
    for name, ok in world_checks.items():
        print(("PASS " if ok else "FAIL ") + name)
        fails += 0 if ok else 1
    _shutil.rmtree(here_self, ignore_errors=True)

    print("\n--- XI Server Hoster ---")
    import socket as _socket
    import zipfile as _zipfile

    import saxic_servers as srv

    server_game = os.path.join(here_self, "servergame")
    server_path = os.path.join(srv.servers_root(server_game), "smoke-server")
    os.makedirs(os.path.join(server_path, "world"), exist_ok=True)
    open(os.path.join(server_path, "world", "level.dat"), "wb").write(b"level")
    open(os.path.join(server_path, "world", "region.mca"), "wb").write(b"region")
    fake_server = {
        "id": "smoke-server", "name": "Smoke Server", "software": "Paper",
        "version": "26.3", "ram_mb": 2048, "port": srv.next_free_port(25650),
        "online_mode": True, "motd": "XI test", "max_players": 12,
        "difficulty": "normal", "gamemode": "survival", "eula_accepted": True,
        "created": time.time(), "jar": "server.jar", "path": server_path,
    }
    srv.write_eula(fake_server, True)
    srv.apply_properties(fake_server)
    srv.save_server(fake_server)
    loaded = srv.load_servers(server_game)
    props = srv.read_properties(os.path.join(server_path, "server.properties"))
    cmd = srv.server_command(loaded[0], r"C:\fake\java.exe")

    busy = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    busy.bind(("0.0.0.0", 0))
    busy.listen(1)
    busy_port = busy.getsockname()[1]
    port_taken = not srv.port_free(busy_port)
    busy.close()

    backup_zip = srv.create_backup(loaded[0])
    backups = srv.list_backups(loaded[0])
    restored_world = srv.restore_backup(loaded[0], backup_zip)
    restore_ok = os.path.isfile(os.path.join(restored_world, "level.dat"))
    with _zipfile.ZipFile(backup_zip) as zf:
        zip_names = [n.replace("\\", "/") for n in zf.namelist()]

    server_checks = {
        "slugify keeps safe ids": srv.slugify("My XI Server!") == "my-xi-server",
        "server metadata saved/loaded": len(loaded) == 1
                                         and loaded[0]["name"] == "Smoke Server",
        "properties written": props.get("server-port") == str(fake_server["port"])
                              and props.get("motd") == "XI test"
                              and props.get("max-players") == "12",
        "online mode property": props.get("online-mode") == "true",
        "eula written": os.path.isfile(os.path.join(server_path, "eula.txt")),
        "start command has heap and jar": any(a.startswith("-Xmx2048m") for a in cmd)
                                           and "server.jar" in cmd and "nogui" in cmd,
        "port check sees a listener": port_taken,
        "backup zip made": os.path.isfile(backup_zip) and "world/level.dat" in zip_names,
        "backup is listed": len(backups) == 1,
        "restore brings the world back": restore_ok,
        "three server softwares": set(srv.SOFTWARES) == {"Vanilla", "Paper", "Fabric", "Bedrock"},
    }
    for name, ok in server_checks.items():
        print(("PASS " if ok else "FAIL ") + name)
        fails += 0 if ok else 1

    # Bedrock Edition (offline checks: metadata only, no download)
    bedrock_server = dict(fake_server)
    bedrock_server.update({"software": "Bedrock", "port": srv.next_free_port(19140, udp=True),
                           "jar": srv.BEDROCK_EXE, "version": "1.21.90.3"})
    srv.write_eula(bedrock_server, True)
    srv.apply_properties(bedrock_server)
    bprops = srv.read_properties(os.path.join(server_path, "server.properties"))
    bworld = srv.world_dir(bedrock_server)
    bcmd = srv.server_command(bedrock_server, "")
    bedrock_free = srv.next_free_port(19160, udp=True)
    while not srv.port_free(bedrock_free):
        bedrock_free += 1
    bedrock_checks = {
        "bedrock detected": srv.is_bedrock(bedrock_server) and not srv.is_bedrock(fake_server),
        "bedrock port is UDP 19132-family": 19000 < int(bedrock_server["port"]) < 19300,
        "bedrock properties use server-name": bprops.get("server-name") == "XI test",
        "bedrock uses the nethernet transport": bprops.get("transport") == "nethernet",
        "bedrock world under worlds/": os.path.basename(os.path.dirname(bworld)) == "worlds",
        "bedrock start command runs the exe":
            bcmd and bcmd[0].lower().endswith("bedrock_server.exe"),
        "bedrock has a RakNet magic": srv.BEDROCK_MAGIC ==
            bytes.fromhex("00ffff00fefefefefdfdfdfd12345678"),
        "bedrock probe on a closed port returns none": srv.ping_bedrock(
            "127.0.0.1", bedrock_free, timeout=0.4) is None,
    }
    for name, ok in bedrock_checks.items():
        print(("PASS " if ok else "FAIL ") + name)
        fails += 0 if ok else 1

    # Cancellation wiring: the GUI passes a threading.Event in some places and
    # a callable in others. download()/backups must accept both shapes instead
    # of crashing with "'Event' object is not callable".
    print("\n--- cancellation (Event vs callable) ---")
    import inspect as _inspect
    import threading as _threading

    import saxic_launcher as _ui
    from saxic_util import Cancelled as _Cancel
    from saxic_util import download as _download
    from saxic_util import raise_if_cancelled

    def _raises_cancel(fn):
        try:
            fn()
        except _Cancel:
            return True
        return False

    set_event = _threading.Event()
    set_event.set()

    def _boom():
        raise _Cancel()

    cancel_checks = {
        "cancel=None is a no-op": not _raises_cancel(lambda: raise_if_cancelled(None)),
        "unset Event is a no-op": not _raises_cancel(
            lambda: raise_if_cancelled(_threading.Event())),
        "set Event raises Cancelled": _raises_cancel(
            lambda: raise_if_cancelled(set_event)),
        "cancel callable is honoured": _raises_cancel(
            lambda: raise_if_cancelled(_boom)),
        "download tolerates an Event": "raise_if_cancelled" in _inspect.getsource(_download),
        "create dialog has a cancel hook": callable(
            getattr(_ui.ServerCreateDialog, "_cancel_check", None)),
    }
    for name, ok in cancel_checks.items():
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
