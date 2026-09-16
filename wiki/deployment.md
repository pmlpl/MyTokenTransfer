# 部署

> 一句话定位：三种跑法（Docker 生产 / Docker dev overlay / 纯本地进程），一份根 `.env`，四个要注意的端口。

相关页面：[architecture.md](architecture.md)（拓扑）· [troubleshooting.md](troubleshooting.md)（跑不起来时看这里）

---

## 1. 前置要求

| 方式 | 需要 |
| --- | --- |
| A 生产 / B dev overlay | Docker Desktop（含 `docker compose` v2） |
| C 纯本地进程 / D 手动装依赖 | Python **3.11+**（`pyproject.toml` 的 `requires-python`）、Node **20+**、一个可达的 MySQL 8.0 |

镜像里的运行时版本（`backend/Dockerfile` / `frontend/Dockerfile`）：`python:3.11-slim`、`node:20-alpine`（构建）、`nginx:1.27-alpine`（托管）。

> Windows：`deploy.ps1` / `dev.ps1` 是 PowerShell 脚本，必须 `powershell -File` 调用。在 Git Bash 里直接跑会失败。

## 2. 端口与访问入口（先记住这张表）

| 模式 | 管理台入口 | 后端 API | MySQL | Redis |
| --- | --- | --- | --- | --- |
| **A 生产**（`docker compose up`） | `http://127.0.0.1:${WEB_PORT}`（默认 `8080`） | 容器内 `8082`，**不对外** | 宿主机 `3307` | 宿主机 `127.0.0.1:6379` |
| **B dev overlay**（`dev.ps1`） | `http://127.0.0.1:5173` | `127.0.0.1:8082`（dev overlay 把它映射出来） | 宿主机 `3307` | 宿主机 `6379` |
| **C 纯本地进程** | `http://127.0.0.1:5175`（Vite HMR） | `127.0.0.1:8082` | 宿主机 `3307` | 不使用 |

### 四个端口坑

1. **`WEB_PORT` 默认是 8080，但本仓库当前 `.env` 设成了 `8085`。** 依据：`frontend/vite.config.ts` 的 dev proxy 目标写死为 `http://127.0.0.1:8085`，`.env.development` 注释也写「转给 prod 8085 nginx」，`Playground.vue` 里的固定接入地址是 `http://100.83.19.7:8085/v1`。自己部署时以 `.env` 为准，并同步改 vite proxy 目标。
2. **MySQL 映射在宿主机 3307，不是 3306**（避开本机已占用的 3306）。backend 容器通过 `host.docker.internal:3307` 绕回宿主机访问 —— Windows Docker Desktop 桥接网络下容器间直连常不通。
3. **容器内 uvicorn 固定监听 8082**，`docker-compose.yml` 用 `expose` 而不是 `ports`，所以宿主机上访问不到 8082（要调试需自己打开注释掉的 `ports` 段）。
4. **Vite 的 `server.port` 是 5175，但 `docker-compose.dev.yml` 用 `--port 5173` 覆盖**，所以容器里跑的是 5173。改端口时两处都要动。

## 3. 方式 A：Docker 生产部署（推荐）

### 用一键脚本

```powershell
cd "C:\Users\MOM\Desktop\学习项目\MyTokenTransfer"
powershell -ExecutionPolicy Bypass -File .\deploy.ps1
```

脚本做四件事：

1. **生成 / 校验 `.env`** —— 不存在则从 `.env.example` 复制并把占位值替换为强随机值（`MASTER_PASSWORD` = `MTT-` + 24 hex、`SECRET_KEY` = 96 hex、`GATEWAY_TOKEN` = `mtt-gw-` + 48 hex、`REDIS_PASSWORD` = 64 hex），同时同步改写 `REDIS_URL`。已存在则**校验**：占位值或长度 < 16 直接报错退出。
2. `docker compose build --pull`
3. `docker compose up -d --wait --wait-timeout 300`
4. `docker compose ps` 打印状态，并输出入口地址

