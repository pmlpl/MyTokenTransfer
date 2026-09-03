from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import alerts, auth, channels, dashboard, groups, keys, logs, pricing, providers, relay
from app.api import settings as settings_api
from app.config import settings
from app.core.scheduler import shutdown_scheduler, start_scheduler
from app.core.structlog import configure_logging
from app.relay.logging_middleware import RelayLoggingMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()  # 统一结构化日志（structlog + stdlib）
    from app.core.security import load_pwd_override
    load_pwd_override()  # 恢复 runtime 主密码 override（改密接口写入）
    start_scheduler()  # 定时巡检：自动重测并恢复劣化密钥
    await _scan_undecryptable_keys()  # 启动时扫一遍，警告用旧 SECRET_KEY 加密的历史数据
    yield
    shutdown_scheduler()


async def _scan_undecryptable_keys() -> None:
    """启动期扫描：若 DB 中存在用当前 SECRET_KEY 解不开的 api_key 密文，打 WARNING。

    触发场景：历史部署用过别的 SECRET_KEY、.env 被错填为占位值等。
    不阻断启动（业务里会逐条用 InvalidToken 走 invalid 状态），但要让运维第一时间看到。
    """
    import logging
    from sqlalchemy import select
    from app.core.security import decrypt_value
    from app.database import AsyncSessionLocal
    from app.models import ApiKey

    log = logging.getLogger("startup")
    try:
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(select(ApiKey.id, ApiKey.key_value))).all()
    except Exception as e:
        log.warning("undecryptable scan skipped: cannot reach DB (%s)", e)
        return

    bad: list[int] = []
    for kid, ct in rows:
        if not ct:
            continue
        try:
            decrypt_value(ct)
        except Exception:
            bad.append(kid)
    if bad:
        log.warning(
            "found %d api_key(s) that cannot be decrypted with current SECRET_KEY: %s "
            "—— 历史数据需要删除后重新添加（明文已不可恢复）",
            len(bad), bad,
        )
    else:
        log.info("all %d api_key ciphertexts decryptable with current SECRET_KEY", len(rows))


app = FastAPI(
    title="MyTokenTransfer",
    version="0.1.0",
    description="API 密钥管理台 + OpenAI 兼容转发网关",
    lifespan=lifespan,
)

app.add_middleware(RelayLoggingMiddleware)  # /v1/* 请求结构化摘要
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(providers.router)
app.include_router(groups.router)
app.include_router(keys.router)
app.include_router(channels.router)
app.include_router(settings_api.router)
app.include_router(pricing.router)
app.include_router(logs.router)
app.include_router(relay.router)
app.include_router(alerts.router)
