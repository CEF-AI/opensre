"""Decode a Polkadot keyring JSON wallet into an ed25519 :class:`Signer`.

Owns only key material: turns a ``wallet.json`` (Polkadot "encrypted JSON" v3 —
scrypt-derived key + xsalsa20-poly1305 secretbox + PKCS8-wrapped ed25519 seed) into
something that can sign vault-api requests. Transport lives in :mod:`app.services.cef.client`.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from nacl.exceptions import CryptoError
from nacl.secret import SecretBox
from nacl.signing import SigningKey

# Polkadot PKCS8 framing for an ed25519 keypair: header + seed(32) + divider + pubkey(32).
_PKCS8_HEADER = bytes([48, 83, 2, 1, 1, 48, 5, 6, 3, 43, 101, 112, 4, 34, 4, 32])
_PKCS8_DIVIDER = bytes([161, 35, 3, 33, 0])
_SCRYPT_HEADER_LEN = 44  # salt(32) + N(4) + p(4) + r(4), little-endian u32s
_NONCE_LEN = 24
_SEED_LEN = 32
_PUBKEY_LEN = 32
_SECRETBOX_KEY_LEN = 32
# Polkadot stores the ed25519 secret as 64 bytes (seed‖pubkey); older wallets used a
# bare 32-byte seed. Try the 64-byte layout first, then fall back — matching decodePkcs8.
_SECRET_LENGTHS = (64, _SEED_LEN)


class WalletSigner:
    """Ed25519 signer backed by a decoded wallet seed (satisfies ``client.Signer``)."""

    def __init__(self, seed: bytes, public_key: bytes) -> None:
        self._signing_key = SigningKey(seed)
        self._public_key = public_key

    @property
    def public_key_hex(self) -> str:
        return "0x" + self._public_key.hex()

    def sign(self, message: bytes) -> bytes:
        return self._signing_key.sign(message).signature


def _derive_scrypt_key(encoded: bytes, password: str) -> tuple[bytes, bytes]:
    """Return (secretbox_key, body_after_header) for a scrypt-encrypted wallet."""
    salt = encoded[0:32]
    n = int.from_bytes(encoded[32:36], "little")
    p = int.from_bytes(encoded[36:40], "little")
    r = int.from_bytes(encoded[40:44], "little")
    # hashlib rejects maxmem == 128*N*r*p, so give it explicit headroom.
    maxmem = 128 * n * r * p * 2
    key = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=maxmem, dklen=_SECRETBOX_KEY_LEN
    )
    return key, encoded[_SCRYPT_HEADER_LEN:]


def _signer_from_pkcs8(pkcs8: bytes) -> WalletSigner:
    if pkcs8[: len(_PKCS8_HEADER)] != _PKCS8_HEADER:
        raise ValueError("unexpected PKCS8 header (not an ed25519 keypair?)")
    secret_start = len(_PKCS8_HEADER)
    for secret_len in _SECRET_LENGTHS:
        divider_start = secret_start + secret_len
        if pkcs8[divider_start : divider_start + len(_PKCS8_DIVIDER)] != _PKCS8_DIVIDER:
            continue
        seed = pkcs8[secret_start : secret_start + _SEED_LEN]  # first 32 bytes are the seed
        pub_start = divider_start + len(_PKCS8_DIVIDER)
        public_key = pkcs8[pub_start : pub_start + _PUBKEY_LEN]
        if bytes(SigningKey(seed).verify_key) != public_key:
            raise ValueError("wallet keypair integrity check failed")
        return WalletSigner(seed, public_key)
    raise ValueError("unexpected PKCS8 divider (unsupported secret-key layout)")


def signer_from_wallet_json(wallet: Mapping[str, Any], password: str) -> WalletSigner:
    """Build a :class:`WalletSigner` from a parsed Polkadot keyring JSON."""
    encoding = wallet.get("encoding") or {}
    content = list(encoding.get("content") or [])
    enc_type = list(encoding.get("type") or [])
    if "ed25519" not in content:
        raise ValueError(f"unsupported key content {content!r}; only ed25519 is supported")

    encoded_b64 = wallet.get("encoded")
    if not isinstance(encoded_b64, str):
        raise ValueError("wallet json missing string 'encoded'")
    encoded = base64.b64decode(encoded_b64)

    if "scrypt" in enc_type:
        key, body = _derive_scrypt_key(encoded, password)
    elif not enc_type or "none" in enc_type:
        key = password.encode("utf-8").ljust(_SECRETBOX_KEY_LEN, b"\0")[:_SECRETBOX_KEY_LEN]
        body = encoded
    else:
        raise ValueError(f"unsupported wallet encoding type {enc_type!r}")

    nonce, ciphertext = body[:_NONCE_LEN], body[_NONCE_LEN:]
    try:
        pkcs8 = SecretBox(key).decrypt(ciphertext, nonce)
    except CryptoError as exc:
        raise ValueError("failed to decrypt wallet (wrong password?)") from exc
    return _signer_from_pkcs8(pkcs8)


def signer_from_file(path: str | Path, password: str) -> WalletSigner:
    """Build a :class:`WalletSigner` from a ``wallet.json`` file path."""
    wallet = json.loads(Path(path).read_text(encoding="utf-8"))
    return signer_from_wallet_json(wallet, password)


def signer_from_material(
    *, wallet_json: str = "", wallet_path: str = "", password: str = ""
) -> WalletSigner:
    """Build a signer from in-memory wallet JSON (preferred) or a wallet file path.

    ``wallet_json`` is the raw keyring-JSON string a microservice caller passes per request, so
    the key never touches disk. Falls back to ``wallet_path`` for the env/CLI path. Raises
    ``ValueError`` if neither is supplied.
    """
    if wallet_json.strip():
        return signer_from_wallet_json(json.loads(wallet_json), password)
    if wallet_path.strip():
        return signer_from_file(wallet_path, password)
    raise ValueError("no wallet material: provide wallet_json or wallet_path")


__all__ = [
    "WalletSigner",
    "signer_from_file",
    "signer_from_material",
    "signer_from_wallet_json",
]
