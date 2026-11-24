"""Headless smoke test: build the real launcher UI, pump events, then close.

Runs in this process only, so it never touches the background install.
"""
import os
import sys
import time
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import saxic_install as install_mod  # noqa: E402
import saxic_launcher as sl  # noqa: E402


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    do_install = "--install" in argv
    root = tk.Tk()
    root.withdraw()  # keep the window off-screen for the test
    started_at = time.time()
    app = sl.App(root)
    deadline = time.time() + 12
    while time.time() < deadline:
        root.update()
        if app.entries:
            break
        time.sleep(0.02)
    list_time = time.time() - started_at

    print("title      :", root.title())
    print("list time  : %.2fs" % list_time)
    print("cached list:", bool(install_mod.cached_manifest()))
    print("versions   :", len(app.entries))
    if app.entries:
        print("first      :", app.entries[0]["id"], app.entries[0]["type"])
    print("selected   :", (app._selected_version() or {}).get("id"))
    print("play label :", app.play_btn.cget("text"))
    print("account    :", app.account["name"], "|", app.account["kind"])
    print("game dir   :", app.settings["game_dir"])

    # exercise the settings dialog construction/save path without showing it
    sl.SettingsDialog(app)
    print("settings dlg: constructed")

    if "--dialogs" in argv:
        print("--- extra dialogs ---")
        for cls in (sl.WorldsDialog, sl.ScreenshotsDialog, sl.NewsDialog,
                    sl.ServerHosterDialog, sl.BedrockDialog):
            dlg = cls(app)
            dlg.show()
            for _ in range(15):
                root.update()
                time.sleep(0.02)
            wins = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]
            print("%-18s: %d window(s)" % (cls.__name__, len(wins)))
            for w in wins:
                w.destroy()
            root.update()
        root.after(1200, lambda: [w.destroy() for w in root.winfo_children()
                                  if isinstance(w, tk.Toplevel)])
        sl.SettingsDialog(app).show()
        root.update()
        print("SettingsDialog    : opened and closed")

        print("--- server hoster forms ---")
        hoster = sl.ServerHosterDialog(app)
        hoster.show()
        root.update()
        print("ServerHosterDialog: %d server row(s), %s" % (
            len(hoster.tree.get_children()), hoster.status.cget("text")[:70]))
        create = sl.ServerCreateDialog(hoster)
        create.show()
        root.update()
        print("ServerCreateDialog: version %s, port %s, software %s" % (
            create.version_var.get(), create.port_var.get(),
            create.software_var.get()))
        if not (create.version_var.get() and create.port_var.get().isdigit()):
            print("RESULT: GUI SMOKE FAILED (server form)")
            return 1
        create._close()
        bedrock = sl.ServerCreateDialog(hoster)
        bedrock.show()
        bedrock.software_var.set("Bedrock")
        root.update()
        bport = bedrock.port_var.get()
        print("ServerCreateDialog (Bedrock): port %s, version box %s" % (
            bport, bedrock.version_box.cget("state")))
        if not (bport.isdigit() and 19000 < int(bport) < 19300
                and str(bedrock.version_box.cget("state")) == "disabled"):
            print("RESULT: GUI SMOKE FAILED (bedrock form)")
            return 1
        bedrock._close()
        hoster._close()
        root.update()

    credits = sl.credits_info()
    print("credits    :", credits["created"], "|", credits["updated"], "|",
          ", ".join(credits["publishers"]), "|", credits["inspiration"])
    if not (credits["publishers"] == ["SARVESH.S", "srxvesh._.11"]
            and credits["inspiration"] == "clumsydev"
            and credits["created"] and credits["updated"]):
        print("RESULT: GUI SMOKE FAILED (credits)")
        return 1

    if "--mods" in argv:
        print("--- mod library dialog ---")
        import saxic_mods as mods_mod
        dlg = sl.ModLibraryDialog(app)
        dlg.show()
        deadline = time.time() + 25
        while time.time() < deadline and not dlg.hits:
            root.update()
            time.sleep(0.05)
        print("target     :", dlg.target)
        print("mod hits   :", len(dlg.hits))
        if dlg.hits:
            root.update()
            deadline = time.time() + 25
            while time.time() < deadline and not dlg.versions:
                root.update()
                time.sleep(0.05)
            print("mod builds :", len(dlg.versions))
            print("installable:", dlg.install_btn.cget("state"))
        print("installed  :", mods_mod.installed_count(app.settings["game_dir"]))
        dlg._close()
        root.update()

    # simulate the pump handling every message kind we emit
    for kind, payload in [("log", "hello"), ("status", "s"), ("progress", (1, 2, "x")),
                          ("account", app.account), ("busy", True), ("busy", False),
                          ("playing", True), ("playing", False), ("iconify", True),
                          ("exit", 0), ("error", "boom")]:
        app.q(kind, payload)
    root.update()
    print("pump       : handled all kinds")

    if "--quickplay" in argv:
        print("--- quick play path ---")
        import saxic_launch as launch_mod
        captured = {}

        class FakeGame:
            def stop(self):
                pass

            def alive(self):
                return False

        real_launch = launch_mod.launch_game
        launch_mod.launch_game = lambda cmd, cwd, on_line=None, on_exit=None: (
            captured.setdefault("cmd", cmd), FakeGame())[1]
        app.play_world("New World")
        deadline = time.time() + 180
        while time.time() < deadline and (app.busy or not captured.get("cmd")):
            root.update()
            time.sleep(0.05)
        deadline = time.time() + 10
        while time.time() < deadline and app.busy:
            root.update()
            time.sleep(0.02)
        root.update()
        launch_mod.launch_game = real_launch
        cmd = captured.get("cmd") or []
        try:
            at = cmd.index("--quickPlaySingleplayer")
            world = cmd[at + 1]
        except (ValueError, IndexError):
            world = ""
        print("command parts:", len(cmd))
        print("quick play   :", world or "(missing)")
        if not world:
            print("status       :", app.status.cget("text"))
            print("log tail     :")
            print(app.log.get("1.0", "end").strip()[-3000:])
            print("RESULT: QUICK PLAY FAILED")
            return 1
        app.q("exit", 0)
        root.update()
        print("RESULT: QUICK PLAY PASSED")

    if do_install:
        # this is exactly what the PLAY button does, minus starting the game
        print("--- running the GUI install path (install_only) ---")
        app.install_only()
        started = False
        deadline = time.time() + 30
        while time.time() < deadline and not app.busy:
            root.update()
            started = started or app.busy
            time.sleep(0.05)
        print("worker started:", app.busy)
        peak = 0.0
        deadline = time.time() + 900
        while time.time() < deadline and app.busy:
            root.update()
            peak = max(peak, float(app.progress.cget("value")))
            time.sleep(0.1)
        root.update()
        status = app.status.cget("text")
        print("status     :", status)
        print("peak pct   :", peak)
        if not started or "installed" not in status.lower():
            print("RESULT: GUI INSTALL FAILED")
            return 1
        print("RESULT: GUI INSTALL PASSED")

    if "--play" in argv:
        # full PLAY path: install, fetch java, build the command, start the game
        print("--- running the GUI PLAY path ---")
        app.play()
        deadline = time.time() + 60
        while time.time() < deadline and not app.busy:
            root.update()
            time.sleep(0.05)
        deadline = time.time() + 900
        while time.time() < deadline and app.busy:
            root.update()
            time.sleep(0.1)
        print("game started:", app.game is not None)
        markers = {"Setting user": False, "Backend library": False, "Sound engine": False}
        try:
            import saxic_mods as mods_mod
            if mods_mod.installed_count(app.settings["game_dir"]):
                markers["Fabric"] = False
                markers["mods"] = False
        except Exception:  # noqa: BLE001
            pass
        deadline = time.time() + 300
        while time.time() < deadline:
            root.update()
            text = app.log.get("1.0", "end")
            for key in markers:
                if key in text:
                    markers[key] = True
            if markers["Sound engine"]:
                break
            time.sleep(0.2)
        for key, hit in markers.items():
            print("  %-16s %s" % (key, "seen" if hit else "MISSING"))
        if "Fabric" in markers:
            for line in app.log.get("1.0", "end").splitlines():
                low = line.lower()
                if "fabric" in low or ("mod" in low and ("loading" in low or "sodium" in low)):
                    print("  |", line[:150])
        crashed = "Game crashed" in app.log.get("1.0", "end")
        app.stop_game()
        time.sleep(5)
        root.update()
        if all(markers.values()) and not crashed:
            print("RESULT: GUI PLAY PASSED")
        else:
            print("RESULT: GUI PLAY FAILED (log tail below)")
            print("\n".join(app.log.get("1.0", "end").splitlines()[-15:]))
            return 1

    root.deiconify()
    root.update()
    root.destroy()
    print("RESULT: GUI SMOKE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
