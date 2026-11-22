"""SAXIC Launcher - desktop app (Python + Tkinter, standard library only).

Run with:  python saxic_launcher.py     (or double-click "SAXIC Launcher.bat")

Everything in this folder is original code for the SAXIC Launcher project.
It reads Mojang's public metadata to install and start Minecraft, the same
way any launcher does; it ships no game files itself.
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

import saxic_auth as auth
import saxic_install as install
import saxic_launch as launch
import saxic_mods as mods
try:
    from saxic_build import BUILD_DATE as BUILD_DATE_STAMP
except Exception:  # source tree without a build stamp
    BUILD_DATE_STAMP = ""
from saxic_util import (Cancelled, app_dir, available_ram_mb, backup_world,
                        backups_dir, data_dir, dir_size, format_duration,
                        http_json_cached, human_size, list_backups,
                        list_java_runtimes, memory_status, open_path,
                        physical_ram_mb, read_json_cache, relative_time,
                        resource_path, restore_world, scan_screenshots,
                        scan_worlds, screenshots_dir, worlds_dir)

APP_NAME = "SAXIC Launcher"
APP_VERSION = "1.0"
APP_DIR = app_dir()
HERE = data_dir()
SETTINGS_PATH = os.path.join(HERE, "settings.json")

PUBLISHERS = ("SARVESH.S", "srxvesh._.11")
INSPIRATION = "clumsydev"
PROJECT_CREATED = "18 November 2026"
NEWS_URL = "https://launchercontent.mojang.com/v2/news.json"
NEWS_CACHE = os.path.join(HERE, "news-cache.json")

BG = "#0b0f16"
PANEL = "#121826"
PANEL2 = "#0d131f"
LINE = "#1e2a3c"
TEXT = "#e6edf6"
DIM = "#8fa3bf"
ACCENT = "#4da3ff"
GREEN = "#3fb950"
RED = "#f06060"
AMBER = "#f5b041"

def recommended_ram_mb(requested: int = 0, mod_count: int = 0) -> int:
    """A safe Java heap size (MB) for this machine and the installed mod count.

    Two different crashes matter here:

    * too *small* a heap  -> Java ``OutOfMemoryError`` while a world loads;
    * too *large* a heap  -> a **native** out-of-memory error (the JVM, the
      JIT code cache, mod native libraries and direct buffers all live *outside*
      the Java heap), which shows up in ``hs_err_pid*.log`` as
      "insufficient memory ... Chunk::new". On Windows the RAM + pagefile commit
      limit is the wall, so an oversized ``-Xmx`` starves native memory.

    So the heap is capped by the RAM that is *actually free right now*, after
    reserving space for the JVM/native side (which grows with the mod count) and
    for the operating system / other apps.

    When this PC has **no pagefile** (virtual memory turned off), the commit
    limit is physical RAM only, so the cap is much lower to leave headroom for
    the native side and avoid "insufficient memory" crashes mid-play.
    """
    mods = max(0, int(mod_count))
    total = physical_ram_mb()
    avail = available_ram_mb() or int(total * 0.85)

    mem = memory_status()
    has_pagefile = mem.get("pagefile_mb", 0) >= 1024

    if has_pagefile:
        overall_cap = int(total * 0.60)              # never more than ~60% of RAM
    else:
        # No pagefile: commit limit = physical RAM only. Cap much lower so the
        # JVM/native side still has commit headroom while a world loads.
        overall_cap = int(total * 0.40)

    native_reserve = 1792 + 40 * mods            # JVM + metaspace + native libs
    room = max(2048, avail - native_reserve)     # what is really left to commit
    ceiling = max(2048, min(overall_cap, room))

    base = max(2048, min(7168, int(total * 0.5)))
    want = base + 48 * mods
    suggested = max(2048, min(want, ceiling))

    if requested > 0:
        # An explicit choice is honored, but never allowed to starve the native
        # side (that is exactly what produced the hs_err crashes).
        return int(max(2048, min(requested, ceiling)))
    return int(suggested)


DEFAULTS = {
    "game_dir": os.path.join(os.environ.get("APPDATA", HERE), ".saxic"),
    "java_path": "",
    "ram_mb": recommended_ram_mb(),
    "workers": 16,
    "client_id": "",
    "show_snapshots": False,
    "keep_open": True,
    "last_version": "",
    "offline_name": "Player",
    "mod_profiles": {},
    # launch options
    "jvm_args": "",
    "game_args": "",
    "window_width": 0,
    "window_height": 0,
    # statistics
    "playtime": {},
    "last_played": {},
}


def load_settings() -> dict:
    data = dict(DEFAULTS)
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as fh:
            data.update(json.load(fh))
    except (OSError, ValueError):
        pass
    return data


def save_settings(data: dict) -> None:
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
    except OSError:
        pass


# --------------------------------------------------------------------------
# credits


def format_date(timestamp: float) -> str:
    d = datetime.fromtimestamp(timestamp)
    return "%d %s %d" % (d.day, d.strftime("%B"), d.year)


def updated_date() -> str:
    """Newest source file date, so the Credits window never goes stale."""
    if getattr(sys, "frozen", False) and BUILD_DATE_STAMP:
        return BUILD_DATE_STAMP
    newest = 0.0
    try:
        names = os.listdir(APP_DIR)
    except OSError:
        names = []
    for name in names:
        if name.endswith((".py", ".md")):
            try:
                newest = max(newest, os.path.getmtime(os.path.join(APP_DIR, name)))
            except OSError:
                pass
    return format_date(newest or datetime.now().timestamp())


def credits_info() -> dict:
    return {
        "created": PROJECT_CREATED,
        "updated": updated_date(),
        "publishers": list(PUBLISHERS),
        "inspiration": INSPIRATION,
    }


def draw_app_logo(c: tk.Canvas) -> None:
    c.create_oval(1, 1, 33, 33, fill="#224a78", outline="#4d8fd0")
    c.create_polygon(17, 4, 12, 15, 22, 15, fill="white")
    c.create_rectangle(12, 13, 22, 26, fill="white", outline="")
    c.create_polygon(12, 20, 5, 28, 12, 26, fill="white", outline="")
    c.create_polygon(22, 20, 29, 28, 22, 26, fill="white", outline="")
    c.create_oval(14, 16, 20, 22, fill="#224a78", outline="")
    c.create_polygon(14, 26, 20, 26, 17, 32, fill="#ffb454", outline="")


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.settings = load_settings()
        self.queue: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self.busy = False
        self.game: launch.GameProcess | None = None
        self.account: dict | None = None
        self.manifest = None
        self.version_cache: dict = {}
        self.entries: list = []
        self._session = None  # (version_id, start_time) while a game runs

        root.title("%s %s" % (APP_NAME, APP_VERSION))
        root.configure(bg=BG)
        root.geometry("1020x660")
        root.minsize(940, 600)
        self._style()
        self._build()
        self._set_account(self._offline_account())
        self.root.after(80, self._pump)
        self.refresh_versions()

    # ------------------------------------------------------------------ ui
    def _style(self):
        st = ttk.Style(self.root)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure(".", background=BG, foreground=TEXT, fieldbackground=PANEL2,
                     bordercolor=LINE, lightcolor=LINE, darkcolor=LINE)
        st.configure("TFrame", background=BG)
        st.configure("Panel.TFrame", background=PANEL)
        st.configure("TLabel", background=BG, foreground=TEXT)
        st.configure("Panel.TLabel", background=PANEL, foreground=TEXT)
        st.configure("Dim.TLabel", background=BG, foreground=DIM)
        st.configure("PanelDim.TLabel", background=PANEL, foreground=DIM)
        st.configure("Head.TLabel", background=BG, foreground=TEXT, font=("Segoe UI Semibold", 15))
        st.configure("Sub.TLabel", background=BG, foreground=DIM, font=("Segoe UI", 9))
        st.configure("TButton", background=PANEL, foreground=TEXT, focuscolor=BG,
                     font=("Segoe UI", 9), borderwidth=0, padding=(10, 6))
        st.map("TButton", background=[("active", "#1b2434"), ("disabled", PANEL)],
               foreground=[("disabled", DIM)])
        st.configure("Go.TButton", background=GREEN, foreground="#06210f",
                     font=("Segoe UI Semibold", 12), padding=(16, 14))
        st.map("Go.TButton", background=[("active", "#54d06a"), ("disabled", "#1f3a26")],
               foreground=[("disabled", DIM)])
        st.configure("Stop.TButton", background="#3a1d1d", foreground="#ffd7d7")
        st.map("Stop.TButton", background=[("active", "#4d2626")])
        st.configure("TCheckbutton", background=BG, foreground=DIM)
        st.map("TCheckbutton", background=[("active", BG)])
        st.configure("TEntry", fieldbackground=PANEL2, foreground=TEXT, insertcolor=TEXT)
        st.configure("TCombobox", fieldbackground=PANEL2, foreground=TEXT, background=PANEL,
                     bordercolor=LINE, arrowcolor=TEXT, selectbackground=PANEL2,
                     selectforeground=TEXT)
        st.map("TCombobox",
               fieldbackground=[("readonly", PANEL2), ("disabled", PANEL2)],
               foreground=[("readonly", TEXT), ("disabled", DIM)],
               background=[("readonly", PANEL), ("active", "#1b2434")],
               selectbackground=[("readonly", PANEL2)],
               selectforeground=[("readonly", TEXT)])
        st.configure("Mods.Treeview", background=PANEL2, fieldbackground=PANEL2,
                     foreground=TEXT, borderwidth=0, rowheight=22)
        st.configure("Mods.Treeview.Heading", background=PANEL, foreground=DIM,
                     font=("Segoe UI Semibold", 8), borderwidth=0)
        st.map("Mods.Treeview", background=[("selected", ACCENT)],
               foreground=[("selected", "#04121f")])
        st.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=PANEL2,
                     bordercolor=PANEL2, lightcolor=ACCENT, darkcolor=ACCENT)

    def _build(self):
        top = ttk.Frame(self.root, style="TFrame")
        top.pack(fill="x", padx=18, pady=(14, 6))
        self.logo = tk.Canvas(top, width=34, height=34, bg=BG, highlightthickness=0)
        self.logo.pack(side="left")
        self._draw_logo()
        ttk.Label(top, text=APP_NAME.upper(), style="Head.TLabel").pack(side="left", padx=(10, 0))
        ttk.Button(top, text="Settings", command=self.open_settings).pack(side="right")
        ttk.Button(top, text="Credits", command=self.open_credits).pack(side="right", padx=(0, 8))
        ttk.Button(top, text="News", command=self.open_news).pack(side="right", padx=(0, 8))
        ttk.Button(top, text="Screenshots", command=self.open_screenshots).pack(side="right", padx=(0, 8))
        ttk.Button(top, text="Worlds", command=self.open_worlds).pack(side="right", padx=(0, 8))
        ttk.Button(top, text="Mods", command=self.open_mods).pack(side="right", padx=(0, 8))
        ttk.Button(top, text="Open folder", command=self.open_folder).pack(side="right", padx=(0, 8))
        self.realms_btn = ttk.Button(top, text="Realms", command=self.open_realms, state="disabled")
        self.realms_btn.pack(side="right", padx=(0, 8))

        body = ttk.Frame(self.root, style="TFrame")
        body.pack(fill="both", expand=True, padx=18, pady=(4, 14))

        left = ttk.Frame(body, style="Panel.TFrame", padding=14)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        left.configure(width=332)

        ttk.Label(left, text="ACCOUNT", style="PanelDim.TLabel",
                  font=("Segoe UI Semibold", 8)).pack(anchor="w")
        self.acct_lbl = ttk.Label(left, text="Offline", style="Panel.TLabel",
                                  font=("Segoe UI Semibold", 11), wraplength=270, justify="left")
        self.acct_lbl.pack(anchor="w", pady=(4, 2))
        self.acct_sub = ttk.Label(left, text="", style="PanelDim.TLabel", wraplength=270,
                                  justify="left")
        self.acct_sub.pack(anchor="w")

        row = ttk.Frame(left, style="Panel.TFrame")
        row.pack(fill="x", pady=(8, 0))
        self.name_var = tk.StringVar(value=self.settings.get("offline_name", "Player"))
        ttk.Entry(row, textvariable=self.name_var, width=14).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Use", width=5, command=self._use_offline).pack(side="left", padx=(6, 0))
        ttk.Button(left, text="Sign in with Microsoft", command=self._sign_in).pack(
            fill="x", pady=(8, 0))

        ttk.Separator(left, orient="horizontal").pack(fill="x", pady=12)
        ttk.Label(left, text="VERSIONS", style="PanelDim.TLabel",
                  font=("Segoe UI Semibold", 8)).pack(anchor="w")
        listwrap = ttk.Frame(left, style="Panel.TFrame")
        listwrap.pack(fill="both", expand=True, pady=(6, 6))
        self.listbox = tk.Listbox(listwrap, bg=PANEL2, fg=TEXT, selectbackground=ACCENT,
                                  selectforeground="#04121f", activestyle="none",
                                  highlightthickness=0, borderwidth=0, font=("Consolas", 9))
        sb = ttk.Scrollbar(listwrap, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.listbox.pack(side="left", fill="both", expand=True)
        self.listbox.bind("<<ListboxSelect>>", lambda e: self._update_go_text())

        self.snap_var = tk.BooleanVar(value=bool(self.settings.get("show_snapshots")))
        ttk.Checkbutton(left, text="Show snapshots", variable=self.snap_var,
                        command=self.refresh_versions).pack(anchor="w")

        # right side
        right = ttk.Frame(body, style="TFrame")
        right.pack(side="left", fill="both", expand=True, padx=(14, 0))

        go = ttk.Frame(right, style="TFrame")
        go.pack(fill="x")
        self.play_btn = ttk.Button(go, text="PLAY", style="Go.TButton", command=self.play)
        self.play_btn.pack(side="left", fill="x", expand=True)
        self.stop_btn = ttk.Button(go, text="Stop", style="Stop.TButton", command=self.stop_game,
                                   state="disabled")
        self.stop_btn.pack(side="left", padx=(8, 0), fill="y")
        self.install_btn = ttk.Button(go, text="Install / verify", command=self.install_only)
        self.install_btn.pack(side="left", padx=(8, 0), fill="y")

        self.progress = ttk.Progressbar(right, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(10, 4))
        self.status = ttk.Label(right, text="Ready.", style="Sub.TLabel")
        self.status.pack(anchor="w")
        self.stats_lbl = ttk.Label(right, text="", style="Sub.TLabel")
        self.stats_lbl.pack(anchor="w", pady=(2, 0))

        logf = ttk.Frame(right, style="TFrame")
        logf.pack(fill="both", expand=True, pady=(10, 0))
        self.log = tk.Text(logf, bg=PANEL2, fg="#c9d6e6", insertbackground=TEXT, wrap="none",
                           relief="flat", font=("Consolas", 9), state="disabled")
        lsb = ttk.Scrollbar(logf, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=lsb.set)
        lsb.pack(side="right", fill="y")
        xsb = ttk.Scrollbar(logf, orient="horizontal", command=self.log.xview)
        self.log.configure(xscrollcommand=xsb.set)
        xsb.pack(side="bottom", fill="x")
        self.log.pack(side="left", fill="both", expand=True)

    def _draw_logo(self):
        draw_app_logo(self.logo)

    # ------------------------------------------------------------- accounts
    def _offline_account(self) -> dict:
        acct = launch.offline_account(self.name_var.get() if hasattr(self, "name_var")
                                      else self.settings.get("offline_name", "Player"))
        self.settings["offline_name"] = acct["name"]
        return acct

    def _set_account(self, acct: dict):
        self.account = acct
        kind = acct.get("kind")
        if kind == "microsoft":
            self.acct_lbl.configure(text=acct["name"])
            self.acct_sub.configure(text="Microsoft account - online play enabled")
            self.realms_btn.configure(state="normal")
        else:
            self.acct_lbl.configure(text=acct["name"] + "  (offline)")
            self.acct_sub.configure(text="Singleplayer and LAN worlds. Online servers need "
                                         "a Microsoft account that owns Minecraft.")
            self.realms_btn.configure(state="disabled")
        self._update_go_text()

    def _use_offline(self):
        save_settings(self.settings)
        self._set_account(self._offline_account())
        self.log_line("offline profile: " + self.account["name"])

    def _sign_in(self):
        cid = (self.settings.get("client_id") or "").strip()
        if not cid:
            messagebox.showinfo(APP_NAME, "Microsoft sign-in needs a client ID.\n\n"
                                          "Create a free app registration in Azure (Mobile and "
                                          "desktop, public client flows = Yes), then paste its "
                                          "Application (client) ID in Settings.")
            self.open_settings()
            return
        if self.busy:
            return
        threading.Thread(target=self._sign_in_worker, args=(cid,), daemon=True).start()

    def _sign_in_worker(self, cid: str):
        try:
            dev = auth.begin_device_login(cid)
            self.q("status", "Microsoft: enter the code %s at %s" %
                   (dev.get("user_code"), dev.get("verification_uri")))
            self.q("log", "Microsoft sign-in: open %s and enter %s" %
                   (dev.get("verification_uri"), dev.get("user_code")))
            try:
                webbrowser.open(dev.get("verification_uri"))
            except Exception:  # noqa: BLE001
                pass
            acct = auth.login_with_microsoft(cid, dev["device_code"], dev.get("interval", 5),
                                             dev.get("expires_in", 900), cancel=self.cancel,
                                             on_tick=lambda left: self.q("status",
                                                                         "waiting for sign-in... %ds" % left))
            self.q("account", acct)
            self.q("status", "Signed in as %s" % acct["name"])
        except Exception as exc:  # noqa: BLE001
            self.q("status", "Microsoft sign-in failed: %s" % exc)

    # ------------------------------------------------------------- versions
    def refresh_versions(self):
        if self.busy:
            return
        # read Tk state on the UI thread, then hand a plain value to the worker
        snap = bool(self.snap_var.get())
        if self.settings.get("show_snapshots") != snap:
            self.settings["show_snapshots"] = snap
            save_settings(self.settings)

        # Paint the saved list immediately, then revalidate in the background.
        # Waiting on the network before showing anything is what made startup
        # feel slow.
        cached = install.cached_manifest()
        if cached:
            self._fill_versions(self._filter_versions(cached, snap))
            self.q("status", "%d versions (checking for updates...)" % len(self.entries))
        else:
            self.q("status", "fetching version list...")
        threading.Thread(target=self._versions_worker, args=(snap,), daemon=True).start()

    @staticmethod
    def _filter_versions(manifest: dict, snap: bool) -> list:
        keep = ("release", "snapshot") if snap else ("release",)
        return [v for v in manifest.get("versions", []) if v.get("type") in keep]

    def _versions_worker(self, snap: bool):
        try:
            man, state = install.fetch_manifest_state()
        except Exception as exc:  # noqa: BLE001
            self.q("status", "could not fetch versions: %s" % exc)
            return
        self.manifest = man
        entries = self._filter_versions(man, snap)
        if [v["id"] for v in entries] == [v["id"] for v in self.entries]:
            # nothing changed - leave the listbox (and the user's scroll) alone
            self.q("status", "%d versions - list is current" % len(entries))
            return
        self.q("versions", entries)
        if state == "stale":
            self.q("status", "%d versions (offline copy)" % len(entries))

    def _fill_versions(self, entries):
        self.entries = entries
        game_dir = self.settings["game_dir"]
        installed = set(install.installed_versions(game_dir))
        modded = self.settings.get("mod_profiles") or {}
        mod_count = mods.installed_count(game_dir)
        self.listbox.delete(0, "end")
        for v in entries:
            mark = " *" if v["id"] in installed else ""
            if v["id"] in modded and mod_count:
                mark += " +mods"
            self.listbox.insert("end", "%-18s %s%s" % (v["id"], v["type"][:3], mark))
        want = self.settings.get("last_version") or ""
        pick = None
        for i, v in enumerate(entries):
            if v["id"] == want:
                pick = i
                break
        if pick is None and entries:
            pick = 0  # fall back to the newest version in the list
        if pick is not None:
            self.listbox.selection_clear(0, "end")
            self.listbox.selection_set(pick)
            self.listbox.activate(pick)
            self.listbox.see(pick)
        self._update_go_text()

    def _selected_version(self) -> dict | None:
        sel = self.listbox.curselection()
        if not sel or not self.entries:
            return None
        return self.entries[sel[0]]

    def _update_go_text(self):
        v = self._selected_version()
        if not v:
            self.play_btn.configure(text="PLAY")
            self._update_stats(None)
            return
        prof = (self.settings.get("mod_profiles") or {}).get(v["id"]) or {}
        if prof.get("profile") and mods.installed_count(self.settings["game_dir"]):
            self.play_btn.configure(text="PLAY  %s  ·  Fabric" % v["id"])
        else:
            self.play_btn.configure(text="PLAY  " + v["id"])
        self._update_stats(v["id"])

    def _update_stats(self, version_id):
        """Show last-played / total play time for the selected version."""
        if not version_id:
            self.stats_lbl.configure(text="")
            return
        keys = [version_id]
        prof = (self.settings.get("mod_profiles") or {}).get(version_id) or {}
        if prof.get("profile"):
            keys.append(prof["profile"])
        played = self.settings.get("playtime") or {}
        seen = self.settings.get("last_played") or {}
        spent = sum(float(played.get(k, 0) or 0) for k in keys)
        last = max([float(seen.get(k, 0) or 0) for k in keys] or [0])
        parts = ["Last played: " + relative_time(last)]
        if spent:
            parts.append("Total: " + format_duration(spent))
        self.stats_lbl.configure(text="   ·   ".join(parts))

    # ----------------------------------------------------------------- act
    def open_folder(self):
        d = self.settings["game_dir"]
        os.makedirs(d, exist_ok=True)
        try:
            os.startfile(d)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            subprocess.Popen(["explorer", d])

    def open_mods(self):
        ModLibraryDialog(self).show()

    def open_credits(self):
        CreditsDialog(self).show()

    def open_settings(self):
        SettingsDialog(self).show()

    def open_worlds(self):
        WorldsDialog(self).show()

    def open_screenshots(self):
        ScreenshotsDialog(self).show()

    def open_news(self):
        NewsDialog(self).show()

    def open_realms(self):
        if self.account and self.account.get("kind") == "microsoft":
            RealmsDialog(self).show()
        else:
            messagebox.showinfo(APP_NAME, "Sign in with Microsoft first to use Realms.",
                                parent=self.root)

    def log_line(self, text: str):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        if int(self.log.index("end-1c").split(".")[0]) > 1200:
            self.log.delete("1.0", "300.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def q(self, kind, payload=None):
        self.queue.put((kind, payload))

    def _pump(self):
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "log":
                    self.log_line(payload)
                elif kind == "status":
                    self.status.configure(text=str(payload))
                elif kind == "progress":
                    done, total, label = payload
                    pct = int(done * 100 / total) if total else 0
                    self.progress.configure(value=max(0, min(100, pct)))
                    self.status.configure(text="%s  -  %s / %s (%d%%)" %
                                               (label, human_size(done), human_size(total), pct))
                elif kind == "versions":
                    self._fill_versions(payload)
                    self.q("status", "%d versions available" % len(payload))
                elif kind == "mods-changed":
                    self._fill_versions(self.entries)
                    self._update_go_text()
                elif kind == "account":
                    self._set_account(payload)
                elif kind == "busy":
                    self.busy = bool(payload)
                    self.install_btn.configure(text="Cancel" if self.busy else "Install / verify",
                                               command=self.cancel_work if self.busy else self.install_only)
                    self.play_btn.configure(state="disabled" if (self.busy or self.game) else "normal")
                elif kind == "playing":
                    self.stop_btn.configure(state="normal" if payload else "disabled")
                    self.play_btn.configure(state="disabled" if payload else "normal")
                elif kind == "exit":
                    self._game_exit(payload)
                elif kind == "iconify":
                    try:
                        self.root.iconify()
                    except tk.TclError:
                        pass
                elif kind == "error":
                    self.status.configure(text=str(payload))
                    self.log_line("ERROR " + str(payload))
        except queue.Empty:
            pass
        self.root.after(80, self._pump)

    def cancel_work(self):
        self.cancel.set()
        self.q("status", "cancelling...")

    # ------------------------------------------------------------ installing
    def _cancel_check(self):
        if self.cancel.is_set():
            raise Cancelled()

    def _pick_meta(self):
        """Read the listbox selection. MUST run on the UI thread."""
        sel = self._selected_version()
        if not sel:
            self.q("status", "pick a version on the left first")
        return sel

    def install_only(self):
        self._start_work(False)

    def play(self):
        if self.game:
            return
        self._start_work(True)

    def play_world(self, world_name: str):
        """Start the selected version straight into a singleplayer world."""
        if self.game or self.busy:
            messagebox.showinfo(APP_NAME, "A game is already running or being prepared.")
            return
        self._start_work(True, world_name)

    def _start_work(self, then_launch: bool, world: str = ""):
        if self.busy:
            return
        sel = self._pick_meta()  # Tk read happens here, on the main thread
        if not sel:
            return
        self.cancel.clear()
        self.q("busy", True)
        threading.Thread(target=self._work, args=(then_launch, sel, world),
                         daemon=True).start()

    def _work(self, then_launch: bool, sel: dict, world: str = ""):
        try:
            vid = sel["id"]
            game_dir = self.settings["game_dir"]
            prof = (self.settings.get("mod_profiles") or {}).get(vid) or {}
            profile_id = prof.get("profile") or ""
            # Only use the loader profile if it is actually on disk; otherwise
            # fall back to vanilla rather than failing at launch.
            modded = bool(profile_id) and install.local_version_json(game_dir, profile_id) is not None
            run_id = profile_id if modded else vid
            try:
                if run_id not in self.version_cache:
                    if modded:
                        self.version_cache[run_id] = install.load_version_json(
                            profile_id, None, game_dir, self.manifest)
                    else:
                        self.version_cache[run_id] = install.fetch_version_json(sel)
                vj = self.version_cache[run_id]
            except Exception as exc:  # noqa: BLE001
                self.q("error", "could not read version %s: %s" % (run_id, exc))
                return
            self.settings["last_version"] = vid
            self.settings["offline_name"] = self.account["name"]
            save_settings(self.settings)
            info = install.plan(vj)
            self.q("log", "version %s | client %s | %d libraries (%s) | %d native jars | "
                          "assets %s (%s) | java %s" %
                   (run_id, human_size(info["client_bytes"]), info["library_count"],
                    human_size(info["library_bytes"]), info["native_count"],
                    info["asset_index"], human_size(info["asset_bytes"]), info["java"]))
            if modded:
                self.q("log", "mods: Fabric %s | %d mod(s) from %s" %
                       (prof.get("loader") or "?", mods.installed_count(game_dir),
                        mods.mods_dir(game_dir)))
            self.q("status", "preparing %s ..." % run_id)
            prepared = install.install_version(
                run_id, vj, game_dir,
                log=lambda s: self.q("log", s),
                progress=lambda d, t, l: self.q("progress", (d, t, l)),
                cancel=self._cancel_check, with_assets=True,
                workers=int(self.settings.get("workers", 16)),
                fast=then_launch)
            install.write_launcher_profile(game_dir, run_id)
            self.q("log", "game files ready")
            java = install.ensure_java(
                vj, self.settings["game_dir"], (self.settings.get("java_path") or "").strip() or None,
                log=lambda s: self.q("log", s),
                progress=lambda d, t, l: self.q("progress", (d, t, l)),
                cancel=self._cancel_check)
            if not then_launch:
                self.q("status", "%s installed - press PLAY" % vid)
                return
            ram_now = int(self.settings.get("ram_mb") or 0)
            mod_now = mods.installed_count(game_dir)
            mem = memory_status()
            if mem.get("pagefile_mb", 0) < 1024 and mod_now >= 25:
                self.q("log", "warning: this PC has virtual memory (a pagefile) turned off, so "
                              "Windows can only commit %d MB beyond what is already in use. "
                              "Big modpacks can crash while a world loads. Turn on System managed "
                              "virtual memory in System Properties > Advanced > Performance > "
                              "Advanced, then restart the PC."
                       % mem.get("commit_free_mb", 0))
            ram_use = recommended_ram_mb(ram_now, mod_now)
            if ram_use != ram_now:
                self.q("log", "memory: %d MB heap for %d mod(s) (requested %d MB) - sized to "
                              "leave enough RAM for the JVM/native side"
                       % (ram_use, mod_now, ram_now))
            run_settings = dict(self.settings)
            run_settings["ram_mb"] = ram_use
            if world:
                quick = '--quickPlaySingleplayer "%s"' % world.replace('"', "")
                run_settings["game_args"] = ("%s %s" % (run_settings.get("game_args", ""), quick)).strip()
                self.q("log", "quick play: " + world)
            cmd = launch.build_command(prepared, vj, self.account, run_settings, java)
            self.q("log", "launch: " + " ".join(cmd))
            self.q("status", "starting Minecraft (%s) ..." % self.account["name"])
            if not self.settings.get("keep_open", True):
                self.q("iconify", True)
            self._session = (vid, time.time())
            last = dict(self.settings.get("last_played") or {})
            last[vid] = self._session[1]
            self.settings["last_played"] = last
            save_settings(self.settings)
            self.game = launch.launch_game(cmd, self.settings["game_dir"],
                                           on_line=lambda s: self.q("log", s),
                                           on_exit=lambda c: self.q("exit", c))
            self.q("playing", True)
        except Cancelled:
            self.q("status", "cancelled")
        except Exception as exc:  # noqa: BLE001
            self.q("error", str(exc))
        finally:
            self.q("busy", False)

    def stop_game(self):
        if self.game:
            self.game.stop()
            self.q("status", "asking the game to close...")

    def _game_exit(self, code):
        self.game = None
        if self._session:
            run_id, started = self._session
            self._session = None
            elapsed = max(0.0, time.time() - started)
            if elapsed >= 1:
                spent = dict(self.settings.get("playtime") or {})
                spent[run_id] = float(spent.get(run_id, 0)) + elapsed
                self.settings["playtime"] = spent
                save_settings(self.settings)
            self._update_go_text()
        self.q("playing", False)
        note = self._diagnose_exit(code)
        self.q("status", "Minecraft exited (code %s)%s" % (code, note))
        if note:
            self.q("log", "exit note: %s" % note.strip())
        try:
            self.root.deiconify()
        except tk.TclError:
            pass

    def _diagnose_exit(self, code: int) -> str:
        """When the game closes itself, look at JVM/game logs for a reason."""
        if code == 0:
            return ""
        game_dir = self.settings.get("game_dir", "")
        # JVM-level crash (native OOM etc.) leaves an hs_err file next to the game
        try:
            names = sorted(
                (n for n in os.listdir(game_dir) if n.startswith("hs_err_pid") and n.endswith(".log")),
                key=lambda n: os.path.getmtime(os.path.join(game_dir, n)), reverse=True)
        except OSError:
            names = []
        if names:
            path = os.path.join(game_dir, names[0])
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    head = fh.read(2000)
            except OSError:
                head = ""
            if "insufficient memory" in head or "Out of Memory" in head:
                mem = memory_status()
                if mem.get("pagefile_mb", 0) < 1024:
                    hint = (" Windows reports no pagefile / virtual memory, so the commit "
                            "limit is only the physical RAM. A large modpack needs more. "
                            "Turn on a System managed pagefile in System Properties > "
                            "Advanced > Performance > Advanced, then restart and retry.")
                else:
                    hint = (" Lower Memory in Settings or close other programs, then retry.")
                return " The Java runtime ran out of memory." + hint
            return (" The Java runtime crashed (see %s)." % names[0])
        # Java-level crash reports written by Minecraft itself
        cr = os.path.join(game_dir, "crash-reports")
        try:
            reports = sorted(
                (n for n in os.listdir(cr) if n.endswith(".txt")),
                key=lambda n: os.path.getmtime(os.path.join(cr, n)), reverse=True)
        except OSError:
            reports = []
        if reports:
            path = os.path.join(cr, reports[0])
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    text = fh.read(3000)
            except OSError:
                text = ""
            if "OutOfMemoryError" in text:
                return (" The game ran out of memory (see crash report %s). "
                        "Raise Memory in Settings." % reports[0])
            first = text.splitlines()[0] if text.splitlines() else "crash report"
            return (" The game crashed (see crash report %s)." % reports[0])
        return ""


class CreditsDialog:
    """Project dates, publisher names and inspiration."""

    def __init__(self, app: App):
        self.app = app

    def show(self):
        app = self.app
        info = credits_info()
        win = tk.Toplevel(app.root)
        win.title("Credits")
        win.configure(bg=BG)
        win.geometry("520x392")
        win.resizable(False, False)
        win.transient(app.root)
        win.grab_set()

        head = ttk.Frame(win, style="TFrame")
        head.pack(fill="x", padx=18, pady=(16, 10))
        logo = tk.Canvas(head, width=34, height=34, bg=BG, highlightthickness=0)
        logo.pack(side="left")
        draw_app_logo(logo)
        titles = ttk.Frame(head, style="TFrame")
        titles.pack(side="left", padx=(10, 0))
        ttk.Label(titles, text=APP_NAME.upper(), style="Head.TLabel").pack(anchor="w")
        ttk.Label(titles, text="Credits  ·  version %s" % APP_VERSION,
                  style="Sub.TLabel").pack(anchor="w")

        panel = ttk.Frame(win, style="Panel.TFrame", padding=16)
        panel.pack(fill="both", expand=True, padx=18)
        panel.columnconfigure(1, weight=1)
        rows = [
            ("CREATED", info["created"], False),
            ("UPDATED", info["updated"], False),
            ("PUBLISHER", info["publishers"][0], True),
            ("", info["publishers"][1], False),
            ("INSPIRATION", info["inspiration"], False),
        ]
        for i, (label, value, strong) in enumerate(rows):
            ttk.Label(panel, text=label, style="PanelDim.TLabel",
                      font=("Segoe UI Semibold", 8)).grid(
                row=i, column=0, sticky="nw", padx=(0, 18), pady=(0, 10))
            ttk.Label(panel, text=value, style="Panel.TLabel",
                      font=("Segoe UI Semibold" if strong else "Segoe UI", 11)).grid(
                row=i, column=1, sticky="w", pady=(0, 10))

        ttk.Label(win, text="Original launcher code and UI made for this project.\n"
                            "Not affiliated with Mojang, Microsoft or any other launcher.",
                  style="Dim.TLabel", justify="center").pack(pady=(12, 0))
        btns = ttk.Frame(win, style="TFrame")
        btns.pack(fill="x", padx=18, pady=14)
        ttk.Button(btns, text="Close", command=win.destroy).pack(side="right")
        win.bind("<Escape>", lambda _e: win.destroy())
        win.wait_window()


class ModLibraryDialog:
    """Browse Modrinth, install Fabric mods, and manage the mods folder."""

    def __init__(self, app: App):
        self.app = app
        self.win = None
        self.events: queue.Queue = queue.Queue()
        self.hits: list = []
        self.versions: list = []
        self.project: dict | None = None
        self.installed_items: list = []
        self.target = ""
        self.busy = False

    # ------------------------------------------------------------- window
    def show(self):
        app = self.app
        sel = app._selected_version()
        self.target = ((sel or {}).get("id") or app.settings.get("last_version")
                       or (app.entries[0]["id"] if app.entries else ""))
        if not self.target:
            messagebox.showinfo(APP_NAME, "Pick a Minecraft version in the list first, then "
                                          "open the mod library.")
            return

        win = tk.Toplevel(app.root)
        self.win = win
        win.title("Mod library")
        win.configure(bg=BG)
        win.geometry("880x620")
        win.minsize(760, 540)
        win.transient(app.root)
        win.grab_set()

        head = ttk.Frame(win, style="TFrame")
        head.pack(fill="x", padx=14, pady=(12, 4))
        ttk.Label(head, text="MOD LIBRARY", style="Head.TLabel").pack(side="left")
        ttk.Label(head, text="  Fabric mods for %s  ·  from Modrinth" % self.target,
                  style="Sub.TLabel").pack(side="left", pady=(6, 0))
        ttk.Button(head, text="Mods folder",
                   command=lambda: mods.open_folder(app.settings["game_dir"])).pack(side="right")

        srow = ttk.Frame(win, style="TFrame")
        srow.pack(fill="x", padx=14, pady=(6, 0))
        self.query_var = tk.StringVar()
        entry = ttk.Entry(srow, textvariable=self.query_var)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda _e: self._search())
        self.sort_var = tk.StringVar(value="Relevance")
        ttk.Combobox(srow, textvariable=self.sort_var, values=list(mods.SORTS),
                     width=11, state="readonly").pack(side="left", padx=(8, 0))
        self.cat_var = tk.StringVar(value="All categories")
        ttk.Label(srow, text="  Category:", style="Dim.TLabel").pack(
            side="left", padx=(8, 0))
        ttk.Combobox(srow, textvariable=self.cat_var,
                     values=["All categories"] + mods.category_names(),
                     width=20, state="readonly").pack(side="left", padx=(2, 0))
        ttk.Button(srow, text="Search", command=self._search).pack(side="left", padx=(8, 0))

        body = ttk.Frame(win, style="TFrame")
        body.pack(fill="both", expand=True, padx=14, pady=(10, 4))

        left = ttk.Frame(body, style="Panel.TFrame", padding=1)
        left.pack(side="left", fill="both", expand=True)
        self.results = ttk.Treeview(left, columns=("mod", "downloads", "author"),
                                    show="headings", style="Mods.Treeview",
                                    selectmode="browse")
        for col, text, width, anchor in (("mod", "MOD", 300, "w"),
                                         ("downloads", "DOWNLOADS", 90, "e"),
                                         ("author", "AUTHOR", 120, "w")):
            self.results.heading(col, text=text)
            self.results.column(col, width=width, anchor=anchor, stretch=(col == "mod"))
        rsb = ttk.Scrollbar(left, orient="vertical", command=self.results.yview)
        self.results.configure(yscrollcommand=rsb.set)
        rsb.pack(side="right", fill="y")
        self.results.pack(side="left", fill="both", expand=True)
        self.results.bind("<<TreeviewSelect>>", self._on_hit)

        right = ttk.Frame(body, style="Panel.TFrame", padding=12)
        right.pack(side="left", fill="y", padx=(10, 0))
        right.configure(width=330)
        right.pack_propagate(False)
        self.detail_title = ttk.Label(right, text="Pick a mod", style="Panel.TLabel",
                                      font=("Segoe UI Semibold", 11), wraplength=300,
                                      justify="left")
        self.detail_title.pack(anchor="w")
        self.detail_meta = ttk.Label(right, text="", style="PanelDim.TLabel",
                                     wraplength=300, justify="left")
        self.detail_meta.pack(anchor="w", pady=(4, 0))
        self.detail_desc = ttk.Label(right, text="Search above to see Fabric mods that work "
                                                  "with this Minecraft version.",
                                     style="PanelDim.TLabel", wraplength=300, justify="left")
        self.detail_desc.pack(anchor="w", pady=(8, 10))
        ttk.Label(right, text="BUILD", style="PanelDim.TLabel",
                  font=("Segoe UI Semibold", 8)).pack(anchor="w")
        self.version_var = tk.StringVar()
        self.version_box = ttk.Combobox(right, textvariable=self.version_var, values=[],
                                        state="disabled", width=36)
        self.version_box.pack(anchor="w", pady=(4, 8), fill="x")
        self.install_btn = ttk.Button(right, text="Install mod", command=self._install,
                                      state="disabled")
        self.install_btn.pack(fill="x")
        ttk.Label(right, text="Installing a mod also sets up Fabric for %s and switches "
                              "PLAY to it automatically." % self.target,
                  style="PanelDim.TLabel", wraplength=300, justify="left").pack(
            anchor="w", pady=(10, 0))

        self.installed_lbl = ttk.Label(win, text="INSTALLED MODS", style="Dim.TLabel",
                                       font=("Segoe UI Semibold", 8))
        self.installed_lbl.pack(anchor="w", padx=14, pady=(8, 2))
        ifr = ttk.Frame(win, style="TFrame")
        ifr.pack(fill="x", padx=14)
        self.installed = ttk.Treeview(ifr, columns=("mod", "version", "state"), show="headings",
                                      height=5, style="Mods.Treeview", selectmode="browse")
        for col, text, width, anchor in (("mod", "MOD", 390, "w"),
                                         ("version", "VERSION", 250, "w"),
                                         ("state", "STATE", 110, "w")):
            self.installed.heading(col, text=text)
            self.installed.column(col, width=width, anchor=anchor, stretch=(col != "state"))
        isb = ttk.Scrollbar(ifr, orient="vertical", command=self.installed.yview)
        self.installed.configure(yscrollcommand=isb.set)
        isb.pack(side="right", fill="y")
        self.installed.pack(side="left", fill="x", expand=True)
        self.installed.bind("<<TreeviewSelect>>", lambda _e: self._sync_buttons())

        ib = ttk.Frame(win, style="TFrame")
        ib.pack(fill="x", padx=14, pady=(6, 0))
        self.toggle_btn = ttk.Button(ib, text="Disable", command=self._toggle, state="disabled")
        self.toggle_btn.pack(side="left")
        self.remove_btn = ttk.Button(ib, text="Remove", command=self._remove, state="disabled")
        self.remove_btn.pack(side="left", padx=(8, 0))
        ttk.Button(ib, text="Remove all", command=self._remove_all).pack(side="left", padx=(8, 0))

        self.status = ttk.Label(win, text="", style="Sub.TLabel")
        self.status.pack(anchor="w", padx=14, pady=(8, 10))

        win.protocol("WM_DELETE_WINDOW", self._close)
        self._refresh_installed()
        win.after(120, self._pump)
        win.after(150, self._search)

    def _close(self):
        if self.win is not None:
            try:
                self.win.destroy()
            except tk.TclError:
                pass
        self.win = None

    # -------------------------------------------------------------- plumbing
    def _pump(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "search":
                    self._show_hits(*payload)
                elif kind == "versions":
                    self._show_versions(payload)
                elif kind == "installed":
                    self._show_installed(payload)
                elif kind == "status":
                    self.status.configure(text=str(payload))
                elif kind == "error":
                    self.status.configure(text=str(payload))
                    self.app.log_line("MODS " + str(payload))
                elif kind == "done-install":
                    self.busy = False
                    self.install_btn.configure(state="normal" if self.versions else "disabled",
                                               text="Install mod")
        except queue.Empty:
            pass
        if self.win is not None and self.win.winfo_exists():
            self.win.after(120, self._pump)

    def _refresh_installed(self):
        self.events.put(("installed", mods.installed_mods(self.app.settings["game_dir"])))

    # ---------------------------------------------------------------- search
    def _search(self):
        if self.busy:
            return
        query = self.query_var.get().strip()
        sort = mods.SORTS.get(self.sort_var.get(), "relevance")
        self.status.configure(text="searching Modrinth...")
        threading.Thread(target=self._search_worker, args=(query, sort), daemon=True).start()

    def _search_worker(self, query, sort):
        try:
            if sort == "featured":
                cat = self.cat_var.get()
                category = None if cat == "All categories" else cat
                hits = mods.featured_mods_by_category(category, self.target, limit=120)
                self.events.put(("search", (hits, len(hits))))
            else:
                hits, total = mods.search(query, self.target, limit=30, sort=sort)
                self.events.put(("search", (hits, total)))
        except Exception as exc:  # noqa: BLE001
            self.events.put(("error", "search failed: %s" % exc))

    def _show_hits(self, hits, total):
        self.hits = hits
        self.results.delete(*self.results.get_children())
        for i, hit in enumerate(hits):
            self.results.insert("", "end", iid=str(i),
                                values=(hit["title"], _fmt_count(hit["downloads"]),
                                        hit["author"]))
        if hits:
            self.results.selection_set("0")
            self.results.focus("0")
        if hits and hits[0].get("_version"):
            # curated "featured" rows: every hit already carries its working build
            self.versions = [h["_version"] for h in hits if h.get("_version")]
            labels = ["%s  ·  %s" % (v.get("version_number") or "", v.get("version_type") or "")
                      for v in self.versions]
            self.version_box.configure(values=labels, state="readonly")
            pick = mods.pick_version(self.versions, prefer_release=True)
            idx = self.versions.index(pick) if pick in self.versions else 0
            self.version_box.current(idx)
            self.install_btn.configure(text="Install mod", state="normal")
            cat = self.cat_var.get()
            cat_text = (" in " + cat) if cat != "All categories" else ""
            self.status.configure(text="%d mod(s)%s for %s" %
                                  (len(self.versions), cat_text, self.target))
            return
        self.status.configure(text="%d mods found" % total)

    def _on_hit(self, _event=None):
        sel = self.results.selection()
        if not sel:
            return
        try:
            self.project = self.hits[int(sel[0])]
        except (IndexError, ValueError):
            return
        p = self.project
        self.detail_title.configure(text=p["title"])
        self.detail_meta.configure(text="%s  ·  %s downloads" %
                                        (p["author"] or "unknown", _fmt_count(p["downloads"])))
        self.detail_desc.configure(text=p["description"] or "")
        self.versions = []
        self.version_var.set("")
        if p.get("_version"):
            self.versions = [p["_version"]]
            labels = ["%s  ·  %s" % (p["_version"].get("version_number") or "",
                                     p["_version"].get("version_type") or "")]
            self.version_box.configure(values=labels, state="readonly")
            self.version_box.current(0)
            self.install_btn.configure(text="Install mod", state="normal")
            self.status.configure(text="install the featured build for %s" % self.target)
            return
        self.version_box.configure(values=[], state="disabled")
        self.install_btn.configure(text="Loading builds...", state="disabled")
        threading.Thread(target=self._versions_worker, args=(p["slug"],), daemon=True).start()

    def _versions_worker(self, slug):
        try:
            versions = mods.project_versions(slug, self.target)
            self.events.put(("versions", versions))
        except Exception as exc:  # noqa: BLE001
            self.events.put(("error", "could not list builds: %s" % exc))

    def _show_versions(self, versions):
        self.versions = versions
        if not versions:
            self.version_box.configure(values=[], state="disabled")
            self.install_btn.configure(text="No build for %s" % self.target, state="disabled")
            self.status.configure(text="this mod has no Fabric build for %s" % self.target)
            return
        labels = ["%s  ·  %s" % (v["version_number"], v["version_type"]) for v in versions]
        self.version_box.configure(values=labels, state="readonly")
        pick = mods.pick_version(versions)
        idx = versions.index(pick) if pick in versions else 0
        self.version_box.current(idx)
        self.install_btn.configure(text="Install mod", state="normal")
        self.status.configure(text="%d build(s) for %s" % (len(versions), self.target))

    # --------------------------------------------------------------- install
    def _install(self):
        if self.busy or not self.project or not self.versions:
            return
        idx = self.version_box.current()
        if idx < 0 or idx >= len(self.versions):
            return
        self.busy = True
        self.install_btn.configure(text="Installing...", state="disabled")
        threading.Thread(target=self._install_worker,
                         args=(self.project, self.versions[idx]), daemon=True).start()

    def _install_worker(self, project, version):
        app = self.app
        game_dir = app.settings["game_dir"]
        try:
            self.events.put(("status", "finding a Fabric build for %s..." % self.target))
            loaders = install.fabric_loader_versions(self.target)
            stable = [x for x in loaders if (x.get("loader") or {}).get("stable")]
            if not (stable or loaders):
                raise RuntimeError("Fabric has no build for %s" % self.target)
            loader = (stable or loaders)[0]["loader"]["version"]

            self.events.put(("status", "setting up Fabric %s for %s..." %
                             (loader, self.target)))
            profile_id, _prep = install.ensure_loader(
                self.target, loader, game_dir,
                log=lambda s: app.q("log", s),
                progress=lambda d, t, l: app.q("progress", (d, t, l)),
                fast=True)

            self.events.put(("status", "downloading %s..." % project["title"]))
            entry = mods.install_mod(game_dir, version, project, install_deps=True, game_version=self.target)

            profiles = dict(app.settings.get("mod_profiles") or {})
            profiles[self.target] = {"loader": loader, "profile": profile_id}
            app.settings["mod_profiles"] = profiles
            save_settings(app.settings)
            app.q("mods-changed", None)

            self.events.put(("installed", mods.installed_mods(game_dir)))
            self.events.put(("status", "%s %s installed  ·  PLAY %s now uses Fabric %s" %
                             (project["title"], entry.get("version_number", ""),
                              self.target, loader)))
            app.log_line("mod installed: %s %s (Fabric %s for %s)" %
                         (project["title"], entry.get("version_number", ""), loader, self.target))
        except Exception as exc:  # noqa: BLE001
            self.events.put(("error", "install failed: %s" % exc))
        finally:
            self.events.put(("done-install", None))

    def _show_installed(self, items):
        self.installed_items = items
        self.installed.delete(*self.installed.get_children())
        for item in items:
            self.installed.insert("", "end", iid=item["filename"],
                                  values=(item.get("title") or item["filename"],
                                          item.get("version_number", ""),
                                          "enabled" if item["enabled"] else "disabled"))
        self.installed_lbl.configure(text="INSTALLED MODS  ·  %d" % len(items))
        self._sync_buttons()

    def _sync_buttons(self):
        sel = self.installed.selection()
        item = None
        if sel:
            filename = sel[0]
            item = next((m for m in self.installed_items
                         if m["filename"] == filename), None)
        state = "normal" if item else "disabled"
        self.toggle_btn.configure(state=state,
                                  text=("Disable" if (item and item["enabled"]) else "Enable"))
        self.remove_btn.configure(state=state)

    def _selected_installed(self):
        sel = self.installed.selection()
        if not sel:
            return None
        filename = sel[0]
        return next((m for m in self.installed_items if m["filename"] == filename), None)

    def _toggle(self):
        item = self._selected_installed()
        if not item:
            return
        enabled = mods.set_enabled(self.app.settings["game_dir"], item["filename"],
                                   not item["enabled"])
        self.app.log_line("mod %s: %s" % (item.get("title") or item["filename"],
                                          "enabled" if enabled else "disabled"))
        self._refresh_installed()
        self.app.q("mods-changed", None)

    def _remove(self):
        item = self._selected_installed()
        if not item:
            return
        if not messagebox.askyesno(APP_NAME, "Remove %s from the mods folder?" %
                                   (item.get("title") or item["filename"])):
            return
        mods.remove_mod(self.app.settings["game_dir"], item["filename"])
        self._after_change("%s removed" % (item.get("title") or item["filename"]))

    def _remove_all(self):
        if not self.installed_items:
            return
        if not messagebox.askyesno(APP_NAME, "Remove all %d mods?" % len(self.installed_items)):
            return
        n = mods.remove_all(self.app.settings["game_dir"])
        self._after_change("%d mod(s) removed - PLAY %s is vanilla again" % (n, self.target))

    def _after_change(self, message):
        app = self.app
        if mods.installed_count(app.settings["game_dir"]) == 0:
            profiles = dict(app.settings.get("mod_profiles") or {})
            profiles.pop(self.target, None)
            app.settings["mod_profiles"] = profiles
            save_settings(app.settings)
        app.q("mods-changed", None)
        app.log_line(message)
        self.events.put(("status", message))
        self._refresh_installed()


def _fmt_count(n) -> str:
    n = int(n or 0)
    if n >= 1_000_000_000:
        return "%.1fB" % (n / 1_000_000_000)
    if n >= 1_000_000:
        return "%.1fM" % (n / 1_000_000)
    if n >= 1_000:
        return "%.1fk" % (n / 1_000)
    return str(n)


class WorldsDialog:
    """Singleplayer worlds: play, back up, restore, open, delete."""

    def __init__(self, app: App):
        self.app = app
        self.win = None
        self.tree = None
        self.backup_tree = None
        self.count_lbl = None
        self.status = None
        self.worlds: dict = {}
        self.backups: dict = {}

    def show(self):
        app = self.app
        win = tk.Toplevel(app.root)
        self.win = win
        win.title("Worlds")
        win.configure(bg=BG)
        win.geometry("820x600")
        win.minsize(720, 500)
        win.transient(app.root)
        win.grab_set()
        win.focus_set()

        head = ttk.Frame(win, style="TFrame")
        head.pack(fill="x", padx=14, pady=(12, 2))
        ttk.Label(head, text="WORLDS", style="Head.TLabel").pack(side="left")
        self.count_lbl = ttk.Label(head, text="", style="Sub.TLabel")
        self.count_lbl.pack(side="left", padx=(10, 0), pady=(6, 0))
        ttk.Button(head, text="Game folder",
                   command=lambda: open_path(app.settings["game_dir"])).pack(side="right")
        ttk.Button(head, text="Refresh", command=self.refresh).pack(side="right", padx=(0, 8))

        wrap = ttk.Frame(win, style="Panel.TFrame", padding=1)
        wrap.pack(fill="both", expand=True, padx=14, pady=(8, 0))
        self.tree = ttk.Treeview(wrap, columns=("world", "size", "when"),
                                 show="headings", style="Mods.Treeview",
                                 selectmode="browse")
        for col, text, width, anchor in (("world", "WORLD", 430, "w"),
                                         ("size", "SIZE", 90, "e"),
                                         ("when", "LAST PLAYED", 150, "w")):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, anchor=anchor, stretch=(col == "world"))
        tsb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tsb.set)
        tsb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda _e: self._play())

        actions = ttk.Frame(win, style="TFrame")
        actions.pack(fill="x", padx=14, pady=(8, 0))
        ttk.Button(actions, text="Play world", command=self._play).pack(side="left")
        ttk.Button(actions, text="Back up now", command=self._backup).pack(
            side="left", padx=(8, 0))
        ttk.Button(actions, text="Open folder", command=self._open_world).pack(
            side="left", padx=(8, 0))
        ttk.Button(actions, text="Delete", command=self._delete).pack(
            side="left", padx=(8, 0))

        ttk.Label(win, text="BACKUPS", style="Dim.TLabel",
                  font=("Segoe UI Semibold", 8)).pack(anchor="w", padx=14, pady=(12, 2))
        bwrap = ttk.Frame(win, style="Panel.TFrame", padding=1)
        bwrap.pack(fill="x", padx=14)
        self.backup_tree = ttk.Treeview(bwrap, columns=("file", "size", "when"),
                                        show="headings", height=5,
                                        style="Mods.Treeview", selectmode="browse")
        for col, text, width, anchor in (("file", "BACKUP", 430, "w"),
                                         ("size", "SIZE", 90, "e"),
                                         ("when", "CREATED", 150, "w")):
            self.backup_tree.heading(col, text=text)
            self.backup_tree.column(col, width=width, anchor=anchor,
                                    stretch=(col == "file"))
        bsb = ttk.Scrollbar(bwrap, orient="vertical", command=self.backup_tree.yview)
        self.backup_tree.configure(yscrollcommand=bsb.set)
        bsb.pack(side="right", fill="y")
        self.backup_tree.pack(side="left", fill="x", expand=True)

        bacts = ttk.Frame(win, style="TFrame")
        bacts.pack(fill="x", padx=14, pady=(8, 0))
        ttk.Button(bacts, text="Restore selected",
                   command=self._restore_selected).pack(side="left")
        ttk.Button(bacts, text="Restore from file...",
                   command=self._restore_file).pack(side="left", padx=(8, 0))
        ttk.Button(bacts, text="Backups folder",
                   command=self._open_backups).pack(side="left", padx=(8, 0))

        self.status = ttk.Label(win, text="", style="Sub.TLabel")
        self.status.pack(anchor="w", padx=14, pady=(8, 10))
        self.refresh()

    # ------------------------------------------------------------- helpers
    def refresh(self):
        if not self.win or not self.win.winfo_exists():
            return
        game_dir = self.app.settings["game_dir"]
        self.worlds = {}
        self.tree.delete(*self.tree.get_children())
        found = scan_worlds(game_dir)
        for w in found:
            self.worlds[w["name"]] = w
            self.tree.insert("", "end", iid=w["name"],
                             values=(w["name"], human_size(w["size"]),
                                     relative_time(w["modified"])))
        self.count_lbl.configure(text="%d world%s  -  double-click to play" %
                                      (len(found), "" if len(found) == 1 else "s"))
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children[0])

        self.backups = {}
        self.backup_tree.delete(*self.backup_tree.get_children())
        blist = list_backups(game_dir)
        for b in blist:
            self.backups[b["name"]] = b
            self.backup_tree.insert("", "end", iid=b["name"],
                                    values=(b["name"], human_size(b["size"]),
                                            relative_time(b["modified"])))
        if blist:
            self.backup_tree.selection_set(blist[0]["name"])

    def _selected_world(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo(APP_NAME, "Pick a world first.", parent=self.win)
            return None
        return self.worlds.get(sel[0])

    def _run(self, label, func, done=None):
        """Run a slow world operation without freezing the dialog."""
        if not self.win or not self.win.winfo_exists():
            return
        self.status.configure(text=label + " ...")
        win = self.win
        results = queue.Queue()

        def work():
            try:
                results.put((func(), ""))
            except Exception as exc:  # noqa: BLE001
                results.put((None, str(exc)))

        def poll():
            try:
                result, error = results.get_nowait()
            except queue.Empty:
                if win.winfo_exists():
                    win.after(120, poll)
                return
            if not win.winfo_exists():
                return
            if error:
                self.status.configure(text="Failed: " + error)
                messagebox.showerror(APP_NAME, error, parent=win)
            else:
                self.status.configure(text=done(result) if done else "Done.")
                self.refresh()

        threading.Thread(target=work, name="saxic-world-op", daemon=True).start()
        win.after(120, poll)

    # ------------------------------------------------------------- actions
    def _play(self):
        w = self._selected_world()
        if not w:
            return
        if not self.app._selected_version():
            messagebox.showinfo(APP_NAME, "Pick a Minecraft version in the launcher first.",
                                parent=self.win)
            return
        name = w["name"]
        self.win.destroy()
        self.app.play_world(name)

    def _backup(self):
        w = self._selected_world()
        if not w:
            return
        game_dir = self.app.settings["game_dir"]
        name = w["name"]
        self._run("Backing up %s" % name,
                  lambda: backup_world(game_dir, name),
                  lambda path: "Saved " + os.path.basename(path))

    def _open_world(self):
        w = self._selected_world()
        if w:
            open_path(w["path"])

    def _delete(self):
        w = self._selected_world()
        if not w:
            return
        if self.app.game:
            messagebox.showinfo(APP_NAME, "Close Minecraft before deleting a world.",
                                parent=self.win)
            return
        if not messagebox.askyesno(
                APP_NAME,
                "Delete the world '%s'?\n\nThis deletes %s permanently. Back it up first "
                "if you are not sure." % (w["name"], w["path"]),
                parent=self.win):
            return
        self._run("Deleting %s" % w["name"],
                  lambda: shutil.rmtree(w["path"]),
                  lambda _res: "Deleted " + w["name"])

    def _restore_selected(self):
        sel = self.backup_tree.selection()
        if not sel:
            messagebox.showinfo(APP_NAME, "Pick a backup first.", parent=self.win)
            return
        self._restore_path(self.backups[sel[0]]["path"])

    def _restore_file(self):
        path = filedialog.askopenfilename(
            parent=self.win, title="Choose a world backup",
            initialdir=backups_dir(self.app.settings["game_dir"]),
            filetypes=[("World backups", "*.zip"), ("All files", "*.*")])
        if path:
            self._restore_path(path)

    def _restore_path(self, path):
        if not messagebox.askyesno(
                APP_NAME,
                "Restore '%s'?\n\nThe world is added as a new save; nothing is overwritten."
                % os.path.basename(path), parent=self.win):
            return
        game_dir = self.app.settings["game_dir"]
        self._run("Restoring %s" % os.path.basename(path),
                  lambda: restore_world(game_dir, path),
                  lambda name: "Restored as " + name)

    def _open_backups(self):
        folder = backups_dir(self.app.settings["game_dir"])
        os.makedirs(folder, exist_ok=True)
        open_path(folder)


class ScreenshotsDialog:
    """A small gallery of screenshots from the game folder."""

    LIMIT = 60

    def __init__(self, app: App):
        self.app = app
        self.win = None
        self.canvas = None
        self.gallery = None
        self.count_lbl = None
        self.images: list = []

    def show(self):
        app = self.app
        win = tk.Toplevel(app.root)
        self.win = win
        win.title("Screenshots")
        win.configure(bg=BG)
        win.geometry("900x640")
        win.minsize(780, 500)
        win.transient(app.root)
        win.grab_set()
        win.focus_set()

        head = ttk.Frame(win, style="TFrame")
        head.pack(fill="x", padx=14, pady=(12, 2))
        ttk.Label(head, text="SCREENSHOTS", style="Head.TLabel").pack(side="left")
        self.count_lbl = ttk.Label(head, text="", style="Sub.TLabel")
        self.count_lbl.pack(side="left", padx=(10, 0), pady=(6, 0))
        ttk.Button(head, text="Open folder", command=self._open_folder).pack(side="right")
        ttk.Button(head, text="Refresh", command=self.refresh).pack(side="right", padx=(0, 8))

        wrap = ttk.Frame(win, style="TFrame")
        wrap.pack(fill="both", expand=True, padx=14, pady=(8, 14))
        self.canvas = tk.Canvas(wrap, bg=BG, highlightthickness=0)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.gallery = ttk.Frame(self.canvas, style="TFrame")
        self.canvas.create_window((0, 0), window=self.gallery, anchor="nw")
        self.gallery.bind("<Configure>",
                          lambda _e: self.canvas.configure(
                              scrollregion=self.canvas.bbox("all")))
        win.bind("<MouseWheel>",
                 lambda e: self.canvas.yview_scroll(int(-e.delta / 120), "units"))
        self.refresh()

    def refresh(self):
        if not self.win or not self.win.winfo_exists():
            return
        for child in self.gallery.winfo_children():
            child.destroy()
        self.images = []
        self.canvas.yview_moveto(0)
        game_dir = self.app.settings["game_dir"]
        items = scan_screenshots(game_dir)
        self.count_lbl.configure(text="%d picture%s" %
                                      (len(items), "" if len(items) == 1 else "s"))
        if not items:
            ttk.Label(self.gallery,
                      text="No screenshots yet.\n\nPress F2 in Minecraft to take one - "
                           "it will show up here.",
                      style="Dim.TLabel", justify="left").pack(anchor="w", padx=4, pady=10)
            return
        shown = items[:self.LIMIT]
        for i, shot in enumerate(shown):
            cell = ttk.Frame(self.gallery, style="TFrame")
            cell.grid(row=i // 3, column=i % 3, padx=(0, 10), pady=(0, 14), sticky="nw")
            try:
                img = tk.PhotoImage(file=shot["path"])
                factor = max(1, img.width() // 240, img.height() // 150)
                if factor > 1:
                    img = img.subsample(factor)
                self.images.append(img)
                pic = tk.Label(cell, image=img, bg=PANEL2, bd=0, cursor="hand2")
                pic.pack()
                pic.bind("<Button-1>", lambda _e, p=shot["path"]: open_path(p))
            except tk.TclError:
                ttk.Label(cell, text="(cannot preview)", style="Dim.TLabel").pack()
            ttk.Label(cell, text=shot["name"], style="Dim.TLabel",
                      wraplength=250, justify="left").pack(anchor="w", pady=(4, 0))
            ttk.Label(cell, text="%s  -  %s" % (human_size(shot["size"]),
                                                relative_time(shot["modified"])),
                      style="Dim.TLabel").pack(anchor="w")
        if len(items) > len(shown):
            ttk.Label(self.gallery,
                      text="Showing the latest %d of %d screenshots." % (len(shown), len(items)),
                      style="Dim.TLabel").grid(row=(len(shown) + 2) // 3, column=0,
                                               columnspan=3, sticky="w", pady=(0, 10))

    def _open_folder(self):
        folder = screenshots_dir(self.app.settings["game_dir"])
        os.makedirs(folder, exist_ok=True)
        open_path(folder)


class NewsDialog:
    """Latest Minecraft news from Mojang's public launcher feed."""

    def __init__(self, app: App):
        self.app = app
        self.win = None
        self.list_frame = None
        self.canvas = None
        self.status = None
        self.only_java = None
        self.entries: list = []

    def show(self):
        app = self.app
        win = tk.Toplevel(app.root)
        self.win = win
        win.title("News")
        win.configure(bg=BG)
        win.geometry("820x640")
        win.minsize(720, 500)
        win.transient(app.root)
        win.grab_set()
        win.focus_set()

        head = ttk.Frame(win, style="TFrame")
        head.pack(fill="x", padx=14, pady=(12, 2))
        ttk.Label(head, text="NEWS", style="Head.TLabel").pack(side="left")
        ttk.Label(head, text="  from minecraft.net", style="Sub.TLabel").pack(
            side="left", pady=(6, 0))
        ttk.Button(head, text="Open minecraft.net",
                   command=lambda: webbrowser.open("https://www.minecraft.net/")).pack(side="right")
        ttk.Button(head, text="Refresh", command=self._load_async).pack(
            side="right", padx=(0, 8))

        filters = ttk.Frame(win, style="TFrame")
        filters.pack(fill="x", padx=14, pady=(4, 6))
        self.only_java = tk.BooleanVar(value=True)
        ttk.Checkbutton(filters, text="Java Edition only", variable=self.only_java).pack(side="left")
        self.only_java.trace_add("write", lambda *_a: self._render())

        wrap = ttk.Frame(win, style="TFrame")
        wrap.pack(fill="both", expand=True, padx=14)
        self.canvas = tk.Canvas(wrap, bg=BG, highlightthickness=0)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.list_frame = ttk.Frame(self.canvas, style="TFrame")
        self.canvas.create_window((0, 0), window=self.list_frame, anchor="nw")
        self.list_frame.bind("<Configure>",
                             lambda _e: self.canvas.configure(
                                 scrollregion=self.canvas.bbox("all")))
        win.bind("<MouseWheel>",
                 lambda e: self.canvas.yview_scroll(int(-e.delta / 120), "units"))

        self.status = ttk.Label(win, text="Loading news ...", style="Sub.TLabel")
        self.status.pack(anchor="w", padx=14, pady=(8, 10))

        cached = read_json_cache(NEWS_CACHE)
        if cached and isinstance(cached.get("data"), dict):
            self.entries = cached["data"].get("entries") or []
            self._render()
            self.status.configure(text="Showing the saved copy while refreshing ...")
        self._load_async()

    def _load_async(self):
        if not self.win or not self.win.winfo_exists():
            return
        self.status.configure(text="Loading news ...")
        win = self.win
        results = queue.Queue()

        def work():
            try:
                data, _state = http_json_cached(NEWS_URL, NEWS_CACHE, timeout=25)
                results.put((data, ""))
            except Exception as exc:  # noqa: BLE001
                results.put((None, str(exc)))

        def poll():
            try:
                data, error = results.get_nowait()
            except queue.Empty:
                if win.winfo_exists():
                    win.after(150, poll)
                return
            if not win.winfo_exists():
                return
            if isinstance(data, dict):
                self.entries = data.get("entries") or []
                self._render()
                self.status.configure(
                    text="Updated at %s  -  %d stories" %
                         (datetime.now().strftime("%H:%M"), len(self.entries)))
            elif error:
                self.status.configure(text="Could not load news: " + error)

        threading.Thread(target=work, name="saxic-news", daemon=True).start()
        win.after(150, poll)

    def _render(self):
        if not self.win or not self.win.winfo_exists():
            return
        for child in self.list_frame.winfo_children():
            child.destroy()
        items = [e for e in self.entries if isinstance(e, dict)]
        if self.only_java.get():
            items = [e for e in items if "Java" in (e.get("newsType") or [])]
        items = items[:40]
        if not items:
            ttk.Label(self.list_frame, text="No news to show yet.",
                      style="Dim.TLabel").pack(anchor="w", pady=10)
            return
        for entry in items:
            card = ttk.Frame(self.list_frame, style="Panel.TFrame", padding=12)
            card.pack(fill="x", pady=(0, 8))
            ttk.Label(card, text=entry.get("title") or "Untitled",
                      style="Panel.TLabel", font=("Segoe UI Semibold", 11),
                      wraplength=650, justify="left").pack(anchor="w")
            meta = "  -  ".join(x for x in (entry.get("date", ""), entry.get("category", ""))
                                if x)
            ttk.Label(card, text=meta, style="PanelDim.TLabel").pack(
                anchor="w", pady=(2, 4))
            text = (entry.get("text") or "").strip()
            if text:
                ttk.Label(card, text=text, style="PanelDim.TLabel",
                          wraplength=650, justify="left").pack(anchor="w")
            link = entry.get("readMoreLink") or ""
            if link:
                ttk.Button(card, text="Read more",
                           command=lambda u=link: webbrowser.open(u)).pack(
                    anchor="w", pady=(6, 0))


