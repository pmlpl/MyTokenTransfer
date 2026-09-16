# 架构

> 一句话定位：把 N 个上游账号的 API Key 收成一个池，对外只暴露**一个 OpenAI 兼容地址 + 一个令牌**；同时把「选哪把 key、失败怎么办、花了多少钱」三件事各自独立成层。

相关页面：[scheduling.md](scheduling.md)（调度细节）· [gateway-api.md](gateway-api.md)（对外契约）· [billing-and-usage.md](billing-and-usage.md)（计费）

---

## 1. 整体拓扑

```
                    ┌────────────────────────────────────────────┐
   任意 OpenAI      │        管理台（Vue3 + Naive UI + ECharts）    │
   兼容客户端   ──┐  │  密钥 / 供应商 / 通道 / Playground / 日志 /   │
   Cline Cursor  │  │  用量 / 定价 / 告警 / 设置                   │
   Continue      │  └───────────────────┬────────────────────────┘
   Dify LangChain│                      │ Authorization: Bearer <session-token>
   Agent         │                      │ 路径 /api/*  (nginx 去掉 /api 前缀)
                 │                      │
                 ▼                      ▼
        ┌───────────────────────────────────────────────────────┐
        │        frontend 容器 = nginx（对外唯一入口）             │
        │  /api/*  → rewrite 去前缀 → backend:8082               │
        │  /v1/*  → 原样转发        → backend:8082               │
        │  /*     → try_files 兜底 → /index.html (SPA 路由)      │
        │  SSE 透传：proxy_buffering off, timeout 900s           │
        └───────────────────────┬───────────────────────────────┘
                                │ Authorization: Bearer <GATEWAY_TOKEN>
                                ▼
        ┌───────────────────────────────────────────────────────┐
        │              FastAPI backend（容器内 uvicorn :8082）     │
        │                                                       │
        │  api/relay.py     路由 + 通道查找 + 生图自动分发         │
        │  relay/gateway.py HTTP 编排：构造尝试序列、透传、切 key   │
        │  relay/balancer.py 选 key / 状态机 / Tier 兜底（不碰 HTTP）│
        │  relay/token_bucket.py  事前限速（无令牌 → 换 key）       │
        │  relay/accounting.py    计费 + 写 CallLog（独立会话）      │
        │  relay/errors.py        上游错误分类                      │
        │  relay/logging_middleware.py  /v1/* 结构化摘要日志        │
        │  core/scheduler.py      APScheduler 定时任务              │
        │  services/*             health_check / alerts / usage    │
        └──────┬──────────────────────────────┬─────────────────┘
               │ SQLAlchemy 2.0 async          │ httpx.AsyncClient
               ▼                              ▼
        ┌──────────────┐            ┌──────────────────────────┐
        │  MySQL 8.0   │            │ 上游 OpenAI 兼容端点        │
        │  12 张表      │            │ （多中转商 / 官方 API）     │
        └──────────────┘            └──────────────────────────┘
        ┌──────────────┐
        │  Redis 7     │  ← compose 已起服务；应用代码当前未接线
        └──────────────┘
```

### 为什么是「nginx 在前」

三个理由，都不是为了好看：

1. **同域**：管理台前端和 `/v1/*` 网关同源，前端不用配 CORS，浏览器也不用处理预检。
2. **SSE 友好**：`proxy_buffering off` + `chunked_transfer_encoding on` + `proxy_read_timeout 900s`，流式响应能逐块透传到客户端，而不是等完整响应再吐。
3. **不暴露 8082**：backend 用 `expose` 而非 `ports`，宿主机上只有一个入口，减少暴露面。

## 2. 一次请求的完整链路

以 `POST /v1/chat/completions` 为例（`stream=false` 与 `stream=true` 的差异只在响应阶段）：

