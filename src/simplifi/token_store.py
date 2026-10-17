"""Atomic, current-Windows-user DPAPI token storage. No plaintext fallback."""

from __future__ import annotations

import ctypes
import os
import tempfile
from ctypes import wintypes
from pathlib import Path

from simplifi.errors import AuthError
from simplifi.models import TokenSet


def default_token_path() -> Path:
    override = os.environ.get("SIMPLIFI_TOKEN_PATH")
    return (
        Path(override)
        if override
        else Path(__file__).resolve().parents[2] / ".local" / "tokens.dpapi"
    )


def _crypt(data: bytes, *, decrypt: bool = False) -> bytes:
    if os.name != "nt":
        raise AuthError("Token storage requires Windows DPAPI; no plaintext fallback")

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    function.argtypes = [
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(Blob),
    ]
    function.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise AuthError("Windows could not protect/unprotect the token cache")
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel32.LocalFree(result.pbData)


class TokenStore:
    def __init__(self, path: Path | None = None):
        self.path = path or default_token_path()

    def load(self) -> TokenSet | None:
        if not self.path.exists():
            return None
        try:
            return TokenSet.model_validate_json(_crypt(self.path.read_bytes(), decrypt=True))
        except (AuthError, ValueError, OSError):
            raise AuthError(
                "Token cache could not be read; run logout then login locally"
            ) from None

    def save(self, tokens: TokenSet) -> None:
        encrypted = _crypt(tokens.model_dump_json().encode())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary and temporary.exists():
                temporary.unlink()

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)
