"""XI Launcher - XI Server Hoster.

An original, local Minecraft server manager: create a Vanilla, Paper or
Fabric server in one window, start/stop it, watch the console, change the
MOTD and player limit, make backups, and share the address with friends on
the same network (with an optional UPnP port-forward attempt for internet
play).

This is not Aternos and does not use Aternos code or servers. Aternos is a
cloud service with a private API; this module runs the server on the player's
own PC, the same way the official server jar does.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import deque
from datetime import datetime
from xml.etree import ElementTree

import saxic_install as install
from saxic_util import (Cancelled, data_dir, download, http_json, human_size,
                        os_name)

SOFTWARES = ("Vanilla", "Paper", "Fabric")
DEFAULT_PORT = 25565
EULA_URL = "https://aka.ms/MinecraftEULA"
PAPER_API = "https://fill.papermc.io/v3/projects/paper"
FABRIC_META = "https://meta.fabricmc.net/v2"
META_FILE = "server.json"
MANAGER_VERSION = 1


# --------------------------------------------------------------------------
# small helpers


def servers_root(game_dir: str) -> str:
    return os.path.join(game_dir, "servers")


def slugify(name: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return (text[:32] or "server")


def _read_json(path: str, default=None):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _write_json(path: str, data) -> None:
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


def _meta_path(path: str) -> str:
    return os.path.join(path, META_FILE)


def load_servers(game_dir: str) -> list:
    """Every server folder with a ``server.json``, newest first."""
    root = servers_root(game_dir)
    out = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return []
    for name in names:
        path = os.path.join(root, name)
        if not os.path.isdir(path):
            continue
        data = _read_json(_meta_path(path), None)
        if not isinstance(data, dict):
            continue
        data.setdefault("id", name)
        data.setdefault("name", name)
        data.setdefault("software", "Vanilla")
        data.setdefault("version", "")
        data.setdefault("ram_mb", 2048)
        data.setdefault("port", DEFAULT_PORT)
        data.setdefault("online_mode", True)
        data.setdefault("motd", "An XI Server")
        data.setdefault("max_players", 10)
        data.setdefault("difficulty", "normal")
        data.setdefault("gamemode", "survival")
        data.setdefault("eula_accepted", False)
        data.setdefault("created", 0)
        data.setdefault("jar", "server.jar")
        data["path"] = path
        out.append(data)
    out.sort(key=lambda s: (s.get("created") or 0), reverse=True)
    return out


def get_server(game_dir: str, server_id: str):
    for server in load_servers(game_dir):
        if server.get("id") == server_id:
            return server
    return None


def save_server(server: dict) -> None:
    data = {k: v for k, v in server.items() if k != "path"}
    _write_json(_meta_path(server["path"]), data)


def port_free(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            # No SO_REUSEADDR on purpose: Windows otherwise lets a second
            # socket bind a port that a server is already listening on.
            sock.bind(("0.0.0.0", int(port)))
        return True
    except OSError:
        return False


def next_free_port(start: int = DEFAULT_PORT) -> int:
    for port in range(int(start), int(start) + 200):
        if port_free(port):
            return port
    return int(start)


def lan_ip() -> str:
    """The private address friends on the same Wi-Fi should use."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"


def public_ip() -> str:
    for url in ("https://api.ipify.org?format=json", "https://api.ipify.org"):
        try:
            data = http_json(url, timeout=8)
            if isinstance(data, dict):
                return str(data.get("ip") or "")
            return str(data or "").strip()
        except Exception:  # noqa: BLE001
            continue
    return ""


def _properties_path(server: dict) -> str:
    return os.path.join(server["path"], "server.properties")


def read_properties(path: str) -> dict:
    out = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.rstrip("\r\n")
                if not line or line.lstrip().startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                out[key.strip()] = value
    except OSError:
        pass
    return out


