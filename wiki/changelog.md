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

## [未发布] — 2026-11-05

**修复**

- `fix(relay): TPM 计费口径改为只算未命中缓存的部分` — 上游（SenseNova）的 TPM 只算**未命中前缀缓存**的输入 + 输出，缓存命中部分几乎不占配额。实测依据：同一段 48k 文本连发，命中后不再计入限流额度；用户侧统计印证日用量 5500 万 token 里 5400 万是「输入（命中）」却全程未限流。网关此前按 `prompt_tokens` 全额计入，把自己限得比上游还死——一条 70k prompt / 69k 命中的真实 agent 请求，对上游只占约 1k，按全额记账却要吃掉整个分钟预算。新增 `billable_tokens()`，结算路径（同步 + 流式）统一改用该口径。
- `fix(relay): TPM 预算种子从 40 万降到 15 万，贴合实测上游边界` — 2026-11-05 容器内直连上游实测（单 key，48k 冷请求每 10 秒一发）：`t+0/10 429 → t+21/32/42 200 200 200 → t+50/60/70 429 → t+82 200`，即每分钟约放行 **15.6 万** 未命中缓存 token。旧种子 40 万是真实上限的 **2.5 倍**——网关按 40 万放行、上游按 15.6 万拒收，正是「账号显示有额度却被限流」的直接原因。上限同步从 400 万收到 60 万（留 4 倍余量给大配额账号）。
- `fix(balancer): 账号冷却阶梯从 60/120/180/300 收到 20/40/60/90 秒` — 实测上游 429 后 **20–32 秒**即恢复，旧阶梯把 20 秒的可恢复限流锁成最长 5 分钟死时间，是 ZCode 长时间「重新连接中」的直接来源。新阶梯首档略短于实测恢复时间，撞一次 429 自动升档自纠，封顶 90 秒仍能止住高频重试。
- `fix(relay): 全池短冷却时在网关内部等掉，而非返回 429 让客户端重连` — 新增 `MTT_RELAY_SHORT_COOLDOWN_WAIT`（默认 25 秒）：剩余冷却 ≤ 该值就 `sleep` 后继续本次请求。客户端拿到 429 会先做自己的退避再重建连接（ZCode 表现为「重新连接中 5/10」），比原地等慢得多；实测原地等 20 秒成功率远高于让客户端重试。

**文档**

- `docs: 同步 TPM 计费口径与新默认值到 README / wiki` — README 参数表、`wiki/scheduling.md` 限速章节（新增「计费口径」小节）、`wiki/deployment.md` 参数表、`.env.example`、`docker-compose.yml` 注释全部对齐到 15 万种子 / 60 万上限 / 20-90 秒阶梯。

**测试**

- 新增 `backend/tests/test_upstream_limits.py`（13 例），把三个实测数字钉成回归：缓存命中不计入 TPM、阶梯首档落在 20–30 秒、预算种子落在 10–25 万。
- 修正 `test_oversized_request_fits_after_learning_its_size`：原断言「换账号能继承已学到的规模」是错的（预算状态是 per-account 的），旧种子 40 万 > 365k 让它假通过；拆成「单条超大请求必须放行」与「账号自己学到请求规模」两例。

---

## [未发布] — 2026-11-02

**修复**

- `fix(relay): TPM 令牌桶改为滚动 60 秒窗口 + 自适应预算，修复事前控速完全失效` — 旧实现 TPM=30000、burst 比例 0.5 → 桶容量 15000，而实测单次 agent 请求平均 33.6 万 prompt tokens（最大 96 万），`try_consume` 永远失败 → `any_tpm_headroom` 永远 False → 每个请求都走「顾问放行」→ 只剩事后补扣，桶被打成深负值。现改为按 (账号, 原生模型) 的滚动 60 秒窗口记账：窗口内已用 + 本次预估 ≤ 预算才放行；放不下则短暂等待（≤ `TPM_MAX_WAIT`，默认 8 秒）再试，等不及返回 429 + **精确 `Retry-After`**。
- `feat(relay): TPM 预算 AIMD 自学习` — SenseNova 未公开 TPM 上限，故预算由上游反馈学习：成功则 `max(budget, actual × 1.15)`；每 10 次成功 ×1.02 慢速探测；429 且窗口消耗 ≥ 预算 50% 则 ×0.6（每 30 秒最多降一次）；窗口很空却 429 不动预算（多半是并发/RPM 限制，误降会震荡）。钳制在 `TPM_MIN`–`TPM_MAX`。
- `fix(relay): 失败请求退回 TPM 预留与 RPM 令牌` — 此前 base_url 缺失 / 解密失败 / 网络异常 / 上游非 200 都不退预扣，失败请求持续抽干桶。现在这些路径全部 `_refund_budget()`。
- `fix(balancer): 账号限流冷却改阶梯退避，止住「每 60 秒原样重试全池」` — 固定 60 秒冷却在持续限流场景下每分钟重试全池 key，每次撞 429 又重置计时器，客户端表现为"一直在重试"。改为 60 → 120 → 300 → 600 → 900 秒逐级拉长，任意一次成功调用 `reset_account_backoff()` 清零。
- `fix(relay): 全账号冷却短路的 Retry-After 用真实剩余秒数` — 此前硬编码 `60`，退避到 15 分钟时提前重试只白挨一次 429。
- `fix(relay): 错误分类优先用结构化 error.code` — SenseNova 把可恢复的分钟级限流包成 `429 {"message":"inference exceeds tpm/rpm limit","code":"insufficient_quota"}`，只看关键词会误判成额度耗尽。现在 `RateLimitExceeded.*` / `EndpointTPMExceeded` 等 code 直接判 `rate_limited`；额度类 code 需同时满足"正文无速率关键词"才判 `exhausted`。新增 `extract_error_code()`，并把解析出的 code 打进 `upstream_error_classified` 日志。
- `feat(relay): 预估 token 按模型校准` — `prompt 字符数÷3` 对中文明显低估（一个汉字约 1 token）。按原生模型维护 `actual/预估` 的 EMA（钳制 0.5–5.0），下次预估乘上它。

