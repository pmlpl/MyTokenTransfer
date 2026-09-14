# MTT 密钥×模型细粒度状态改造 — 交接文档

## 会话摘要

本会话完成了 MyTokenTransfer 项目的核心改造：**密钥×模型细粒度状态管理**。解决了"中转站显示可用但实际不可用"和"一个模型额度耗尽导致整把密钥全局失效"两大问题。

改造前，健康检查用 `provider.models[0]`（如 sensenova-6.8-flash-lite）探测密钥，通过后标记 active，但用户实际用 glm-5.2 时额度已耗尽返回 429。同时，`"token plan entitlement exhausted"` 错误会把密钥全局标记为 exhausted，导致同一密钥上的其他模型（deepseek-v4-flash、sensenova-6.8-flash-lite）也全部不可用。

改造后，新增 `key_model_statuses` 表以 `(key_id, model_name)` 为唯一键记录每个密钥在每个原生模型上的独立状态。路由按模型级状态过滤，错误按模型级标记而非污染密钥全局状态，健康检查和巡检都用用户实际模型名探测。

## 已完成改动清单

### 新增文件
- `backend/app/models/key_model_status.py` — KeyModelStatus SQLAlchemy 模型（字段：id, key_id, provider_id, model_name, status, last_error, rate_limited_until, last_tested_at, created_at, updated_at，唯一约束 uq_key_model）

### 修改文件（git diff: 231 insertions, 16 deletions）
- `backend/app/models/__init__.py` — 导入并导出 KeyModelStatus
- `backend/app/relay/balancer.py` — 新增 `get_key_model_status()`、`mark_key_model_status()`（upsert 逻辑）；`ordered_keys()` 新增 `model_name` 参数，过滤时额外检查该密钥在当前模型上的 exhausted/invalid/冷却状态
- `backend/app/relay/gateway.py` — 导入 `mark_key_model_status`；`_build_attempts` 中两处 `ordered_keys` 调用前先 `resolve_native` 并传 `model_name=native`；三个错误处理分支（流式 chat、非流式 chat、embeddings）重构——rate_limited 同时标 tier 冷却和模型级状态；exhausted 且（is_model_pool_quota 或含 "entitlement"）时只标模型级状态（内存冷却 + 持久化），不污染密钥全局状态；invalid 且含 "no access to model" 时只标模型级 invalid；其他错误仍走 mark_key 全局标记
- `backend/app/services/health_check.py` — `_probe()` 和 `test_key()` 新增 `model_name` 参数，指定时跳过列模型接口直接用对话探测；新增 `test_key_model_status(db, kms)` 函数，用 kms.model_name（原生模型名）直接探测并更新 key_model_statuses 状态
- `backend/app/core/scheduler.py` — 新增 `sweep_degraded_model_statuses()` 函数，每 10 分钟巡检所有非 active 的密钥×模型组合，exhausted 延长到 30 分钟复测间隔；注册为定时任务 + 启动后 35 秒首轮执行

### 数据库
- `key_model_statuses` 表已创建（DESCRIBE 验证通过，当前为空表）
- `model_mappings` 表已有 id=1 记录（glm-5.3-free → z-ai/glm-5.3-free, provider_id=9）

### 部署验证
- 后端容器 mtt-backend 健康启动
- 调度器日志确认 `sweep_degraded_model_statuses` 已注册并执行首轮巡检（表为空直接返回）
- glm-5.2 测试请求成功，call_logs 显示路由到 key_id=10、provider_id=7，native_model=glm-5.2
- 所有 6 个 sensenova 密钥（id 10-14,16）均为 active
- 无导入错误、无启动异常

## 本次会话追加改动（令牌桶限流 + 代理修复 + bug 修复 + Dashboard + 按渠道探测 + 生图接口）