def update_properties(path: str, updates: dict) -> None:
    keys = {str(k): str(v) for k, v in updates.items()}
    lines = []
    seen = set()
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        lines = ["#Minecraft server properties"]
    out = []
    for line in lines:
        if "=" in line and not line.lstrip().startswith("#"):
            key = line.split("=", 1)[0]
            if key in keys:
                out.append("%s=%s" % (key, keys[key]))
                seen.add(key)
                continue
        out.append(line)
    for key, value in keys.items():
        if key not in seen:
            out.append("%s=%s" % (key, value))
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write("\n".join(out) + "\n")
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass


def eula_path(server: dict) -> str:
    return os.path.join(server["path"], "eula.txt")


def write_eula(server: dict, accepted: bool) -> None:
    try:
        with open(eula_path(server), "w", encoding="utf-8") as fh:
            fh.write("#By changing the setting below to TRUE you are indicating your "
                     "agreement to the Minecraft EULA (%s).\n" % EULA_URL)
            fh.write("eula=%s\n" % ("true" if accepted else "false"))
    except OSError:
        pass


def default_properties(server: dict) -> dict:
    return {
        "server-port": int(server.get("port") or DEFAULT_PORT),
        "server-ip": "",
        "motd": str(server.get("motd") or "An XI Server").replace("\n", " ")[:120],
        "online-mode": "true" if server.get("online_mode", True) else "false",
        "max-players": int(server.get("max_players") or 10),
        "difficulty": str(server.get("difficulty") or "normal"),
        "gamemode": str(server.get("gamemode") or "survival"),
        "level-name": "world",
        "view-distance": 8,
        "simulation-distance": 6,
        "spawn-protection": 16,
        "pvp": "true",
        "allow-nether": "true",
        "allow-flight": "false",
        "enable-command-block": "false",
        "enable-status": "true",
        "hide-online-players": "false",
        "enforce-secure-profile": "true" if server.get("online_mode", True) else "false",
        "network-compression-threshold": 256,
        "sync-chunk-writes": "false",
        "prevent-proxy-connections": "false",
    }


def apply_properties(server: dict) -> None:
    update_properties(_properties_path(server), default_properties(server))


def world_dir(server: dict) -> str:
    props = read_properties(_properties_path(server))
    name = props.get("level-name") or "world"
    return os.path.join(server["path"], name)


# --------------------------------------------------------------------------
# downloading server software


def _vanilla_server(version: str):
    manifest = install.cached_manifest() or install.fetch_manifest()
    meta = install.version_meta(manifest, version)
    if not meta:
        raise RuntimeError("Minecraft %s is not in Mojang's version list." % version)
    ver_json = install.fetch_version_json(meta)
    server = (ver_json.get("downloads") or {}).get("server")
    if not server or not server.get("url"):
        raise RuntimeError("Minecraft %s has no standalone server download." % version)
    return ver_json, server.get("url"), "server.jar", {
        "sha1": server.get("sha1"), "size": server.get("size")
    }


def _paper_server(version: str):
    manifest = install.cached_manifest() or install.fetch_manifest()
    meta = install.version_meta(manifest, version)
    ver_json = install.fetch_version_json(meta) if meta else {}
    data = http_json("%s/versions/%s/builds" % (PAPER_API, urllib.parse.quote(version)))
    builds = [b for b in (data if isinstance(data, list) else [])
              if isinstance(b, dict) and (b.get("downloads") or {}).get("server:default")]
    if not builds:
        raise RuntimeError("Paper has no build for Minecraft %s yet." % version)
    builds.sort(key=lambda b: (str(b.get("channel") or "").upper() == "STABLE",
                               int(b.get("id") or 0)), reverse=True)
    build = builds[0]
    dl = build["downloads"]["server:default"]
    name = dl.get("name") or ("paper-%s-%s.jar" % (version, build.get("id")))
    return ver_json, dl.get("url"), name, {
        "sha256": dl.get("checksums", {}).get("sha256"),
        "size": dl.get("size"),
    }


