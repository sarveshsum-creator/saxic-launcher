"""XI Launcher - build the Java command line and run the game.

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

LAUNCHER_NAME = "xi-launcher"
LAUNCHER_VERSION = "1.1"


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


def _split_args(text) -> list:
    """Split a user-entered argument string, honouring quotes."""
    text = (text or "").strip()
    if not text:
        return []
    try:
        return shlex.split(text, posix=True)
    except ValueError:
        return text.split()


def build_command(prepared: dict, ver_json: dict, account: dict, settings: dict,
                  java_path: str) -> list:
    args = ver_json.get("arguments")
    if args:
        jvm = allowed_args(args.get("jvm"))
        # the version json's default tuning (GC/JIT hints), when present
        default_jvm = allowed_args(args.get("default-user-jvm"))
        game = allowed_args(args.get("game"))
    else:
        jvm = ["-Djava.library.path=${natives_directory}"]
        default_jvm = []
        game = shlex.split(ver_json.get("minecraftArguments", ""))

    low_memory = bool(settings.get("low_memory"))

    # -XX:+AlwaysPreTouch commits the whole Java heap up front. On a PC with
    # little commit headroom (no pagefile, shared Intel graphics) that is
    # exactly what starves native memory and kills the JVM after a few
    # minutes ("insufficient memory ... Chunk::new" in hs_err_pid*.log).
    default_jvm = [a for a in default_jvm
                   if a.strip().lower() != "-xx:+alwayspretouch"]
    if low_memory:
        # ZGC keeps large native forwarding tables; G1 is far lighter on RAM.
        default_jvm = [a for a in default_jvm
                       if a.strip().lower() not in ("-xx:+usezgc", "-xx:+zgenerational")]
    jvm += default_jvm

    # our own memory settings win over anything baked into the version json
    jvm = [a for a in jvm if not (a.startswith("-Xmx") or a.startswith("-Xms"))]
    ram = int(settings.get("ram_mb") or 2048)
    initial = min(ram, 512) if low_memory else min(ram, 1024)
    if ram > 0:
        jvm = ["-Xmx%dm" % ram, "-Xms%dm" % initial] + jvm
    if not any(a == "-cp" or a == "-classpath" for a in jvm):
        jvm += ["-cp", "${classpath}"]

    if low_memory:
        # Hard ceilings for the native side so a long play session cannot
        # slowly eat the Windows commit limit.
        jvm += [
            "-XX:+UseG1GC",
            "-XX:MaxGCPauseMillis=50",
            "-XX:MaxDirectMemorySize=512m",
            "-XX:MaxMetaspaceSize=512m",
            "-XX:ReservedCodeCacheSize=160m",
            "-XX:-UsePerfData",
        ]

    # extra JVM options typed by the user (Settings > Launch options)
    jvm += _split_args(settings.get("jvm_args", ""))

    log_arg, log_path = prepared.get("logging") or (None, None)
    if log_arg and log_path:
        jvm.append(log_arg)

    table = _subst_map(prepared, ver_json, account, settings)
    jvm = _apply(jvm, table)
    game = _apply(game, table)

    # custom game window and extra game arguments
    try:
        width = int(settings.get("window_width") or 0)
        height = int(settings.get("window_height") or 0)
    except (TypeError, ValueError):
        width = height = 0
    if width > 0 and height > 0:
        game += ["--width", str(width), "--height", str(height)]
    game += _split_args(settings.get("game_args", ""))

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