首次运行会把凭据打印在黄色框里 —— **务必抄下来**。

### 子命令

| 命令 | 作用 |
| --- | --- |
| `deploy.ps1`（无参）/ `deploy` | 首次部署 / 升级：build + up + 等健康 |
| `deploy.ps1 start` | 启动已有容器 |
| `deploy.ps1 stop` | 停止 |
| `deploy.ps1 restart` | 重启 |
| `deploy.ps1 status` | 容器健康状态 |
| `deploy.ps1 logs` | 跟踪日志（`--tail=200`，Ctrl+C 退出） |
| `deploy.ps1 migrate` | 手动 `alembic upgrade head`（通常在容器启动时自动跑） |
| `deploy.ps1 down` | 删除容器与网络（**保留 `mysql_data` 卷**，会先问 Y/N） |

### 纯手动

```bash
cp .env.example .env
# 编辑 .env：填 MASTER_PASSWORD / SECRET_KEY / GATEWAY_TOKEN / REDIS_PASSWORD / WEB_PORT
docker compose up -d --build
```

### 容器启动顺序与依赖

```
mysql (healthcheck: mysqladmin ping, 30 次重试)
  ↓ service_healthy
redis (healthcheck: redis-cli ping)
  ↓ service_healthy
backend  → entrypoint.sh:
             ① python 探 TCP 等 MySQL 可达（最多 60 s）
             ② aiomysql 真连一次（最多 90 × 2 s）
             ③ alembic upgrade head
             ④ exec uvicorn 0.0.0.0:8082 --proxy-headers
  ↓ service_healthy (curl /health)
frontend (nginx) → 对外 ${WEB_PORT}:80
```

所以**迁移是全自动的**，不需要任何手工步骤。backend 的 `/health` 返回 `{"status":"ok"}`。

### compose 服务清单

| 服务 | 镜像 | 备注 |
| --- | --- | --- |
| `mysql` | `mysql:8.0` | `caching_sha2_password`、`utf8mb4`、InnoDB buffer pool 256M；卷 `mysql_data` |
| `redis` | `redis:7-alpine` | `requirepass` + AOF 持久化 + `maxmemory 128mb` + `allkeys-lru`；卷 `redis_data`。**应用代码当前未使用** |
| `backend` | 本地构建（多阶段） | `extra_hosts: host.docker.internal:host-gateway`，让容器能绕回宿主机 |
| `frontend` | 本地构建（node 构建 → nginx 托管） | 构建时注入 `VITE_API_BASE=/api` |

## 4. 方式 B：dev overlay（热更新）

后端源码 bind-mount 进容器跑 `uvicorn --reload`，前端换 `node:20-alpine` 跑 Vite HMR，**不构建 nginx 生产镜像**。

```powershell
powershell -File .\dev.ps1            # up + 等健康
powershell -File .\dev.ps1 stop
powershell -File .\dev.ps1 restart
powershell -File .\dev.ps1 down
powershell -File .\dev.ps1 logs
powershell -File .\dev.ps1 status
powershell -File .\dev.ps1 shell-backend
powershell -File .\dev.ps1 shell-frontend
```

等价命令：

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

要点：

- `dev.ps1` 只 build `mysql`（backend / frontend 用 image + bind-mount，无需构建）。
- dev overlay 只跑 `mysql` 服务镜像的构建，因此首次会比 `deploy.ps1` 快很多。
- `.env` 缺失时会自动调一次 `deploy.ps1 deploy` 生成。
- 匿名卷 `/app/node_modules` 保护容器内依赖不被宿主机空目录覆盖。
- **dev 与 prod 共用同一份根 `.env`**，prod 仍占 `${WEB_PORT}`，两者互不影响（只要端口不撞）。

## 5. 方式 C：纯本地进程