def _fabric_server(version: str):
    manifest = install.cached_manifest() or install.fetch_manifest()
    meta = install.version_meta(manifest, version)
    ver_json = install.fetch_version_json(meta) if meta else {}
    loaders = install.fabric_loader_versions(version)
    stable = [x for x in loaders if (x.get("loader") or {}).get("stable")]
    if not (stable or loaders):
        raise RuntimeError("Fabric has no loader for Minecraft %s yet." % version)
    loader = (stable or loaders)[0]["loader"]["version"]
    installers = http_json(FABRIC_META + "/versions/installer")
    inst = next((x for x in installers if x.get("stable")), installers[0])
    installer = inst.get("version") or "1.1.2"
    url = "%s/versions/loader/%s/%s/%s/server/jar" % (
        FABRIC_META, urllib.parse.quote(version), urllib.parse.quote(loader),
        urllib.parse.quote(installer))
    return ver_json, url, "fabric-server-launch.jar", {"loader": loader}


def resolve_server_jar(version: str, software: str):
    """Return ``(version_json, url, filename, extra)`` for a server build."""
    software = (software or "Vanilla").strip()
    if software.lower() == "paper":
        return _paper_server(version)
    if software.lower() == "fabric":
        return _fabric_server(version)
    return _vanilla_server(version)


def install_server_jar(server: dict, log=print, progress=None, cancel=None) -> None:
    """(Re)download the server jar recorded in the server metadata."""
    ver_json, url, filename, extra = resolve_server_jar(
        server["version"], server.get("software") or "Vanilla")
    if not url:
        raise RuntimeError("no server download was found")
    dest = os.path.join(server["path"], filename)
    log("downloading %s %s server..." % (server["version"], server.get("software")))
    download(url, dest, sha1=extra.get("sha1"), sha256=extra.get("sha256"),
             size=extra.get("size"), progress=progress, cancel=cancel)
    if extra.get("loader"):
        server["loader"] = extra["loader"]
    server["jar"] = filename
    server["java_component"] = ((ver_json.get("javaVersion") or {}).get("component") or "")
    save_server(server)
    log("server jar ready: " + filename)


# --------------------------------------------------------------------------
# creating a server


def _unique_id(game_dir: str, base: str) -> str:
    root = servers_root(game_dir)
    candidate = base
    n = 2
    while os.path.exists(os.path.join(root, candidate)):
        candidate = "%s-%d" % (base, n)
        n += 1
    return candidate


def create_server(game_dir: str, name: str, version: str, software: str = "Vanilla",
                  ram_mb: int = 2048, port: int = 0, online_mode: bool = True,
                  motd: str = "An XI Server", max_players: int = 10,
                  difficulty: str = "normal", gamemode: str = "survival",
                  eula: bool = False, log=print, progress=None, cancel=None) -> dict:
    """Create a fully working server folder and download its jar."""
    name = (name or "").strip()
    if not name:
        raise ValueError("give the server a name")
    software = (software or "Vanilla").strip()
    if software.lower() not in {s.lower() for s in SOFTWARES}:
        raise ValueError("unsupported server software: %s" % software)
    if not version:
        raise ValueError("pick a Minecraft version")
    port = int(port or next_free_port())
    if not port_free(port):
        raise ValueError("port %d is already in use" % port)
    if not (1024 <= int(ram_mb) <= 16384):
        raise ValueError("server memory must be between 1024 and 16384 MB")

    sid = _unique_id(game_dir, slugify(name))
    path = os.path.join(servers_root(game_dir), sid)
    os.makedirs(path, exist_ok=False)
    server = {
        "id": sid,
        "name": name,
        "software": software,
        "version": version,
        "ram_mb": int(ram_mb),
        "port": port,
        "online_mode": bool(online_mode),
        "motd": (motd or "An XI Server").strip()[:120],
        "max_players": max(1, min(100, int(max_players))),
        "difficulty": difficulty if difficulty in ("peaceful", "easy", "normal", "hard") else "normal",
        "gamemode": gamemode if gamemode in ("survival", "creative", "adventure", "spectator") else "survival",
        "created": time.time(),
        "last_started": 0,
        "eula_accepted": bool(eula),
        "jar": "server.jar",
        "manager_version": MANAGER_VERSION,
        "path": path,
    }
    try:
        for sub in ("logs", "backups", "plugins", "mods"):
            os.makedirs(os.path.join(path, sub), exist_ok=True)
        write_eula(server, bool(eula))
        apply_properties(server)
        install_server_jar(server, log=log, progress=progress, cancel=cancel)
        save_server(server)
        log("server %s is ready on port %d" % (name, port))
        return server
    except Exception:
        try:
            shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------
