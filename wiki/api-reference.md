# 接口参考

> 一句话定位：全部 HTTP 端点的清单速查 —— 共 **43 个业务端点** + `GET /health`。管理台端点走登录态，网关端点走网关令牌，两套鉴权互不影响。

相关页面：[gateway-api.md](gateway-api.md)（`/v1/*` 详细契约）· [architecture.md](architecture.md)（路由分层）

**来源**：`backend/app/api/*.py` + `backend/app/main.py`。此处以**路径与用途**为主；请求/响应字段以 Pydantic schema（`backend/app/schemas/`）与实际代码为准。

---

## 0. 鉴权与路径前缀总览

| 鉴权 | 依赖 | 请求头 | 适用 |
| --- | --- | --- | --- |
| 登录态 | `core/deps.py: require_auth` | `Authorization: Bearer <session-token>` | 所有管理台端点（`/auth/login` 除外） |
| 网关令牌 | `core/deps.py: require_gateway` | `Authorization: Bearer <GATEWAY_TOKEN>` | `/v1/*` |

| 访问路径（生产） | 实际后端路径 | 说明 |
| --- | --- | --- |
| `http://<host>:<WEB_PORT>/api/<path>` | `/<path>` | nginx `rewrite ^/api/(.*)$ /$1` 去掉前缀 |
| `http://<host>:<WEB_PORT>/v1/<path>` | `/v1/<path>` | 原样转发 |
| `http://127.0.0.1:8082/<path>` | `/<path>` | 仅本地进程/dev overlay 直连后端 |

**下面所有路径都写「后端路径」**，生产经 nginx 访问时管理台端点要加 `/api` 前缀（如 `/keys` → `/api/keys`）。

---

## 1. 系统（`main.py`）

| 方法 | 路径 | 鉴权 | 说明 |
| --- | --- | --- | --- |
| `GET` | `/health` | 无 | 返回 `{"status":"ok"}`。被 compose 的 backend healthcheck 与 Dockerfile 的 `HEALTHCHECK` 使用 |

---

## 2. 认证（`api/auth.py`，前缀 `/auth`）

| 方法 | 路径 | 鉴权 | 说明 |
| --- | --- | --- | --- |
| `POST` | `/auth/login` | 无 | 用 `MASTER_PASSWORD` 登录，签发会话令牌 |
| `GET` | `/auth/me` | `require_auth` | 返回当前登录态信息，前端用于判断是否已登录 |
| `POST` | `/auth/change-password` | `require_auth` | 修改主密码。写入 runtime override（启动时 `load_pwd_override()` 恢复） |

> 改密不落库为明文配置文件，而是进程内 override + 启动时恢复；`core/security.py` 里由 `_MASTER_PWD_OVERRIDE` 承载。改密后 `deploy.ps1` 的 `.env` 弱凭据校验不受影响。

---

## 3. 密钥（`api/keys.py`，前缀 `/keys`）— 10 个端点

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/keys` | 列表。返回掩码（`key_mask`）而非明文 |
| `POST` | `/keys` | 新增密钥（明文只在此刻传入，落库前 Fernet 加密） |
| `GET` | `/keys/{key_id}` | 单条详情 |
| `PUT` | `/keys/{key_id}` | 更新（名称、分组、权重、标签、状态等） |
| `DELETE` | `/keys/{key_id}` | 删除 |
| `POST` | `/keys/{key_id}/reveal` | **显式**取回明文。这是唯一暴露明文的接口 |
| `POST` | `/keys/{key_id}/test` | 测试单把密钥（写入状态与 `last_tested_at`） |
| `POST` | `/keys/test` | 批量测试（并发 5，返回进度） |
| `POST` | `/keys/export` | 导出加密包（PBKDF2-SHA256 200k 迭代 + 随机盐，口令不落库） |
| `POST` | `/keys/import` | 导入加密包 |

> 密钥 API 响应只含 `key_mask`（如 `sk-...abcd`）。明文只在 `reveal` 与创建时出现，日志与错误信息里不写明文。

---

## 4. 供应商（`api/providers.py`，前缀 `/providers`）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/providers` | 列表 |
| `POST` | `/providers` | 新建（`name` / `slug` 唯一 / `base_url` / `probe_kind` / `models` JSON / `is_active`） |
| `GET` | `/providers/{provider_id}` | 详情 |
| `PUT` | `/providers/{provider_id}` | 更新 |
| `DELETE` | `/providers/{provider_id}` | 删除 |

`probe_kind` 取值：`openai_models` / `anthropic` / `gemini_models` / `custom`（未实现）。`base_url` 为空时按 `slug` 落到内置默认基址（`openai` → `api.openai.com` 等）。

---

## 5. 密钥分组（`api/groups.py`，前缀 `/groups`）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/groups` | 分组列表 |
| `POST` | `/groups` | 新建 |
| `PUT` | `/groups/{group_id}` | 更新 |
| `DELETE` | `/groups/{group_id}` | 删除 |

---

