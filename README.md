# MyTokenTransfer

自研轻量版 one-api/new-api：**API 密钥管理台 + OpenAI 兼容转发网关**，本地单机自用。

## 核心能力
- 多账号密钥统一管理（加密存储、掩码展示、分组、批量健康测试）
- OpenAI 兼容转发：统一 `/v1/chat/completions` 端点
- 多账号密钥池 + 故障转移：某 token 用完/受限时自动切换到下一个账号；支持跨供应商 fallback（在 OpenAI 兼容端点之间，带模型名映射）
- 调用日志（含流式 usage）；用量图表与告警为三期

## 统一网关接入（给任意 OpenAI 兼容客户端）
```
Base URL: http://127.0.0.1:8082/v1
API Key:  <GATEWAY_TOKEN>（backend/.env，或管理台 Playground 页查看）
```
```python
from openai import OpenAI
client = OpenAI(base_url="http://127.0.0.1:8082/v1", api_key="<GATEWAY_TOKEN>")
r = client.chat.completions.create(model="<通道虚拟模型名>", messages=[...])
```
流程：密钥页录入多账号 key → 建供应商（含模型列表）→ 转发通道页把 key 组成池（T0 主池，可加 T1+ 跨供应商兜底层与模型映射）→ 客户端只需统一地址 + 统一密钥。

## 技术栈
- 后端：FastAPI + SQLAlchemy 2.0 async + MySQL 8.0 + Alembic（转发层用 httpx 直连 OpenAI 兼容端点；原生 Anthropic/Gemini 格式翻译暂未接入，后续如需可再引入 LiteLLM）
- 前端：Vue3 + Vite + TypeScript + ElementPlus + ECharts
- 部署：docker-compose 起 MySQL，前后端本地 dev 运行

## 快速开始

### 1. 起 MySQL
```bash
docker compose up -d mysql
```
容器 MySQL 映射到宿主机 **3307**（避开本地 3306）。查看数据：
- 任意客户端连 `127.0.0.1:3307`，库 `mytokentransfer`，用户 `mtt`/`mttpass`（或 `root`/`rootpass`）
- 或进容器命令行：`docker exec -it mtt-mysql mysql -uroot -prootpass mytokentransfer`

### 2. 后端
```bash
cd backend
python -m venv .venv
source .venv/Scripts/activate   # Git Bash on Windows
pip install -e .
cp .env.example .env            # 按需修改
alembic upgrade head
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

### 3. 前端
```bash
cd frontend
pnpm install
pnpm dev
```

打开 http://127.0.0.1:5173 ，用 `.env` 里的 `MASTER_PASSWORD` 登录。

## 文档
详见实施计划（项目根 `PLAN.md` 待补）。
