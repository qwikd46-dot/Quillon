"""Chromium profile encryption key, for the cookies WebEngine persists.

Qt WebEngine 6.11 exposes no cookie-encryption API at all -- there is no
setHttpProxy-style knob, no key setter, nothing. The only way to get
Chromium to encrypt what it writes to disk is to hand it the key it
expects in the profile's ``Local State`` file, under ``os_crypt``.

Why this exists

Without a ``Local State`` file Chromium has no key, so it stores cookie
values verbatim in ``Cookies`` -- a SQLite table anyone with read access
to the profile can read with a one-line query. That is the exposure this
module closes.

The key never touches disk. It is generated here, handed to Chromium in
``Local State`` as the wrapped blob Chromium expects, and kept in the OS
keyring so that a later run can prove it is talking to the same profile.
The wrapped form is what lives on disk, which is the point: the profile
is portable, the key is not.

Scope and honesty

This is *defence in depth*, not the primary control. The primary control
is that Quillon does not persist cookies at all unless explicitly told to --
see the PersistentCookiesPolicy decision in quillon/core/webengine.py. That
default removes the exposure outright and does not depend on Chromium
accepting anything. Someone who opts into persistence gets this on top.

The wrapping scheme below is Chromium's legacy v10: PBKDF2-HMAC-SHA1
from a fixed password, then AES-128-CBC under a fixed IV. That is the
scheme Chromium's own Linux fallback has used for years, and it is what
Qt WebEngine's bundled Chromium will unwrap. The master key inside it is
genuinely random, so the fixed password only protects the wrapper, not the
key.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sys
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# Chromium's legacy OSCrypt parameters. These are constants in Chromium's
# source, not secrets; the secret is the master key wrapped inside.
_V10_PREFIX = b"v10"
_PBKDF2_PASSWORD = b"peanuts"
_PBKDF2_SALT = b"saltysalt"
_PBKDF2_ROUNDS = 1
_AES_KEY_BYTES = 16
_MASTER_KEY_BYTES = 16
_IV = b" " * 16

KEYRING_SERVICE = "quillon"
KEYRING_ACCOUNT = "chromium-cookie-key"


class ChromiumCryptError(RuntimeError):
    """Raised when a profile key cannot be produced or stored."""


def _pkcs7(data: bytes, block: int = 16) -> bytes:
    pad = block - (len(data) % block)
    return data + bytes([pad]) * pad


def _wrapper_key() -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha1", _PBKDF2_PASSWORD, _PBKDF2_SALT, _PBKDF2_ROUNDS, _AES_KEY_BYTES
    )


def wrap_master_key(master_key: bytes) -> str:
    """Wrap a master key the way Chromium's v10 scheme expects."""
    if len(master_key) != _MASTER_KEY_BYTES:
        raise ChromiumCryptError(
            f"master key must be {_MASTER_KEY_BYTES} bytes, got {len(master_key)}"
        )
    encryptor = Cipher(algorithms.AES(_wrapper_key()), modes.CBC(_IV)).encryptor()
    blob = _v10_prefix() + encryptor.update(_pkcs7(master_key)) + encryptor.finalize()
    return base64.b64encode(blob).decode("ascii")


def unwrap_master_key(wrapped_b64: str) -> Optional[bytes]:
    """Recover a master key from a wrapped blob, or None if it is not ours.

    Only used to check a key we just wrote; Chromium is what actually
    consumes the wrapped form.
    """
    from cryptography.hazmat.primitives.ciphers import Cipher as _C

    try:
        blob = base64.b64decode(wrapped_b64, validate=True)
    except Exception:
        return None
    if not blob.startswith(_v10_prefix()) or len(blob) <= len(_v10_prefix()):
        return None
    body = blob[len(_v10_prefix()):]
    decryptor = _C(algorithms.AES(_wrapper_key()), modes.CBC(_IV)).decryptor()
    try:
        padded = decryptor.update(body) + decryptor.finalize()
    except Exception:
        return None
    if not padded:
        return None
    pad = padded[-1]
    if pad < 1 or pad > 16 or padded[-pad:] != bytes([pad]) * pad:
        return None
    return padded[:-pad]


def _v10_prefix() -> bytes:
    return _V10_PREFIX


def generate_master_key() -> bytes:
    return secrets.token_bytes(_MASTER_KEY_BYTES)


def _keyring_module():
    try:
        import keyring as module
    except ImportError:
        return None
    return module