### 新增文件
- `backend/app/relay/token_bucket.py` — 令牌桶事前控速模块。按 `(key_id, native_model)` 维度维护桶，默认 20 RPM/把、突发容量 10；`acquire_token()` 无令牌返回 False，由 gateway 跳过该 key 换下一把；速率可通过环境变量 `MTT_TOKEN_BUCKET_RPM`（全局默认）、`MTT_TOKEN_BUCKET_BURST`（突发容量）、`MTT_TOKEN_BUCKET_MODEL_RPM`（按模型覆盖，格式 `glm-5.2:10,deepseek-v4-flash:15`）配置
- `scripts/Fix-TailscaleProxyBypass.ps1` — 确保 `100.*` 在 Windows 代理绕过列表中（VPN 重连覆盖后重跑即可）

### 修改文件
- `backend/app/relay/gateway.py` — 导入 `acquire_token`；`route_chat_completions` 和 `route_embeddings` 中，`resolve_native` 之后、发请求之前加令牌桶检查，无令牌则 `continue` 换下一把 key；新增 `route_image_generations()`（/v1/images/generations 生图接口，复用 tier fallback + 密钥池 + 令牌桶，超时 120s）
- `backend/app/services/health_check.py` — 修复遗留 bug：缺少 `import structlog` 和 `log = structlog.get_logger("health_check")`，导致 `test_key_model_status` 中 `log.info()` 报 `name 'log' is not defined`，调度器巡检全部失败；新增 `test_channel(db, channel)` 按渠道探测函数
- `backend/app/api/relay.py` — 新增 `POST /v1/images/generations` 路由；新增 `_IMAGE_MODEL_HINTS` + `_is_image_channel()` 图片渠道识别；`POST /v1/chat/completions` 命中图片渠道时自动分发到 `route_image_generations(..., wrap_as_chat=True)`
- `backend/app/relay/gateway.py` — `route_image_generations()` 新增 `wrap_as_chat` 参数和 `_wrap_image_as_chat()`（把生图 URL 包装成 `![image](url)` 的 chat.completion 响应）；新增 `_build_image_payload()`（从 messages 提取 prompt、剔除 chat 专用字段），agent 只调 chat/completions 也能出图；**新增 B.AI 网关 read body failed 原地重试**：Zcode 请求体 ~2MB 触发 B.AI 网关（实测 >1MB 挂起/失败）返回 `400001 read body failed`（400 不可重试、Zcode 端直接报错），MTT 现在识别该错误后同一 key 原地重试一次（sleep 3s），重试仍失败才按原逻辑处理
- `backend/app/api/channels.py` — 新增 `POST /channels/{channel_id}/test` 按渠道测试接口
- `backend/app/api/dashboard.py` — 新增 `GET /stats/key-model-statuses` 接口（join api_keys + providers）
- `frontend/src/views/Dashboard.vue` — 新增"密钥×模型 细粒度状态"表格（状态标签/最近错误/最近测试）+ 渠道选择器 + "按渠道测试"按钮 + 结果摘要；选中渠道后表格按该渠道的 (key_id, native_model) 集合过滤
- `frontend/src/api/index.ts` — 新增 `KeyModelStatusItem` 类型、`stats.keyModelStatuses()`、`channels.test()`

### 代理冲突修复（非代码）
- Windows 注册表 `HKCU\...\Internet Settings\ProxyOverride` 追加 `100.*`，使 Tailscale 网段（100.64.0.0/10）流量绕过 WestWorld VPN 的 Privoxy 代理，解决其他 agent 通过 Tailscale IP 访问 MTT 时的 500 Internal Privoxy Error

### AgnesAI 生图配置（已验证可用）
- provider id=10（Agnes，base_url=`https://apihub.agnes-ai.com`，models 含 agnes-2.5-flash / agnes-3.0-flash / agnes-image-2.0-flash / agnes-image-2.5-flash）
- channel 9 = Agnes-image-flash（agnes-image-2.5-flash）→ tier 35 → provider 10，key 20（agnes，active）
- **免费用户限流**：文本 20 RPM 实际；生图按分辨率 1K=20 RPM / 2K=10 RPM / 3K=1 RPM；429 需等 1 分钟
- **验证**：GET /v1/models 200；POST /v1/images/generations 经 MTT 中继 200，返回 `{"data":[{"url":"https://platform-outputs.agnes-ai.space/..."}]}`
- 生图错误分类：400 "is an image model" 走 `False, None` 不标状态，不污染密钥