| 步 | 位置 | 做什么 |
| :---: | --- | --- |
| 1 | nginx | 匹配 `/v1/`，原样转发到 `backend:8082`，关闭 buffering |
| 2 | `relay/logging_middleware.py` | 记请求摘要（结构化日志），含决策点 |
| 3 | `core/deps.py: require_gateway` | `hmac.compare_digest` 校验 `Bearer <GATEWAY_TOKEN>`；失败 401 |
| 4 | `api/relay.py: chat_completions` | 解析 JSON、取 `model`；`model` 即**虚拟模型名** |
| 5 | 同上 | 查 `channels` 表：`virtual_model == model AND is_active`，找不到 404 |
| 6 | 同上 | 判断是否生图渠道（模型名/渠道名含 `image/img/t2i/dall-e/sora/flux/midjourney`）→ 是则转 `route_image_generations(..., wrap_as_chat=True)` |
| 7 | `relay/gateway.py` | 拉通道的 Tier 结构 + 模型映射，调 balancer 生成有序 `(tier, key)` 尝试序列 |
| 8 | `relay/token_bucket.py` | 对 `(key, 原生模型)` 取令牌；取不到就换下一把 key（**不会硬失败**） |
| 9 | `relay/gateway.py` | httpx 直连上游；请求体里的 `model` 换成**原生模型名**（经 `model_mappings` 映射） |
| 10 | `relay/errors.py` | 失败时分类：限流 / 额度 / 认证 / 其他 → 交给 balancer 标状态 |
| 11 | `relay/balancer.py` | 按分类标记 key 或 `(key, 模型)` 或整个 tier 冷却，返回下一个候选 |
| 12 | 循环 8–11 | 直到成功、或四级兜底全部用尽 → 503 |
| 13 | `relay/accounting.py` | 用**独立 DB 会话**写 `call_logs`（含 tokens / cost / 延迟 / 状态）—— 独立会话是为了应对「流式响应中途客户端断开、主会话已关闭」 |
| 14 | 响应 | 非流式：直接返回上游 JSON；流式：SSE 逐块透传，结束后补写日志 |

> 关键设计：`balancer` **完全不碰 HTTP**，只做「选谁、标记谁」；`gateway` 负责 HTTP 编排。这个切分（`refactor(relay): 拆 gateway 到 balancer + accounting`）是后来四级兜底能持续演进而没有变成一坨 if-else 的前提。

## 3. 模块职责表

### `backend/app/api/` — 路由层（43 个端点）

| 文件 | 前缀 | 鉴权 | 职责 |
| --- | --- | --- | --- |
| `auth.py` | `/auth` | 登录接口除外 | 登录、当前用户、改密 |
| `keys.py` | `/keys` | `require_auth` | 密钥 CRUD、单测/批测、明文 reveal、加密包导入导出 |
| `providers.py` | `/providers` | `require_auth` | 供应商 CRUD（base_url、探测方式、模型列表） |
| `groups.py` | `/groups` | `require_auth` | 密钥分组 |
| `channels.py` | `/channels` | `require_auth` | 转发通道 CRUD（Tier 结构 + 模型映射）、按渠道测试 |
| `pricing.py` | `/pricing` | `require_auth` | 模型定价 CRUD |
| `logs.py` | `/logs` | `require_auth` | 调用日志分页查询（UTC → 东八区） |
| `alerts.py` | `/alerts` | `require_auth` | 告警列表、手工 resolve |
| `settings.py` | `/settings` | `require_auth` | 只读系统设置（当前只暴露 `gateway_token`） |
| `dashboard.py` | `/stats` | `require_auth` | 概览、用量聚合、密钥×模型状态 |
| `relay.py` | `/v1` | `require_gateway` | 对外网关：chat / embeddings / images / models |

完整清单见 [api-reference.md](api-reference.md)。

### `backend/app/relay/` — 转发核心

