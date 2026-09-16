# 排错

> 一句话定位：按「现象」查原因和处理。分三部分：**启动/部署问题**、**运行期问题**、**已知的文档与代码不一致点**。

相关页面：[deployment.md](deployment.md)（部署细节）· [scheduling.md](scheduling.md)（429/503 的机制背景）

---

## 0. 先跑这三条命令

出任何问题，先把这三条的输出拿到手，八成的答案就在里面：

```bash
# ① 容器健康状态（哪个服务不健康一眼看到）
docker compose ps

# ② 后端日志（找 ERROR / WARNING；失败尝试会记 network_retry / no_active_key_last_resort 等 key）
docker compose logs -f --tail=200 backend

# ③ 健康探针
curl -fsS http://127.0.0.1:8082/health        # 需先暴露 8082，或经 nginx
curl -fsS http://127.0.0.1:8085/health
```

管理台侧的两个直接证据来源：**日志页**（每次调用的 `tier_order` / `status` / `error` / `cost` / `latency_ms`）和**告警页**（密钥状态劣化事件）。

---

## 1. 启动 / 部署问题

### 「`.env` 包含占位/弱凭据，拒绝继续」

| 项 | 内容 |
| --- | --- |
| 出处 | `deploy.ps1: Ensure-EnvFile()` |
| 触发 | `MASTER_PASSWORD` / `SECRET_KEY` / `GATEWAY_TOKEN` 的值以 `change-me` / `please-change` 开头，或长度 < 16 |
| 处理 | 换成强随机值；或**删掉 `.env`** 让脚本重新生成（注意：删了会丢失与历史密文绑定的 `SECRET_KEY`） |

生成参考：

```bash
openssl rand -hex 48                                              # SECRET_KEY
openssl rand -hex 24                                              # GATEWAY_TOKEN（前缀 mtt-gw-）后半段
openssl rand -hex 32                                              # REDIS_PASSWORD
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"   # ENCRYPTION_KEY
```

### backend 容器起来又挂 / `MySQL not reachable in time`

| 可能原因 | 排查 | 处理 |
| --- | --- | --- |
| MySQL 还没就绪 | `docker compose ps` 看 mysql 是否 `healthy` | mysql healthcheck 有 30 次重试 × 5 s，一般稍等即可 |
| `DATABASE_URL` 写错端口 | `docker compose exec backend env \| grep DATABASE_URL` | 必须是 `host.docker.internal:3307`，**不是 3306**、也不是 `mysql:3306` |
| 容器间互访被隔离 | entrypoint 里的探针日志 | Windows Docker Desktop 桥接网络下容器间直连常不通，这就是为什么走 `host.docker.internal` 绕回；确认 `extra_hosts: host.docker.internal:host-gateway` 在 |
| 宿主机 3307 被占 | `netstat -ano \| findstr 3307` | 换 `docker-compose.yml` 里 `3307:3306` 的左值，并同步改 `DATABASE_URL` |

> entrypoint 的逻辑是两段式：先用 `python socket` 探 TCP（最多 60 s），再用 `aiomysql` 真连一次（最多 90 × 2 s = 180 s）。日志会打 `[entrypoint] MySQL is reachable (attempt N/60)`，看不到这行就是第一步就卡住了。

### `alembic upgrade head` 失败

```bash
docker compose exec backend sh -c "alembic current"      # 看当前版本
docker compose exec backend sh -c "alembic history"      # 看迁移链是否断裂（8 个版本）
docker compose exec backend sh -c "alembic upgrade head"
```

常见原因：手工改过表结构导致 autogenerate 出来的迁移与库不一致。单机自用场景可以直接清库重来（或用 `alembic stamp`），但**会丢数据**。

### 前端 502 / 打不开

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 浏览器连不上 `${WEB_PORT}` | frontend 容器不健康或不健康依赖未满足 | `docker compose ps`；frontend 依赖 backend `service_healthy`，backend 没起来 frontend 就不会 start |
| 页面打开但接口 502 | nginx 转发不到 `backend:8082` | `docker compose logs frontend`；确认 backend 容器在同网络内且 8082 在监听 |
| 页面 404（刷新子路由） | 少了 SPA 兜底 | `nginx.conf` 里 `location / { try_files $uri $uri/ /index.html; }` 必须存在 |

### 端口冲突

