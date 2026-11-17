"""Hash e verificação de senhas (PBKDF2-HMAC-SHA256, só biblioteca padrão).

Formato guardado em ``senha_hash``:
    ``pbkdf2_sha256$<iterações>$<salt hex>$<hash hex>``

Valores sem esse prefixo são tratados como senha em texto puro (legado de
desenvolvimento). Ao fazer login com sucesso, o backend troca-os por um hash.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

PREFIX = "pbkdf2_sha256"
ITERATIONS = 200_000


def hash_password(password: str, *, iterations: int = ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{PREFIX}${iterations}${salt.hex()}${digest.hex()}"


def is_hashed(stored: str) -> bool:
    return isinstance(stored, str) and stored.startswith(PREFIX + "$")


def verify_password(password: str, stored: str) -> bool:
    """Compara em tempo constante; aceita hash PBKDF2 ou texto puro legado."""
    if not isinstance(stored, str) or not stored:
        return False
    if not is_hashed(stored):
        return hmac.compare_digest(password.encode("utf-8"), stored.encode("utf-8"))
    try:
        _, iterations, salt_hex, digest_hex = stored.split("$")
        expected = bytes.fromhex(digest_hex)
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, expected)
