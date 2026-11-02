"""测试公共夹具。

用内存 SQLite 跑，避免依赖 MySQL。测试自建 sqlite 引擎，不触碰 app.database
的模块级 MySQL 引擎（后者惰性创建、不连库，导入无副作用）。
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base  # noqa: E402
import app.models  # noqa: F401,E402  确保所有模型注册到 Base.metadata


@pytest.fixture(autouse=True)
def _clear_relay_cooldowns():
    """清空中继层的进程内状态（账号冷却 / 退避阶梯 / 模型池 / TPM 预算）。

    这些都挂在模块级 dict 上、生命周期跨用例，不清理会串扰：
    A 用例标的冷却会让 B 用例的调度悄悄走兜底分支；
    A 用例消耗的 TPM 预算会让 B 用例的 tpm_reserve 意外被拒。
    """
    from app.relay import balancer, token_bucket

    balancer._account_rate_limited_until.clear()
    balancer._model_quota_limited_until.clear()
    balancer._account_backoff_level.clear()
    token_bucket._buckets.clear()
    token_bucket.reset_tpm_state()
    yield
    balancer._account_rate_limited_until.clear()
    balancer._model_quota_limited_until.clear()
    balancer._account_backoff_level.clear()
    token_bucket._buckets.clear()
    token_bucket.reset_tpm_state()


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    """每测一个全新的内存库，隔离互不影响。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        yield session
    await engine.dispose()