| 冲突端口 | 谁占的 | 怎么改 |
| --- | --- | --- |
| `3306` | 本机已有 MySQL | 本项目已避开（用 3307） |
| `6379` | 本机已有 Redis | 改 compose 里 `127.0.0.1:6379:6379` 左值 |
| `8080` / `8085` | 其它 Web 服务 | 改 `.env` 的 `WEB_PORT`，**并同步改 `vite.config.ts` 的 proxy target** |
| `8082` | 其它 uvicorn | 要改四处：`Dockerfile` 的 EXPOSE、`entrypoint.sh` 的 uvicorn 端口、`nginx.conf` 的 `proxy_pass`、`vite.config.ts` 的 proxy |
| `5173` / `5175` | 其它 Vite | `vite.config.ts` 的 `server.port` + `docker-compose.dev.yml` 的 `--port` |

### `vite.config.js` 盖住 `vite.config.ts`

`vue-tsc -b` 会生成 `vite.config.js` / `vite.config.d.ts`，同名 `.js` 优先级更高，导致改了 `.ts` 不生效。已在 `.gitignore` 里忽略，删掉即可：

```bash
cd frontend && rm -f vite.config.js vite.config.d.ts
```

---

## 2. 运行期问题

### 前端全页 Network Error

**根因**：`frontend/vite.config.ts` 把 `/api` 与 `/v1` 代理到 `http://127.0.0.1:8085`（生产 nginx），但生产栈没起。

**两条出路**：

1. `docker compose up -d` 起生产栈（占 8085），再 `npm run dev` 做前端 HMR —— **推荐**，也是仓库的设计意图。
2. 把 `vite.config.ts` 里两个 `target` 改成 `http://127.0.0.1:8082`，并确认后端 `CORS_ORIGINS` 放行 5175。

> 历史佐证：`da29808 fix(frontend): .env.development 端口 8082→8000, 修前端全页 Network Error` —— 这个坑踩过一次了。

### 日志出现 `found N api_key(s) that cannot be decrypted with current SECRET_KEY`

| 项 | 内容 |
| --- | --- |
| 出处 | `main.py: _scan_undecryptable_keys()`，启动时扫描 |
| 原因 | 换过 `SECRET_KEY`（或曾用 `.env.example` 的公开占位值起过服务） |
| 后果 | 那些 key 在业务里会逐条抛 `InvalidToken` → 标 `invalid` |
| 处理 | **明文不可恢复**，只能删掉重新录入。密文与 `SECRET_KEY` 是绑死的 |

启动日志里另外一行 `all N api_key ciphertexts decryptable with current SECRET_KEY` 表示全部正常。

### 某个虚拟模型 404 `未找到 model '<x>' 对应的通道`

按顺序查：

```bash
# 1. 通道是否存在、是否启用（把 xxx 换成模型名）
curl http://127.0.0.1:8085/api/channels -H "Authorization: Bearer <session-token>"

# 2. 网关侧看到的模型列表
curl http://127.0.0.1:8085/v1/models -H "Authorization: Bearer $GATEWAY_TOKEN"
```

- 通道没建 → 去「转发通道」页建。
- `is_active = false` → 启用它。
- 名字不一致（大小写、空格、全半角）→ `virtual_model` 必须与客户端填的**完全一致**。

### 一直 429

按可能原因逐条排除：

| 原因 | 证据 | 处理 |
| --- | --- | --- |
| 密钥真被上游限流 | 日志页 `status` 为限流类，告警页有事件 | 加更多密钥进池（这是池化的意义）；或降 `MTT_TOKEN_BUCKET_RPM` |
| 令牌桶没生效（单 key 高频打） | 池里只有 1 把 key | 令牌桶无令牌时是「换下一把」，只有一把就无处可换。加 key |
| 同一 provider 的 TPM 池被打满 | 64 s 内该层全部 429 | 这是 tier 级冷却（60 s）。加 T1 跨供应商兜底层 |
| 某模型积分池耗尽 | 只有这一个模型 429，同 key 其它模型正常 | 正常行为（300 s 冷却）。换模型或等窗口滚动 |
| 上游额度真的用尽 | 告警页 `exhausted` | 充值 / 换 key |

想更主动地避免 429，在 compose 的 `backend.environment` 里加：

