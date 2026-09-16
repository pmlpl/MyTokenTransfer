from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # MySQL
    DATABASE_URL: str = "mysql+aiomysql://mtt:mttpass@127.0.0.1:3306/mytokentransfer"

    # 认证
    MASTER_PASSWORD: str = "change-me"
    SECRET_KEY: str = "change-me-secret"
    SESSION_MAX_AGE: int = 7 * 24 * 3600  # 秒，默认 7 天

    # 网关统一访问密钥（客户端用 Bearer <GATEWAY_TOKEN> 调 /v1/*）
    GATEWAY_TOKEN: str = "change-me-gateway"

    # 密钥静态加密（Fernet key），留空则启动时按 SECRET_KEY 派生
    ENCRYPTION_KEY: str = ""

    # 前端地址（CORS）
    CORS_ORIGINS: list[str] = ["http://127.0.0.1:5173", "http://localhost:5173"]

    # Redis（配置热缓存；不接线时优雅降级，回退 DB）
    REDIS_URL: str = "redis://127.0.0.1:6379/0"


settings = Settings()
