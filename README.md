# XI Launcher

An original Minecraft launcher for Windows with a built-in **XI Server
Hoster**. It is written from scratch: it talks only to Microsoft's sign-in
endpoints, Mojang's public metadata/file servers, Modrinth and (optionally)
Fabric/Paper metadata. It ships **no** game files, no third-party launcher
code and no borrowed assets.

> Not affiliated with Mojang, Microsoft, Aternos or any other launcher project.

## Download

**[Download XI-Launcher.exe](../../releases/latest/download/XI-Launcher.exe)**

**[Download the share-ready zip](../../releases/latest/download/XI-Launcher-win64.zip)** — the same `.exe` plus a one-page quick start, README and licence. This is the easiest file to send to a friend.

One file, no installer, no Python needed. The links always serve the newest
build, because every change pushed to this repository is rebuilt automatically
by GitHub Actions. SHA-256 checksums (`*.sha256`) are on the same release.

Windows SmartScreen may show "unrecognised app" because the `.exe` is not
code-signed. Choose **More info → Run anyway**.

## Run it

Double-click **`XI Launcher.bat`** (or run `python saxic_launcher.py`).

Requirements: Windows + **Python 3.10 or newer**. Java is *not* required —
the launcher downloads Mojang's own Java runtime for the version you pick.

Building the standalone `.exe` yourself: `python build_exe.py` (needs
`pip install pyinstaller`). Result: `dist/XI-Launcher.exe`.

## What it does