## 6. 转发通道（`api/channels.py`，前缀 `/channels`）— 配置的核心

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/channels` | 列表。用 `selectinload` 预取 `tiers → keys → api_key` 与 `provider`，避免异步懒加载 |
| `POST` | `/channels` | 新建。`virtual_model` **重复直接 400**；`tiers[].provider_id` 与 `keys[].key_id` 不存在会 400 |
| `GET` | `/channels/{channel_id}` | 详情（含 tiers、keys、mappings） |
| `PUT` | `/channels/{channel_id}` | 更新。传 `tiers` 时**整体替换**（先删后建，触发 delete-orphan 级联）；传 `mappings` 时先按 `virtual_model` 全删再重建 |
| `POST` | `/channels/{channel_id}/test` | 按渠道测试：用该渠道对应的原生模型名测其下所有密钥，结果写 `key_model_statuses` |
| `DELETE` | `/channels/{channel_id}` | 删除，返回 `204` |

`virtual_model` 唯一约束是「一个虚拟模型一个通道」的强制手段 —— 客户端填的模型名必须唯一对应一套 Tier 结构。

---

## 7. 定价（`api/pricing.py`，前缀 `/pricing`）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/pricing` | 价表列表 |
| `POST` | `/pricing` | 新建（`model_name` 唯一） |
| `PUT` | `/pricing/{pricing_id}` | 更新 |
| `DELETE` | `/pricing/{pricing_id}` | 删除 |

字段说明见 [billing-and-usage.md](billing-and-usage.md#3-单价表model_pricings)。

---

## 8. 调用日志（`api/logs.py`，前缀 `/logs`）

| 方法 | 路径 | 查询参数 | 说明 |
| --- | --- | --- | --- |
| `GET` | `/logs` | `limit`(默认 100，≤500)、`offset`、`channel_id`、`status` | 调用日志分页，按 `id` 倒序。`created_at` 由 UTC 转**东八区**后返回 |

响应形态：`{"items": [...], "limit": N, "offset": N}`。单条字段：`id`、`created_at`、`channel_id`、`tier_order`、`provider_id`、`key_id`、`virtual_model`、`native_model`、`prompt_tokens`、`completion_tokens`、`total_tokens`、`cached_tokens`、`cost`、`latency_ms`、`status`、`error`、`stream`。

---

## 9. 告警（`api/alerts.py`，前缀 `/alerts`）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/alerts` | 告警列表（`type` / `severity` / `resolved`） |
| `POST` | `/alerts/{alert_id}/resolve` | 手工 resolve。真实流量拿到 200 或探针复测成功时也会**自动** resolve |

---

## 10. 统计（`api/dashboard.py`，前缀 `/stats`）

| 方法 | 路径 | 参数 | 说明 |
| --- | --- | --- | --- |
| `GET` | `/stats/overview` | — | 供应商数 / 密钥总数 / active 数 / 按状态计数 |
| `GET` | `/stats/usage` | `days`(1..365，默认 14) | 先用 `rollup_date` 重算今天，再读 `daily_usage`；返回按日聚合、按日×模型聚合等维度 |
| `GET` | `/stats/key-model-statuses` | — | (key, 模型) 级细粒度状态 |

---

## 11. 设置（`api/settings.py`，前缀 `/settings`）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/settings` | 当前只返回 `{"gateway_token": "..."}`。管理台 Playground 页用它填充接入信息 |

> 这个接口会**回传网关令牌明文**（给 Playground 展示用）。它受 `require_auth` 保护，所以拿到网关令牌的前提是已登录管理台 —— 权限边界是「管理台登录态 ⊃ 网关令牌」。若要更严，可考虑改成只回掩码。

---

## 12. 网关（`api/relay.py`，前缀 `/v1`，`require_gateway`）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/v1/chat/completions` | 对话补全（支持 `stream`）；生图渠道自动分发 |
| `POST` | `/v1/embeddings` | 向量嵌入 |
| `POST` | `/v1/images/generations` | 生图 |
| `GET` | `/v1/models` | 已启用通道对应的虚拟模型列表 + `context_length` |

详细契约（请求体、流式、错误码）见 [gateway-api.md](gateway-api.md)。

---

## 13. 端点计数核对

| 来源 | 端点数 |
| --- | :---: |
| `auth.py` | 3 |
| `dashboard.py` | 3 |
| `providers.py` | 5 |
| `groups.py` | 4 |
| `keys.py` | 10 |
| `channels.py` | 6 |
| `pricing.py` | 4 |
| `logs.py` | 1 |
| `alerts.py` | 2 |
| `settings.py` | 1 |
| `relay.py` | 4 |
| **小计** | **43** |
| `main.py`（`/health`） | +1 |
| **合计** | **44** |

---

## 14. 其它

- **OpenAPI 文档**：FastAPI 默认在 `/docs`（Swagger UI）与 `/redoc` 提供交互式文档，`/openapi.json` 为 schema。生产模式下这些路径在宿主机上**不可达**（backend 只 `expose` 8082），需要临时调试时打开 `docker-compose.yml` 里注释掉的 `ports: 127.0.0.1:8082:8082` 段重启。
- **CORS**：`main.py` 里 `CORSMiddleware` 的 `allow_origins` 由 `settings.CORS_ORIGINS` 控制。生产 compose 里默认放行 8080 / 5173 / 5174 / 5175 的 `127.0.0.1` 与 `localhost`；同源反代场景其实用不到 CORS。
- **CORS 与凭证**：`allow_credentials=True` 且 `allow_methods=["*"]`、`allow_headers=["*"]`。

## 相关页面

- [gateway-api.md](gateway-api.md) —— `/v1/*` 的完整契约与接入示例
- [architecture.md](architecture.md) —— 路由层在整体架构中的位置
- [billing-and-usage.md](billing-and-usage.md) —— 统计类接口的字段口径
- [troubleshooting.md](troubleshooting.md) —— 401 / 404 / 503 排查
