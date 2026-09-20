"""Production token-protection adapter.

The application must provide a Fernet key from a secret manager or KMS. The
key is never generated, logged, or committed by this module.
"""

from __future__ import annotations


class FernetTokenProtector:
    def __init__(self, key: bytes):
        if not key:
            raise ValueError("token-protection key is required")
        try:
            from cryptography.fernet import Fernet
        except ImportError as error:
            raise RuntimeError(
                "cryptography is required for FernetTokenProtector"
            ) from error
        self._fernet = Fernet(key)

    def seal(self, plaintext: str) -> str:
        if not plaintext:
            raise ValueError("cannot protect an empty token")
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def open(self, ciphertext: str) -> str:
        if not ciphertext:
            raise ValueError("cannot open an empty protected token")
        return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
