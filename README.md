# SAXIC Launcher

An original Minecraft launcher for Windows, written from scratch for this
project. It talks only to Microsoft's sign-in endpoints and Mojang's public
metadata/file servers, and it ships **no** game files, no third-party launcher
code and no borrowed assets.

> Not affiliated with Mojang, Microsoft or any other launcher project.
> "anyother Launcher" is not used anywhere in this codebase.

## Download

**[Download SAXIC-Launcher.exe](../../releases/latest/download/SAXIC-Launcher.exe)**

**[Download the share-ready zip](../../releases/latest/download/SAXIC-Launcher-win64.zip)** —
the same `.exe` plus a one-page quick start, README and licence. This is the
easiest file to send to a friend.

One file, no installer, no Python needed. The links always serve the newest
build, because every change pushed to this repository is rebuilt automatically
by GitHub Actions. SHA-256 checksums (`*.sha256`) are on the same release.

Windows SmartScreen may show "unrecognised app" because the `.exe` is not
code-signed. Choose **More info → Run anyway**.

## Run it

Double-click **`SAXIC Launcher.bat`** (or run `python saxic_launcher.py`).

Requirements: Windows + **Python 3.10 or newer**. Java is *not* required —
the launcher downloads Mojang's own Java runtime for the version you pick.

Building the standalone `.exe` yourself: `python build_exe.py` (needs
`pip install pyinstaller`). Result: `dist/SAXIC-Launcher.exe`.

## What it does