```bash
docker compose up -d mysql          # 只起数据库（宿主机 3307）
```

后端与前端按「方式 D」装完依赖后：

```bash
cd backend
alembic upgrade head
uvicorn app.main:app --reload --host 127.0.0.1 --port 8082

cd ../frontend
npm run dev                          # http://127.0.0.1:5175
```

> [!WARNING]
> **Vite dev server（5175）不直连 8082。** `vite.config.ts` 把 `/api` 与 `/v1` 代理到 `http://127.0.0.1:8085`（生产 nginx）。两条路：
> 1. 先 `docker compose up -d` 起生产栈（占 8085），再用 `npm run dev` 做前端 HMR —— **推荐**，也是当前仓库的设计意图（`.env.development` 注释：「不再直连 backend 8082（prod 模式未暴露 8082）」）。
> 2. 把 `vite.config.ts` 里两个 `target` 改成 `http://127.0.0.1:8082`，并确认后端 `CORS_ORIGINS` 放行 5175，才能脱离生产栈独立联调。

### 本地模式的前端基址链路

```mermaid
flowchart LR
  A["浏览器 :5175<br/>axios baseURL=/api"] --> B["Vite dev server<br/>proxy /api /v1"]
  B --> C["目标: 127.0.0.1:8085<br/>生产 nginx"]
  C --> D["backend:8082"]
```

生产模式则是：`浏览器 → nginx（同域 /api、/v1）→ backend:8082`。构建时 Dockerfile 注入 `VITE_API_BASE=/api`，`nginx.conf` 匹配 `/api/` 后 `rewrite ^/api/(.*)$ /$1` 去掉前缀再转发。

> [!NOTE]
> 仓库里**只有 `frontend/.env.development` 一个前端环境文件**（`VITE_API_BASE=/api`）。生产不用 `.env.production`，靠 `Dockerfile` 里的 `RUN VITE_API_BASE=/api npm run build`。

## 6. 方式 D：手动安装依赖

### 后端（`backend/pyproject.toml`）

```bash
cd backend
python -m venv .venv
source .venv/Scripts/activate        # Git Bash on Windows
# .\.venv\Scripts\Activate.ps1       # PowerShell

pip install -e .                     # 运行时依赖
pip install -e ".[dev]"              # pytest / pytest-asyncio / aiosqlite / ruff

cp .env.example .env                 # 见下节变量表
```

运行时依赖清单：`fastapi>=0.115`、`uvicorn[standard]>=0.30`、`sqlalchemy[asyncio]>=2.0`、`aiomysql>=0.2`、`pymysql>=1.1`、`alembic>=1.13`、`pydantic-settings>=2.0`、`cryptography>=42`、`itsdangerous>=2.1`、`httpx>=0.27`、`structlog>=24.1`、`apscheduler>=3.10`、`redis>=5.0`。

> `.venv` 里还能看到 `litellm`、`boto3`、`tiktoken`、`openai` 等包，它们**不是**本项目声明的依赖，是传递依赖带进来的；`backend/app/` 下对这些包零 import。判断依赖请以 `pyproject.toml` 为准。

### 前端（`frontend/package.json`）

```bash
cd frontend
npm install
npm run dev        # 开发服务器（5175）
npm run build      # vue-tsc -b && vite build
npm run preview    # 预览构建产物
```

主要依赖：`vue` 3.5、`vue-router` 4.4、`pinia` 2.2、`naive-ui` 2.45、`echarts` 5.5、`vue-echarts` 7、`axios` 1.7、`@vicons/ionicons5`、`vfonts`；dev：`vite` 5.4、`vue-tsc` 2.1、`typescript` 5.6、`tailwindcss` 3.4。

> IDE 提示：`vue-tsc -b` 会生成 `vite.config.js`，可能**盖住** `vite.config.ts` 的改动。该文件已在 `.gitignore` 里，删掉即可。

