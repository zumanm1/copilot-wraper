"""
cookie_manager/extractor.py
===========================
Extracts session cookies for Bing/Copilot, ChatGPT and Claude from the
user's locally installed Chrome and Firefox browsers — zero manual steps.

macOS Chrome cookie encryption
-------------------------------
Chrome encrypts cookie values with AES-128-CBC using a key derived from
the "Chrome Safe Storage" password stored in the macOS Keychain:

    key = PBKDF2(password=keychain_pw, salt=b'saltysalt',
                 iterations=1003, dklen=16, hash=SHA1)
    plaintext = AES-128-CBC-decrypt(ciphertext=value[3:], key=key,
                                    iv=b' ' * 16)

The first 3 bytes of the stored value are the version prefix (b'v10').

Firefox cookies (macOS)
-----------------------
Firefox stores cookies in plain SQLite with no encryption — direct read.
"""
from __future__ import annotations

import glob
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from hashlib import pbkdf2_hmac

# ── Optional crypto import ───────────────────────────────────────────────────
try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import unpad
    _CRYPTO_AVAILABLE = True
except ImportError:
    _CRYPTO_AVAILABLE = False


# ── Cookie definitions ───────────────────────────────────────────────────────

# domain patterns (SQLite LIKE syntax) → list of cookie names to extract
COOKIE_TARGETS: dict[str, dict] = {
    "BING_COOKIES": {
        "domains": ["%.bing.com", "copilot.microsoft.com", "%.msn.com"],
        "names":   ["_U", "SRCHHPGUSR", "MUID", "MUIDB", "_EDGE_V",
                    "_RwBf", "SRCHUID", "SRCHD"],
        "required": ["_U"],   # must have this or cookie string is useless
    },
    "CHATGPT_COOKIES": {
        "domains": ["%.openai.com", "chat.openai.com"],
        "names":   ["__Secure-next-auth.session-token",
                    "__Secure-next-auth.callback-url",
                    "cf_clearance", "_cfuvid"],
        "required": ["__Secure-next-auth.session-token"],
    },
    "CLAUDE_COOKIES": {
        "domains": ["%.claude.ai", "claude.ai", "%.anthropic.com"],
        "names":   ["sessionKey", "__cf_bm", "_cfuvid",
                    "__Host-next-auth.csrf-token"],
        "required": ["sessionKey"],
    },
}

# Chromium-family browser Keychain service names
CHROME_VARIANTS = [
    ("Google Chrome",    "Chrome Safe Storage",         "Chrome"),
    ("Brave Browser",    "Brave Safe Storage",          "Brave"),
    ("Microsoft Edge",   "Microsoft Edge Safe Storage", "Microsoft Edge"),
    ("Chromium",         "Chromium Safe Storage",       "Chromium"),
]

# Chrome epoch offset: Windows FILETIME to Unix epoch (microseconds)
_CHROME_EPOCH_OFFSET = 11644473600 * 1_000_000


# ── Chrome helpers ───────────────────────────────────────────────────────────

def _chrome_profiles(browser_name: str) -> list[str]:
    """Return all Cookie DB paths for a given Chromium browser."""
    base = os.path.expanduser(
        f"~/Library/Application Support/{browser_name}"
    )
    if not os.path.isdir(base):
        return []
    paths = []
    for candidate in ["Default"] + [f"Profile {i}" for i in range(1, 10)]:
        db = os.path.join(base, candidate, "Cookies")
        if os.path.isfile(db):
            paths.append(db)
    return paths


