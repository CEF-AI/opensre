"""Tests for decoding a Polkadot keyring JSON into an ed25519 signer."""

from __future__ import annotations

import base64
import hashlib
import os
from typing import Any

import pytest
from nacl.secret import SecretBox
from nacl.signing import SigningKey, VerifyKey

from app.services.cef.wallet_signer import (
    _PKCS8_DIVIDER,
    _PKCS8_HEADER,
    signer_from_wallet_json,
)

# Small scrypt cost so the round-trip test stays fast (real wallets use N=32768).
_N, _R, _P = 1024, 8, 1


def _encode_wallet(seed: bytes, password: str, *, secret_len: int = 64) -> dict[str, Any]:
    """Encode a seed as a Polkadot v3 encrypted-JSON wallet (mirrors the decode path).

    ``secret_len=64`` is the real Polkadot layout (secret = seed‖pubkey); ``32`` is legacy.
    """
    public_key = bytes(SigningKey(seed).verify_key)
    secret = (seed + public_key) if secret_len == 64 else seed
    pkcs8 = _PKCS8_HEADER + secret + _PKCS8_DIVIDER + public_key
    salt = os.urandom(32)
    key = hashlib.scrypt(
        password.encode(), salt=salt, n=_N, r=_R, p=_P, maxmem=128 * _N * _R * _P * 2, dklen=32
    )
    nonce = os.urandom(24)
    ciphertext = SecretBox(key).encrypt(pkcs8, nonce).ciphertext
    header = salt + _N.to_bytes(4, "little") + _P.to_bytes(4, "little") + _R.to_bytes(4, "little")
    encoded = base64.b64encode(header + nonce + ciphertext).decode("ascii")
    return {
        "encoded": encoded,
        "encoding": {
            "content": ["pkcs8", "ed25519"],
            "type": ["scrypt", "xsalsa20-poly1305"],
            "version": "3",
        },
        "address": "unused-in-test",
        "meta": {},
    }


@pytest.mark.parametrize("secret_len", [64, 32])
def test_decode_roundtrip_recovers_key_and_signs(secret_len: int) -> None:
    seed = os.urandom(32)
    expected_pub = bytes(SigningKey(seed).verify_key)

    wallet = _encode_wallet(seed, "cef-agents", secret_len=secret_len)
    signer = signer_from_wallet_json(wallet, "cef-agents")

    assert signer.public_key_hex == "0x" + expected_pub.hex()
    message = b"canonical-preamble-bytes"
    VerifyKey(expected_pub).verify(message, signer.sign(message))  # raises on mismatch


def test_wrong_password_raises_clean_error() -> None:
    wallet = _encode_wallet(os.urandom(32), "cef-agents")
    with pytest.raises(ValueError, match="wrong password"):
        signer_from_wallet_json(wallet, "not-the-password")


def test_non_ed25519_content_rejected() -> None:
    wallet = _encode_wallet(os.urandom(32), "cef-agents")
    wallet["encoding"]["content"] = ["pkcs8", "sr25519"]
    with pytest.raises(ValueError, match="only ed25519"):
        signer_from_wallet_json(wallet, "cef-agents")
