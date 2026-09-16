# 更新日志 · MyTokenTransfer

本文件记录 MyTokenTransfer 的版本变更。早期条目由 `git log` 回溯整理（单分支 `master`，无 tag），新建条目请遵循 [README.md](README.md) 里约定的格式。

- **当前版本**：`0.1.0`（尚未打 tag，见 `backend/pyproject.toml`、`frontend/package.json`、`app/main.py` 的 `FastAPI(version=)`）
- **时间跨度**：2026-09-01 ~ 2026-09-14（35 次提交）
- **日期口径**：作者本地日期（北京时间）

格式约定：

- **新增** — 新功能
- **修复** — bug 修复
- **变更** — 行为/接口调整（可能不兼容）
- **文档** — 仅文档/注释
- **工程** — 构建、依赖、配置、CI、杂项

---

## [未发布] — 2026-09-16

**文档**

- 重写 `README.md`：补齐安装步骤（Docker 部署 / dev overlay / 纯本地进程 / 手动装依赖）、环境变量全表、使用示例（curl、openai SDK、流式、embeddings、生图、跨设备接入、密钥池备份）、贡献指南（提交前检查项与编码约定）、排错表。
- 新建 `wiki/` 文档目录：`README.md`（阅读方法）、`index.md`（目录）、`changelog.md`（本文件），以及 `architecture.md`、`deployment.md`、`gateway-api.md`、`scheduling.md`、`billing-and-usage.md`、`api-reference.md`、`troubleshooting.md` 七个正文页。

**澄清（文档与代码长期不一致，本次在文档中显式标注）**

- `README.md` 原先声称 Redis 用于「session pin / 限流 / 热点缓存」，实际 `backend/app/` 内**零 Redis 调用**，`config.py` 也无 `REDIS_URL` 字段；冷却状态均在单进程内存。
- 原 README 的网关 Base URL 写 `8085` 却注释「默认 8080」；实际口径为 `WEB_PORT` 默认 `8080`、本仓库当前 `.env` 设为 `8085`。
- 原 README 未提及 `/v1/images/generations` 端点与 `context_length`、峰谷 `off_peak_*` 定价字段。

---

## [0.1.0] — 2026-09-01 ~ 2026-09-14

### 2026-09-14

**新增**

- `feat(relay): 密钥×模型细粒度状态管理 + 令牌桶事前控速` — 密钥状态由「整把 key 好坏」细化到 `(key, 模型)` 维度；新增按 `(key, 原生模型)` 的令牌桶，在请求发出前限速（默认 20 RPM / burst 10，可用 `MTT_TOKEN_BUCKET_RPM` 等环境变量覆写）。
  ⚠️ 影响面：新增 `key_model_statuses` 表与迁移；`relay/balancer.py` 的失败标记路径改为优先标模型级。

**修复**

- `fix(health): 生图模型探测不走 chat/completions，退到列模型接口验证密钥` — 生图模型上游对 chat 请求回 400，改为识别生图特征词后走列模型接口探测。

### 2026-09-06

**新增**

- `feat(usage): 今日波形改滚动近 24 小时窗口，跨午夜连续` — 原「今日按自然日」在午夜断档，改为滚动 24 小时窗口。

**文档**

- `docs: README 标题嵌入 MTT 品牌像素 Logo`；`docs: README 对齐现状（部署方式/网关入口/兜底机制/计费）`。

**工程**

- `test(relay): 四级兜底调度回归测试 + 修复过时的 ordered_keys 断言` — 四级兜底从此有回归保护。

### 2026-09-05

本日为兜底机制成型的关键一天。

**新增**

- `feat(relay): 全池耗尽兜底最后尝试 + 网络层重试 + exhausted 快速复测` — 全池 `exhausted` 时追加最久未测的 key 赌额度窗口滚动恢复；TLS/连接重置类瞬时故障原地重试 3 次（退避 1s）；`exhausted` 复测周期由长周期收紧为 **10 分钟**。
- `feat(pricing): 高峰/空闲双档定价` — 同一模型可配高峰与空闲两档价，用量成本随档位自动切换。
- `feat(usage): 今日小时级聚合视图（北京时间波形数据）`。
- `feat(usage): 趋势分析按模型视图支持指标切换`。

**修复**

