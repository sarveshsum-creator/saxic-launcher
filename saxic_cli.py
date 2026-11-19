"""SAXIC Launcher - command line helper (same engine as the desktop app).

    python saxic_cli.py status
    python saxic_cli.py install 26.3
    python saxic_cli.py launch 26.3 --offline Steve --timeout 120
    python saxic_cli.py command 26.3 --offline Steve     (print the java line only)
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import saxic_install as install
import saxic_launch as launch

DEFAULTS = {
    "game_dir": os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), ".saxic"),
    "java_path": "",
    "ram_mb": 2048,
    "workers": 16,
}


def load_settings():
    import json
    from saxic_util import data_dir
    path = os.path.join(data_dir(), "settings.json")
    data = dict(DEFAULTS)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data.update(json.load(fh))
    except (OSError, ValueError):
        pass
    return data


def get_version_json(vid: str):
    man = install.fetch_manifest()
    meta = install.version_meta(man, vid) if vid else install.version_meta(man, man["latest"]["release"])
    if not meta:
        raise SystemExit("version not found: " + str(vid))
    return meta["id"], install.fetch_version_json(meta)


def progress_printer():
    state = {"last": 0.0}

    def cb(done, total, label):
        now = time.time()
        if now - state["last"] < 0.4 and done < total:
            return
        state["last"] = now
        pct = int(done * 100 / total) if total else 0
        sys.stdout.write("\r  %-34s %5.1f%%  " % (label[:34], pct))
        sys.stdout.flush()

    return cb


def cmd_status(args):
    s = load_settings()
    print("game folder :", s["game_dir"])
    print("installed   :", ", ".join(install.installed_versions(s["game_dir"])) or "(none)")
    print("java (sys)  :", install.find_system_java() or "(none found)")
    print("java (local):", end=" ")
    rt = os.path.join(s["game_dir"], "runtime")
    found = []
    for root, _dirs, files in os.walk(rt) if os.path.isdir(rt) else []:
        for f in files:
            if f.lower() == "javaw.exe":
                found.append(os.path.join(root, f))
    print(found[0] if found else "(none)")
    return 0


def cmd_install(args):
    s = load_settings()
    vid, vj = get_version_json(args.version)
    print("installing", vid, "into", s["game_dir"])
    install.install_version(vid, vj, s["game_dir"], log=lambda m: print("  " + m),
                            progress=progress_printer(), workers=int(s.get("workers", 16)))
    print()
    install.write_launcher_profile(s["game_dir"], vid)
    java = install.ensure_java(vj, s["game_dir"], s.get("java_path") or None,
                              log=lambda m: print("  " + m), progress=progress_printer())
    print("\njava:", java)
    print("done -", vid, "is ready")
    return 0


def cmd_command(args):
    s = load_settings()
    vid, vj = get_version_json(args.version)
    prep = _prepared_stub(s["game_dir"], vid, vj)
    acct = launch.offline_account(args.offline or "Player")
    print(" ".join(launch.build_command(prep, vj, acct, s, "<java>")))
    return 0


def _prepared_stub(game_dir, vid, vj):
    libs, natives, _ = install.library_tasks(vj)
    return {
        "version_id": vid,
        "classpath": [os.path.join(game_dir, "libraries", a["path"].replace("/", os.sep)) for a in libs]
                     + [os.path.join(game_dir, "versions", vid, vid + ".jar")],
        "natives_dir": os.path.join(game_dir, "versions", vid, "natives"),
        "assets_root": os.path.join(game_dir, "assets"),
        "asset_index": (vj.get("assetIndex") or {}).get("id", "legacy"),
        "main_class": vj["mainClass"],
        "logging": None,
        "java_version": vj.get("javaVersion") or {},
    }


def cmd_launch(args):
    s = load_settings()
    vid, vj = get_version_json(args.version)
    print("preparing", vid, "...")
    prep = install.install_version(vid, vj, s["game_dir"], log=lambda m: print("  " + m),
                                   progress=progress_printer(), workers=int(s.get("workers", 16)),
                                   fast=True)
    print()
    install.write_launcher_profile(s["game_dir"], vid)
    java = install.ensure_java(vj, s["game_dir"], s.get("java_path") or None,
                              log=lambda m: print("  " + m), progress=progress_printer())
    print("\njava:", java)
    acct = launch.offline_account(args.offline or "Player")
    cmd = launch.build_command(prep, vj, acct, s, java)
    print("launching as", acct["name"], "(offline profile)")
    hits = {"n": 0, "ready": False}

    def on_line(line):
        print("  | " + line[:160])
        hits["n"] += 1
        low = line.lower()
        if "setting user" in low or "backend library" in low or "lwjgl" in low:
            hits["ready"] = True

    proc = launch.launch_game(cmd, s["game_dir"], on_line=on_line)
    deadline = time.time() + max(30, args.timeout)
    while time.time() < deadline and proc.alive():
        time.sleep(1)
    if proc.alive():
        print("\nstill running after %ds - window should be open; stopping it" % args.timeout)
        proc.stop()
        time.sleep(3)
    code = proc.popen.poll()
    print("\nsaw %d log lines | game initialised: %s | exit code: %s"
          % (hits["n"], hits["ready"], code))
    return 0 if hits["n"] > 0 else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="saxic_cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status").set_defaults(func=cmd_status)

    p = sub.add_parser("install")
    p.add_argument("version", nargs="?")
    p.set_defaults(func=cmd_install)

    p = sub.add_parser("command")
    p.add_argument("version", nargs="?")
    p.add_argument("--offline", default="Player")
    p.set_defaults(func=cmd_command)

    p = sub.add_parser("launch")
    p.add_argument("version", nargs="?")
    p.add_argument("--offline", default="Player")
    p.add_argument("--timeout", type=int, default=120)
    p.set_defaults(func=cmd_launch)

    args = ap.parse_args(argv)
    if not hasattr(args, "func"):
        ap.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
