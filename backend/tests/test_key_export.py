"""密钥导出/导入加密包往返测试。"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.core.security import InvalidToken, decrypt_bundle, decrypt_value, encrypt_bundle, encrypt_value
from app.models import ApiKey, KeyGroup, KeyStatus, Provider
from app.models.enums import ProbeKind
from app.services.key import build_export_bundle, import_bundle


def test_bundle_roundtrip():
    bundle = encrypt_bundle('{"hello":"world"}', "pass123")
    assert bundle["v"] == 1
    assert json.loads(decrypt_bundle(bundle, "pass123")) == {"hello": "world"}


def test_bundle_wrong_passphrase_raises():
    bundle = encrypt_bundle("secret", "right")
    with pytest.raises(InvalidToken):
        decrypt_bundle(bundle, "wrong")


async def _seed(db) -> None:
    p = Provider(name="OpenAI", slug="openai", base_url="https://api.openai.com", probe_kind=ProbeKind.openai_models)
    db.add(p)
    await db.flush()
    g = KeyGroup(name="proj-a", description="测试分组")
    db.add(g)
    await db.flush()
    db.add_all([
        ApiKey(provider_id=p.id, group_id=g.id, name="k1", account_label="acct1",
               key_value=encrypt_value("sk-real-AAAA"), key_mask="sk-…AAAA", weight=2, status=KeyStatus.active),
        ApiKey(provider_id=p.id, group_id=None, name="k2", account_label=None,
               key_value=encrypt_value("sk-real-BBBB"), key_mask="sk-…BBBB", weight=1, status=KeyStatus.active),
    ])
    await db.commit()


async def test_export_then_import_into_same_db_skips_duplicates(db_session):
    db = db_session
    await _seed(db)
    bundle = await build_export_bundle(db, "pw")

    stats = await import_bundle(db, bundle, "pw")
    # 已存在同名供应商/分组/密钥 → 全部跳过
    assert stats["keys_imported"] == 0
    assert stats["keys_skipped"] == 2
    # 不应产生重复
    keys = list((await db.execute(select(ApiKey))).scalars())
    assert len(keys) == 2


async def test_export_then_import_into_emptied_db_restores(db_session):
    db = db_session
    await _seed(db)
    bundle = await build_export_bundle(db, "pw")

    # 清空：先删 key，再删 group 和 provider（FK RESTRICT）
    for k in list((await db.execute(select(ApiKey))).scalars()):
        await db.delete(k)
    for g in list((await db.execute(select(KeyGroup))).scalars()):
        await db.delete(g)
    for p in list((await db.execute(select(Provider))).scalars()):
        await db.delete(p)
    await db.commit()

    stats = await import_bundle(db, bundle, "pw")
    assert stats["providers"] == 1
    assert stats["groups"] == 1
    assert stats["keys_imported"] == 2
    assert stats["keys_skipped"] == 0

    keys = list((await db.execute(select(ApiKey).order_by(ApiKey.name))).scalars())
    assert [k.name for k in keys] == ["k1", "k2"]
    # 明文应与原始一致（重新用本地 Fernet 加密，解出比对）
    assert decrypt_value(keys[0].key_value) == "sk-real-AAAA"
    assert decrypt_value(keys[1].key_value) == "sk-real-BBBB"
