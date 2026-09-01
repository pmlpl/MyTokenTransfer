from __future__ import annotations

import enum


class KeyStatus(str, enum.Enum):
    """密钥运行时状态机。调度时跳过非 active 的密钥。"""

    active = "active"                  # 可用
    rate_limited = "rate_limited"      # 命中 429，冷却期内跳过
    exhausted = "exhausted"            # 余额/额度用尽，需人工或定时刷新恢复
    invalid = "invalid"                # 认证失败（401/403），需人工处理
    disabled = "disabled"              # 手动停用


class ProbeKind(str, enum.Enum):
    """各供应商的健康探测方式。"""

    openai_models = "openai_models"    # GET {base}/v1/models, Authorization: Bearer
    anthropic = "anthropic"            # GET {base}/v1/models, x-api-key + anthropic-version
    gemini_models = "gemini_models"    # GET {base}/v1beta/models?key=
    custom = "custom"                  # 自定义（暂不实现）


class KeyStrategy(str, enum.Enum):
    """通道内密钥选择策略（二期用，此处先定义）。"""

    round_robin = "round_robin"
    weighted = "weighted"
    priority = "priority"
