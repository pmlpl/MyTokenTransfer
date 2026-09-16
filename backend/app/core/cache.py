"""Redis 配置热缓存层：减少转发请求的 DB 往返。

缓存 ModelMapping（虚拟模型→原生模型映射，每次转发读 2-6 次）和
ModelPricing（定价表，每次转发读 1 次）——纯配置数据，变更频率低但
读取频率高。写时失效（渠道/定价增删改时主动删 key），TTL 300 秒兜底。

优雅降级：Redis 不可用时所有操作静默跳过，回退到 DB 查询，不影响转发。
测试环境不调 init_redis → client 为 None → cache_get 返回 None → 直连 DB。
"""
from __future__ import annotations

from typing import Any

import structlog

log = structlog.get_logger("cache")

_client: Any = None  # redis.asyncio.Redis | None
_pool: Any = None  # redis.asyncio.ConnectionPool | None

_CACHE_TTL = 300  # 5 分钟安全网；正常靠写时失效


async def init_redis(url: str) -> None:
    """在 lifespan 启动时初始化连接池。Redis 不可用时降级（client 置 None）。"""
    global _client, _pool
    try:
        from redis.asyncio import ConnectionPool, Redis

        _pool = ConnectionPool.from_url(url, decode_responses=True)
        _client = Redis(connection_pool=_pool)
        await _client.ping()
        log.info("redis_connected")
    except Exception as e:
        log.warning("redis_unavailable", error=str(e))
        _client = None
        _pool = None


async def close_redis() -> None:
    """在 lifespan 关闭时释放连接池。"""
    global _client, _pool
    if _client:
        try:
            await _client.aclose()
        except Exception:
            pass
    if _pool:
        try:
            await _pool.disconnect()
        except Exception:
            pass
    _client = None
    _pool = None


def _get_client() -> Any:
    return _client


async def cache_get(key: str) -> str | None:
    """读缓存，Redis 不可用或 key 不存在返回 None。"""
    c = _get_client()
    if not c:
        return None
    try:
        return await c.get(key)
    except Exception as e:
        log.warning("cache_get_failed", key=key, error=str(e))
        return None


async def cache_set(key: str, value: str, ttl: int = _CACHE_TTL) -> None:
    """写缓存，Redis 不可用时静默跳过。"""
    c = _get_client()
    if not c:
        return
    try:
        await c.setex(key, ttl, value)
    except Exception as e:
        log.warning("cache_set_failed", key=key, error=str(e))


async def cache_delete(*keys: str) -> None:
    """删缓存，Redis 不可用时静默跳过。"""
    c = _get_client()
    if not c or not keys:
        return
    try:
        await c.delete(*keys)
    except Exception as e:
        log.warning("cache_delete_failed", keys=keys, error=str(e))


async def cache_scan_delete(pattern: str) -> None:
    """删除匹配 pattern 的所有 key（如 mm:glm-5.2:*）。"""
    c = _get_client()
    if not c:
        return
    try:
        batch: list[str] = []
        async for key in c.scan_iter(match=pattern, count=100):
            batch.append(key)
            if len(batch) >= 100:
                await c.delete(*batch)
                batch.clear()
        if batch:
            await c.delete(*batch)
    except Exception as e:
        log.warning("cache_scan_delete_failed", pattern=pattern, error=str(e))
