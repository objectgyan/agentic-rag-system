"""JWT token creation, verification, and password hashing.

PyJWT replaces python-jose (unmaintained, with open CVEs), and bcrypt is called directly instead
of through passlib (unmaintained, and broken by bcrypt>=4). Existing password hashes and tokens
remain valid: both are the same formats (bcrypt ``$2b$`` hashes, HS256 JWTs).
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt

from app.core.config import settings

# bcrypt only reads the first 72 bytes. passlib truncated silently; bcrypt>=5 raises instead.
# Truncating explicitly keeps every hash created under passlib verifiable.
_BCRYPT_MAX_BYTES = 72


def _pw_bytes(password: str) -> bytes:
    return password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_pw_bytes(password), bcrypt.gensalt()).decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_pw_bytes(plain), hashed.encode("ascii"))
    except (ValueError, TypeError):  # malformed / non-bcrypt hash
        return False


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.jwt_access_token_expire_minutes)
    )
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_refresh_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=settings.jwt_refresh_token_expire_days)
    to_encode.update({"exp": expire, "type": "refresh"})
    return jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        return payload
    except jwt.PyJWTError:
        return None
