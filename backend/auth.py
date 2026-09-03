"""Password hashing and random session tokens. Stdlib only."""

import hashlib
import hmac
import secrets

PBKDF2_ROUNDS = 120_000
COOKIE_NAME = "repertoire_session"
GATE_COOKIE = "repertoire_gate"
PASSWORD_CHARS = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        PBKDF2_ROUNDS,
    )
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
    except ValueError:
        return False
    check = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        PBKDF2_ROUNDS,
    )
    return hmac.compare_digest(check.hex(), digest)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def gate_token(access_code: str) -> str:
    """HttpOnly cookie value proving the invitation code was entered."""
    return hmac.new(access_code.encode("utf-8"), b"repertoire-gate", hashlib.sha256).hexdigest()


def random_password(length: int = 8) -> str:
    return "".join(secrets.choice(PASSWORD_CHARS) for _ in range(length))


def random_otp() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_otp(code: str) -> str:
    """Fast hash for short-lived 6-digit codes (not long-lived passwords)."""
    salt = secrets.token_hex(8)
    digest = hashlib.sha256(f"{salt}:{code}".encode()).hexdigest()
    return f"{salt}${digest}"


def verify_otp(code: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
    except ValueError:
        return False
    check = hashlib.sha256(f"{salt}:{code}".encode()).hexdigest()
    return hmac.compare_digest(check, digest)