**工程**

- `config: TPM 配置项补齐到 .env.example 与 docker-compose` — 新增 `MTT_TOKEN_BUCKET_TPM`（种子 40 万）/ `TPM_MIN` / `TPM_MAX` / `TPM_MAX_WAIT`，移除已无意义的 `TPM_BURST_RATIO`。`token_bucket.py` 在 `os.environ` 缺失时回退读 `backend/.env`，修掉「写进 backend/.env 不生效」的坑。
- `test: 新增 test_token_bucket.py（20 例）` — 覆盖滚动窗口准入/等待秒数、超大单请求一次性放行、settle/release、AIMD 升降与上下限钳制；conftest 一并清理 TPM 状态与退避阶梯，避免跨用例串扰。全量 89 例通过。

---

## [未发布] — 2026-09-24

**新增**

- `feat(relay): 令牌桶加 TPM 维度，从源头减少 DeepSeek-Flash 的 TPM 429` — 新增按 (账号×模型) 的 TPM 令牌桶，与原有 RPM 桶互补。`acquire_token` 接收 `est_tokens` 参数，按预估 token 数事前扣减；响应返回后按实际 `usage` 修正桶（`tpm_correct`）。DeepSeek-Flash 等快生成模型单次请求可烧上万 token，原 RPM 桶只扣请求数无法感知，导致 TPM 配额在上游被打满后才开始事后冷却。
  ⚠️ 影响面：`token_bucket.py` 新增 TPM 桶 + `try_consume`/`deduct`/`refund`/`tpm_correct`；`gateway.py` 的 `route_chat_completions` 加预估 + 预检 + 事后修正。embeddings/images 保持 RPM-only 不变。

**修复**

- `fix(relay): 全部账号刚被限流时直接返回 429，跳过 final_resort 避免慢 502` — 所有候选 key 的账号限流冷却剩余 >0 时直接返回 429 + `Retry-After: 60`，不再走 final_resort 兜底。final_resort 对每把冷却中的 key 串行发 doomed 请求（含 3 次 network_retry × ConnectTimeout 10s），把 502 延迟拉到 30-40s → dsh 超时杀任务。`balancer.py` 加 `account_rate_limited_remaining()`。
- `fix(relay): 冷却短路阈值降为 0，彻底消除 final_resort 死循环` — 阈值 50s 留了 10s 缝隙，三把 key 标记时间不同导致 `all()` 判断在缝隙里不成立 → 放行 → 429 又重置冷却 → 死循环。设 0 = 只要全在冷却中就短路。
- `fix(balancer): per-model rate_limited 冷却过期后立即回池，不等巡检` — `ordered_keys` 的 per-model 状态检查没有 fast-path：`kms.status=rate_limited` 时无条件跳过（不看 `rate_limited_until` 是否过期），导致免费 key 被 429 后即使 60 秒冷却已过期也要等 10 分钟巡检复测才回 `active` → 这 10 分钟一直走付费兜底层。改为与密钥级 fast-path 同理。

**工程**

- `config: DeepSeek-Flash 令牌桶 RPM=5/TPM=10000，适配 SenseNova 限流` — Docker 日志确认原生模型名 `deepseek-flash`，原默认 RPM=20 远超上游实际限额触发 `EndpointRPMExceeded`；TPM=30000 也偏高。docker-compose.yml 加 `MTT_TOKEN_BUCKET_MODEL_RPM` 和 `MTT_TOKEN_BUCKET_MODEL_TPM` 环境变量。

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
| Redis | 热缓存已接线 | `ModelMapping` / `ModelPricing` 缓存已接 Redis（commit fc14053），不可用时降级 DB；冷却与令牌桶状态仍在单进程内存，多副本部署不共享 |
| 原生 Anthropic / Gemini 格式翻译 | 未接入 | 请求体翻译未做（探测层已支持 `anthropic` / `gemini_models` 两种方式）；如需可考虑引入 LiteLLM |
| LICENSE | 缺失 | 无 `LICENSE` 文件，`pyproject.toml` / `package.json` 也无 license 字段 |
| `.zcode/plans/` | 内容过时 | 早期实施方案写的是 LiteLLM / asyncmy / Element Plus，实际落地为 httpx / aiomysql / Naive UI |
| 多副本水平扩展 | 不可用 | 依赖单进程内存状态；需先把冷却与令牌桶状态外置 |