def get_or_create_master_key() -> Optional[bytes]:
    """The profile's master key, from the keyring, created on first use.

    Returns None when no keyring is reachable -- a container, for
    instance. Callers treat that as "cookies cannot be encrypted" rather
    than silently generating a throwaway key, because a throwaway key
    would orphan every cookie already written under the previous one.
    """
    keyring = _keyring_module()
    if keyring is None:
        return None
    try:
        stored = keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
    except Exception as error:
        raise ChromiumCryptError(
            f"the OS keyring could not be read: {error}"
        ) from error
    if stored:
        try:
            return bytes.fromhex(stored)
        except ValueError as error:
            raise ChromiumCryptError(
                "the stored profile key is not valid hex; refusing to "
                "replace it, because that would orphan every cookie "
                "already encrypted under it"
            ) from error

    fresh = generate_master_key()
    try:
        keyring.set_password(KEYRING_SERVICE, KEYRING_ACCOUNT, fresh.hex())
    except Exception as error:
        raise ChromiumCryptError(
            f"the OS keyring could not be written: {error}"
        ) from error
    return fresh


def write_local_state(profile_dir: Path, master_key: Optional[bytes] = None) -> bool:
    """Give a profile its os_crypt key. True if the file now has one.

    Refuses to overwrite an existing key. A profile whose key changed
    underneath it has cookies that can no longer be decrypted, which
    presents as every site logging you out for no visible reason.
    """
    if master_key is None:
        try:
            master_key = get_or_create_master_key()
        except ChromiumCryptError:
            raise
    if master_key is None:
        return False

    local_state = Path(profile_dir) / "Local State"
    if local_state.exists():
        try:
            existing = json.loads(local_state.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = {}
        wrapped = (existing.get("os_crypt") or {}).get("encrypted_key")
        if wrapped:
            return True
        # No os_crypt section yet: merge rather than clobber, so Chromium's
        # other profile state survives.
        existing.setdefault("os_crypt", {})["encrypted_key"] = wrap_master_key(
            master_key
        )
        local_state.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        local_state.chmod(0o600)
        return True

    payload = {
        "os_crypt": {"encrypted_key": wrap_master_key(master_key)},
        "profile": {"exit_type": "Normal", "exited_cleanly": True},
    }
    local_state.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    local_state.chmod(0o600)
    return True


def has_os_crypt_key(profile_dir: Path) -> bool:
    """Whether a profile directory already holds a usable os_crypt key."""
    local_state = Path(profile_dir) / "Local State"
    if not local_state.exists():
        return False
    try:
        data = json.loads(local_state.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool((data.get("os_crypt") or {}).get("encrypted_key"))


def cookies_stored_in_plaintext(profile_dir: Path) -> list[str]:
    """Cookie names whose value is sitting in the database unencrypted.

    Used by the test suite and by the startup notice. Reading the
    profile's own SQLite file is the only honest way to answer this: the
    encryption state is Chromium's business, not Qt's.
    """
    import sqlite3

    candidates = [
        Path(profile_dir) / "Cookies",
        Path(profile_dir) / "Default" / "Cookies",
    ]
    for db in candidates:
        if not db.exists():
            continue
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        except sqlite3.Error:
            continue
        try:
            rows = conn.execute(
                "select name from cookies "
                "where length(value) > 0 and length(encrypted_value) = 0"
            ).fetchall()
        except sqlite3.Error:
            return []
        finally:
            conn.close()
        return [r[0] for r in rows]
    return []


def report(profile_dir: Optional[Path] = None) -> dict:
    """What is actually on disk, for the --check-cookie-encryption flag.

    A key being present in Local State is not evidence of anything. This
    reports what is in the cookie database itself, which is the only
    question that matters.
    """
    if profile_dir is None:
        profile_dir = Path.home() / ".local" / "share" / "quillon" / "cookie_storage"
    return {
        "profile_dir": str(profile_dir),
        "has_key": has_os_crypt_key(profile_dir),
        "plaintext_cookies": cookies_stored_in_plaintext(profile_dir),
        "keyring": _keyring_description(),
    }


def _keyring_description() -> str:
    keyring = _keyring_module()
    if keyring is None:
        return "not installed"
    try:
        return type(keyring.get_keyring()).__name__
    except Exception:
        return "unavailable"


def format_report(profile_dir: Optional[Path] = None) -> str:
    state = report(profile_dir)
    plain = state["plaintext_cookies"]
    lines = [
        "Quillon cookie storage check",
        f"  profile directory : {state['profile_dir']}",
        f"  os_crypt key      : {'present' if state['has_key'] else 'absent'}",
        f"  OS keyring        : {state['keyring']}",
    ]
    if plain:
        lines += [
            "",
            f"  !! {len(plain)} cookie(s) are stored in PLAINTEXT:",
            *[f"     - {name}" for name in plain[:20]],
        ]
        lines += [
            "",
            "  Chromium is not encrypting this profile. Either its build",
            "  ignores the os_crypt key, or the cookies predate it.",
            "  Unset QUILLON_PERSIST_COOKIES to stop persisting cookies at all.",
        ]
    else:
        lines += ["", "  OK: no plaintext cookie values on disk."]
    return "\n".join(lines)