```yaml
MTT_TOKEN_BUCKET_RPM: "10"        # 更保守的每 key 速率
MTT_TOKEN_BUCKET_BURST: "5"
MTT_TOKEN_BUCKET_MODEL_RPM: "glm-5.2:5,deepseek-v4-flash:15"
```

### 一直 503 `无可用密钥（全部失效/限流）`

这是四级兜底**全部用尽**的结果（[scheduling.md](scheduling.md)）。诊断顺序：

```bash
curl http://127.0.0.1:8085/api/stats/overview -H "Authorization: Bearer <session-token>"
# 看 by_status：如果 active = 0，先解决这个

curl http://127.0.0.1:8085/api/alerts -H "Authorization: Bearer <session-token>"
# 看有哪些未 resolve 的告警
```

| `by_status` 情况 | 含义 | 处理 |
| --- | --- | --- |
| `active = 0` 且多 `exhausted` | 额度全耗尽 | 充值或换 key |
| `active = 0` 且多 `invalid` | 密钥全失效（或 `SECRET_KEY` 换过） | 重录密钥 |
| `active > 0` 但仍 503 | 所有层都在冷却中 | 等 60 s / 300 s；或看日志 `no_active_key_last_resort` |
| 通道没配 key | `GET /channels` 里 tiers 为空 | 补配 |

日志里的关键行：

- `no_available_key` —— 连兜底都没拼出候选，通常意味着通道没配 key。
- `no_active_key_last_resort` —— 有候选但**全部非 active**，说明已经走到赌博层了。
- `network_retry` —— 网络层原地重试，说明上游域名在抖动。
- `gateway_read_body_retry` —— 上游网关读 body 失败（> 1MB 请求体），原地重试了一次。

### 走兜底了，但我不知道为什么

查 `call_logs.tier_order`：

| `tier_order` | 含义 |
| --- | --- |
| `0` | T0 主池服务，正常 |
| `> 0` | 走了跨供应商兜底。看 `provider_id` 是哪个供应商 |
| 同一时段大量 `> 0` | T0 池有问题（限流/额度/密钥失效），去看告警页 |

Playground 页调试时也会显示「本次由 key #N（T<层> 层）服务」，配合日志页一起看。

### 成本显示为 0 或明显偏低

