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
    start_scheduler()  # 定时巡检：自动重测并恢复劣化密钥
    yield
    shutdown_scheduler()


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