### 验证结果
- 10 并发 burst 测试：15 个请求全部 success，0 失败，分散到 key 11/12/13/14 四把密钥
- 调度器 `sweep_degraded_model_statuses` 恢复正常，成功恢复 2 个模型状态
- 令牌桶自动为每把 key×模型创建（日志 `token_bucket_created`，rpm=20, burst=10）
- 外部网站经 VPN 代理访问正常（未影响翻墙）

## 关键设计决策

1. **与现有内存级机制共存而非替换**：代码库已有 `mark_model_quota_limited`/`model_quota_is_limited`（in-memory dict，300 秒冷却，匹配 "allocated quota exceeded"）。新方案保留它作为快速短期冷却，同时用 `key_model_statuses` 表做持久化记录（dashboard 可见、重启不丢、巡检可恢复）。

2. **"entitlement" 关键词扩展**：SenseNova 的 `"token plan entitlement exhausted"` 之前不匹配 `is_model_pool_quota`（只匹配 "allocated quota exceeded"），导致落入 else 分支被全局标记。现在 gateway 中用 `_is_model_quota = is_model_pool_quota(err_text) or "entitlement" in err_text.lower()` 统一识别为模型池级额度问题。

3. **ordered_keys 中 resolve_native 的位置**：在 `_build_attempts` 中每个 tier 先 resolve_native 再传给 ordered_keys，而不是在 ordered_keys 内部解析。因为 ordered_keys 是通用函数，不应依赖 channel 上下文。

4. **健康检查指定模型名时跳过列模型接口**：`/v1/models` 只验证密钥有效性，不验证具体模型可用性。指定 model_name 时直接走对话探测，确保测的就是用户用的模型。

## 项目环境关键信息

- 项目根目录：`C:\Users\MOM\Desktop\学习项目\MyTokenTransfer`
- 后端容器：mtt-backend，MySQL 容器：mtt-mysql（宿主机端口 3307，容器内 3306），root 密码 rootpass，业务库 mytokentransfer，业务用户 mtt/mttpass
- 前端访问：http://127.0.0.1:8085，后端容器内端口 8082（经 nginx 反代）
- GATEWAY_TOKEN：`mtt-gw-64a0612d12eb8d22fdf0bcc23faeb7fb5b41e2b1c36f54b9`
- 部署命令：`docker compose build backend; docker compose up -d backend`（build 走 stderr exit 1 但实际成功，看容器状态确认）
- PowerShell 坑：`&&` 非法用 `;`，`curl` 是 Invoke-WebRequest 别名用 `curl.exe`，JSON 请求体用临时文件 `--data-binary "@file.json"` 避免引号转义