| 症状 | 原因 |
| --- | --- |
| 成本恒为 0 | 定价表里没有这个模型（**注意查价用的是原生模型名**，不是虚拟名） |
| 全是 0 且 token 也 0 | 上游没返回 `usage`（流式时网关会补 `stream_options.include_usage`，但上游可能不支持） |
| 明显偏低 | **兜底试错产生的上游消耗不计入成本** —— 只有最终结果写一条 `call_logs`。这是已知取舍，见 [billing-and-usage.md](billing-and-usage.md#7-计费口径的已知取舍) |

### 用量页数据不新鲜

`GET /stats/usage` 每次查询会先对**今天**调一次 `rollup_date`，所以最多滞后到你上次打开页面。历史日期的数据靠每日 **02:30** 的定时任务；如果那段时间服务没跑，启动时会回填近 **60 天**。60 天以前的数据不会自动补 —— 手动触发 rollup 即可。

### 告警一直不 resolve

自动 resolve 的触发条件是 **密钥恢复到 `active`**，路径有三条：

1. 定时巡检探针复测成功（每 10 分钟一轮）；
2. 真实流量拿到 200（`_revive_if_exhausted`）；
3. 手工 `POST /alerts/{id}/resolve`。

若三条都没生效，检查是否有**探测保护期**在起作用（`_DEGRADED_PROBE_GRACE_MINUTES = 5`）：刚判 degraded 的 key 在 5 分钟内探测成功也**不会**恢复 active，这是为了防状态横跳。

### 流式响应卡住 / 断开

| 检查项 | 期望 |
| --- | --- |
| nginx `proxy_buffering` | `off` |
| nginx `proxy_read_timeout` | `900s` |
| nginx `proxy_set_header Connection` | `''`（空，不能是 `close`） |
| Vite dev proxy | `proxyRes` 里删掉 `content-encoding` |
| 客户端 | 别自己加 `Accept-Encoding: gzip` 后手工解压 |

一旦流已打开（200 到手），网关**不会再切换 key**。中途出错只记日志，不会重试 —— 这是为了避免响应内容错乱。

---

## 3. 已知的文档与代码不一致点

这些是在写本 wiki 时核对出来的、**长期存在但没被修正**的地方。列出来是为了避免以后被误导。

| # | 不一致 | 事实 | 影响 |
| :---: | --- | --- | --- |
| 1 | README 曾称 Redis 用于「session pin / 限流 / 热点缓存」 | `backend/app/` 下**零 Redis 调用**；`core/config.py` 也没有 `REDIS_URL` 字段（且 `extra="ignore"` 会静默忽略）。所有冷却/限流状态在单进程内存 | **多副本部署会各自持一份状态**，且重启丢冷却态 |
| 2 | README 的网关 Base URL 写 `8085` 而注释写「默认 8080」 | `WEB_PORT` 默认 `8080`，本仓库当前 `.env` 为 `8085`（vite proxy 与 Playground 固定地址都是 8085） | 自部署时以自己 `.env` 为准，并同步 vite proxy |
| 3 | `docker-compose.dev.yml` 注释称「`.env.development` 里的 `VITE_API_BASE=http://127.0.0.1:8082` 自动生效」 | `.env.development` 实际是 `VITE_API_BASE=/api`，走 vite proxy 到 **8085** | 容器内 dev 的前端如果指 8085，在容器里是 `127.0.0.1:8085`（容器自身），**指向不到宿主机** —— 这一路径需要落到 `docker-compose.dev.yml` 里 `ports` 映射的 `127.0.0.1:8082` 或改用 host.docker.internal |
| 4 | `frontend/nginx.conf` 注释写「外部用户可通过 `http://<host>:8080/v1/*`」 | 端口由 `WEB_PORT` 决定，当前是 8085 | 仅注释过时，无功能影响 |
| 5 | README 未提 `/v1/images/generations`、`context_length`、`off_peak_*` | 三者都已实现 | 已在新版 README 补齐 |
| 6 | `pyproject.toml` 注释称 Redis「async 支持，5.x 自带 asyncio API」，但实际上没用 | 声明未接线 | 要么接线，要么删依赖 + 删 compose 的 redis 服务 |
| 7 | 无 `LICENSE` 文件 / 无 `license`、`authors` 字段 | 默认保留所有权利 | 开源前必须补 |
| 8 | `.zcode/plans/` 里的原始实施方案写的是 **LiteLLM / asyncmy / Element Plus** | 实际落地是 **httpx / aiomysql / Naive UI** | 读那份 plan 会被误导；`changelog.md` 里已标注 |

> 处理原则：**不改代码来迁就文档，也不悄悄改文档掩盖差异**。把事实写清楚，标好在哪里。修改代码或文档时同步更新本表。

---

## 4. 日志阅读指南

### 结构化日志（structlog）

`/v1/*` 的请求由 `relay/logging_middleware.py` 记结构化摘要，字段化输出便于 grep：

```bash
# 找所有网络重试
docker compose logs backend | grep network_retry

# 找走了兜底的请求（tier_order > 0）
docker compose logs backend | grep -E "tier_order[^0]"

# 找兜底赌博层
docker compose logs backend | grep no_active_key_last_resort

# 按 key_id 过滤
docker compose logs backend | grep "key_id=42"
```

### 关键日志键

| 键 | 含义 |
| --- | --- |
| `network_retry` | 网络层瞬时故障，原地重试中 |
| `gateway_read_body_retry` | 上游网关读 body 失败（> 1MB 请求体），原地重试一次 |
| `no_available_key` | `_build_attempts` 返回空 —— 连兜底都没候选 |
| `no_active_key_last_resort` | 候选全非 active，已到赌博层 |
| `sweep_key_failed` | 巡检中单把 key 测试失败（不影响其它 key） |
| `undecryptable scan skipped` | 启动期扫描因连不上 DB 而跳过 |

### 调高日志详细度

日志配置在 `core/structlog.py`，随 FastAPI `lifespan` 里的 `configure_logging()` 生效。临时调详细度最省事的办法是改那里，或用 uvicorn 的 `--log-level` 参数。

## 相关页面

- [deployment.md](deployment.md) —— 端口表、环境变量、部署方式
- [scheduling.md](scheduling.md) —— 429 / 503 背后的机制与全部常量
- [gateway-api.md](gateway-api.md) —— 错误码对照表
- [architecture.md](architecture.md) —— 已知架构约束（为什么不能多副本）