def _get_chrome_key(service: str, account: str) -> bytes | None:
    """Retrieve the Chrome Safe Storage password from macOS Keychain."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password",
             "-w", "-s", service, "-a", account],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return None
        pw = result.stdout.strip().encode("utf-8")
        # Derive 128-bit key via PBKDF2-SHA1, salt='saltysalt', 1003 rounds
        return pbkdf2_hmac("sha1", pw, b"saltysalt", 1003, dklen=16)
    except Exception:
        return None


def _decrypt_chrome_value(encrypted: bytes, key: bytes) -> str | None:
    """Decrypt an AES-128-CBC Chrome cookie value."""
    if not _CRYPTO_AVAILABLE:
        return None
    if not encrypted or len(encrypted) < 4:
        return None
    # Strip version prefix (b'v10' or b'v11')
    if encrypted[:3] in (b"v10", b"v11"):
        ciphertext = encrypted[3:]
    else:
        # Unencrypted (legacy) value
        return encrypted.decode("utf-8", errors="replace")
    try:
        cipher = AES.new(key, AES.MODE_CBC, iv=b" " * 16)
        decrypted = cipher.decrypt(ciphertext)
        return unpad(decrypted, AES.block_size).decode("utf-8", errors="replace")
    except Exception:
        return None


def _chrome_epoch_expired(expires_utc: int) -> bool:
    """Return True if the Chrome cookie is expired."""
    if expires_utc == 0:
        return False  # session cookie — treat as valid
    unix_us = expires_utc - _CHROME_EPOCH_OFFSET
    return unix_us < time.time() * 1_000_000


def read_chrome_cookies(target_key: str) -> dict[str, str]:
    """
    Extract cookies for `target_key` from all installed Chromium browsers.
    Returns a dict of {cookie_name: cookie_value}.
    """
    cfg = COOKIE_TARGETS[target_key]
    collected: dict[str, str] = {}

    for browser_name, service, account in CHROME_VARIANTS:
        profiles = _chrome_profiles(browser_name)
        if not profiles:
            continue

        key = _get_chrome_key(service, account)
        if key is None and _CRYPTO_AVAILABLE:
            # Keychain access denied or browser not installed — try next
            continue

        for db_path in profiles:
            tmp = f"/tmp/cookies_{os.getpid()}_{browser_name.replace(' ','_')}.db"
            try:
                shutil.copy2(db_path, tmp)
                conn = sqlite3.connect(f"file:{tmp}?immutable=1", uri=True)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()

                for domain_pattern in cfg["domains"]:
                    placeholders = ",".join("?" * len(cfg["names"]))
                    cur.execute(
                        f"SELECT host_key, name, encrypted_value, expires_utc "
                        f"FROM cookies "
                        f"WHERE host_key LIKE ? AND name IN ({placeholders})",
                        [domain_pattern] + cfg["names"],
                    )
                    for row in cur.fetchall():
                        if _chrome_epoch_expired(row["expires_utc"]):
                            continue
                        if key:
                            val = _decrypt_chrome_value(row["encrypted_value"], key)
                        else:
                            # Crypto not available — try as plaintext
                            val = row["encrypted_value"].decode(
                                "utf-8", errors="replace"
                            )
                        if val and row["name"] not in collected:
                            collected[row["name"]] = val

                conn.close()
            except Exception:
                pass
            finally:
                try:
                    os.unlink(tmp)
                except Exception:
                    pass

        # If we found the required cookies, stop scanning more browsers
        if all(r in collected for r in cfg["required"]):
            break

    return collected


# ── Firefox helpers ──────────────────────────────────────────────────────────

def _firefox_profiles() -> list[str]:
    """Return all Firefox cookie DB paths (macOS)."""
    base = os.path.expanduser("~/Library/Application Support/Firefox/Profiles")
    if not os.path.isdir(base):
        return []
    return glob.glob(os.path.join(base, "*/cookies.sqlite"))


def _firefox_epoch_expired(expiry: int) -> bool:
    """Return True if the Firefox cookie is expired."""
    if expiry == 0:
        return False
    return expiry < time.time()


def read_firefox_cookies(target_key: str) -> dict[str, str]:
    """
    Extract cookies for `target_key` from Firefox (no decryption needed).
    Returns a dict of {cookie_name: cookie_value}.
    """
    cfg = COOKIE_TARGETS[target_key]
    collected: dict[str, str] = {}

    for db_path in _firefox_profiles():
        tmp = f"/tmp/ff_cookies_{os.getpid()}.db"
        try:
            shutil.copy2(db_path, tmp)
            conn = sqlite3.connect(f"file:{tmp}?immutable=1", uri=True)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            for domain_pattern in cfg["domains"]:
                # Firefox uses host field with optional leading dot
                sql_pattern = domain_pattern.replace("%", "")
                placeholders = ",".join("?" * len(cfg["names"]))
                cur.execute(
                    f"SELECT host, name, value, expiry "
                    f"FROM moz_cookies "
                    f"WHERE (host LIKE ? OR host LIKE ?) AND name IN ({placeholders})",
                    [sql_pattern, "." + sql_pattern.lstrip(".")] + cfg["names"],
                )
                for row in cur.fetchall():
                    if _firefox_epoch_expired(row["expiry"]):
                        continue
                    if row["name"] not in collected and row["value"]:
                        collected[row["name"]] = row["value"]

            conn.close()
        except Exception:
            pass
        finally:
            try:
                os.unlink(tmp)
            except Exception:
                pass

    return collected


# ── Assembler ────────────────────────────────────────────────────────────────

def build_cookie_string(cookies: dict[str, str]) -> str:
    """Convert {name: value} dict to semicolon-separated cookie string."""
    return ";".join(f"{k}={v}" for k, v in cookies.items())


def extract_for_service(target_key: str) -> str | None:
    """
    Extract cookies for one service from Chrome then Firefox.
    Returns a cookie string or None if required cookies are missing.
    """
    cfg = COOKIE_TARGETS[target_key]

    # Try Chrome first (most common)
    cookies = read_chrome_cookies(target_key)

    # Fall back to Firefox for any missing cookies
    if not all(r in cookies for r in cfg["required"]):
        ff_cookies = read_firefox_cookies(target_key)
        cookies.update({k: v for k, v in ff_cookies.items() if k not in cookies})

    # Verify we have the required cookie(s)
    missing = [r for r in cfg["required"] if r not in cookies]
    if missing:
        print(
            f"[cookie_manager] {target_key}: missing required cookies {missing}. "
            f"Make sure you are logged into the service in Chrome or Firefox.",
            file=sys.stderr,
        )
        return None

    cookie_str = build_cookie_string(cookies)
    print(
        f"[cookie_manager] {target_key}: extracted {len(cookies)} cookies "
        f"({', '.join(cookies.keys())})"
    )
    return cookie_str


def extract_all() -> dict[str, str | None]:
    """
    Extract cookies for all three services.
    Returns {env_var_name: cookie_string_or_None}.
    """
    if not _CRYPTO_AVAILABLE:
        print(
            "[cookie_manager] WARNING: pycryptodome not installed. "
            "Chrome cookies cannot be decrypted. "
            "Run: pip3 install pycryptodome",
            file=sys.stderr,
        )

    result = {}
    for key in COOKIE_TARGETS:
        result[key] = extract_for_service(key)
    return result


if __name__ == "__main__":
    # Quick smoke test: print extracted cookies (masked)
    cookies = extract_all()
    for env_var, value in cookies.items():
        if value:
            masked = value[:20] + "..." if len(value) > 20 else value
            print(f"{env_var} = {masked}")
        else:
            print(f"{env_var} = NOT FOUND")