| 文件 | 一句话职责 | 备注 |
| --- | --- | --- |
| `gateway.py` | HTTP 编排：构造尝试序列、替换模型名、流式透传、失败切 key | 唯一发上游请求的地方 |
| `balancer.py` | 选 key + 状态机 + Tier 兜底 | 纯逻辑，不碰 HTTP；11643 字符，是项目最核心文件 |
| `token_bucket.py` | 按 `(key, 原生模型)` 事前限速 | 单进程无锁（事件循环内无 await 的算术无竞态） |
| `accounting.py` | 计费 + 写 `call_logs` | 独立会话，抗流式断开 |
| `errors.py` | 上游错误 → 状态分类 | **速率关键词优先于额度关键词**（429 同时含两者时判限流） |
| `logging_middleware.py` | `/v1/*` 结构化摘要日志 | 含决策点，便于事后复盘「为什么选了这个 key」 |

### `backend/app/core/` 与 `services/`

| 文件 | 职责 |
| --- | --- |
| `core/config.py` | `pydantic-settings` 读 `.env`（`extra="ignore"`，读到不认识的键会忽略） |
| `core/deps.py` | 两套鉴权依赖：`require_auth`（管理台会话）/ `require_gateway`（网关令牌） |
| `core/security.py` | Fernet 加解密、会话签名、导出包 PBKDF2 派生 |
| `core/scheduler.py` | APScheduler：10 min 巡检、每日 02:30 rollup、启动回填 60 天 |
| `core/structlog.py` | structlog + stdlib logging 统一配置 |
| `services/health_check.py` | 单 key / 批量（并发 5）/ 按渠道探测；三种探测方式 |
| `services/alerts.py` | 状态劣化发告警、恢复自动 resolve |
| `services/usage.py` | `CallLog` → `DailyUsage` 按日重算（delete + insert，幂等） |
| `services/key.py` | 加密包导出 / 导入 |

## 4. 数据模型（12 张表）

```
providers ──┬─< api_keys >── key_groups
            │      │
            │      └─< key_model_statuses          ← (key, 模型) 细粒度状态
            │      └─< channel_keys >── channel_tiers >── channels
            │                                │
            └─< model_mappings >─────────────┘        ← 虚拟模型 → 原生模型名

channels ──< call_logs ──< daily_usage              ← 明细 + 按日预聚合
model_pricings                                      ← 单价表（含峰谷双档）
alert_events                                        ← 告警事件
```

| 表 | 关键字段 | 说明 |
| --- | --- | --- |
| `providers` | `name`、`slug`（唯一）、`base_url`、`probe_kind`、`models`(JSON)、`is_active` | 一个上游账号体系 |
| `api_keys` | `provider_id`、`group_id`、`account_label`、`name`、`key_value`(**密文**)、`key_mask`、`status`、`balance`、`weight`、`tags`、`last_tested_at`、`rate_limited_until` | 密钥池的成员；明文只存密文 + 掩码 |
| `key_groups` | — | 密钥分组 |
| `channels` | `virtual_model`（**唯一**）、`strategy`、`max_retries`、`is_active`、`note` | 对外暴露的「虚拟模型」 |
| `channel_tiers` | `channel_id`、`order`、`provider_id` | 兜底层级；`order` 升序尝试 |
| `channel_keys` | `tier_id`、`key_id`、`weight`、`priority` | 层内成员及调度权重 |
| `model_mappings` | `virtual_model` + `provider_id` → `native_model` | 跨供应商模型名映射 |
| `call_logs` | `channel_id`、`tier_order`、`provider_id`、`key_id`、`virtual_model`、`native_model`、`prompt/completion/total/cached_tokens`、`cost`、`latency_ms`、`status`、`error`、`stream`、`created_at`(UTC) | 每次调用的明细 |
| `daily_usage` | `day` + `key_id` + `model` → 计数与成本合计 | 用量页读它，不扫明细 |
| `model_pricings` | `input_price`、`cached_input_price`、`output_price`、`off_peak_input_price`、`off_peak_output_price`、`context_length`、`currency` | 单价表 |
| `alert_events` | `type`、`severity`、`resolved` | 告警事件 |
| `key_model_statuses` | `key_id` + `model` → 细粒度状态 | 支撑「只冷却某个模型的积分池」 |

### 密钥状态机（`models/enums.py: KeyStatus`）

