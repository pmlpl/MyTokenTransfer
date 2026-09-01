"""structlog 全局配置。

输出 JSON 行：每条日志带结构化字段（channel_id/key_id/status 等），
终端可读、且可用 grep/jq 按字段排查（如 grep '"key_id": 5'）。
"""
from __future__ import annotations

import logging

import structlog


def configure_logging() -> None:
    """在应用启动时调一次，统一 structlog 与 stdlib 日志口径。"""
    # stdlib（uvicorn 等）走 structlog 的处理器，保证格式一致
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # 让标准库 logging 也经同一套处理器输出
    handler = logging.StreamHandler()
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=[
                structlog.processors.TimeStamper(fmt="iso", utc=True),
                structlog.processors.add_log_level,
            ],
            processors=[structlog.processors.JSONRenderer()],
        )
    )
    root = logging.getLogger()
    # 避免重复添加 handler（reload 时）
    if not any(isinstance(h, logging.StreamHandler) and getattr(h, "_mtt", False) for h in root.handlers):
        handler._mtt = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    root.setLevel(logging.INFO)
