"""
cookie_extractor_linux.py
=========================
Cookie extractor that runs INSIDE Container 1 (Linux Docker container).

Unlike cookie_manager/extractor.py (macOS host-side), this module does NOT
call the macOS `security` CLI. Instead, it receives the Chrome Safe Storage
password via the CHROME_KEY_PASSWORD environment variable, which is derived
on the host and injected at container start-up via docker-compose.yml.

Architecture
------------
Host (macOS):
  - Chrome cookie DB is mounted read-only at /chrome-data inside the container
  - CHROME_KEY_PASSWORD env var carries the Keychain password
    (set via: export CHROME_KEY_PASSWORD=$(security find-generic-password
     -w -s "Chrome Safe Storage" -a "Chrome"))

Container 1 (Linux):
  - Reads /chrome-data/<profile>/Cookies SQLite files
  - Derives AES-128-CBC key: PBKDF2(CHROME_KEY_PASSWORD, b'saltysalt', 1003)[:16]
  - Decrypts v10-prefixed cookie values
  - Returns cookie strings for BING, CHATGPT, CLAUDE
  - Stores results back into the running process's environment +
    writes them to /app/.env for persistence across restarts

POST /v1/cookies/extract  →  triggers this module
"""
from __future__ import annotations

import glob
import os
import shutil
import sqlite3
import sys
import time
from hashlib import pbkdf2_hmac

try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import unpad
    _CRYPTO_OK = True
except ImportError:
    _CRYPTO_OK = False

# ── Cookie targets (same as host extractor) ───────────────────────────────────
COOKIE_TARGETS = {
    "BING_COOKIES": {
        "domains": ["%.bing.com", "copilot.microsoft.com", "%.msn.com"],
        "names":   ["_U", "SRCHHPGUSR", "MUID", "MUIDB", "_EDGE_V",
                    "_RwBf", "SRCHUID", "SRCHD"],
        "required": ["_U"],
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
    "COPILOT_COOKIES": {
        "domains": ["copilot.microsoft.com", "%.microsoft.com"],
        "names":   ["__cf_bm", "_C_ETH", "_EDGE_S", "MUID", "MUIDB",
                    "_EDGE_V", "MSFPC", "__Host-copilot-anon"],
        "required": ["MUID"],
    },
}

_CHROME_EPOCH_OFFSET = 11644473600 * 1_000_000


# ── Key derivation (no Keychain call needed) ──────────────────────────────────

def _derive_key(password: str) -> bytes:
    """PBKDF2-SHA1 key derivation — identical to macOS Chrome AES-128 key."""
    return pbkdf2_hmac("sha1", password.encode("utf-8"), b"saltysalt", 1003, dklen=16)


def _is_valid_cookie_value(value: str) -> bool:
    """Reject values with control characters (0x00-0x1F or 0x7F) which cause
    CookieError in Python's http.cookies / aiohttp. Allow all printable ASCII
    and Unicode — Microsoft's _U cookie is ASCII-safe; garbage decryption
    typically produces raw ciphertext bytes that include control chars."""
    return bool(value) and not any(ord(c) < 0x20 or ord(c) == 0x7F for c in value)


def _decrypt(encrypted: bytes, key: bytes) -> str | None:
    if not _CRYPTO_OK or not encrypted or len(encrypted) < 4:
        return None
    if encrypted[:3] in (b"v10", b"v11"):
        ciphertext = encrypted[3:]
    else:
        # Unencrypted legacy value — decode directly
        val = encrypted.decode("utf-8", errors="replace")
        return val if _is_valid_cookie_value(val) else None
    try:
        cipher = AES.new(key, AES.MODE_CBC, iv=b" " * 16)
        plaintext = unpad(cipher.decrypt(ciphertext), AES.block_size)
        # Chrome prepends 32 bytes of random padding before the actual cookie value
        val = plaintext[32:].decode("utf-8", errors="replace") if len(plaintext) > 32 else plaintext.decode("utf-8", errors="replace")
        # Validate: if decryption produced control chars, the key was wrong
        return val if _is_valid_cookie_value(val) else None
    except Exception:
        return None


def _expired(expires_utc: int) -> bool:
    if expires_utc == 0:
        return False
    return (expires_utc - _CHROME_EPOCH_OFFSET) < time.time() * 1_000_000


# ── Profile discovery (mounted path) ─────────────────────────────────────────

def _find_profiles(chrome_data_path: str) -> list[str]:
    """Find all Cookie DB files under the mounted Chrome data directory."""
    paths = []
    for candidate in ["Default"] + [f"Profile {i}" for i in range(1, 10)]:
        db = os.path.join(chrome_data_path, candidate, "Cookies")
        if os.path.isfile(db):
            paths.append(db)
    return paths


# ── Main extraction ───────────────────────────────────────────────────────────

def extract_cookies(
    chrome_data_path: str,
    chrome_key_password: str,
) -> dict[str, str | None]:
    """
    Extract cookies from the mounted Chrome data directory.
    Returns {env_var_name: cookie_string_or_None}.
    """
    if not _CRYPTO_OK:
        return {k: None for k in COOKIE_TARGETS}

    key = _derive_key(chrome_key_password)
    profiles = _find_profiles(chrome_data_path)

    results: dict[str, str | None] = {}

    for target_key, cfg in COOKIE_TARGETS.items():
        collected: dict[str, str] = {}

        for db_path in profiles:
            tmp = f"/tmp/ctr1_cookies_{os.getpid()}_{target_key}.db"
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
                        if _expired(row["expires_utc"]):
                            continue
                        val = _decrypt(row["encrypted_value"], key)
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

        missing = [r for r in cfg["required"] if r not in collected]
        if missing:
            print(
                f"[cookie_extractor] {target_key}: missing {missing}. "
                f"Sign into the service in Chrome first.",
                file=sys.stderr,
            )
            results[target_key] = None
        else:
            cookie_str = ";".join(f"{k}={v}" for k, v in collected.items())
            print(f"[cookie_extractor] {target_key}: extracted {len(collected)} cookies "
                  f"({', '.join(collected.keys())})")
            results[target_key] = cookie_str

    return results


def patch_env_file(env_path: str, updates: dict[str, str]) -> bool:
    """Write new cookie values into the container's .env file atomically."""
    import re
    try:
        lines = open(env_path).readlines() if os.path.exists(env_path) else []
    except Exception:
        lines = []

    changed = False
    for key, value in updates.items():
        pattern = re.compile(rf"^{re.escape(key)}\s*=.*$")
        replaced = False
        for i, line in enumerate(lines):
            if pattern.match(line.rstrip()):
                if lines[i].rstrip() != f"{key}={value}":
                    lines[i] = f"{key}={value}\n"
                    changed = True
                replaced = True
                break
        if not replaced:
            lines.append(f"{key}={value}\n")
            changed = True

    if changed:
        tmp = env_path + ".tmp"
        with open(tmp, "w") as f:
            f.writelines(lines)
        os.replace(tmp, env_path)

    return changed
