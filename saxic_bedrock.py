"""XI Launcher - Minecraft: Bedrock Edition helpers.

Bedrock Edition on Windows is a Microsoft Store (UWP) app. Windows will only
run the copy installed from the Store, so - unlike Java Edition - the launcher
cannot download and run it itself. This module detects the installed app,
launches it, and points to the official Store page when it is missing.

Nothing here bypasses the Store, and no game files are redistributed.
"""
from __future__ import annotations

import os
import re
import subprocess

PACKAGE_NAME = "Microsoft.MinecraftUWP"
PACKAGE_FAMILY = "Microsoft.MinecraftUWP_8wekyb3d8bbwe"
APP_TARGET = r"shell:appsFolder\%s!App" % PACKAGE_FAMILY
STORE_PRODUCT_ID = "9NBLGGH2JHXJ"
STORE_URI = "ms-windows-store://pdp/?productid=%s" % STORE_PRODUCT_ID
WEB_URL = "https://www.minecraft.net/en-us/download"


def _packages_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "Packages", PACKAGE_FAMILY)


def package_folder_exists() -> bool:
    """Fast offline check: does Windows have Bedrock's package data folder?"""
    return os.path.isdir(_packages_dir())


def _powershell(script: str, timeout: int = 25) -> str:
    if os.name != "nt":
        return ""
    try:
        done = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return (done.stdout or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def installed_version() -> str:
    """The installed Bedrock version (for example ``1.21.90.3``), or ``""``."""
    text = _powershell(
        "(Get-AppxPackage -Name %s | Select-Object -First 1 "
        "-ExpandProperty Version).ToString()" % PACKAGE_NAME)
    match = re.search(r"(\d+(?:\.\d+){2,})", text)
    return match.group(1) if match else ""


def is_installed() -> bool:
    """True when Bedrock Edition appears to be installed on this PC."""
    if os.name != "nt":
        return False
    return bool(installed_version()) or package_folder_exists()


def launch() -> bool:
    """Start the installed Bedrock Edition app.

    Returns ``True`` once a launch was issued. Raises when Windows could not
    be asked to open the app.
    """
    if os.name != "nt":
        raise RuntimeError("Bedrock Edition runs on Windows only.")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen(["explorer.exe", APP_TARGET], creationflags=flags)
        return True
    except Exception:  # noqa: BLE001
        pass
    try:
        os.startfile("minecraft:")  # the URI scheme Bedrock registers
        return True
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("Could not start Bedrock Edition: %s" % exc)


def open_store() -> None:
    """Open the Microsoft Store page (or the web download page) for Bedrock."""
    if os.name == "nt":
        try:
            os.startfile(STORE_URI)
            return
        except Exception:  # noqa: BLE001
            pass
    try:
        os.startfile(WEB_URL)
    except Exception:  # noqa: BLE001
        pass