| Area | Behaviour |
| --- | --- |
| Versions | Reads Mojang's official version manifest; choose any release (plus snapshots with the checkbox). Installed versions are marked `*`. |
| Install | Downloads the client jar, libraries (honouring OS rules), native jars (extracted), the asset index and its ~490 MB of objects, and the log4j config. Verifies SHA-1 and resumes from a partial file each run. |
| Java | Detects a system JVM, otherwise installs the matching Mojang runtime (`java-runtime-epsilon`, `java-runtime-gamma`, …) into the game folder. |
| Launch | Builds the real Java command line from the version JSON (modern `arguments` and legacy `minecraftArguments` both supported), streams the game log into the window, and stops the game on request. |
| Mods | A **Mod library** window searches [Modrinth](https://modrinth.com) for Fabric mods that match the selected Minecraft version, installs them into `mods/`, and can disable/remove them. Installing a mod automatically sets up Fabric and switches **PLAY** to it. |
| Worlds | The **Worlds** window lists every singleplayer save with its size and last-played time. Double-click a world (or use *Play world*) to launch straight into it, make a `.zip` backup, restore a backup as a new save, open the folder or delete a world. |
| Screenshots | The **Screenshots** window shows a thumbnail gallery of everything in `screenshots/`. Click a picture to open it full-size. |
| News | The **News** window shows the latest stories from Mojang's public launcher feed, with a *Java Edition only* filter and *Read more* links. |
| Play time | The launcher remembers when you last played each version and how long you have played it in total. |
| Launch options | Extra **JVM arguments**, extra **game arguments**, a custom **window size**, and a picker for any installed **Java runtime** (or leave it on Auto-detect). |
| Accounts | **Offline profile** (username only) and **Microsoft sign-in** (device-code flow). |

## Credits

| | |
| --- | --- |
| Created | 18 November 2026 |
| Publisher | **SARVESH.S** · srxvesh._.11 |
| Inspiration | clumsydev |

The same details are in the launcher: click **Credits** in the top-right corner.
The **Updated** date shown there is read from the newest source file, so it
always matches the build you are running.

## Accounts, friends and the Google question

- **Offline profile** — works immediately. Great for singleplayer, and for
  playing with friends on the same network using **Open to LAN** in the pause
  menu. Offline accounts cannot join normal online servers; servers reject them.
- **Microsoft sign-in** — required for online servers and Realms. Minecraft is
  authenticated by Microsoft/Xbox, so a **Google sign-in cannot log into
  Minecraft**. A Gmail address can be the email *on* a Microsoft account, and
  that works fine (it is still Microsoft sign-in underneath).
- Microsoft sign-in also needs a client ID that belongs to *you*: create a free
  app registration in Azure ("Mobile and desktop applications", enable
  **Allow public client flows**), then paste its Application (client) ID into
  **Settings → Microsoft client ID**. The launcher then shows a code to enter at
  `microsoft.com/link`.
- Note: only accounts that actually own Minecraft can play online. The launcher
  can tell you whether the signed-in account has a Minecraft profile.

## Where things go

| What | Where |
| --- | --- |
| Launcher settings | `settings.json` next to `saxic_launcher.py`; the `.exe` build keeps it in `%APPDATA%\SAXIC Launcher` |
| Version list cache | `cache/version_manifest_v2.json` — lets the window show the list instantly and only re-download it when Mojang changes it |
| Game files | `%APPDATA%\.saxic` (change it in Settings) — `versions/`, `libraries/`, `assets/`, `runtime/` |
| Mods | `%APPDATA%\.saxic\mods` — jars downloaded by the Mod library (jars you drop in yourself are listed too). Disabled mods become `*.jar.disabled`; `.saxic-mods.json` records what installed them |
| World backups | `%APPDATA%\.saxic\backups` — one `.zip` per backup, made by the Worlds window |
| Screenshots | `%APPDATA%\.saxic\screenshots` — shown in the Screenshots window |
| News cache | `%APPDATA%\SAXIC Launcher\news-cache.json` for the `.exe`, or `news-cache.json` next to the source, so the News window opens instantly and still works offline |
| Fabric profile | `versions/fabric-loader-<loader>-<game>/` — generated from Fabric's official profile JSON, merged with the vanilla version it inherits from |
| Rough size | ~0.8 GB measured for a modern version: ~474 MB assets, ~121 MB libraries, ~117 MB version files, ~100 MB Java runtime |

### Starting it

Use the **Desktop shortcut** (`SAXIC Launcher.lnk`) or the one in this folder.
It runs `pythonw.exe` directly, so no console window appears. If you
double-click `saxic_launcher.py` or `SAXIC Launcher.bat` instead, Windows
opens a terminal window first — that is normal, not an error.

### Download speed

Assets are ~5000 small files. They are fetched with a thread pool
(`Download threads` in Settings, default 16). Lower it on a flaky
connection; raise it on a fast one.

### Startup speed

The version list is cached on disk. On launch the window fills the list from
that cache immediately and then revalidates it in the background with a
conditional request (`If-None-Match` / `If-Modified-Since`), so most launches
download nothing at all and show the list at once. The first ever launch, or a
launch with the cache deleted, still has to fetch it.

### Launch speed

After a version has been installed once, **PLAY** trusts the file names and
sizes instead of re-reading every file to check its SHA-1. That removes roughly
600 MB of hashing per launch (measured: ~7.8 s down to ~1.6 s). The
**Install / verify** button always does the full hash check, so run it if you
suspect a damaged file.

## Command line

Handy for scripting or if the GUI gives you trouble:

```
python saxic_cli.py status                 # what is installed, where
python saxic_cli.py install 26.3           # download + verify a version
python saxic_cli.py command 26.3           # print the java command, run nothing
python saxic_cli.py launch 26.3 --offline Steve --timeout 120
python saxic_selftest.py                   # metadata + command-builder checks
python saxic_guitest.py                    # headless UI smoke test
python saxic_guitest.py --mods             # also open the mod library dialog
python saxic_guitest.py --install          # also drive the Install button
python saxic_guitest.py --play             # also install, launch and verify
```

## Files

- `saxic_launcher.py` — desktop app (Tkinter): account box, version list, Play,
  progress, log, settings dialog
- `saxic_install.py` — version manifest, install engine, Java runtime installer
- `saxic_launch.py` — command-line builder and process handling
- `saxic_auth.py` — offline profiles + Microsoft device-code sign-in
- `saxic_util.py` — HTTP/retry, SHA-1, zip extraction, rule evaluation
- `saxic_cli.py` — command-line front end
- `saxic_mods.py` — Modrinth search, mod downloads, the installed-mod manifest
- `saxic_selftest.py` — offline checks (metadata + command builder + mods)
- `saxic_guitest.py` — headless UI smoke test (`--install`, `--play` for more)
- `assets/saxic.ico` — launcher icon (drawn for this project)
- `SAXIC Launcher.bat` — Windows entry point
- `build_exe.py` — builds the standalone `SAXIC-Launcher.exe`
- `publish.cmd` — commits + pushes so GitHub rebuilds the public download
- `setup-github.cmd` — one-time: signs in to GitHub and creates the repo

## Honest limits (v1)

- **Fabric mods are supported** (the launcher installs Fabric for you). Forge,
  Quilt and NeoForge are not installed yet.
- Mods are shared by every Minecraft version in one game folder, so pick builds
  that match the version you play; the Mod library filters builds by the
  selected version.
- No auto-update for the launcher itself; no skins/capes; no Microsoft "Xbox
  friends" list.
- Snapshots are listed but only official Mojang versions are supported.
- The asset download is the slow part (≈470 MB) and needs a real internet
  connection; SHA-1 verification is done, but there is no torrent/peer source.
- Microsoft login needs your own Azure client ID (see above). Without it, you
  stay on the offline profile.

## Notes for anyone reading the code

Three details that are easy to get wrong and are handled here:

- **JVM args come from `arguments.jvm`.** Rules in the version JSON use either
  a regex `version` or a `versionRange` object (`{"min": "10.0.17134"}`).
  Ignoring `versionRange` makes the launcher apply *both* GC branches at once,
  and the JVM refuses to start with two collectors selected.
- **Native layout.** Modern JSONs point `-Djava.library.path` and
  `-Dorg.lwjgl.system.SharedLibraryExtractPath` at sub-folders of the natives
  directory (`natives/java`, `natives/lwjgl`, ...). The launcher reads those
  paths out of the JSON and extracts to match.
- **Native jars stay on the classpath.** LWJGL 3.4 loads its DLLs from the
  classpath jars. Excluding the `:natives-*` artifacts produces
  `UnsatisfiedLinkError: Failed to locate library: lwjgl.dll`.
- **Parallel `.part` files.** Downloaded temp files are named per process, so
  two launcher windows downloading at once cannot corrupt each other.
- **`inheritsFrom` profiles.** Fabric ships a profile that only contains its own
  libraries and main class and points at the vanilla version it extends. The
  launcher merges the two (child libraries first, then the parent's; game/JVM
  arguments concatenated) and reuses the parent's client jar instead of
  downloading a second copy.
- **Fabric libraries are Maven coordinates.** They have `name` + `url` instead
  of Mojang's `downloads.artifact`, so they are resolved from
  `group:artifact:version` into `group/artifact/version/...jar`.
- **CDN certificate fallback.** Some CDNs (Modrinth's, for example) chain to a
  root that does not ship with every Python build's OpenSSL store. When TLS
  verification fails for that reason, downloads retry through Windows' own
  `curl.exe`; the SHA-1 from Modrinth's API is still checked afterwards.

## Next steps we can add

Forge/Quilt/NeoForge loaders, modpack (`.mrpack`) import, a server list +
one-click join, multiple profiles per version, instance folders, and an
updater that pulls new launcher builds.

## License

Free to **use**: download and play with the official build, and share the
download link. Copying, modifying, rebranding, or redistributing the code,
name, icon or other assets is **not** allowed. Full terms: [`LICENSE`](LICENSE).
Copyright © 2026 SARVESH.S.