## 7. 环境变量全表

### 根 `.env`（Docker Compose 用；示例 `.env.example`）

| 变量 | 必填 | 默认值 | 说明 |
| --- | :---: | --- | --- |
| `MASTER_PASSWORD` | ✅ | — | 管理台登录密码 |
| `SECRET_KEY` | ✅ | — | 会话签名密钥；`ENCRYPTION_KEY` 留空时也由它 PBKDF2 派生 Fernet key |
| `GATEWAY_TOKEN` | ✅ | — | 客户端调 `/v1/*` 的 `Bearer` 令牌 |
| `REDIS_PASSWORD` | ✅ | — | Redis `requirepass`；改了必须同步 `REDIS_URL` |
| `WEB_PORT` | ⬜ | `8080` | 前端 nginx 对外端口 |
| `REDIS_URL` | ⬜ | `redis://redis:6379/0` | 注入 backend 容器，**但应用代码当前未读取** |
| `ENCRYPTION_KEY` | ⬜ | 空 | 密钥静态加密 Fernet key；留空按 `SECRET_KEY` 派生 |
| `SESSION_MAX_AGE` | ⬜ | `604800` | 登录态有效期（秒） |
| `MYSQL_ROOT_PASSWORD` | ⬜ | `rootpass` | MySQL root |
| `MYSQL_DATABASE` | ⬜ | `mytokentransfer` | 库名 |
| `MYSQL_USER` / `MYSQL_PASSWORD` | ⬜ | `mtt` / `mttpass` | 业务账号 |
| `DATABASE_URL` | — | 由 compose 拼装 | 生产由 compose 直接注入，不用手填 |

生成参考：

```bash
openssl rand -hex 48        # SECRET_KEY（96 hex）
openssl rand -hex 24        # GATEWAY_TOKEN 后半段 / REDIS_PASSWORD 用 32
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### `backend/.env`（本地进程跑时用；示例 `backend/.env.example`）

| 变量 | 示例值 | 说明 |
| --- | --- | --- |
| `DATABASE_URL` | `mysql+aiomysql://mtt:mttpass@127.0.0.1:3307/mytokentransfer` | 注意端口 **3307** |
| `MASTER_PASSWORD` / `SECRET_KEY` / `GATEWAY_TOKEN` | — | 同上 |
| `ENCRYPTION_KEY` | 空 | 留空派生 |
| `SESSION_MAX_AGE` | `604800` | |
| `CORS_ORIGINS` | JSON 数组，含 5173/5174/5175 | 本地多端口调试时用 |

`core/config.py` 用 `extra="ignore"`，所以 `.env` 里多写的键不会报错（这也是 `REDIS_URL` 被静默忽略的原因）。

### 运行期环境变量（`os.environ`，未进 `.env.example`）

| 变量 | 默认 | 说明 | 代码 |
| --- | :---: | --- | --- |
| `MTT_TOKEN_BUCKET_RPM` | `20` | 每把 key 每分钟请求上限 | `relay/token_bucket.py` |
| `MTT_TOKEN_BUCKET_BURST` | `10` | 突发容量 | 同上 |
| `MTT_TOKEN_BUCKET_MODEL_RPM` | 空 | 按原生模型名覆盖 RPM，格式 `glm-5.2:10,deepseek-v4-flash:15` | 同上 |

> 想调令牌桶就得在 compose 的 `backend.environment` 里加这几项（或本地 shell 里 `export`），因为 compose 默认不注入它们。

## 8. 数据库运维

### 连接信息

| 项 | 值 |
| --- | --- |
| 宿主机地址 | `127.0.0.1:3307` |
| 库名 | `mytokentransfer`（`MYSQL_DATABASE`） |
| 业务账号 | `mtt` / `mttpass` |
| root | `root` / `rootpass` |

```bash
mysql -h 127.0.0.1 -P 3307 -u mtt -pmttpass mytokentransfer
```