- `fix(relay): tier TPM 冷却期最终兜底，补齐最后一级 503 缺口` — 原来全层冷却后直接 503，改为无视冷却取层内密钥试最后一轮。
- `fix(relay): 全池模型池冷却时兜底尝试，不再 503`。
- `fix(relay): 模型池级额度 429 不再标死整把密钥` — 某模型积分池 429 只冷却 `(key, 模型)` 组合 300 秒，避免误伤同 key 下其它模型。
- `fix(pricing): 峰谷窗口改为 DeepSeek 官方定义并按官方价校准价表` — 收敛为周一至周五 09:00–12:00、14:00–18:00。
- `fix(usage): 切换条改用 radio-button 修复文字不显示`、`fix(usage): segment 切换条移出卡片头修复宽度塌陷` — 纯 UI 修复。

### 2026-09-04

**新增**

- `feat(relay): 缓存亲和路由提升命中率，用量接口新增各密钥命中率统计` — 按消息稳定前缀（4096 字符预算）哈希固定到同一把上游 key，复用其前缀缓存。
- `feat(ui): 接入 MTT 品牌像素 Logo（侧栏/登录页/favicon）`。
- `feat(keys): ID 列、批量测试进度条`。
- `feat(usage): 默认查今天`；`feat(dashboard): 渲染已删除密钥的名称`。

**修复**

- `fix(alerts): auto-resolve unresolved alerts when key recovers to active` — 密钥恢复 `active` 时自动 resolve 未处理告警。
- `fix(health): disabled 保护、批量并发探测、anthropic 兜底、探测保护期、exhausted 巡检间隔` — 批量测试并发 5；非 active 密钥探测成功后有 **5 分钟保护期**，避免状态横跳。
- `fix(usage): 修复成本科学计数法显示`。
- `fix(api): 用量统计未知密钥名称显示为「未知」`。

### 2026-09-03

**新增**

- `feat(pricing): 模型计价新增缓存命中价计费并完成 Docker 生产部署` — 计费维度扩展为 输入 / 输出 / **缓存命中** 三类，成本更贴近上游实际账单。
- `feat(gateway): 支持 1M 上下文中转，新增 context_length 字段` — 定价表带上下文长度，`/v1/models` 一并返回。

**工程**

- `chore(git): 忽略 .env 密钥备份文件(.env.bak.*)`。

### 2026-09-02

**新增**

- `feat(keys): 加密包导出/导入 — 备份迁移密钥` — PBKDF2-SHA256、200,000 次迭代、随机盐，口令不落库；对应 `POST /keys/export`、`POST /keys/import`。

**修复**

- `fix(health): 统一错误分类，额度用尽(429 quota) 标 exhausted 而非 rate_limited` — 分类错会导致「额度耗尽」被当成「限流」而反复重试打爆上游。

### 2026-09-01（项目起点）

**新增**

- `init: MyTokenTransfer 密钥管理台 + OpenAI 兼容转发网关` — 项目初始化。
- `feat: /v1/embeddings 端点 + 核心单测 + 清理 npm 锁文件`。
- `feat(log): structlog 全局配置 + /v1/* 请求结构化日志中间件 + 决策点日志`。
- `feat(stats): DailyUsage 预聚合 + rollup + 仪表盘改查聚合表` — 用量页不再扫 `call_logs` 明细，改读预聚合小表。

**变更**

- `refactor(relay): 拆 gateway 到 balancer + accounting` — 把「HTTP 编排」「选 key 调度」「计费落库」三件事拆开，是后来四级兜底能持续演进的架构基础。
- `fix(frontend): .env.development 端口 8082→8000, 修前端全页 Network Error`。

**工程**

- `build(frontend): 从 pnpm 切回 npm`。
- `config: 固定端口 前端 5175 / 后端 8082; 删 vite 编译产物`。

---

## 尚未有版本号的功能缺口（待办）

以下为当前代码中已存在但文档中标注为「未接线 / 未接入」的部分，列在此处便于追踪：

| 项 | 状态 | 说明 |
| --- | --- | --- |
| Redis | 声明未使用 | `redis>=5.0` 已声明、compose 已起服务，但 `app/` 内零调用；多副本部署会各自持有调度状态 |
| 原生 Anthropic / Gemini 格式翻译 | 未接入 | 请求体翻译未做（探测层已支持 `anthropic` / `gemini_models` 两种方式）；如需可考虑引入 LiteLLM |
| LICENSE | 缺失 | 无 `LICENSE` 文件，`pyproject.toml` / `package.json` 也无 license 字段 |
| `.zcode/plans/` | 内容过时 | 早期实施方案写的是 LiteLLM / asyncmy / Element Plus，实际落地为 httpx / aiomysql / Naive UI |
| 多副本水平扩展 | 不可用 | 依赖单进程内存状态；需先把冷却与令牌桶状态外置 |
