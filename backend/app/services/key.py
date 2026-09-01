"""密钥导出/导入：加密包备份与迁移。

导出包用单独的口令(用户在导出时输入)经 PBKDF2 派生 Fernet key 加密，
口令不落库——即使包文件泄露，没有口令也无法还原明文密钥。
导入时按 provider.slug / group.name / key(provider+name) 调谐，
重名跳过、缺失补建，明文重新用本地主 Fernet 加密落库。
"""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decrypt_bundle, decrypt_value, encrypt_bundle, encrypt_value, mask_key
from app.models import ApiKey, KeyGroup, KeyStatus, Provider
from app.models.enums import ProbeKind

_BUNDLE_VERSION = 1


async def build_export_bundle(db: AsyncSession, passphrase: str) -> dict:
    """收集全部供应商/分组/密钥，明文解出 key_value，加密成导出包。"""
    providers = list((await db.execute(select(Provider).order_by(Provider.id))).scalars())
    groups = list((await db.execute(select(KeyGroup).order_by(KeyGroup.id))).scalars())
    keys = list((await db.execute(select(ApiKey).order_by(ApiKey.id))).scalars())

    slug_by_id = {p.id: p.slug for p in providers}
    gname_by_id = {g.id: g.name for g in groups}

    payload = {
        "version": _BUNDLE_VERSION,
        "providers": [
            {
                "slug": p.slug,
                "name": p.name,
                "base_url": p.base_url,
                "probe_kind": p.probe_kind.value if p.probe_kind else "openai_models",
                "models": p.models,
                "is_active": p.is_active,
                "note": p.note,
            }
            for p in providers
        ],
        "groups": [{"name": g.name, "description": g.description} for g in groups],
        "keys": [
            {
                "provider_slug": slug_by_id.get(k.provider_id),
                "group_name": gname_by_id.get(k.group_id) if k.group_id else None,
                "name": k.name,
                "account_label": k.account_label,
                "key_value": decrypt_value(k.key_value),  # 解出明文
                "weight": k.weight,
                "tags": k.tags,
            }
            for k in keys
        ],
    }
    return encrypt_bundle(json.dumps(payload, ensure_ascii=False), passphrase)


async def import_bundle(db: AsyncSession, bundle: dict, passphrase: str) -> dict:
    """解包并调谐导入。返回 {providers, groups, keys_imported, keys_skipped}。"""
    plaintext = decrypt_bundle(bundle, passphrase)
    payload = json.loads(plaintext)

    stats = {"providers": 0, "groups": 0, "keys_imported": 0, "keys_skipped": 0}

    # 供应商：按 slug 调谐，缺失则补建
    slug_to_provider: dict[str, Provider] = {}
    for p in payload.get("providers", []):
        prov = await db.scalar(select(Provider).where(Provider.slug == p["slug"]))
        if not prov:
            try:
                probe = ProbeKind(p.get("probe_kind", "openai_models"))
            except ValueError:
                probe = ProbeKind.openai_models
            prov = Provider(
                slug=p["slug"],
                name=p["name"],
                base_url=p.get("base_url"),
                probe_kind=probe,
                models=p.get("models"),
                is_active=p.get("is_active", True),
                note=p.get("note"),
            )
            db.add(prov)
            await db.flush()
            stats["providers"] += 1
        slug_to_provider[prov.slug] = prov

    # 分组：按 name 调谐
    name_to_group: dict[str, KeyGroup] = {}
    for g in payload.get("groups", []):
        grp = await db.scalar(select(KeyGroup).where(KeyGroup.name == g["name"]))
        if not grp:
            grp = KeyGroup(name=g["name"], description=g.get("description"))
            db.add(grp)
            await db.flush()
            stats["groups"] += 1
        name_to_group[grp.name] = grp

    # 密钥：按 (provider_id, name) 去重，重名跳过
    for k in payload.get("keys", []):
        prov = slug_to_provider.get(k.get("provider_slug"))
        if not prov:
            stats["keys_skipped"] += 1
            continue
        existing = await db.scalar(
            select(ApiKey).where(ApiKey.provider_id == prov.id, ApiKey.name == k["name"])
        )
        if existing:
            stats["keys_skipped"] += 1
            continue
        plaintext_key = k.get("key_value") or ""
        grp = name_to_group.get(k.get("group_name")) if k.get("group_name") else None
        db.add(
            ApiKey(
                provider_id=prov.id,
                group_id=grp.id if grp else None,
                account_label=k.get("account_label"),
                name=k["name"],
                key_value=encrypt_value(plaintext_key),
                key_mask=mask_key(plaintext_key),
                weight=k.get("weight", 1),
                tags=k.get("tags"),
                status=KeyStatus.active,
            )
        )
        stats["keys_imported"] += 1

    await db.commit()
    return stats
