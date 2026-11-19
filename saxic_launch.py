"""SAXIC Launcher - build the Java command line and run the game.

The argument templating follows the public "launcher arguments" spec that
every Minecraft launcher implements (placeholders like ${auth_player_name}).
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
import threading

from saxic_util import allowed_args, offline_uuid, os_name, sha1_hex

LAUNCHER_NAME = "saxic-launcher"
LAUNCHER_VERSION = "1.0"


# --------------------------------------------------------------------------
# accounts


def offline_account(username: str) -> dict:
    name = clean_username(username)
    return {
        "name": name,
        "uuid": offline_uuid(name),
        "access_token": sha1_hex("saxic-token:" + name),  # local-only dummy token
        "user_type": "legacy",
        "xuid": "",
        "client_id": "",
        "kind": "offline",
    }


def clean_username(name: str) -> str:
    keep = "".join(c for c in (name or "").strip() if c.isalnum() or c == "_")
    if not keep:
        keep = "Player"
    return keep[:16]


# --------------------------------------------------------------------------
# command line


def _subst_map(prepared: dict, ver_json: dict, account: dict, settings: dict) -> dict:
    cp = os.pathsep.join(prepared["classpath"])
    log_arg, log_path = prepared.get("logging") or (None, None)
    return {
        "auth_player_name": account["name"],
        "version_name": prepared["version_id"],
        "game_directory": settings["game_dir"],
        "assets_root": prepared["assets_root"],
        "assets_index_name": prepared["asset_index"],
        "auth_uuid": account["uuid"].replace("-", ""),
        "auth_access_token": account["access_token"],
        "auth_session": account["access_token"],
        "user_type": account["user_type"],
        "user_properties": "{}",
        "classpath": cp,
        "natives_directory": prepared["natives_dir"],
        "launcher_name": LAUNCHER_NAME,
        "launcher_version": LAUNCHER_VERSION,
        "clientid": account.get("client_id", "") or "",
        "auth_xuid": account.get("xuid", "") or "",
        "version_type": ver_json.get("type", "release"),
        "path": log_path or "",
    }


def _apply(values, table):
    out = []
    for v in values:
        for key, val in table.items():
            if "${%s}" % key in v:
                v = v.replace("${%s}" % key, str(val))
        out.append(v)
    return out


def build_command(prepared: dict, ver_json: dict, account: dict, settings: dict,
                  java_path: str) -> list:
    args = ver_json.get("arguments")
    if args:
        jvm = allowed_args(args.get("jvm"))
        # the version json's default tuning (GC/JIT hints), when present
        jvm += allowed_args(args.get("default-user-jvm"))
        game = allowed_args(args.get("game"))
    else:
        jvm = ["-Djava.library.path=${natives_directory}"]
        game = shlex.split(ver_json.get("minecraftArguments", ""))

    # our own memory settings win over anything baked into the version json
    jvm = [a for a in jvm if not (a.startswith("-Xmx") or a.startswith("-Xms"))]
    ram = int(settings.get("ram_mb") or 2048)
    if ram > 0:
        jvm = ["-Xmx%dm" % ram, "-Xms%dm" % min(ram, 1024)] + jvm
    if not any(a == "-cp" or a == "-classpath" for a in jvm):
        jvm += ["-cp", "${classpath}"]

    log_arg, log_path = prepared.get("logging") or (None, None)
    if log_arg and log_path:
        jvm.append(log_arg)

    table = _subst_map(prepared, ver_json, account, settings)
    jvm = _apply(jvm, table)
    game = _apply(game, table)
    if log_arg:
        jvm = [a.replace("${path}", log_path) for a in jvm]

    return [java_path] + jvm + [prepared["main_class"]] + game


# --------------------------------------------------------------------------
# process handling


class GameProcess:
    def __init__(self, popen: subprocess.Popen):
        self.popen = popen

    def stop(self) -> None:
        try:
            self.popen.terminate()
        except Exception:  # noqa: BLE001
            pass

    def alive(self) -> bool:
        return self.popen.poll() is None


def launch_game(cmd: list, cwd: str, on_line=None, on_exit=None) -> GameProcess:
    flags = 0
    if os_name() == "windows":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    env = dict(os.environ)
    env.setdefault("JAVA_TOOL_OPTIONS", "")
    popen = subprocess.Popen(
        cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
        bufsize=1, creationflags=flags, env=env,
    )

    def pump():
        try:
            for line in popen.stdout:  # type: ignore[union-attr]
                if on_line:
                    on_line(line.rstrip("\n"))
        except Exception:  # noqa: BLE001
            pass
        code = popen.wait()
        if on_exit:
            on_exit(code)

    threading.Thread(target=pump, name="saxic-log", daemon=True).start()
    return GameProcess(popen)