| Area | Behaviour |
| --- | --- |
| Versions | Reads Mojang's official version manifest; choose any release (plus snapshots with the checkbox). Installed versions are marked `*`. |
| Install | Downloads the client jar, libraries (honouring OS rules), native jars (extracted), the asset index and its ~490 MB of objects, and the log4j config. Verifies SHA-1 and resumes from a partial file each run. |
| Java | Detects a system JVM, otherwise installs the matching Mojang runtime (`java-runtime-epsilon`, `java-runtime-gamma`, …) into the game folder. |
| Launch | Builds the real Java command line from the version JSON (modern `arguments` and legacy `minecraftArguments` both supported), streams the game log into the window, and keeps the game running until you close it or press Stop. |
| Mods | A **Mod library** window searches [Modrinth](https://modrinth.com) for Fabric mods that match the selected Minecraft version, installs them into `mods/`, and can disable/remove them. Installing a mod automatically sets up Fabric and switches **PLAY** to it. |
| **XI Server Hoster** | Create a **Vanilla, Paper or Fabric** server, download its official jar, accept the EULA, start/stop it, watch a live console, send commands, see who is online, set MOTD/players/difficulty, share the LAN address, try an automatic UPnP port-forward, and make/restore world backups. |
| Worlds | The **Worlds** window lists every singleplayer save with its size and last-played time. Double-click a world (or use *Play world*) to launch straight into it, make a `.zip` backup, restore a backup as a new save, open the folder or delete a world. |
| Screenshots | The **Screenshots** window shows a thumbnail gallery of everything in `screenshots/`. Click a picture to open it full-size. |
| News | The **News** window shows the latest stories from Mojang's public launcher feed, with a *Java Edition only* filter and *Read more* links. |
| Realms | With Microsoft sign-in, the **Realms** window lists realms the account owns or joined and can launch Minecraft straight into one (`--quickPlayMultiplayer`). |
| Play time | The launcher remembers when you last played each version and how long you have played it in total. |
| Launch options | Extra **JVM arguments**, extra **game arguments**, a custom **window size**, and a picker for any installed **Java runtime** (or leave it on Auto-detect). |
| Accounts | **Offline profile** (username only) and **Microsoft sign-in** (device-code flow). |

## XI Server Hoster

Click **Server hoster** in the top bar.

1. **New server** — pick a name, Minecraft version, software (Vanilla / Paper /
   Fabric), memory, port, MOTD, difficulty and game mode, then tick the
   Minecraft EULA box. The launcher downloads the official server jar and
   writes `server.properties` for you.
2. **Start** — runs a real Java server process. A matching Java runtime is
   downloaded automatically when needed. The first start also creates the
   world, which can take a minute.
3. **Console** — live log, player count and a command prompt (`say`, `list`,
   `op`, `whitelist`, … all work).
4. **Join server** — starts the server if necessary, waits for
   `Done (…)`, then launches Minecraft with
   `--quickPlayMultiplayer 127.0.0.1:<port>`.
5. **Share** — shows the address for this PC, the LAN address for friends on
   the same Wi-Fi, and the public IP. **Try UPnP port forward** asks a
   compatible router to map the TCP port automatically; otherwise forward the
   port manually.
6. **Back up now / Backups** — zips the world (pausing saves first when the
   server is running) and can restore an older world. The current world is kept
   as a `world.before-restore-…` safety copy.
7. **Options** — change MOTD, player limit, memory, port, difficulty, game mode
   and online mode. Port/memory/online-mode changes take effect after a
   restart.

Servers live in `%APPDATA%\.saxic\servers\<server-id>` with their jar,
`server.properties`, `eula.txt`, world, logs and backups. They run **while your
PC is on and the launcher is open** — this is a local hoster, not a cloud
service. Aternos is a separate cloud product with no public API, so XI Server
Hoster is an original local implementation of the same idea, not a clone or an
Aternos client.

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
- Realms also needs the account to have accepted the Realms terms once in the
  official Minecraft client.

## Where things go

| What | Where |
| --- | --- |
| Launcher settings | `settings.json` next to `saxic_launcher.py`; the `.exe` build keeps it in `%APPDATA%\XI Launcher` |
| Version list cache | `cache/version_manifest_v2.json` — lets the window show the list instantly and only re-download it when Mojang changes it |
| Game files | `%APPDATA%\.saxic` (change it in Settings) — `versions/`, `libraries/`, `assets/`, `runtime/` |
| Mods | `%APPDATA%\.saxic\mods` — jars downloaded by the Mod library (jars you drop in yourself are listed too). Disabled mods become `*.jar.disabled`; `.saxic-mods.json` records what installed them |
| **Servers** | `%APPDATA%\.saxic\servers\<id>` — server jar, `server.properties`, `eula.txt`, world, `logs/`, `backups/` |
| World backups | `%APPDATA%\.saxic\backups` — one `.zip` per backup, made by the Worlds window |
| Screenshots | `%APPDATA%\.saxic\screenshots` — shown in the Screenshots window |
| News cache | `%APPDATA%\XI Launcher\news-cache.json` for the `.exe`, or `news-cache.json` next to the source |
| Fabric profile | `versions/fabric-loader-<loader>-<game>/` — generated from Fabric's official profile JSON, merged with the vanilla version it inherits from |
| Rough size | ~0.8 GB for a modern client version, plus ~50-60 MB per hosted server |

### Starting it

Use the **Desktop shortcut** (`XI Launcher.lnk`) or `XI Launcher.bat`. It runs
`pythonw.exe` directly, so no console window appears. If you double-click
`saxic_launcher.py` instead, Windows opens a terminal window first — that is
normal, not an error.

### Download speed

Assets are ~5000 small files. They are fetched with a thread pool
(`Download threads` in Settings, default 16). Lower it on a flaky
connection; raise it on a fast one.

### Startup speed

The version list is cached on disk. On launch the window fills the list from
that cache immediately and then revalidates it in the background with a
conditional request (`If-None-Match` / `If-Modified-Since`), so most launches
download nothing at all and show the list at once.

### Launch speed

After a version has been installed once, **PLAY** trusts the file names and
sizes instead of re-reading every file to check its SHA-1. That removes roughly
600 MB of hashing per launch (measured: ~7.8 s down to ~1.6 s). The
**Install / verify** button always does the full hash check, so run it if you
suspect a damaged file.

### Stability mode

On PCs with no pagefile (or little free commit), the launcher lowers render and
simulation distance, switches to G1 GC, caps native memory and keeps the Java
heap conservative. This prevents the native `insufficient memory … Chunk::new`
crash that large modpacks can hit mid-play. `enable-pagefile.cmd` in the share
zip adds a Windows-managed pagefile for a permanent fix.

## Command line

Handy for scripting or if the GUI gives you trouble:

```
python saxic_cli.py status                 # what is installed, where
python saxic_cli.py install 26.3           # download + verify a version
python saxic_cli.py command 26.3           # print the java command, run nothing
python saxic_cli.py launch 26.3 --offline Steve --timeout 120
python saxic_selftest.py                   # metadata + command-builder checks
python saxic_guitest.py                    # headless UI smoke test
python saxic_guitest.py --dialogs          # also open the windows/dialogs
python saxic_guitest.py --mods             # also open the mod library dialog
python saxic_guitest.py --install          # also drive the Install button
python saxic_guitest.py --play             # also install, launch and verify
```

## Files

- `saxic_launcher.py` — desktop app (Tkinter): account box, version list, Play,
  progress, log, settings, and the XI Server Hoster windows
- `saxic_servers.py` — server creation, jar downloads, Java process, console,
  properties, backups, UPnP and server ping
- `saxic_install.py` — version manifest, install engine, Java runtime installer
- `saxic_launch.py` — command-line builder and process handling
- `saxic_auth.py` — offline profiles + Microsoft device-code sign-in, Realms API
- `saxic_util.py` — HTTP/retry, SHA-1/SHA-256, zip extraction, rule evaluation
- `saxic_cli.py` — command-line front end
- `saxic_mods.py` — Modrinth search, mod downloads, the installed-mod manifest
- `saxic_selftest.py` — offline checks (metadata + command builder + mods + servers)
- `saxic_guitest.py` — headless UI smoke test (`--dialogs`, `--mods`, `--install`, `--play`)
- `assets/saxic.ico` — launcher icon (drawn for this project)
- `XI Launcher.bat` — Windows entry point
- `build_exe.py` — builds the standalone `XI-Launcher.exe`
- `publish.cmd` — commits + pushes so GitHub rebuilds the public download
- `setup-github.cmd` — one-time: signs in to GitHub and creates the repo

## Honest limits

- **XI Server Hoster is local, not cloud hosting.** The server runs on your PC
  and stops when the PC or launcher closes. There is no 24/7 queue-based host
  like Aternos, and no Aternos API is used (it has no public one).
- **Fabric mods are supported** (the launcher installs Fabric for you). Forge,
  Quilt and NeoForge are not installed yet. Fabric *servers* are supported; put
  matching server-side mods in the server's `mods/` folder.
- Mods in the client's `mods/` folder are shared by every Minecraft version, so
  pick builds that match the version you play; the Mod library filters builds by
  the selected version.
- Server internet play needs a port-forward or UPnP; some routers/CGNAT
  connections cannot do it. A VPN such as Tailscale works as a fallback.
- No auto-update for the launcher itself; no skins/capes; no Microsoft "Xbox
  friends" list.
- Snapshots are listed but only official Mojang versions are supported.
- Microsoft login needs your own Azure client ID (see above). Without it, you
  stay on the offline profile.

## Notes for anyone reading the code

- **JVM args come from `arguments.jvm`.** Rules in the version JSON use either
  a regex `version` or a `versionRange` object (`{"min": "10.0.17134"}`).
- **Native layout.** Modern JSONs point `-Djava.library.path` and
  `-Dorg.lwjgl.system.SharedLibraryExtractPath` at sub-folders of the natives
  directory (`natives/java`, `natives/lwjgl`, ...).
- **Native jars stay on the classpath.** LWJGL 3.4 loads its DLLs from the
  classpath jars.
- **Parallel `.part` files.** Downloaded temp files are named per process, so
  two launcher windows downloading at once cannot corrupt each other.
- **`inheritsFrom` profiles.** Fabric ships a profile that only contains its own
  libraries and main class and points at the vanilla version it extends; the
  launcher merges the two and reuses the parent's client jar.
- **Fabric libraries are Maven coordinates.** They are resolved from
  `group:artifact:version` into `group/artifact/version/...jar`.
- **Server software sources.** Vanilla jars come from Mojang's version JSON,
  Paper from `fill.papermc.io/v3`, Fabric from `meta.fabricmc.net`. Each jar is
  hash-checked where the source publishes a hash. `server.properties` is
  rewritten from the server metadata, and `eula.txt` is only set to true after
  the EULA checkbox/confirmation.
- **CDN certificate fallback.** Some CDNs chain to a root that does not ship
  with every Python build's OpenSSL store; downloads retry through Windows'
  `curl.exe` and still verify the published hash.

## Next steps we can add

Forge/Quilt/NeoForge loaders, modpack (`.mrpack`) import, scheduled server
backups, whitelist management in the hoster window, multiple profiles per
version, instance folders, and an updater that pulls new launcher builds.

## License

Free to **use**: download and play with the official build, and share the
download link. Copying, modifying, rebranding, or redistributing the code,
name, icon or other assets is **not** allowed. Full terms: [`LICENSE`](LICENSE).
Copyright © 2026 SARVESH.S.