### providers 表
- id=3 glm(base_url=https://token.sensenova.cn, models=["glm-5.2"])
- id=4 deepseek（无密钥）
- id=7 sensenova(base_url=https://token.sensenova.cn/v1, models=["sensenova-6.8-flash-lite","deepseek-v4-flash","glm-5.2","kimi-k3"])
- id=8 b.ai(https://api.b.ai/v1)
- id=9 tokenrouter(https://api.tokenrouter.com/v1, models=["z-ai/glm-5.3-free"])

### channels 表
- id=1 glm-5.2；id=4 deepseek-v4-flash-vision-exp；id=5 sensenova-6.8-flash-lite；id=6 glm-5.3-flash；id=7 glm-5.3-free

### api_keys
- 10-14,16 → provider 7(sensenova)；18(bai) → provider 8；19(tokenrouter) → provider 9

### SenseNova 免费额度（按模型分别计算，5 小时窗口）
- sensenova-6.8-flash-lite：每 5 小时 1500 次
- glm-5.2：每 5 小时 250 次
- deepseek-v4-flash：每 5 小时 500 次

## 网络与代理配置（Tailscale × 翻墙代理冲突修复）

### 问题现象
其他 agent 通过 Tailscale IP 访问 MTT 中继时，返回 `500 Internal Privoxy Error`（`Could not load template file no-server-data`）。

### 根因
- 本机运行 WestWorld VPN（`D:\WestWorldVPN-For-Windows-1.07\`），其 Privoxy 监听 `127.0.0.1:21882`，全部流量转发到 SOCKS5 `127.0.0.1:21881` → VPN 隧道
- Windows 系统代理设为 `http://127.0.0.1:21882`，代理绕过列表（ProxyOverride）只有 `localhost;127.*;10.*;172.16-31.*;192.168.*`
- **Tailscale 使用 100.64.0.0/10 网段**（本机 Tailscale IP: `100.83.19.7`），不在绕过列表中
- 请求到 `100.83.19.7:8085` 被 Privoxy 拦截，而 VPN 隧道无法路由到 Tailscale 网络，导致无响应 → Privoxy 返回 500

### 修复
- 已将 `100.*` 加入 Windows 注册表 `HKCU\...\Internet Settings\ProxyOverride`
- 修复脚本：`scripts/Fix-TailscaleProxyBypass.ps1`（若 VPN 重连覆盖了绕过列表，重新运行即可）
- 验证：`Invoke-WebRequest http://100.83.19.7:8085/health` 返回 200；完整 `/v1/chat/completions` 请求成功；外部网站代理仍正常

### 注意事项
- 本机 agent 建议用 `http://127.0.0.1:8085`（天然绕过代理），Tailscale IP 主要给远程设备用
- 远程 Tailscale 客户端若也开了翻墙代理，需同样在其系统代理绕过列表中加 `100.*`
- WestWorld VPN 的 PAC 文件（`127.0.0.1:21883/pac/`）中 `subnetIpRangeList` 也不含 100.64.0.0/10，若切换到 PAC 模式需另行处理

## 未完成项 / 后续方向

1. ~~**Dashboard 展示模型级状态**~~ ✅ 已完成：`GET /stats/key-model-statuses` 接口（join api_keys + providers），Dashboard.vue 新增"密钥×模型 细粒度状态"表格（密钥/供应商/模型/状态标签/最近错误/最近测试），支持刷新。验证返回 6 条 sensenova glm-5.2 记录，全部 exhausted 可见。

2. ~~**健康检查页面按渠道探测**~~ ✅ 已完成：新增 `POST /channels/{channel_id}/test` 接口（health_check.py 的 `test_channel()`），对渠道每个 tier 先 resolve_native 得到实际原生模型名，再用该模型名探测所有密钥，结果写入 key_model_statuses。Dashboard 模型级状态卡片新增渠道选择器 + "按渠道测试"按钮，测试后自动刷新状态表并显示可用/失败摘要。

3. **令牌桶增强（已实现基础版，可迭代）**：已实现 `token_bucket.py`，按 (key_id, native_model) 事前控速，无令牌时跳过换下一把 key。后续可加：①全部 key 桶空时短暂等待再放行（当前直接 fall through 到兜底逻辑）；②TPM 维度限流（当前只按请求数 RPM，不按 token 数）；③Dashboard 展示各桶当前令牌数。默认 20 RPM/把，可通过环境变量调。

4. **DeepSeek 官方 API Key 配置**：provider id=4（deepseek 官方）的 tier 11 仍无密钥，兜底层为空。用户用 dsh 直接测试 DeepSeek 官方 API 缓存命中率的结果尚未反馈。

5. **glm-5.3-flash 和 deepseek-v4-flash-vision-exp 的模型映射确认**：glm-5.3-flash 走 b.ai(provider 8)，deepseek-v4-flash-vision-exp 走 provider 3，需确认是否需要 model_mappings 记录。

6. **git 提交**：所有改动均未 commit（backend/app/relay/errors.py、gateway.py、balancer.py、api/dashboard.py、frontend/src/api/index.ts、frontend/src/views/Usage.vue、models/key_model_status.py、models/__init__.py、services/health_check.py、core/scheduler.py）。

## 建议下一会话使用的 Skills

- **sheet** — 如果需要导出 key_model_statuses 或 call_logs 做数据分析
- **doubao-data-analysis** — 分析 call_logs 中各模型的成功率、限流率、额度消耗趋势
- **html** — 如果需要做模型级状态的可视化 dashboard
- **doubao-app-builder** — 如果要做完整的前端状态管理页面

## 复制以下发给新会话

> 请先读取交接文档 `mtt-handoff-key-model-status.md`，然后按照里面的方案继续工作。
