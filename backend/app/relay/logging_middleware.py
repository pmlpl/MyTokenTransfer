"""转发请求结构化日志中间件。

对 /v1/* 每个请求记一条结构化摘要（method/path/status/latency_ms），
出问题时按字段定位是哪类请求、多久。细到 key_id 的决策日志在 gateway/balancer 内打。
"""
from __future__ import annotations

import time

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

log = structlog.get_logger("relay")


class RelayLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        if not path.startswith("/v1/"):
            return await call_next(request)
        t0 = time.time()
        try:
            response = await call_next(request)
        except Exception as e:
            latency_ms = int((time.time() - t0) * 1000)
            log.error(
                "relay_request_failed",
                method=request.method,
                path=path,
                latency_ms=latency_ms,
                error=f"{type(e).__name__}: {e}",
            )
            raise
        latency_ms = int((time.time() - t0) * 1000)
        log.info(
            "relay_request",
            method=request.method,
            path=path,
            status=response.status_code,
            latency_ms=latency_ms,
        )
        return response
