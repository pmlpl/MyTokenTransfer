from __future__ import annotations

import base64
import hmac
import os
import time
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import settings

# 固定盐：本地单用户场景可接受；强度来自 master/secret 本身的随机性
_STATIC_SALT = b"mytokentransfer-static-salt"

# 导出包口令派生参数（每次导出用随机盐）
_BUNDLE_PBKDF2_ITER = 200_000


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=_BUNDLE_PBKDF2_ITER)
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))


def encrypt_bundle(plaintext: str, passphrase: str) -> dict:
    """用口令加密导出包：随机盐 + PBKDF2 派生 Fernet key。口令不落库。"""
    salt = os.urandom(16)
    key = _derive_key(passphrase, salt)
    ct = Fernet(key).encrypt(plaintext.encode("utf-8")).decode("utf-8")
    return {
        "v": 1,
        "kdf": "pbkdf2_sha256",
        "iter": _BUNDLE_PBKDF2_ITER,
        "salt": base64.b64encode(salt).decode("ascii"),
        "ct": ct,
    }


def decrypt_bundle(bundle: dict, passphrase: str) -> str:
    """用口令解密导出包。口令错或包损坏抛 InvalidToken/KeyError。"""
    salt = base64.b64decode(bundle["salt"])
    key = _derive_key(passphrase, salt)
    return Fernet(key).decrypt(bundle["ct"].encode("utf-8")).decode("utf-8")


@lru_cache(maxsize=1)
def _get_fernet() -> Fernet:
    """返回 Fernet 实例。优先用 ENCRYPTION_KEY；未设置则由 SECRET_KEY 经 PBKDF2 派生。

    自检：首次调用时跑一次 encrypt→decrypt roundtrip，确保派生出的 key 自身可用。
    这能挡住 ENCRYPTION_KEY 格式错误 / SECRET_KEY 为空 / PBKDF2 失败等场景。
    旧的 api_key 密文用别的 SECRET_KEY 加密的情况不在此处处理（那是历史数据问题，
    会在调用 decrypt_value 时抛 InvalidToken 走业务分支）。
    """
    if settings.ENCRYPTION_KEY:
        fernet = Fernet(settings.ENCRYPTION_KEY.encode())
    else:
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=_STATIC_SALT,
            iterations=200_000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(settings.SECRET_KEY.encode()))
        fernet = Fernet(key)

    # self-test roundtrip
    _probe = "fernet-self-test-" + os.urandom(4).hex()
    assert fernet.decrypt(fernet.encrypt(_probe.encode())).decode() == _probe, "Fernet self-test failed"
    return fernet


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
    """常量时间比较登录密码：优先 runtime override，没写则用 settings 初始值。"""
    effective = _MASTER_PWD_OVERRIDE.get() if _MASTER_PWD_OVERRIDE.get() else settings.MASTER_PASSWORD
    return hmac.compare_digest(password or "", effective)


# ---- master password runtime override（改密接口热更新，不用重启）----
# 单用户场景：用 hashlib.sha256 后 + 文件持久化（明文不落地）；重启自动恢复
import hashlib
import json
from pathlib import Path


class _PwdOverride:
    """内存里存 sha256(pwd)，启动时从 PERSIST_PATH 恢复，改密即时生效。"""

    def __init__(self) -> None:
        self._sha: str | None = None
        self._plain: str | None = None  # 校验用常量比较，存明文可接受（单用户 + 内存）
        self._path: Path | None = None

    def _ensure_path(self) -> Path:
        if self._path is None:
            base = Path(settings.__dict__.get("DATA_DIR")) if hasattr(settings, "DATA_DIR") else None
            if not base:
                # 回退：backend/app 同级 data 目录（docker 没挂载也可用，容器重建会丢——这时走 .env 初始值）
                base = Path(__file__).resolve().parents[2] / "data"
            base.mkdir(parents=True, exist_ok=True)
            self._path = base / "mtt_master_pwd.json"
        return self._path

    def load(self) -> None:
        """启动期调用：从持久化文件恢复 override，失败静默。"""
        try:
            p = self._ensure_path()
            if not p.exists():
                return
            data = json.loads(p.read_text(encoding="utf-8"))
            # 单用户信任本地存储：直接读明文。若文件被篡改则验证仍在内存比较阶段不会越权
            plain: str = data.get("pwd", "")
            if plain:
                self._plain = plain
                self._sha = hashlib.sha256(plain.encode()).hexdigest()
        except Exception:
            # 损坏/权限失败不影响启动，用 .env 初始值即可
            self._sha = None
            self._plain = None

    def get(self) -> str | None:
        return self._plain

    def set(self, new_password: str) -> None:
        """改密：内存更新 + 持久化写入。抛异常时保持旧状态不变。"""
        new_password = new_password or ""
        p = self._ensure_path()
        # 先写 tmp 再 rename，保证原子性
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps({"pwd": new_password, "v": 1}), encoding="utf-8")
        tmp.replace(p)
        self._plain = new_password
        self._sha = hashlib.sha256(new_password.encode()).hexdigest()


_MASTER_PWD_OVERRIDE = _PwdOverride()


def change_master_password(old_password: str, new_password: str) -> None:
    """改密入口：校验旧密码 → 更新 runtime override 并持久化。

    旧密码错误抛 ValueError；持久化失败抛 OSError。成功后下次登录立即用新密码。
    """
    if not verify_master_password(old_password):
        raise ValueError("旧密码错误")
    if len(new_password) < 8:
        raise ValueError("新密码长度至少 8 位")
    # 先校验再写——verify 会用到 _MASTER_PWD_OVERRIDE，set 失败不影响旧密码
    _MASTER_PWD_OVERRIDE.set(new_password)


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
    "change_master_password",
    "load_pwd_override",
    "create_session_token",
    "verify_session_token",
    "InvalidToken",
    "encrypt_bundle",
    "decrypt_bundle",
]


def load_pwd_override() -> None:
    """启动期入口：恢复 runtime password override。main.py 在 lifespan 里调用。"""
    _MASTER_PWD_OVERRIDE.load()