### 迁移

- 迁移目录：`backend/alembic/versions/`（当前 **8 个版本**）
- 容器启动自动 `alembic upgrade head`（`entrypoint.sh`）
- 手动：`deploy.ps1 migrate`，或 `docker compose exec backend sh -c "alembic upgrade head"`
- 本地新建迁移：

```bash
cd backend && source .venv/Scripts/activate
alembic revision --autogenerate -m "add xxx"
alembic upgrade head
```

### 备份与恢复

**① 数据卷级（整库，最彻底）**

```bash
# 备份
docker compose exec -T mysql mysqldump -uroot -prootpass mytokentransfer > backup_$(date +%F).sql
# 恢复
docker compose exec -T mysql mysql -uroot -prootpass mytokentransfer < backup_2026-09-16.sql
```

**② 密钥池级（只搬密钥，跨部署迁移用这个）**

管理台「密钥」页的导出 / 导入会生成**加密包**（`PBKDF2-SHA256`、200,000 次迭代、16 字节随机盐，口令不落库）。对应 `POST /keys/export`、`POST /keys/import`。

> [!CAUTION]
> **`SECRET_KEY` 与历史密文是绑死的。** 换过 `SECRET_KEY` 就再也解不开旧密文。启动时 `main.py` 的 `_scan_undecryptable_keys()` 会扫一遍并在日志里 WARNING 列出解不开的 key id。此时只能删掉那些密钥重新录入 —— 明文不可恢复。

**③ `.env` 备份**

`.gitignore` 已忽略 `.env`、`.env.bak.*`、`*.pem`、`*.key`。备份 `.env` 到仓库外，别提交。

## 9. 生产前检查清单

- [ ] `.env` 里 `MASTER_PASSWORD` / `SECRET_KEY` / `GATEWAY_TOKEN` / `REDIS_PASSWORD` 全是强随机值（长度 ≥ 16，非 `change-me*`）
- [ ] `SECRET_KEY` 已单独离线备份（丢了历史密钥全废）
- [ ] `WEB_PORT` 与 `vite.config.ts` 的 proxy 目标一致（若要用前端 HMR）
- [ ] `docker compose ps` 四个服务全 `healthy`
- [ ] `GET /health` 返回 `{"status":"ok"}`
- [ ] 能用 `MASTER_PASSWORD` 登录管理台
- [ ] `curl $BASE/v1/models -H "Authorization: Bearer $GATEWAY_TOKEN"` 返回模型列表
- [ ] 确认 `backend` 服务的 `ports` 段仍处于注释状态（不对外暴露 8082）
- [ ] 若跨设备访问：`.env` 所在宿主机端口可被组网内其他设备访问（nginx 绑 `0.0.0.0`）

## 10. 端口冲突速查

| 冲突端口 | 谁占的 | 怎么改 |
| --- | --- | --- |
| `3306` | 本机已有 MySQL | 本项目已避开，用 3307（改 compose 的 `3307:3306` 左值） |
| `6379` | 本机已有 Redis | 改 compose 里 `127.0.0.1:6379:6379` 左值 |
| `8080` | 常见的其它 Web 服务 | 改 `.env` 的 `WEB_PORT` |
| `8082` | 本机其它 FastAPI/uvicorn | 需同时改 `backend/Dockerfile` 的 EXPOSE、`entrypoint.sh` 的 uvicorn 端口、`nginx.conf` 的 `proxy_pass`、`vite.config.ts` 的 proxy |
| `5173` / `5175` | 其它 Vite 项目 | 改 `vite.config.ts` 的 `server.port` 与 `docker-compose.dev.yml` 的 `--port` |

## 相关页面

- [troubleshooting.md](troubleshooting.md) —— 起不来、连不上、报错怎么办
- [architecture.md](architecture.md) —— 为什么这么分层
- [../README.md](../README.md) —— 快速开始与使用示例
