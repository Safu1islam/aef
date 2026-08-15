"""Windows DPAPI binding (T-022, DR-008).

One job: turn bytes into user-bound ciphertext and back. Kept out of
``credentials.py`` so the store stays a store — the platform call has a single
home, and the swap DR-008 describes is a backend choice rather than a rewrite of
the thing that holds secrets.

DPAPI encrypts under a key derived from the *user profile*, so ciphertext copied
to another Windows account cannot be decrypted there. That is the whole of what
this buys, and it is worth being precise about what it does not buy: an attacker
running code AS the operator, in the operator's live session, can call
CryptUnprotectData exactly as this module does. On a single-user machine the
exposure delta over the plaintext file is therefore narrower than "encrypted"
suggests. It closes the copied-file case — a backup, a synced folder, a stolen
disk read from another account — which is the case DR-008 actually cares about,
because the same document is what keeps credentials out of every backup artefact.

``CRYPTPROTECT_LOCAL_MACHINE`` is deliberately NOT used: it would bind the
ciphertext to the machine rather than the user, and any account on the box could
then decrypt it. That flag is how this control gets silently downgraded to
nothing, so its absence is asserted by tests/test_dpapi.py.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

# Entropy mixed into the key derivation. Not a secret and not a password: it
# scopes the ciphertext to this application, so a blob produced here cannot be
# decrypted by another program running as the same user without also knowing it.
# A constant is the correct shape — it is a domain separator, not key material.
ENTROPY = b"ProMedia/credential-store/v1"


class DpapiUnavailable(RuntimeError):
    """This platform has no DPAPI. Raised rather than silently falling back.

    A backend that quietly degraded to plaintext when the OS call was missing
    would be the worst possible failure: the store would report itself as
    encrypted while writing secrets in the clear.
    """


def available() -> bool:
    return sys.platform == "win32"


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> _Blob:
    buffer = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))


def _extract(blob: _Blob) -> bytes:
    out = ctypes.string_at(blob.pbData, blob.cbData)
    # The API allocates; not freeing it leaks for the life of the process.
    ctypes.WinDLL("kernel32", use_last_error=True).LocalFree(blob.pbData)
    return out


def _call(name: str, data: bytes) -> bytes:
    """CryptProtectData / CryptUnprotectData share this signature exactly.

    Both take (in, description, entropy, reserved, prompt, flags, out); the
    description slot is an IN for protect and an OUT for unprotect, and passing
    NULL is correct for both. ``flags = 0`` is what withholds
    CRYPTPROTECT_LOCAL_MACHINE — see the module docstring.
    """
    if not available():  # pragma: no cover - exercised only off Windows
        raise DpapiUnavailable(f"DPAPI is a Windows facility; this is {sys.platform}")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    source, entropy, out = _blob(data), _blob(ENTROPY), _Blob()
    ok = getattr(crypt32, name)(
        ctypes.byref(source), None, ctypes.byref(entropy), None, None, 0, ctypes.byref(out)
    )
    if not ok:
        raise OSError(ctypes.get_last_error(), f"DPAPI {name} failed")
    return _extract(out)


def protect(plaintext: str) -> bytes:
    """User-bound ciphertext for ``plaintext``."""
    return _call("CryptProtectData", plaintext.encode("utf-8"))


def unprotect(ciphertext: bytes) -> str:
    """The original string, or OSError if this user cannot decrypt it."""
    return _call("CryptUnprotectData", ciphertext).decode("utf-8")
