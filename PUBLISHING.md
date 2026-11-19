# Publishing SAXIC Launcher

The download is built automatically by GitHub Actions, so the public link
always serves the newest version.

## One-time setup

1. Create a public repository named `saxic-launcher`.
2. Push this folder to it (see below).
3. The **Build and publish** workflow runs and creates a release called
   `latest` with `SAXIC-Launcher.exe` attached.

## Everyday workflow

1. Change the code locally (saxic_launcher.py, saxic_mods.py, ...).
2. Double-click **`publish.cmd`**.
3. Wait 1-2 minutes for GitHub to rebuild.
4. The same download link serves the updated `.exe`.

Stable download link:

```
https://github.com/<OWNER>/saxic-launcher/releases/latest/download/SAXIC-Launcher.exe
```

## Manual build (optional)

```
python build_exe.py
```

The result is `dist/SAXIC-Launcher.exe` (one file, no console window, app
icon, includes the Credits build date). No Python or Java is required on the
PC that runs the `.exe`; Java for Minecraft is still downloaded by the
launcher itself.

## Notes

- `settings.json`, `cache/`, `build/`, `dist/` and `saxic_build.py` are
  ignored by git; only source, assets and docs are published.
- The workflow needs no secrets: it uses the built-in `GITHUB_TOKEN` and the
  repository's default `contents: write` permission.
- If the workflow ever fails, open the **Actions** tab on GitHub and read the
  failed step; `python build_exe.py` runs the same build locally.