# running a server


def _console_java(exe: str) -> str:
    if exe.lower().endswith("javaw.exe"):
        candidate = exe[:-8] + "java.exe"
        if os.path.isfile(candidate):
            return candidate
    return exe


def _version_json_for(server: dict, game_dir: str) -> dict:
    version = server.get("version") or ""
    try:
        manifest = install.cached_manifest()
        if manifest:
            meta = install.version_meta(manifest, version)
            if meta:
                return install.fetch_version_json(meta)
    except Exception:  # noqa: BLE001 - offline is fine if Java is installed
        pass
    return install.local_version_json(game_dir, version) or {}


def ensure_server_java(server: dict, game_dir: str, log=print) -> str:
    ver_json = _version_json_for(server, game_dir)
    java = install.ensure_java(ver_json, game_dir, log=log)
    return _console_java(java)


def server_command(server: dict, java: str) -> list:
    ram = max(1024, int(server.get("ram_mb") or 2048))
    jar = server.get("jar") or "server.jar"
    return [
        java,
        "-Xms512M",
        "-Xmx%dm" % ram,
        "-XX:+UseG1GC",
        "-XX:MaxGCPauseMillis=50",
        "-Dfile.encoding=UTF-8",
        "-jar", jar,
        "nogui",
    ]


class ServerProcess:
    """A running Minecraft server and its console output."""

    def __init__(self, server: dict, game_dir: str, log=print):
        self.server = dict(server)
        self.game_dir = game_dir
        self.log = log
        self.lines: deque = deque(maxlen=4000)
        self.line_total = 0
        self.state = "stopped"
        self.error = ""
        self.players: set = set()
        self.max_players = int(server.get("max_players") or 10)
        self.started_at = 0.0
        self.popen: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._stop_requested = False
        self._reader: threading.Thread | None = None

    # ------------------------------------------------------------------
    def _set_state(self, state: str, error: str = "") -> None:
        with self._lock:
            self.state = state
            if error:
                self.error = error

    def _add_line(self, line: str) -> None:
        with self._lock:
            self.lines.append(line)
            self.line_total += 1
            if "Done (" in line:
                self.state = "running"
                self.started_at = time.time()
            low = line.lower()
            if "joined the game" in low:
                m = re.search(r"\]:\s+([A-Za-z0-9_]{1,16})\s+joined the game", line)
                if m:
                    self.players.add(m.group(1))
            elif "left the game" in low:
                m = re.search(r"\]:\s+([A-Za-z0-9_]{1,16})\s+left the game", line)
                if m:
                    self.players.discard(m.group(1))
            m = re.search(r"There are (\d+) of a max(?: of)? (\d+) players online", line)
            if m:
                self.max_players = int(m.group(2))
            if "you need to agree to the eula" in low:
                self.error = "Minecraft is waiting for the EULA to be accepted."
            if "stopping server" in low:
                self.state = "stopping"
        try:
            self.log(line)
        except Exception:  # noqa: BLE001
            pass

    def start(self) -> None:
        if self.alive():
            return
        if not self.server.get("eula_accepted"):
            raise RuntimeError("Accept the Minecraft EULA before starting this server.")
        jar = os.path.join(self.server["path"], self.server.get("jar") or "server.jar")
        if not os.path.isfile(jar):
            raise RuntimeError("server jar is missing - use Repair / update files")
        self._set_state("starting")
        self._stop_requested = False
        self.error = ""
        with self._lock:
            self.lines.clear()
            self.line_total = 0
            self.players.clear()
        java = ensure_server_java(self.server, self.game_dir, log=self._add_line)
        cmd = server_command(self.server, java)
        env = os.environ.copy()
        env["JAVA_TOOL_OPTIONS"] = ""
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._add_line("> " + " ".join(cmd))
        self.popen = subprocess.Popen(
            cmd, cwd=self.server["path"], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            env=env, creationflags=flags)
        self.server["last_started"] = time.time()
        try:
            save_server(self.server)
        except Exception:  # noqa: BLE001
            pass
        self._reader = threading.Thread(target=self._read_loop, name="xi-server-%s" % self.server.get("id"),
                                        daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        code = None
        try:
            assert self.popen is not None
            for raw in self.popen.stdout:
                self._add_line(raw.rstrip("\r\n"))
            code = self.popen.wait()
        except Exception as exc:  # noqa: BLE001
            self._set_state("crashed", str(exc))
            return
        if code == 0 or self._stop_requested:
            self._set_state("stopped")
        else:
            self._set_state("crashed", "server exited with code %s" % code)

    def alive(self) -> bool:
        return self.popen is not None and self.popen.poll() is None

    def send(self, command: str) -> None:
        command = (command or "").strip()
        if not command:
            return
        if not self.alive() or self.popen.stdin is None:
            raise RuntimeError("server is not running")
        self.popen.stdin.write(command + "\n")
        self.popen.stdin.flush()

    def stop(self, timeout: float = 30.0) -> None:
        self._stop_requested = True
        if not self.alive():
            self._set_state("stopped")
            return
        self._set_state("stopping")
        try:
            self.send("stop")
        except Exception:  # noqa: BLE001
            pass
        deadline = time.time() + max(3.0, timeout)
        while time.time() < deadline and self.alive():
            time.sleep(0.2)
        if self.alive() and self.popen is not None:
            try:
                self.popen.terminate()
            except Exception:  # noqa: BLE001
                pass
            deadline = time.time() + 8
            while time.time() < deadline and self.alive():
                time.sleep(0.2)
        if self.alive() and self.popen is not None:
            try:
                self.popen.kill()
            except Exception:  # noqa: BLE001
                pass
        self._set_state("stopped")

    def snapshot(self) -> dict:
        with self._lock:
            uptime = time.time() - self.started_at if self.started_at else 0
            return {
                "state": self.state,
                "error": self.error,
                "pid": self.popen.pid if self.popen else 0,
                "uptime": uptime,
                "players": sorted(self.players),
                "player_count": len(self.players),
                "max_players": self.max_players,
                "lines": list(self.lines),
                "line_total": self.line_total,
            }


class ServerManager:
    """Owns the running server processes for one launcher window."""

    def __init__(self, game_dir: str, log=print):
        self.game_dir = game_dir
        self.log = log
        self._runtimes: dict = {}
        self._lock = threading.Lock()

    def servers(self) -> list:
        return load_servers(self.game_dir)

    def runtime(self, server: dict) -> ServerProcess | None:
        with self._lock:
            return self._runtimes.get(server.get("id"))

    def start(self, server: dict) -> ServerProcess:
        with self._lock:
            current = self._runtimes.get(server.get("id"))
            if current and current.alive():
                return current
            runtime = ServerProcess(server, self.game_dir, log=self.log)
            self._runtimes[server.get("id")] = runtime
        try:
            runtime.start()
        except Exception:
            with self._lock:
                self._runtimes.pop(server.get("id"), None)
            raise
        return runtime

    def stop(self, server: dict, timeout: float = 30.0) -> None:
        runtime = self.runtime(server)
        if runtime:
            runtime.stop(timeout=timeout)

    def running(self) -> list:
        with self._lock:
            return [r for r in self._runtimes.values() if r.alive()]

    def remove(self, server: dict) -> None:
        self.stop(server, timeout=12)
        shutil.rmtree(server["path"], ignore_errors=True)


# --------------------------------------------------------------------------
# server ping (the same status request the multiplayer menu uses)


def _varint(value: int) -> bytes:
    value &= 0xFFFFFFFF
    out = b""
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out += bytes([byte | 0x80])
        else:
            out += bytes([byte])
            return out


def _read_varint(sock) -> int:
    value = 0
    shift = 0
    while True:
        b = sock.recv(1)
        if not b:
            raise OSError("connection closed")
        byte = b[0]
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value
        shift += 7
        if shift > 35:
            raise OSError("bad varint")


def ping_server(host: str = "127.0.0.1", port: int = DEFAULT_PORT,
                timeout: float = 3.0) -> dict | None:
    """Return status JSON (players, motd, version) or None."""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout) as sock:
            sock.settimeout(timeout)
            host_b = host.encode("utf-8")
            packet = (_varint(0) + _varint(769) + _varint(len(host_b)) + host_b +
                      struct.pack(">H", int(port)) + _varint(1))
            sock.sendall(_varint(len(packet)) + packet)
            sock.sendall(_varint(1) + _varint(0))
            length = _read_varint(sock)
            if length <= 0 or length > 1 << 20:
                return None
            _packet_id = _read_varint(sock)
            slen = _read_varint(sock)
            chunks = []
            remaining = slen
            while remaining > 0:
                data = sock.recv(min(65536, remaining))
                if not data:
                    break
                chunks.append(data)
                remaining -= len(data)
            return json.loads(b"".join(chunks).decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------
# backups


def backup_folder(server: dict) -> str:
    return os.path.join(server["path"], "backups")


def list_backups(server: dict) -> list:
    root = backup_folder(server)
    out = []
    try:
        for name in os.listdir(root):
            if name.lower().endswith(".zip"):
                path = os.path.join(root, name)
                out.append({"name": name, "path": path,
                            "size": os.path.getsize(path),
                            "modified": os.path.getmtime(path)})
    except OSError:
        pass
    out.sort(key=lambda b: b["modified"], reverse=True)
    return out


def create_backup(server: dict, log=print, cancel=None) -> str:
    world = world_dir(server)
    if not os.path.isdir(world):
        raise RuntimeError("this server has no world yet - start it once first")
    root = backup_folder(server)
    os.makedirs(root, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(root, "%s-%s.zip" % (server.get("id", "server"), stamp))
    log("backing up %s..." % os.path.basename(world))
    try:
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for base, dirs, files in os.walk(world):
                if cancel:
                    cancel()
                for name in files:
                    if name == "session.lock":
                        continue
                    full = os.path.join(base, name)
                    rel = os.path.relpath(full, os.path.dirname(world))
                    zf.write(full, rel)
    except Exception:
        try:
            os.remove(dest)
        except OSError:
            pass
        raise
    log("backup ready: " + os.path.basename(dest))
    return dest


def create_backup_safe(server: dict, runtime=None, log=print, cancel=None) -> str:
    """Back up a world, briefly pausing writes when the server is running."""
    paused = False
    if runtime is not None and runtime.alive():
        try:
            runtime.send("save-off")
            runtime.send("save-all flush")
            paused = True
            time.sleep(2.0)
        except Exception as exc:  # noqa: BLE001
            log("could not pause world saving (%s); copying anyway" % exc)
    try:
        return create_backup(server, log=log, cancel=cancel)
    finally:
        if paused:
            try:
                runtime.send("save-on")
            except Exception:  # noqa: BLE001
                pass


def restore_backup(server: dict, backup_path: str, log=print) -> str:
    if not os.path.isfile(backup_path):
        raise RuntimeError("backup not found")
    target_root = server["path"]
    world = world_dir(server)
    if os.path.isdir(world):
        safety = world + ".before-restore-" + datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.move(world, safety)
        log("old world kept as " + os.path.basename(safety))
    tmp = tempfile.mkdtemp(prefix="xi-restore-")
    try:
        with zipfile.ZipFile(backup_path) as zf:
            zf.extractall(tmp)
        # the zip stores "world/..." relative to the server folder
        candidate = os.path.join(tmp, os.path.basename(world))
        if not os.path.isdir(candidate):
            folders = [e for e in os.listdir(tmp) if os.path.isdir(os.path.join(tmp, e))]
            if not folders:
                raise RuntimeError("this backup has no world folder")
            candidate = os.path.join(tmp, folders[0])
        shutil.move(candidate, world)
        log("world restored from " + os.path.basename(backup_path))
        return world
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
# sharing / UPnP


def _ssdp_locations(timeout: float = 2.0) -> list:
    message = ("M-SEARCH * HTTP/1.1\r\n"
               "HOST: 239.255.255.250:1900\r\n"
               'MAN: "ssdp:discover"\r\n'
               "MX: 2\r\n"
               "ST: urn:schemas-upnp-org:device:InternetGatewayDevice:1\r\n"
               "\r\n").encode("ascii")
    found = []
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    except OSError:
        return found
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.settimeout(0.4)
        sock.sendto(message, ("239.255.255.250", 1900))
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                data, _addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            text = data.decode("latin-1", "replace")
            for line in text.splitlines():
                if line.lower().startswith("location:"):
                    url = line.split(":", 1)[1].strip()
                    if url and url not in found:
                        found.append(url)
    finally:
        sock.close()
    return found


def _wan_service(location: str):
    with urllib.request.urlopen(location, timeout=4) as response:
        root = ElementTree.fromstring(response.read())
    base = location
    for service in root.iter():
        if not service.tag.endswith("service"):
            continue
        stype = service.findtext("{*}serviceType") or ""
        if "WANIPConnection" not in stype and "WANPPPConnection" not in stype:
            continue
        control = service.findtext("{*}controlURL") or ""
        if not control:
            continue
        return stype, urllib.parse.urljoin(base, control)
    return None, None


def try_upnp(port: int, local_ip: str, log=print) -> bool:
    """Try to add a TCP port mapping on the router (UPnP IGD)."""
    locations = _ssdp_locations()
    if not locations:
        log("UPnP: no router answered the discovery request")
        return False
    for location in locations:
        try:
            stype, control = _wan_service(location)
        except Exception as exc:  # noqa: BLE001
            log("UPnP: could not read %s (%s)" % (location, exc))
            continue
        if not stype or not control:
            continue
        service = stype.split(":")[-2] if stype.count(":") >= 2 else "WANIPConnection"
        action = "urn:schemas-upnp-org:service:%s:1#AddPortMapping" % service
        body = (
            '<?xml version="1.0"?>'
            '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
            "<s:Body><u:AddPortMapping xmlns:u=\"urn:schemas-upnp-org:service:%s:1\">"
            "<NewRemoteHost></NewRemoteHost>"
            "<NewExternalPort>%d</NewExternalPort>"
            "<NewProtocol>TCP</NewProtocol>"
            "<NewInternalPort>%d</NewInternalPort>"
            "<NewInternalClient>%s</NewInternalClient>"
            "<NewEnabled>1</NewEnabled>"
            "<NewPortMappingDescription>XI Server Hoster</NewPortMappingDescription>"
            "<NewLeaseDuration>0</NewLeaseDuration>"
            "</u:AddPortMapping></s:Body></s:Envelope>" % (service, int(port), int(port), local_ip)
        ).encode("utf-8")
        request = urllib.request.Request(
            control, data=body,
            headers={"Content-Type": 'text/xml; charset="utf-8"',
                     "SOAPAction": '"%s"' % action,
                     "User-Agent": "XI-Server-Hoster/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=6) as response:
                if 200 <= response.status < 300:
                    log("UPnP: router accepted TCP %d -> %s:%d" % (port, local_ip, port))
                    return True
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            log("UPnP: router refused the mapping (HTTP %s: %s)" % (exc.code, detail[:200]))
        except Exception as exc:  # noqa: BLE001
            log("UPnP: %s" % exc)
    return False
