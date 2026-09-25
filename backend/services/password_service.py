"""Argon2 password hashing — the OWASP-recommended algorithm for new
projects (memory-hard, resistant to GPU/ASIC cracking, unlike bcrypt/PBKDF2).

Deliberately just two functions wrapping argon2-cffi's high-level API rather
than hand-rolling parameters — its defaults already follow the RFC 9106
"first recommended" profile.
"""

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False