| 状态 | 含义 | 谁改它 |
| --- | --- | --- |
| `active` | 可用 | 探测成功、真实流量拿到 200 |
| `rate_limited` | 命中 429，冷却期内跳过 | `balancer`（60 s） |
| `exhausted` | 余额 / 额度用尽 | 错误分类命中 quota 关键词 |
| `invalid` | 认证失败（401 / 403）或密文解不开 | 探测 / gateway |
| `disabled` | 手动停用 | 人工 |

探测方式（`ProbeKind`）：`openai_models`（`GET {base}/v1/models` + Bearer）、`anthropic`（同路径 + `x-api-key` + `anthropic-version`）、`gemini_models`（`GET {base}/v1beta/models?key=`）、`custom`（暂未实现）。

调度策略（`KeyStrategy`）：`round_robin` / `weighted` / `priority`。

## 5. 前端结构

```
frontend/src/
├─ views/            11 个页面（见下表）
├─ design/           设计令牌 + 明暗主题 + 通用组件
│   ├─ tokens.ts
│   ├─ theme-light.ts / theme-dark.ts / use-theme.ts
│   └─ components/   PageHeader / KpiCard / DataCard / EmptyState / StatusDot
├─ api/              client.ts（axios 实例 + 拦截器）/ index.ts（接口封装）
├─ stores/auth.ts    Pinia：登录态
├─ router/index.ts   /login + 主布局下 10 个子路由（无 token 重定向 login）
├─ layouts/MainLayout.vue
└─ main.ts / App.vue
```

| 页面 | 用途 |
| --- | --- |
| `Login` | 用 `MASTER_PASSWORD` 登录 |
| `Dashboard` | 概览 KPI + 密钥×模型细粒度状态 |
| `Keys` | 密钥录入/编辑/批量测试/导入导出/reveal |
| `Providers` | 供应商与模型列表 |
| `Channels` | 转发通道：Tier 结构、策略、模型映射 |
| `Playground` | 网关接入信息（地址 + token）+ 端到端调试 |
| `Logs` | 调用日志明细 |
| `Usage` | 用量统计与趋势（按密钥/模型，近 24 h 波形） |
| `Pricing` | 模型价格表 + 用量 |
| `Alerts` | 告警列表与手工 resolve |
| `Settings` | 系统设置（只读） |

### 前端两套鉴权

`api/client.ts` 里有一个细节值得注意：响应拦截器遇到 401 时会判 URL 是否包含 `/v1/`。**网关调用失败（网关令牌错）不会把管理台会话登出** —— 两套令牌互不影响。

## 6. 已知架构约束

| 约束 | 影响 | 出路 |
| --- | --- | --- |
| 状态全在单进程内存（冷却、令牌桶、`_tier_rate_limited_until`） | **不能多副本部署**；重启丢冷却态；每个副本各选各的 key | 把状态搬到 Redis（compose 里已经起了 Redis，但代码未接线） |
| 无 Redis 接线 | `redis>=5.0` 声明未用；`config.py` 无 `REDIS_URL` 字段 | 同上 |
| 未引入格式翻译层 | 只支持 OpenAI 兼容请求体；原生 Anthropic / Gemini 请求格式需客户端自行转换 | 需要时引入 LiteLLM 或自写转换 |
| 缓存亲和基于消息前缀哈希 | 4096 字符预算；同一 system + 开头消息的会话会聚到同一 key（这是**特性**，用于共享前缀缓存） | 无需改；但要知道会话分布不均属预期 |
| 无 LICENSE | 默认保留所有权利 | 补 `LICENSE` + `pyproject.toml` 的 `license` 字段 |

## 相关页面

- [scheduling.md](scheduling.md) —— 四级兜底与冷却参数（本页第 2 节的第 7–12 步展开）
- [billing-and-usage.md](billing-and-usage.md) —— 第 13 步的成本怎么算
- [deployment.md](deployment.md) —— 拓扑怎么部署起来
- [api-reference.md](api-reference.md) —— 端点清单
