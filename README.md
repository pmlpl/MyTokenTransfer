# <img src="frontend/public/logo.png" width="32" alt="MTT 品牌像素 Logo"> MyTokenTransfer

自研轻量版 one-api/new-api：**API 密钥管理台 + OpenAI 兼容转发网关**，本地单机自用。

把散落在多个中转商账号里的 API Key 统一收拢成一个**密钥池**，对外只暴露**一个地址 + 一个令牌**。客户端换模型不用改代码，改的是管理台里的通道配置。

> 📖 进阶文档全部在 [`wiki/`](wiki/index.md)。第一次读文档请先看 [wiki/README.md](wiki/README.md)（wiki 阅读方法）。

---

## 目录

- [核心能力](#核心能力)
- [架构总览](#架构总览)
- [端口一览](#端口一览)
- [快速开始](#快速开始)
  - [方式 A：Docker 一键部署（推荐）](#方式-adocker-一键部署推荐)
  - [方式 B：dev 热更新（Docker overlay）](#方式-bdev-热更新docker-overlay)
  - [方式 C：纯本地进程调试](#方式-c纯本地进程调试)
  - [方式 D：手动安装后端 / 前端依赖](#方式-d手动安装后端--前端依赖)
- [环境变量](#环境变量)
- [使用示例](#使用示例)
- [调度与兜底](#调度与兜底)
- [计费与用量](#计费与用量)
- [技术栈](#技术栈)
- [项目结构](#项目结构)
- [测试](#测试)
- [常见问题排错](#常见问题排错)
- [贡献指南](#贡献指南)
- [许可证](#许可证)

---

## 核心能力

| 能力 | 说明 |
| --- | --- |
| 多账号密钥统一管理 | Fernet 静态加密存储、掩码展示、分组、标签、权重、批量健康测试 |
| OpenAI 兼容转发 | `/v1/chat/completions`（含流式 SSE）、`/v1/embeddings`、`/v1/images/generations`、`/v1/models` |
| 多账号密钥池 + 四级兜底 | 见 [调度与兜底](#调度与兜底)；支持跨供应商 fallback（带虚拟模型 → 原生模型名映射） |
| 密钥 × 模型细粒度状态 | 状态不再只有「整把 key 好坏」，而是落到 (key, 模型) 维度：某模型积分池 429 只冷却该组合 |
| 令牌桶事前控速 | 按 (key, 原生模型) 在**请求发出前**限速，与「事后 429 冷却」互补，从源头减少 429 |
| 缓存亲和路由 | 按消息稳定前缀哈希固定到同一把上游 key，复用其前缀缓存，提升缓存命中率 |
| 计费 | 按模型定价（输入 / 输出 / 缓存命中），支持高峰 / 空闲双档（时段见 [计费与用量](#计费与用量)） |
| 用量统计 | 近 24 小时滚动波形（跨午夜连续）、多日聚合、按密钥 / 按模型视图，成本随定价档自动切换 |
| 告警 | 密钥状态劣化自动告警，恢复（探针复测或真实流量成功）自动 resolve |
| 备份迁移 | 密钥池加密包导出 / 导入（PBKDF2-SHA256 200k 迭代派生，口令不落库） |

## 架构总览

```
                    ┌──────────────────────────────────────────┐
   任意 OpenAI      │           管理台（Vue3 + Naive UI）        │
   兼容客户端  ──┐  │  密钥 / 供应商 / 通道 / 日志 / 用量 / 定价  │
   (Cline/Cursor │  └────────────────┬─────────────────────────┘
    /自研 Agent) │                   │ Bearer <session-token>
                 │                   │  /api/*  (登录态)
                 ▼                   ▼
        ┌────────────────────────────────────────────┐
        │      nginx (frontend 容器, 对外唯一入口)      │
        │   /api/* → backend:8082   /v1/* → backend   │
        └────────────────┬───────────────────────────┘
                         │ Bearer <GATEWAY_TOKEN>
                         ▼
        ┌────────────────────────────────────────────┐
        │   FastAPI backend :8082                    │
        │   relay/gateway  ← 编排尝试序列 + 流式透传    │
        │   relay/balancer ← Tier 调度 / 状态机 / 兜底  │
        │   relay/token_bucket ← 事前限速              │
        │   relay/accounting   ← 计费落库              │
        │   core/scheduler     ← APScheduler 巡检      │
        └───────┬─────────────────────────┬──────────┘
                │ SQLAlchemy 2.0 async    │ httpx 直连
                ▼                         ▼
        ┌───────────────┐        ┌────────────────────────┐
        │ MySQL 8.0     │        │ 上游 OpenAI 兼容端点       │
        │ (12 张表)      │        │ (多中转商 / 官方 API)      │
        └───────────────┘        └────────────────────────┘
        ┌───────────────┐
        │ Redis 7        │  ← compose 中已起服务，但当前应用代码未接线（见下方说明）
        └───────────────┘
```

> [!IMPORTANT]
> **关于 Redis**：`docker-compose.yml` 起了 `redis:7-alpine`，`pyproject.toml` 也声明了 `redis>=5.0`，但 `backend/app/` 下**没有任何 Redis 调用**（`config.py` 里也没有 `REDIS_URL` 字段）。当前所有限流 / 冷却状态都在**单进程内存**里（见 `relay/token_bucket.py` 与 `relay/balancer.py`）。因此**多副本部署会各自持有一份调度状态**，且重启会丢冷却态。这是已存在的技术债，不是 bug——如要横向扩容，需要先把这部分状态搬到 Redis。

## 端口一览

| 模式 | 管理台入口 | 后端 API | MySQL | Redis |
| --- | --- | --- | --- | --- |
| **生产**（`docker compose up`） | `http://127.0.0.1:${WEB_PORT}`（默认 `8080`） | 容器内 `8082`，**不对外暴露** | 宿主机 `3307` | 宿主机 `127.0.0.1:6379` |
| **dev overlay**（`dev.ps1`） | `http://127.0.0.1:5173`（Vite HMR） | `127.0.0.1:8082` | 宿主机 `3307` | 宿主机 `6379` |
| **纯本地进程** | `http://127.0.0.1:5175`（Vite HMR） | `127.0.0.1:8082` | 宿主机 `3307` | 未使用 |

> [!WARNING]
> **本仓库当前的 `.env` 把 `WEB_PORT` 设成了 `8085`**，所以生产入口实际是 `http://127.0.0.1:8085`；`frontend/vite.config.ts` 的 dev proxy 目标也写死指向 `8085`。自己部署时以 `.env` 里的 `WEB_PORT` 为准，并同步改 vite proxy 目标。

端口口径的坑（都踩过）：

- MySQL 映射到宿主机 **3307**（避开本地 3306）；backend 容器经 `host.docker.internal:3307` 绕回访问 —— Windows Docker Desktop 桥接网络下容器间直连常不通。
- 容器内 uvicorn 固定监听 `8082`，`docker-compose.yml` 里 `expose` 而非 `ports`，外部只能走 nginx。
- Vite 的 `server.port` 是 **5175**，但 `docker-compose.dev.yml` 用 `--port 5173` 覆盖，所以容器里跑的是 5173。

---

## 快速开始

### 前置要求

| 方式 | 需要 |
| --- | --- |
| A / B | Docker Desktop（含 `docker compose`） |
| C / D | Python **3.11+**、Node **20+**、一个可达的 MySQL 8.0 |

Windows 用户注意：`deploy.ps1` / `dev.ps1` 是 PowerShell 脚本，必须走 `powershell -File`，不要在 Git Bash 里直接执行。

### 方式 A：Docker 一键部署（推荐）

最省事，`deploy.ps1` 会自己生成 `.env`（含强随机凭据）、构建镜像、等健康检查通过，并把凭据打印出来。

```powershell
# 在项目根目录
powershell -ExecutionPolicy Bypass -File .\deploy.ps1
```

首次运行会输出并保存这几项（**务必抄下来，`.env` 里的密文跟它绑定**）：

```
Admin login password  (MASTER_PASSWORD)
Gateway token         (GATEWAY_TOKEN)
Signing key           (SECRET_KEY)
Redis password        (REDIS_PASSWORD)
```

脚本另有子命令：

```powershell
powershell -File .\deploy.ps1 start     # 启动已有容器
powershell -File .\deploy.ps1 stop      # 停止
powershell -File .\deploy.ps1 restart   # 重启
powershell -File .\deploy.ps1 status    # 健康状态
powershell -File .\deploy.ps1 logs      # 跟踪日志（Ctrl+C 退出）
powershell -File .\deploy.ps1 migrate   # 手动跑 alembic upgrade head
powershell -File .\deploy.ps1 down      # 删除容器与网络（保留 mysql_data 卷）
```

不想要脚本，手动也可以：

```bash
cp .env.example .env       # 填 MASTER_PASSWORD / SECRET_KEY / GATEWAY_TOKEN / REDIS_PASSWORD / WEB_PORT
docker compose up -d --build
```

后端容器启动时会自动等 MySQL 可达、跑 `alembic upgrade head`、再起 uvicorn（见 `backend/entrypoint.sh`），**不需要手动迁移**。打开 `http://127.0.0.1:<WEB_PORT>`，用 `MASTER_PASSWORD` 登录。

> [!NOTE]
> `deploy.ps1` 会**拒绝**占位/弱凭据：若 `.env` 里 `MASTER_PASSWORD` / `SECRET_KEY` / `GATEWAY_TOKEN` 仍是示例值、或长度小于 16，脚本直接报错退出。这是故意的——用公开示例值起服务既危险，也会让历史密文全部解不开。

查看数据库：任意客户端连 `127.0.0.1:3307`，库 `mytokentransfer`，用户 `mtt` / `mttpass`（或 `root` / `rootpass`）。

### 方式 B：dev 热更新（Docker overlay）

后端源码 bind-mount 进容器跑 `uvicorn --reload`，前端换 `node:20-alpine` 跑 Vite HMR，**不构建 nginx 生产镜像**。

```powershell
powershell -File .\dev.ps1          # up + 等健康
powershell -File .\dev.ps1 logs
powershell -File .\dev.ps1 shell-backend    # bash 进 backend 容器
powershell -File .\dev.ps1 shell-frontend
```

等价的手动命令：

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

入口：Vite `http://127.0.0.1:5173`，后端 `http://127.0.0.1:8082`。dev 与 prod 共用同一份根 `.env`，互不影响（prod 仍占 `${WEB_PORT}`）。

### 方式 C：纯本地进程调试

不用 Docker 跑应用，只借容器起数据库：

```bash
docker compose up -d mysql          # 只起 MySQL（宿主机 3307）
```

然后按下方「方式 D」装依赖，再：

```bash
cd backend
alembic upgrade head
uvicorn app.main:app --reload --host 127.0.0.1 --port 8082

cd ../frontend
npm run dev                          # http://127.0.0.1:5175
```

> [!WARNING]
> **Vite dev server（5175）本身不直连 8082。** `frontend/vite.config.ts` 把 `/api` 与 `/v1` 反向代理到 **`http://127.0.0.1:8085`**（也就是生产 nginx），这样浏览器同域、无 CORS、SSE 流式也能透传。
> 所以两条路：
> 1. 先 `docker compose up -d` 起生产栈（占 8085），再跑 `npm run dev` 做前端 HMR —— **推荐**；
> 2. 或者把 `vite.config.ts` 里两个 `target` 改成 `http://127.0.0.1:8082`（同时确认后端 `CORS_ORIGINS` 放行 5175），才能完全脱离生产栈联调。

### 方式 D：手动安装后端 / 前端依赖

**后端**（`backend/pyproject.toml`，需要 Python ≥ 3.11）：

```bash
cd backend
python -m venv .venv
source .venv/Scripts/activate        # Git Bash on Windows
# .\.venv\Scripts\Activate.ps1       # PowerShell

pip install -e .                     # 运行时依赖
pip install -e ".[dev]"              # 额外：pytest / aiosqlite / ruff

cp .env.example .env                 # 按需修改 DATABASE_URL / MASTER_PASSWORD / SECRET_KEY / GATEWAY_TOKEN
```

**前端**（`frontend/package.json`，需要 Node ≥ 20）：

```bash
cd frontend
npm install
npm run dev        # 开发服务器（5175）
npm run build      # vue-tsc -b && vite build
npm run preview    # 预览构建产物
```

## 环境变量

根 `.env`（供 Docker Compose 使用，示例见 `.env.example`）：

| 变量 | 必填 | 默认值 | 说明 |
| --- | --- | :---: | --- |
| `MASTER_PASSWORD` | ✅ | — | 管理台登录密码 |
| `SECRET_KEY` | ✅ | — | 会话签名密钥；`ENCRYPTION_KEY` 留空时也由它派生 Fernet key |
| `GATEWAY_TOKEN` | ✅ | — | 客户端调 `/v1/*` 的 `Bearer` 令牌 |
| `REDIS_PASSWORD` | ✅ | — | compose 里 Redis 的 `requirepass` |
| `REDIS_URL` | ⬜ | `redis://redis:6379/0` | 传给后端容器，但**当前应用代码未读取**（见上文说明） |
| `ENCRYPTION_KEY` | ⬜ | 空 | 密钥静态加密的 Fernet key；留空则按 `SECRET_KEY` 派生 |
| `SESSION_MAX_AGE` | ⬜ | `604800` | 登录态有效期（秒），默认 7 天 |
| `WEB_PORT` | ⬜ | `8080` | 前端 nginx 对外端口 |
| `MYSQL_ROOT_PASSWORD` | ⬜ | `rootpass` | MySQL root 密码 |
| `MYSQL_DATABASE` | ⬜ | `mytokentransfer` | 库名 |
| `MYSQL_USER` / `MYSQL_PASSWORD` | ⬜ | `mtt` / `mttpass` | 业务账号 |

`backend/.env`（本地进程运行时，示例见 `backend/.env.example`）：`DATABASE_URL`、`MASTER_PASSWORD`、`SECRET_KEY`、`GATEWAY_TOKEN`、`ENCRYPTION_KEY`、`SESSION_MAX_AGE`、`CORS_ORIGINS`。

代码里可直接读到的**运行期**环境变量（`os.environ`，未进 `.env.example`）：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MTT_TOKEN_BUCKET_RPM` | `20` | 每把 key 每分钟请求数上限（令牌桶补充速率） |
| `MTT_TOKEN_BUCKET_BURST` | `10` | 突发容量（桶满时可连发几个请求） |
| `MTT_TOKEN_BUCKET_MODEL_RPM` | 空 | 按原生模型名覆盖 RPM，格式 `glm-5.2:10,deepseek-v4-flash:15` |

生成随机凭据的参考做法：

```bash
openssl rand -hex 48     # SECRET_KEY（96 位 hex）
openssl rand -hex 24     # GATEWAY_TOKEN 后半段
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"   # ENCRYPTION_KEY
```

---

## 使用示例

### 第 0 步：管理台首次配置（4 步）

网关不会自动认识任何模型，先把数据配进去。以下都在管理台 Web UI 里做：

1. **密钥页** → 录入上游多账号 Key（可批量），点「测试」确认可用。
2. **供应商页** → 新建供应商：填 `base_url`（OpenAI 兼容端点）、探测方式、以及该供应商支持的模型列表。
3. **转发通道页** → 建通道：
   - `virtual_model`：对外暴露的**虚拟模型名**（客户端就填这个）；
   - 把第 1 步的 key 组成池挂到 **T0 主池**，选策略（`round_robin` / `weighted` / `priority`）；
   - 可选：再加 **T1+ 跨供应商兜底层**，并配置**模型名映射**（虚拟模型 → 各供应商的原生模型名）。
4. **定价页** → 给模型填价格（输入 / 输出 / 缓存命中，可填高峰与空闲两档），用量页才有成本数据。

配完到 **Playground 页**可以直接看到网关地址与 `GATEWAY_TOKEN`，并做端到端调试。

### 1. 统一网关接入信息

```
Base URL : http://127.0.0.1:<WEB_PORT>/v1
           # 生产：nginx 反代，端口 = 根 .env 的 WEB_PORT（默认 8080，本仓库当前 8085）
           # 纯本地后端直连：http://127.0.0.1:8082/v1
API Key  : <GATEWAY_TOKEN>     # 根 .env，或管理台 Playground 页查看
Auth     : Authorization: Bearer <GATEWAY_TOKEN>
```

### 2. curl 调用

```bash
# 列出可用虚拟模型
curl http://127.0.0.1:8085/v1/models \
  -H "Authorization: Bearer $GATEWAY_TOKEN"

# 对话补全
curl http://127.0.0.1:8085/v1/chat/completions \
  -H "Authorization: Bearer $GATEWAY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "<通道虚拟模型名>",
    "messages": [{"role": "user", "content": "用一句话介绍你自己"}],
    "temperature": 0.7
  }'

# 流式（SSE）
curl -N http://127.0.0.1:8085/v1/chat/completions \
  -H "Authorization: Bearer $GATEWAY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model":"<虚拟模型名>","messages":[{"role":"user","content":"写一首五言绝句"}],"stream":true}'

# 向量嵌入
curl http://127.0.0.1:8085/v1/embeddings \
  -H "Authorization: Bearer $GATEWAY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model":"<embedding 虚拟模型名>","input":["hello","world"]}'

# 生图
curl http://127.0.0.1:8085/v1/images/generations \
  -H "Authorization: Bearer $GATEWAY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model":"<生图虚拟模型名>","prompt":"一只戴宇航头盔的柴犬","n":1}'
```

### 3. Python（openai SDK）

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8085/v1",   # 本地后端直连：http://127.0.0.1:8082/v1
    api_key="<GATEWAY_TOKEN>",
)

# 非流式
r = client.chat.completions.create(
    model="<通道虚拟模型名>",
    messages=[{"role": "user", "content": "你好"}],
)
print(r.choices[0].message.content)
print(r.usage)          # prompt / completion tokens

# 流式
stream = client.chat.completions.create(
    model="<通道虚拟模型名>",
    messages=[{"role": "user", "content": "讲个冷笑话"}],
    stream=True,
)
for chunk in stream:
    delta = chunk.choices[0].delta.content
    if delta:
        print(delta, end="", flush=True)
```

### 4. 接入各类 OpenAI 兼容客户端

因为只有 `/v1/*` 四个标准端点，任何「OpenAI 兼容」客户端都能直接用：Cline / Cursor / Continue / Dify / LangChain `ChatOpenAI` / 自研 Agent。填两个字段即可：

```
API Base / Base URL : http://<host>:<WEB_PORT>/v1
API Key             : <GATEWAY_TOKEN>
Model               : <通道虚拟模型名>
```

### 5. 跨设备 / 远程接入

本项目的设计用法是配 Tailscale 之类的虚拟组网，让固定 IP 不随物理网络（WiFi/热点）变化：

```
Base URL: http://<Tailscale-IP>:<WEB_PORT>/v1     # 管理台 Playground 页的「固定地址」
```

前提是 `WEB_PORT` 在宿主机上可被组网内其他设备访问（nginx 绑定 `0.0.0.0`），且这些设备已加入同一 Tailscale 网络。

### 6. 备份 / 迁移密钥池

管理台密钥页的「导出」会生成加密包（`PBKDF2-SHA256`、200,000 次迭代、随机盐、口令不落库），「导入」时用同一口令解开。对应接口 `POST /keys/export` 与 `POST /keys/import`。**口令丢了密文不可恢复。**

---

## 调度与兜底

请求按 **Tier 升序**、层内按策略（`round_robin` / `weighted` / `priority`，含会话缓存亲和哈希固定同 key）生成候选队列，逐个尝试。四个兜底层级，仅当前面无人可试时才启用后一级：

1. **正常路径**：`active` 密钥；限流冷却到期的直接视为可用（省一个巡检周期）。
2. **全池 exhausted**：追加最久未测的 `exhausted` 密钥，赌额度窗口已滚动恢复（`exhausted` 复测周期 **10 分钟**）。
3. **模型池冷却**：同一把 key 上某模型积分池 429 只冷却 **(key, 模型) 组合 300 秒**，不标死整把 key；全组合冷却时追加冷却组合做最后尝试。
4. **tier TPM 冷却**：高并发打满共享 TPM → 整层 **60 秒**冷却，全层冷却时无视冷却取层内密钥试最后一轮。

补充行为：

- 网络层瞬时故障（TLS 被掐 / 连接重置）**原地重试 3 次**（退避 1s）；最后尝试拿到 200 即把密钥自动拉回 `active` 并 resolve 告警。
- **令牌桶事前控速**：发出请求前按 `(key, 原生模型)` 检查速率，无令牌直接换下一把 key；**永不硬失败**——全部 key 都无令牌时短暂等待后放行。
- **非 active 密钥探测保护期 5 分钟**：刚被判 degraded 的 key，小探针能过但大请求仍可能限流，保护期内探测成功不恢复 `active`，避免状态横跳。
- **定时巡检**（APScheduler）：每 **10 分钟**重测所有非 `active` 且非 `disabled` 的密钥；每日 **02:30** 预聚合 rollup；启动时回填近 **60 天** 用量。

细节与参数见 [wiki/scheduling.md](wiki/scheduling.md)。

## 计费与用量

- 计费维度：`输入` / `输出` / `缓存命中` 三类 token，各自单价。
- **双档定价**：高峰档与空闲档。默认高峰窗口按北京时间 **周一至周五 09:00–12:00、14:00–18:00**，其余走空闲档（对齐 DeepSeek 官方口径）。用量页成本会随定价档自动切换。
- 上下文长度：定价表带 `context_length` 字段，支持百万级上下文中转（`/v1/models` 会一并返回）。
- 落库链路：每次调用写 `call_logs` 明细（tokens / 成本 / 延迟 / 状态 / 是否流式 / 错误），再经 `DailyUsage` 预聚合，用量页读聚合小表而非扫明细。
- 时间口径：`call_logs.created_at` 以 UTC 存储，API 返回时转东八区；「近 24 小时波形」是**滚动窗口**，跨午夜连续。

细节见 [wiki/billing-and-usage.md](wiki/billing-and-usage.md)。

## 技术栈

| 层 | 选型 |
| --- | --- |
| 后端 | FastAPI + SQLAlchemy 2.0 async + MySQL 8.0 + Alembic；转发层用 **httpx 直连** OpenAI 兼容端点；APScheduler 定时任务；structlog 结构化日志 |
| 前端 | Vue 3 + Vite + TypeScript + Naive UI + ECharts |
| 测试 | pytest（内存 SQLite，不依赖 MySQL） |
| 部署 | Docker Compose（MySQL 3307 / Redis / backend 8082 / 前端 nginx 对外入口） |

未引入 LiteLLM 等格式翻译层：原生 Anthropic / Gemini 格式的请求体翻译暂未接入（探测层已支持 `anthropic` / `gemini_models` 两种探测方式）。后续如需再考虑引入。

## 项目结构

```
MyTokenTransfer/
├─ backend/
│  ├─ app/
│  │  ├─ api/            # 43 个管理台 + 网关端点（auth/keys/providers/groups/
│  │  │                  #   channels/pricing/logs/alerts/settings/dashboard/relay）
│  │  ├─ core/           # config / deps(鉴权) / security(加解密) / scheduler / structlog
│  │  ├─ models/         # 12 张 SQLAlchemy 表
│  │  ├─ relay/          # gateway / balancer / token_bucket / accounting / errors / 日志中间件
│  │  ├─ schemas/        # Pydantic 请求响应模型
│  │  ├─ services/       # health_check / alerts / usage / key(导入导出)
│  │  ├─ alembic/        # 数据库迁移（8 个版本）
│  │  └─ main.py         # FastAPI 装配：路由注册 / CORS / lifespan / GET /health
│  ├─ tests/             # 7 个测试文件，59 个用例，内存 SQLite
│  ├─ Dockerfile         # 多阶段构建（builder → runtime）
│  ├─ entrypoint.sh      # 等 DB → alembic upgrade head → uvicorn
│  └─ pyproject.toml
├─ frontend/
│  ├─ src/
│  │  ├─ views/          # 11 个页面：Login/Dashboard/Keys/Providers/Channels/
│  │  │                  #   Playground/Logs/Usage/Pricing/Alerts/Settings
│  │  ├─ design/         # 设计令牌、明暗主题、通用组件
│  │  ├─ api/            # axios 实例与接口封装
│  │  ├─ stores/         # Pinia（登录态）
│  │  └─ router/
│  ├─ nginx.conf         # /api/* 去前缀转 backend:8082；/v1/* 直转；SPA 兜底
│  ├─ vite.config.ts     # 端口 5175，proxy /api + /v1 → 127.0.0.1:8085
│  └─ Dockerfile
├─ wiki/                 # 项目 wiki（入口见 wiki/README.md）
├─ docker-compose.yml    # 生产：mysql / redis / backend / frontend(nginx)
├─ docker-compose.dev.yml# dev overlay：bind-mount + Vite HMR
├─ deploy.ps1            # 一键部署/停止/日志/迁移（Windows）
├─ dev.ps1               # 开发态热更新（Windows）
├─ .env.example
└─ README.md
```

## 测试

测试跑在**内存 SQLite**（`sqlite+aiosqlite:///:memory:`）上，**不需要 MySQL、不需要 Docker**。

```bash
cd backend
source .venv/Scripts/activate     # PowerShell: .\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"           # 首次
python -m pytest tests/           # 或 python -m pytest（testpaths 已指向 tests）
```

当前 **7 个测试文件 / 59 个用例**：

| 文件 | 用例数 | 覆盖 |
| --- | :---: | --- |
| `test_balancer.py` | 17 | Tier 调度、策略、状态机标记 |
| `test_fallback_chain.py` | 12 | 四级兜底尝试序列 |
| `test_alert_auto_resolve.py` | 10 | 告警自动 resolve |
| `test_classify_error.py` | 7 | 上游错误分类（rate / quota / 其他） |
| `test_health_classify.py` | 6 | 探测结果分类 |
| `test_key_export.py` | 4 | 加密包导出 / 导入 |
| `test_rollup.py` | 3 | 用量预聚合 |

代码风格：

```bash
cd backend
ruff check .
ruff format .
```

## 常见问题排错

| 现象 | 原因 / 处理 |
| --- | --- |
| 启动报「`.env` 包含占位/弱凭据，拒绝继续」 | `deploy.ps1` 的安全护栏。把 `MASTER_PASSWORD` / `SECRET_KEY` / `GATEWAY_TOKEN` 换成强随机值（长度 ≥ 16），或直接删掉 `.env` 让脚本重新生成。 |
| 日志出现 `found N api_key(s) that cannot be decrypted with current SECRET_KEY` | 换过 `SECRET_KEY`，历史密文解不开了。明文不可恢复，只能删掉这些密钥重新录入。 |
| 前端全页 Network Error | Vite 把 `/api` 代理到了 `8085`，但生产栈没起。`docker compose up -d` 起生产，或把 `vite.config.ts` 的两个 proxy target 改成 `8082`。 |
| 后端连不上 MySQL | MySQL 容器映射在宿主机 **3307**（不是 3306）。容器内访问要经 `host.docker.internal:3307`。 |
| 某个虚拟模型 404 `未找到 model ... 对应的通道` | 通道没建、或 `is_active` 为 false、或客户端填的虚拟模型名与管理台不一致。 |
| 一直 429 | 看管理台「告警」与「日志」页：可能是 key 状态劣化、模型池冷却、或 tier TPM 冷却。可下调 `MTT_TOKEN_BUCKET_RPM` 让令牌桶更早介入。 |
| 面板成本是科学计数法 | 已修复（`feat(usage): ... fix cost scientific notation`）；若仍出现，检查定价表里该模型是否漏配单价。 |
| `vite.config.js` 盖住了 `vite.config.ts` | 这是 `vue-tsc -b` 的编译产物，已在 `.gitignore` 里忽略；删掉即可。 |

更多排错见 [wiki/troubleshooting.md](wiki/troubleshooting.md)。

## 贡献指南

这是一个**单人自用项目**，但欢迎提 Issue / PR。为保持调度与兜底逻辑的可回归性，请遵守以下约定。

### 开发流程

```bash
# 1. 起依赖（MySQL 3307）
docker compose up -d mysql

# 2. 后端热更新
cd backend && source .venv/Scripts/activate
alembic upgrade head
uvicorn app.main:app --reload --port 8082

# 3. 前端（需生产栈在 8085，见「方式 C」警告）
cd frontend && npm run dev
```

### 提交前必须通过

```bash
cd backend && python -m pytest tests/     # 全绿
cd backend && ruff check . && ruff format --check .
cd frontend && npm run build              # vue-tsc 类型检查 + 构建通过
```

### 编码约定

- **改了调度 / 兜底逻辑，必须补或改 `backend/tests/` 下的回归测试。** 现有 59 个用例是这套机制的唯一防线。
- 数据库结构变更走 **Alembic 迁移**（`cd backend && alembic revision --autogenerate -m "..."`），不要手改表；容器启动会自动 `upgrade head`。
- 密钥相关代码**不得**把明文写进日志、报错信息、API 响应（现有实现只暴露 `key_mask`，`reveal` 是显式接口）。
- 新增管理台接口时统一挂 `dependencies=[Depends(require_auth)]`；`/v1/*` 挂 `require_gateway`，两套鉴权不要混。
- 异步 SQLAlchemy 下**不要依赖隐式懒加载**，用 `selectinload` 预取（见 `app/api/channels.py` 的 `_full_channel_stmt`）。
- 前端新页面放在 `src/views/`，在 `src/router/index.ts` 注册；样式优先复用 `src/design/` 的令牌与通用组件，并同时兼容明暗主题（IDE/系统主题切换）。
- 提交信息沿用现有前缀：`feat(scope):` / `fix(scope):` / `test(scope):` / `docs:` / `refactor(scope):` / `chore:` / `build(scope):` / `config:`。

### 报 Bug 时请附上

1. 复现步骤与期望 / 实际结果；
2. 相关请求的 `call_logs` 记录（管理台「日志」页）或后端结构化日志片段；
3. `docker compose ps` 的容器健康状态；
4. 若涉及调度：该虚拟模型对应通道的 Tier 结构（截图或 `GET /channels` 响应）。

### 文档

改动行为后请同步更新：`README.md`（能力与示例）、`wiki/` 相应页面、以及 `wiki/changelog.md`（追加一条，格式见该文件）。

## 许可证

**本仓库当前没有 LICENSE 文件**，`pyproject.toml` 与 `package.json` 也未声明 license 字段（前端为 `private: true`）。在补充许可证之前，默认保留所有权利，请勿直接再分发。若要开源，建议补一个 `LICENSE`（MIT / Apache-2.0 皆可）并在 `pyproject.toml` 的 `[project]` 里加 `license` 字段。

---

## 文档地图

| 你想知道 | 去哪看 |
| --- | --- |
| 这份 wiki 怎么读 | [wiki/README.md](wiki/README.md) |
| 全部文档目录 | [wiki/index.md](wiki/index.md) |
| 版本变更记录 | [wiki/changelog.md](wiki/changelog.md) |
| 模块职责与数据流 | [wiki/architecture.md](wiki/architecture.md) |
| 部署细节 / 环境变量 / 备份恢复 | [wiki/deployment.md](wiki/deployment.md) |
| 网关四个端点怎么用 | [wiki/gateway-api.md](wiki/gateway-api.md) |
| 调度、Tier、四级兜底 | [wiki/scheduling.md](wiki/scheduling.md) |
| 定价档位与用量聚合 | [wiki/billing-and-usage.md](wiki/billing-and-usage.md) |
| 43 个管理/网关端点清单 | [wiki/api-reference.md](wiki/api-reference.md) |
| 报错排查 | [wiki/troubleshooting.md](wiki/troubleshooting.md) |