class RealmsDialog:
    """Minecraft Realms list for Microsoft-signed-in accounts.

    Shows realms the account owns or has been invited to, with their state
    and a way to join one from inside Minecraft.
    """

    def __init__(self, app: App):
        self.app = app
        self.win = None
        self.tree = None
        self.status = None

    def show(self):
        app = self.app
        win = tk.Toplevel(app.root)
        self.win = win
        win.title("Minecraft Realms")
        win.configure(bg=BG)
        win.geometry("760x520")
        win.minsize(700, 420)
        win.transient(app.root)
        win.grab_set()
        win.focus_set()

        head = ttk.Frame(win, style="TFrame")
        head.pack(fill="x", padx=14, pady=(12, 2))
        ttk.Label(head, text="REALMS", style="Head.TLabel").pack(side="left")
        ttk.Label(head, text="  requires Microsoft sign-in", style="Sub.TLabel").pack(
            side="left", pady=(6, 0))
        ttk.Button(head, text="Refresh", command=self._load_async).pack(
            side="right", padx=(0, 8))

        wrap = ttk.Frame(win, style="Panel.TFrame", padding=1)
        wrap.pack(fill="both", expand=True, padx=14, pady=(8, 0))
        self.tree = ttk.Treeview(wrap, columns=("name", "state", "owner", "days"),
                                 show="headings", style="Mods.Treeview",
                                 selectmode="browse")
        for col, text, width, anchor in (("name", "REALM", 260, "w"),
                                          ("state", "STATE", 110, "w"),
                                          ("owner", "OWNER", 160, "w"),
                                          ("days", "DAYS LEFT", 90, "e")):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, anchor=anchor,
                             stretch=(col == "name"))
        tsb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tsb.set)
        tsb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda _e: self._join())

        acts = ttk.Frame(win, style="TFrame")
        acts.pack(fill="x", padx=14, pady=(8, 0))
        ttk.Button(acts, text="Join selected", command=self._join).pack(side="left")
        ttk.Button(acts, text="Copy address",
                   command=self._copy_address).pack(side="left", padx=(8, 0))
        ttk.Label(acts, text="Join the realm from Minecraft's Multiplayer → Realms screen.",
                  style="Dim.TLabel").pack(side="right")

        self.status = ttk.Label(win, text="Loading realms ...", style="Sub.TLabel")
        self.status.pack(anchor="w", padx=14, pady=(8, 10))

        self._load_async()

    def _load_async(self):
        if not self.win or not self.win.winfo_exists():
            return
        self.status.configure(text="Loading realms ...")
        win = self.win
        results = queue.Queue()

        def work():
            try:
                realms = auth.realms_list(self.app.account)
                results.put((realms, ""))
            except Exception as exc:  # noqa: BLE001
                results.put((None, str(exc)))

        def poll():
            try:
                realms, error = results.get_nowait()
            except queue.Empty:
                if win.winfo_exists():
                    win.after(150, poll)
                return
            if not win.winfo_exists():
                return
            if error:
                self.status.configure(text="Could not load realms: " + error)
                return
            self._render(realms or [])
            self.status.configure(text="%d realm%s" %
                                  (len(realms), "" if len(realms) == 1 else "s"))

        threading.Thread(target=work, name="saxic-realms", daemon=True).start()
        win.after(150, poll)

    def _render(self, realms):
        if not self.win or not self.win.winfo_exists():
            return
        self._realms = realms or []
        self.tree.delete(*self.tree.get_children())
        for r in self._realms:
            state = r.get("state", "closed")
            state_label = {"open": "Open", "closed": "Closed",
                           "expired": "Expired"}.get(state, state.title())
            days = r.get("daysRemaining", 0)
            days_label = str(days) if days > 0 else "—"
            self.tree.insert("", "end", iid=r.get("id", ""),
                             values=(r.get("name", "Untitled"), state_label,
                                     r.get("owner", ""), days_label))

    def _selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo(APP_NAME, "Pick a realm first.", parent=self.win)
            return None
        for r in self._realms:
            if r.get("id") == sel[0]:
                return r
        return None

    def _join(self):
        r = self._selected()
        if not r:
            return
        if r.get("expired") or r.get("state") != "open":
            messagebox.showinfo(APP_NAME,
                                "This realm is not open for joining right now.",
                                parent=self.win)
            return
        app = self.app
        acct = app.account
        msg = ("To join '%s':\n\n"
               "1. Open Minecraft from this launcher.\n"
               "2. Go to Multiplayer → Realms.\n"
               "3. Select '%s' and click Join.\n\n"
               "Make sure you are signed in with the same Microsoft account "
               "(%s)." % (r.get("name", "this realm"), r.get("name", "this realm"),
                          acct.get("name", "your account")))
        messagebox.showinfo(APP_NAME, msg, parent=self.win)

    def _copy_address(self):
        r = self._selected()
        if not r:
            return
        try:
            self.win.clipboard_clear()
            self.win.clipboard_append(r.get("name", ""))
            self.status.configure(text="Realm name copied to clipboard.")
        except tk.TclError:
            pass


