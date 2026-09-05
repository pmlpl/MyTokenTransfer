# MyTokenTransfer

自研轻量版 one-api/new-api：**API 密钥管理台 + OpenAI 兼容转发网关**，本地单机自用。

## 核心能力
- 多账号密钥统一管理（加密存储、掩码展示、分组、批量健康测试）
- OpenAI 兼容转发：`/v1/chat/completions`（含流式）、`/v1/embeddings`、`/v1/models`
- 多账号密钥池 + 四级兜底调度（见下文）；支持跨供应商 fallback（带模型名映射）
- 计费：按模型定价（输入/输出/缓存命中），支持高峰/空闲双档（高峰=北京时间周一至五 9:00–12:00、14:00–18:00，其余按空闲档）
- 用量统计：今日小时级波形、多日聚合、按密钥/按模型视图，成本随定价档自动切换
- 告警：密钥状态劣化自动告警，恢复（探针复测或真实流量成功）自动 resolve

## 统一网关接入（给任意 OpenAI 兼容客户端）
```
Base URL: http://127.0.0.1:8085/v1   # 部署模式经前端 nginx 反代，端口 = 根 .env 的 WEB_PORT（默认 8080）
API Key:  <GATEWAY_TOKEN>            # 根 .env，或管理台 Playground 页查看
```
```python
from openai import OpenAI
client = OpenAI(base_url="http://127.0.0.1:8085/v1", api_key="<GATEWAY_TOKEN>")
r = client.chat.completions.create(model="<通道虚拟模型名>", messages=[...])
```
本地 dev 模式（前端 Vite 5175 + 后端 uvicorn 8082）下 Base URL 用 `http://127.0.0.1:8082/v1`。

流程：密钥页录入多账号 key → 建供应商（含模型列表）→ 转发通道页把 key 组成池（T0 主池，可加 T1+ 跨供应商兜底层与模型映射）→ 客户端只需统一地址 + 统一密钥。

## 调度与兜底
请求按 Tier 升序、层内按策略（round_robin / weighted / priority，含会话亲和哈希固定同 key 复用上游前缀缓存）生成候选队列，逐个尝试。四个兜底层级，仅当前面无人可试时才启用后一级：

1. 正常路径：active 密钥；限流冷却到期的直接视为可用（省一个巡检周期）
2. 全池 exhausted：追加最久未测的 exhausted 密钥赌额度窗口已滚动恢复（exhausted 复测周期 10 分钟）
3. 模型池冷却：同一把 key 上某模型积分池 429 只冷却 (key, 模型) 组合 300 秒，不标死整把 key；全组合冷却时追加冷却组合做最后尝试
4. tier TPM 冷却：高并发打满共享 TPM → 整层 60 秒冷却，全层冷却时无视冷却取层内密钥试最后一轮

网络层瞬时故障（TLS 被掐/连接重置）原地重试 3 次；最后尝试拿到 200 即把密钥自动拉回 active 并 resolve 告警。

## 技术栈
- 后端：FastAPI + SQLAlchemy 2.0 async + MySQL 8.0 + Redis + Alembic（转发层用 httpx 直连 OpenAI 兼容端点；原生 Anthropic/Gemini 格式翻译暂未接入，后续如需可再引入 LiteLLM）
- 前端：Vue3 + Vite + TypeScript + Naive UI + ECharts
- 测试：pytest（宿主机 `backend/.venv` 直接跑 `python -m pytest tests/`，内存 SQLite，不依赖 MySQL）
- 部署：docker compose（MySQL 3307 / Redis / backend 8082 / 前端 nginx 对外入口）

## 快速开始（部署模式）
```bash
cp .env.example .env       # 填 MASTER_PASSWORD / SECRET_KEY / GATEWAY_TOKEN / REDIS_PASSWORD / WEB_PORT
docker compose up -d --build
```
后端容器启动时自动跑 `alembic upgrade head`，无需手动迁移。打开 `http://127.0.0.1:<WEB_PORT>`，用 `MASTER_PASSWORD` 登录。

MySQL 映射到宿主机 **3307**（避开本地 3306），backend 容器经 `host.docker.internal:3307` 绕回访问（Windows Docker Desktop 桥接网络容器间直连不通）。查看数据：任意客户端连 `127.0.0.1:3307`，库 `mytokentransfer`，用户 `mtt`/`mttpass`（或 `root`/`rootpass`）。

## 快速开始（本地 dev）
```bash
docker compose up -d mysql          # 只起 MySQL

cd backend
python -m venv .venv
source .venv/Scripts/activate       # Git Bash on Windows
pip install -e .
cp .env.example .env                # 按需修改
alembic upgrade head
uvicorn app.main:app --reload --host 127.0.0.1 --port 8082

cd ../frontend
npm install
npm run dev                          # http://127.0.0.1:5175
```
