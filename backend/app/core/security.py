from __future__ import annotations

import base64
import hmac
import time
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import settings

# 固定盐：本地单用户场景可接受；强度来自 master/secret 本身的随机性
_STATIC_SALT = b"mytokentransfer-static-salt"


@lru_cache(maxsize=1)
def _get_fernet() -> Fernet:
    """返回 Fernet 实例。优先用 ENCRYPTION_KEY；未设置则由 SECRET_KEY 经 PBKDF2 派生。"""
    if settings.ENCRYPTION_KEY:
        return Fernet(settings.ENCRYPTION_KEY.encode())
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_STATIC_SALT,
        iterations=200_000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(settings.SECRET_KEY.encode()))
    return Fernet(key)


def encrypt_value(plaintext: str) -> str:
    """加密明文密钥，返回密文 token。"""
    if not plaintext:
        return ""
    return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_value(token: str) -> str:
    """解密密文，返回明文。无效时抛 InvalidToken。"""
    if not token:
        return ""
    return _get_fernet().decrypt(token.encode("utf-8")).decode("utf-8")


def mask_key(raw: str) -> str:
    """生成掩码展示串，如 sk-…ab3f。"""
    raw = (raw or "").strip()
    if not raw:
        return "…"
    if len(raw) <= 8:
        return raw[0] + "***"
    return raw[:3] + "…" + raw[-4:]


def verify_master_password(password: str) -> bool:
    """常量时间比较登录密码与配置的主密码。"""
    return hmac.compare_digest(password or "", settings.MASTER_PASSWORD)


# ---- session token ----

_serializer = URLSafeTimedSerializer(settings.SECRET_KEY, salt="auth")


def create_session_token() -> str:
    """签发登录态 token（带过期）。"""
    return _serializer.dumps({"sub": "admin", "iat": int(time.time())})


def verify_session_token(token: str) -> dict | None:
    """校验 token；过期或非法返回 None。"""
    try:
        return _serializer.loads(token, max_age=settings.SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


__all__ = [
    "encrypt_value",
    "decrypt_value",
    "mask_key",
    "verify_master_password",
    "create_session_token",
    "verify_session_token",
    "InvalidToken",
]