class SettingsDialog:
    def __init__(self, app: App):
        self.app = app
        self._java_choices = {"Auto-detect (recommended)": ""}

    def show(self):
        app = self.app
        s = app.settings
        win = tk.Toplevel(app.root)
        win.title("Settings")
        win.configure(bg=BG)
        win.geometry("740x760")
        win.minsize(680, 520)
        win.transient(app.root)
        win.grab_set()
        win.focus_set()

        vals = {
            "game_dir": tk.StringVar(value=s["game_dir"]),
            "java_path": tk.StringVar(value=s.get("java_path", "")),
            "java_pick": tk.StringVar(value="Auto-detect (recommended)"),
            "ram_mb": tk.StringVar(value=str(s.get("ram_mb", recommended_ram_mb()))),
            "workers": tk.StringVar(value=str(s.get("workers", 16))),
            "client_id": tk.StringVar(value=s.get("client_id", "")),
            "keep_open": tk.BooleanVar(value=bool(s.get("keep_open", True))),
            "show_snapshots": tk.BooleanVar(value=bool(s.get("show_snapshots", False))),
            "jvm_args": tk.StringVar(value=s.get("jvm_args", "")),
            "game_args": tk.StringVar(value=s.get("game_args", "")),
            "window_width": tk.StringVar(value=str(s.get("window_width", 0) or 0)),
            "window_height": tk.StringVar(value=str(s.get("window_height", 0) or 0)),
        }

        # Scrollable body so every option stays reachable on small screens.
        outer = ttk.Frame(win, style="TFrame")
        outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer, bg=BG, highlightthickness=0)
        sb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        body = ttk.Frame(canvas, style="TFrame")
        body_id = canvas.create_window((0, 0), window=body, anchor="nw")
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfigure(body_id, width=e.width))
        body.bind("<Configure>",
                  lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        win.bind("<MouseWheel>",
                 lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))

        pad = {"padx": 14, "pady": 5, "sticky": "w"}
        row = [0]

        def next_row():
            row[0] += 1

        def add_label(text):
            ttk.Label(body, text=text, style="TLabel").grid(row=row[0], column=0, **pad)

        def add_hint(text):
            ttk.Label(body, text=text, style="Dim.TLabel", justify="left",
                      wraplength=470).grid(
                row=row[0], column=1, sticky="w", padx=(0, 14), pady=(0, 4))
            next_row()

        def add_section(text):
            next_row()
            ttk.Label(body, text=text, style="Dim.TLabel",
                      font=("Segoe UI Semibold", 8)).grid(
                row=row[0], column=0, columnspan=2, sticky="w", padx=14, pady=(10, 0))
            next_row()

        add_label("Game folder")
        row0 = ttk.Frame(body, style="TFrame")
        row0.grid(row=row[0], column=1, sticky="ew", padx=(0, 14), pady=6)
        ttk.Entry(row0, textvariable=vals["game_dir"], width=46).pack(
            side="left", fill="x", expand=True)
        ttk.Button(row0, text="Browse", command=lambda: self._pick_dir(vals)).pack(
            side="left", padx=(6, 0))
        next_row()

        add_label("Java path (blank = auto)")
        row1 = ttk.Frame(body, style="TFrame")
        row1.grid(row=row[0], column=1, sticky="ew", padx=(0, 14), pady=6)
        ttk.Entry(row1, textvariable=vals["java_path"], width=46).pack(
            side="left", fill="x", expand=True)
        ttk.Button(row1, text="Browse", command=lambda: self._pick_java(vals)).pack(
            side="left", padx=(6, 0))
        next_row()

        add_label("Detected Java")
        java_box = ttk.Combobox(body, textvariable=vals["java_pick"], state="readonly",
                                width=58, values=["Auto-detect (recommended)"])
        java_box.grid(row=row[0], column=1, sticky="w", padx=(0, 14), pady=6)
        next_row()
        add_hint("Pick an installed runtime, or leave it on Auto-detect.")

        def choose_java(_event=None):
            vals["java_path"].set(self._java_choices.get(vals["java_pick"].get(), ""))

        java_box.bind("<<ComboboxSelected>>", choose_java)

        def load_java():
            try:
                found = list_java_runtimes(app.settings["game_dir"])
            except Exception:  # noqa: BLE001 - a broken runtime must not block Settings
                found = []
            java_results.put(found)

        def apply_java():
            try:
                found = java_results.get_nowait()
            except queue.Empty:
                if win.winfo_exists():
                    win.after(150, apply_java)
                return
            if not win.winfo_exists():
                return
            for item in found:
                self._java_choices.setdefault(item["label"], item["path"])
            java_box.configure(values=list(self._java_choices))
            current = vals["java_path"].get().strip()
            if current:
                for label, path in self._java_choices.items():
                    if path and os.path.normcase(path) == os.path.normcase(current):
                        vals["java_pick"].set(label)
                        break

        java_results = queue.Queue()
        threading.Thread(target=load_java, name="saxic-java-scan", daemon=True).start()
        win.after(150, apply_java)

        add_label("Memory (MB)")
        ttk.Spinbox(body, from_=1024, to=32768, increment=512,
                    textvariable=vals["ram_mb"]).grid(
            row=row[0], column=1, sticky="w", padx=(0, 14), pady=6)
        next_row()
        mem = memory_status()
        mem_hint = ("Recommended for this PC: %d MB. Too little memory can crash the game "
                    "while a world loads." % recommended_ram_mb())
        if mem.get("pagefile_mb", 0) < 1024:
            mem_hint += (" This PC has virtual memory (a pagefile) turned off, so very large "
                         "modpacks may still run out of memory. Enable System managed virtual "
                         "memory in System Properties > Advanced > Performance > Advanced.")
        add_hint(mem_hint)

        add_label("Download threads")
        ttk.Spinbox(body, from_=1, to=64, increment=4,
                    textvariable=vals["workers"]).grid(
            row=row[0], column=1, sticky="w", padx=(0, 14), pady=6)
        next_row()
        add_hint("More threads = faster asset download (16 is a good default).")

        add_label("Microsoft client ID")
        ttk.Entry(body, textvariable=vals["client_id"], width=46).grid(
            row=row[0], column=1, sticky="ew", padx=(0, 14), pady=6)
        next_row()
        add_hint("Azure app (Mobile & desktop, public client flows = Yes). Needed only for "
                 "Microsoft sign-in; a Google login cannot sign into Minecraft.")

        add_section("LAUNCH OPTIONS")

        add_label("JVM arguments")
        ttk.Entry(body, textvariable=vals["jvm_args"], width=46).grid(
            row=row[0], column=1, sticky="ew", padx=(0, 14), pady=6)
        next_row()
        add_hint("Extra flags for Java (examples: -XX:+UseG1GC -Dfile.encoding=UTF-8). "
                 "Leave blank unless you know what you need.")

        add_label("Game arguments")
        ttk.Entry(body, textvariable=vals["game_args"], width=46).grid(
            row=row[0], column=1, sticky="ew", padx=(0, 14), pady=6)
        next_row()
        add_hint("Extra flags for Minecraft, for example --fullscreen.")

        add_label("Window size")
        wrow = ttk.Frame(body, style="TFrame")
        wrow.grid(row=row[0], column=1, sticky="w", padx=(0, 14), pady=6)
        ttk.Entry(wrow, textvariable=vals["window_width"], width=7).pack(side="left")
        ttk.Label(wrow, text=" x ", style="TLabel").pack(side="left")
        ttk.Entry(wrow, textvariable=vals["window_height"], width=7).pack(side="left")
        ttk.Label(wrow, text="  px", style="Dim.TLabel").pack(side="left")
        next_row()
        add_hint("Width x height in pixels. 0 x 0 lets Minecraft choose the window size.")

        add_section("BEHAVIOUR")
        ttk.Checkbutton(body, text="Keep launcher visible while playing",
                        variable=vals["keep_open"]).grid(
            row=row[0], column=1, sticky="w", padx=(0, 14), pady=(4, 0))
        next_row()
        ttk.Checkbutton(body, text="Show snapshots in the version list",
                        variable=vals["show_snapshots"]).grid(
            row=row[0], column=1, sticky="w", padx=(0, 14))
        next_row()

        body.columnconfigure(1, weight=1)

        btns = ttk.Frame(win, style="TFrame")
        btns.pack(fill="x", padx=14, pady=12)
        ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="right")
        ttk.Button(btns, text="Save", command=lambda: self._save(win, vals)).pack(
            side="right", padx=(0, 8))

        win.wait_window()

    def _pick_dir(self, vals):
        d = filedialog.askdirectory(initialdir=vals["game_dir"].get() or os.path.expanduser("~"))
        if d:
            vals["game_dir"].set(d)

    def _pick_java(self, vals):
        f = filedialog.askopenfilename(title="java or javaw executable",
                                       filetypes=[("Java executable", "*.exe"), ("All files", "*.*")])
        if f:
            vals["java_path"].set(f)

    def _save(self, win, vals):
        app = self.app
        try:
            ram = max(512, min(32768, int(float(vals["ram_mb"].get())))) if vals["ram_mb"].get() else recommended_ram_mb()
        except ValueError:
            ram = recommended_ram_mb()
        try:
            workers = max(1, min(64, int(float(vals["workers"].get())))) if vals["workers"].get() else 16
        except ValueError:
            workers = 16

        def pixels(var, default=0):
            try:
                return max(0, min(7680, int(float(var.get() or default))))
            except ValueError:
                return default

        app.settings.update({
            "game_dir": os.path.expandvars(os.path.expanduser(vals["game_dir"].get().strip())),
            "java_path": vals["java_path"].get().strip(),
            "ram_mb": ram,
            "workers": workers,
            "client_id": vals["client_id"].get().strip(),
            "keep_open": bool(vals["keep_open"].get()),
            "show_snapshots": bool(vals["show_snapshots"].get()),
            "jvm_args": vals["jvm_args"].get().strip(),
            "game_args": vals["game_args"].get().strip(),
            "window_width": pixels(vals["window_width"]),
            "window_height": pixels(vals["window_height"]),
        })
        save_settings(app.settings)
        app.snap_var.set(app.settings["show_snapshots"])
        app.log_line("settings saved (folder %s, %d MB ram)" % (app.settings["game_dir"], ram))
        win.destroy()
        app.refresh_versions()


def main():
    root = tk.Tk()
    icon = resource_path("assets", "saxic.ico")
    try:
        if os.path.isfile(icon):
            root.iconbitmap(default=icon)
    except Exception:  # noqa: BLE001
        pass
    app = App(root)
    root.protocol("WM_DELETE_WINDOW", lambda: (app.game.stop() if app.game else None, root.destroy()))
    root.mainloop()


if __name__ == "__main__":
    main()
