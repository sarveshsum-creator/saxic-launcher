"""XI Launcher - accounts.

Two ways to play:

* Offline profile - a local username. Perfect for singleplayer and for
  "Open to LAN" worlds with friends on the same network.
* Microsoft sign-in - the only account type Minecraft itself accepts for
  online servers and Realms. It is a Microsoft account; a Google/Gmail
  address can be *used as* the email of a Microsoft account, but Google
  login itself cannot authenticate Minecraft.

The Microsoft flow below is the standard public device-code flow and talks
only to Microsoft/Xbox/Minecraft endpoints.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

DEVICE_CODE_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode"
TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
XBL_URL = "https://user.auth.xboxlive.com/user/authenticate"
XSTS_URL = "https://xsts.auth.xboxlive.com/xsts/authorize"
MC_LOGIN_URL = "https://api.minecraftservices.com/authentication/login_with_xbox"
MC_PROFILE_URL = "https://api.minecraftservices.com/minecraft/profile"
# Java Realms API. The game authenticates with three cookies, not a bearer
# token: sid=token:<access_token>:<uuid>, user=<name>, version=<game version>.
REALMS_URL = "https://pc.realms.minecraft.net/worlds"
REALMS_JOIN_URL = "https://pc.realms.minecraft.net/worlds/v1/%s/join/pc"
SCOPE = "XboxLive.signin offline_access"


class AuthError(Exception):
    pass


def _post(url: str, data: dict, headers=None, form=True, timeout=40):
    if form:
        body = urllib.parse.urlencode(data).encode()
        hdrs = {"Content-Type": "application/x-www-form-urlencoded"}
    else:
        body = json.dumps(data).encode()
        hdrs = {"Content-Type": "application/json", "Accept": "application/json"}
    hdrs.update(headers or {})
    hdrs.setdefault("User-Agent", "XILauncher/1.1")
    req = urllib.request.Request(url, data=body, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise AuthError("HTTP %s from %s: %s" % (exc.code, url, detail[:400])) from None


def _get(url: str, token: str, timeout=40):
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/json",
        "User-Agent": "XILauncher/1.1",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise AuthError("HTTP %s from %s" % (exc.code, url)) from None


# --------------------------------------------------------------------------


def begin_device_login(client_id: str) -> dict:
    if not client_id:
        raise AuthError("No Microsoft client ID configured. Add one in Settings "
                        "(a free Azure app registration with 'Allow public client flows').")
    return _post(DEVICE_CODE_URL, {"client_id": client_id, "scope": SCOPE})


def poll_device_login(client_id: str, device_code: str, interval: int, expires_in: int,
                      cancel: threading.Event | None = None, on_tick=None) -> dict:
    deadline = time.time() + expires_in
    wait = max(2, int(interval or 5))
    while time.time() < deadline:
        if cancel is not None and cancel.is_set():
            raise AuthError("cancelled")
        if on_tick:
            on_tick(int(deadline - time.time()))
        time.sleep(wait)
        try:
            tok = _post(TOKEN_URL, {
                "client_id": client_id,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
            })
        except AuthError as exc:
            msg = str(exc)
            if "authorization_pending" in msg:
                continue
            if "slow_down" in msg:
                wait += 2
                continue
            if "expired_token" in msg:
                raise AuthError("the code expired - start the sign-in again") from None
            if "authorization_declined" in msg:
                raise AuthError("sign-in was declined") from None
            raise
        return tok
    raise AuthError("timed out waiting for sign-in")


def _xbox_token(msa_access_token: str) -> dict:
    return _post(XBL_URL, {
        "Properties": {"AuthMethod": "RPS", "SiteName": "user.auth.xboxlive.com",
                       "RpsTicket": "d=" + msa_access_token},
        "RelyingParty": "http://auth.xboxlive.com",
        "TokenType": "JWT",
    }, form=False)


def _xsts_token(xbl_token: str) -> dict:
    try:
        return _post(XSTS_URL, {
            "Properties": {"SandboxId": "RETAIL", "UserTokens": [xbl_token]},
            "RelyingParty": "rp://api.minecraftservices.com/",
            "TokenType": "JWT",
        }, form=False)
    except AuthError as exc:
        hints = {
            "2148916233": "this Microsoft account has no Xbox profile yet - sign in once at xbox.com",
            "2148916235": "Xbox Live is not available in this account's country",
            "2148916238": "this account is managed by a family group (adult approval needed)",
        }
        text = str(exc)
        for code, hint in hints.items():
            if code in text:
                raise AuthError(hint) from None
        raise


def _minecraft_token(xsts: dict, uhs: str) -> dict:
    identity = "XBL3.0 x=%s;%s" % (uhs, xsts["Token"])
    return _post(MC_LOGIN_URL, {"identityToken": identity}, form=False)


def login_with_microsoft(client_id: str, device_code: str, interval: int, expires_in: int,
                         cancel: threading.Event | None = None, on_tick=None) -> dict:
    tokens = poll_device_login(client_id, device_code, interval, expires_in, cancel, on_tick)
    msa = tokens.get("access_token")
    if not msa:
        raise AuthError("Microsoft did not return an access token")
    xbl = _xbox_token(msa)
    uhs = ((xbl.get("DisplayClaims") or {}).get("xui") or [{}])[0].get("uhs", "")
    xsts = _xsts_token(xbl["Token"])
    mc = _minecraft_token(xsts, uhs)
    profile = _get(MC_PROFILE_URL, mc["access_token"])
    return {
        "name": profile.get("name", "Player"),
        "uuid": profile.get("id", ""),
        "access_token": mc["access_token"],
        "user_type": "msa",
        "xuid": uhs,
        "client_id": client_id,
        "kind": "microsoft",
    }


def check_ownership(account: dict) -> bool:
    """True when the signed-in account actually owns Minecraft."""
    try:
        _get(MC_PROFILE_URL, account["access_token"])
        return True
    except AuthError:
        return False


# --------------------------------------------------------------------------
# Realms


def _realms_headers(account: dict, game_version: str = "") -> dict:
    """Cookies used by the vanilla client for pc.realms.minecraft.net."""
    version = str(game_version or "26.3").strip()
    bits = version.split(".")
    release = 2 <= len(bits) <= 3 and all(bit.isdigit() for bit in bits)
    uuid = str(account.get("uuid") or "").replace("-", "")
    return {
        "Cookie": "sid=token:%s:%s; user=%s; version=%s" % (
            account.get("access_token") or "", uuid,
            account.get("name") or "", version),
        "Accept": "application/json",
        "User-Agent": "XILauncher/1.1",
        "Is-Prerelease": "false" if release else "true",
    }


def _realms_get(url: str, account: dict, game_version: str = "", timeout=40):
    req = urllib.request.Request(url, headers=_realms_headers(account, game_version))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        if exc.code == 401:
            raise AuthError("Realms sign-in expired - sign in with Microsoft again.") from None
        if exc.code == 403:
            raise AuthError("Realms refused access (403). Open Minecraft once with this "
                            "account and accept the Realms terms, or check that the "
                            "account has an active Realms subscription.") from None
        if exc.code == 404:
            raise AuthError("that realm was not found (404).") from None
        raise AuthError("HTTP %s from Realms: %s" % (exc.code, detail[:300])) from None
    except urllib.error.URLError as exc:
        raise AuthError("could not reach Realms: %s" % exc.reason) from None
    except ValueError:
        raise AuthError("Realms returned an unexpected response.") from None


def realms_list(account: dict, game_version: str = "") -> list:
    """List Minecraft Realms the signed-in account has access to.

    Requires a Microsoft-signed-in account (``kind == "microsoft"``).
    Returns a list of realm dicts with id, name, state, owner, motd, etc.
    """
    if account.get("kind") != "microsoft":
        return []
    if not account.get("access_token"):
        raise AuthError("sign in with Microsoft first.")
    data = _realms_get(REALMS_URL, account, game_version)
    servers = data.get("servers", []) if isinstance(data, dict) else []
    out = []
    for s in servers:
        out.append({
            "id": s.get("id", ""),
            "name": s.get("name") or "Untitled Realm",
            "state": str(s.get("state") or "CLOSED").lower(),
            "owner": s.get("owner") or "",
            "ownerUuid": s.get("ownerUUID") or s.get("ownerUuid") or "",
            "motd": s.get("motd") or "",
            "players": s.get("players", []) or [],
            "expired": bool(s.get("expired", False)),
            "trial": bool(s.get("trial", False)),
            "daysRemaining": int(s.get("daysLeft") or s.get("daysRemaining") or 0),
            "maxPlayers": int(s.get("maxPlayers") or 0),
            "compatible": str(s.get("compatibility") or "COMPATIBLE").upper()
                          == "COMPATIBLE",
            "activeVersion": s.get("activeVersion") or "",
        })
    return out


def realms_address(account: dict, realm_id, game_version: str = "") -> str:
    """Ask Mojang for the server address of one realm.

    The vanilla client connects to this address after the Realms API has
    approved the session. ``--quickPlayMultiplayer`` can use it directly.
    """
    if account.get("kind") != "microsoft" or not account.get("access_token"):
        raise AuthError("sign in with Microsoft first.")
    realm_id = str(realm_id or "").strip()
    if not realm_id:
        raise AuthError("this realm has no id.")
    data = _realms_get(REALMS_JOIN_URL % urllib.parse.quote(realm_id, safe=""),
                       account, game_version)
    address = (data or {}).get("address") if isinstance(data, dict) else ""
    if not address:
        raise AuthError("Mojang did not return a join address for this realm.")
    return str(address)
