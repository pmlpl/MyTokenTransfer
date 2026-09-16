# 调度与兜底

> 一句话定位：这是本项目最核心的机制 —— 一次请求从上到下有四层「还有谁能试」的兜底，只有四层全部用尽才返回 503。目标是把「上游偶发限流/额度窗口滚动/网络抖动」这些噪音吸收掉，不让客户端看见。

相关页面：[architecture.md](architecture.md)（数据流）· [gateway-api.md](gateway-api.md)（503 是什么）· [troubleshooting.md](troubleshooting.md)（一直 429 怎么办）

**代码位置**：`backend/app/relay/balancer.py`（选 key + 状态机，11643 字符）· `backend/app/relay/gateway.py`（HTTP 编排，29151 字符）· `backend/app/relay/token_bucket.py`（事前限速）· `backend/app/relay/errors.py`（错误分类）

---

## 1. 概念：Tier 与虚拟模型

一个**虚拟模型**（`channels.virtual_model`）就是一个对外暴露的模型名，它下面挂着一串**Tier**（`channel_tiers`），Tier 之间是**兜底关系**：

```
virtual_model = "deepseek-v4-flash"        ← 客户端填这个
   │
   ├─ T0  order=0  provider=A  ──< 密钥池 { key1(weight=3), key2(weight=1) }   ← 主池
   ├─ T1  order=1  provider=B  ──< 密钥池 { key5, key6 }                     ← 跨供应商兜底
   └─ T2  order=2  provider=C  ──< 密钥池 { key9 }                            ← 再兜一层
```

- **Tier 之间**：按 `order` 升序尝试。T0 只要能给出可试的 key，就**永不**碰 T1。
- **Tier 之内**：按策略排序（`round_robin` / `weighted` / `priority`），叠加**缓存亲和哈希**。
- **跨供应商**：靠 `model_mappings` 把虚拟模型名翻译成各供应商的原生模型名（`deepseek-v4-flash` → A 家叫 `deepseek-v4-flash`、B 家叫 `glm-5.2`）。

`channel.max_retries` 是**每层的尝试上限**（`max(1, max_retries)`），圈定该层最多试几把 key，防止一个池子很大时打光所有重试预算。

## 2. 候选队列（`_build_attempts`）—— 四层是怎么拼出来的

`gateway._build_attempts()` 按顺序做四件事，每一件对应一级兜底：

```
① 正常路径
   for tier in sorted(tiers, key=order):
       if tier_is_rate_limited(tier.id):  continue        ← 整层 TPM 冷却 → 跳过
       native = resolve_native(virtual, tier.provider_id)
       keys   = ordered_keys(tier, strategy, affinity, model_name=native)
       for k in keys[:max_retries]:
           if model_quota_is_limited(k.id, virtual):  continue   ← 模型池冷却 → 跳过
           attempts.append((tier, k))

② 全池 exhausted 兜底
   for tier in tiers:
       if tier_is_rate_limited(tier.id):  continue
       for k in last_resort_keys(tier, max_retries):   ← 最久未测的 exhausted 密钥
           attempts.append((tier, k))

③ 全池模型池冷却兜底（仅当 ① + ② 产出空 attempts 时）
   for tier in tiers:
       if tier_is_rate_limited(tier.id):  continue
       for k in keys[:max_retries]:
           if model_quota_is_limited(k.id, virtual):
               attempts.append((tier, k))              ← 明知在冷却也塞进来

④ tier TPM 最终兜底（仅当上面仍为空时）
   for tier in tiers:                                   ← 注意：不再判 tier_is_rate_limited
       for k in final_resort_keys(tier, max_retries):
           attempts.append((tier, k))
```

四层都空 → 直接返回 `503`：

```json
{"error": {"message": "无可用密钥（全部失效/限流）", "type": "no_available_key"}}
```

### 为什么「明知在冷却还要试」

②③④ 三层的共同逻辑是：**额度窗口 / 积分池是按时间窗口滚动的，恢复往往快于冷却期**。

- 试一次的代价：一次快速的 429（几乎不消耗时间与额度）。
- 不试的代价：直接 503，客户端看到失败。

所以在「别无选择」时，宁可赌一把。**这三层只在前面无人可试时才启用，正常路径零影响** —— 这是这套设计的核心取舍。

### 守卫豁免（`_cooldown_exemptions`）

兜底塞进来的组合带着冷却标记，正常路径的守卫会跳过它们。所以 `route_chat_completions` 在进入尝试循环前先算两个豁免位：

```python
allow_quota_limited = attempts 非空 and 所有 attempt 都处于模型池冷却
allow_tier_limited  = attempts 非空 and 所有 attempt 都处于 tier TPM 冷却
```

- 全部带冷却 → 豁免守卫（否则兜底白加）。
- 只有部分冷却 → 不豁免，冷却中的照旧跳过（避免浪费重试次数）。

## 3. 尝试循环里发生了什么

进入 `for tier, key in attempts:` 之后，每个候选按顺序做这些检查，任一失败就 `continue` 换下一个：

| 顺 | 检查 | 不通过时 |
| :---: | --- | --- |
| 1 | `tier_is_rate_limited(tier.id)` 且未豁免 | 跳过 |
| 2 | `model_quota_is_limited(key.id, virtual)` 且未豁免 | 跳过 |
| 3 | `resolve_native()` 取原生模型名 | — |
| 4 | **`acquire_token(key.id, native)` 令牌桶** | 跳过（换下一把 key，**不硬失败**） |
| 5 | `base_url_for(tier.provider)` 非空 | 记 `供应商 X 缺少 base_url`，跳过 |
| 6 | `decrypt_value(key.key_value)` 解密钥 | **把 key 标 `invalid`**（"密文解密失败"），跳过 |
| 7 | 发请求（`_send_with_retry`） | 见下 |

### 请求构造的两个细节

```python
_build_payload(body, native, stream):
    payload = dict(body)                 # 原样透传
    payload["model"]  = native_model     # 覆盖为原生模型名
    payload["stream"] = stream
    _normalize_thinking(payload)         # 修正 thinking / reasoning_effort 矛盾组合
    if stream and "stream_options" not in payload:
        payload["stream_options"] = {"include_usage": True}   # 流式也要拿到 usage
```

- **`_normalize_thinking`**：智谱 API 要求 `reasoning_effort=none` 时 `thinking=disabled`，反之 `enabled`。部分客户端（如 zcode）会发矛盾组合导致上游 `400 invalid thinking type`，这里自动对齐。
- **超时**：`httpx.Timeout(600.0, connect=10.0)` —— 总 600 s，建连 10 s（长响应/长思考模型友好）。

### 网络层重试（`_send_with_retry`）

```python
_NETWORK_RETRY   = 3
_NETWORK_BACKOFF = 1.0   # 秒
```

- **只在连接/发送阶段抛异常时触发**（流未打开、请求未发出）。
- **不换 key**，原地重试 —— 因为同一池子的所有 key 指向同一上游域名，换 key 没有意义。典型场景：SNI 阻断、NAT 抖动导致 TLS 握手被掐 / 连接重置。
- **HTTP 状态码一律不在这里重试**，交给 `classify_error` 分类。

### 上游网关 `read body failed` 原地重试

```
_GATEWAY_READ_BODY_ERRORS = ("read body failed", "read_body_failed", "reading request body")
```

实测某上游网关对 **> 1MB 请求体**偶发返回 `400001 read body failed`。这不是密钥状态问题，同一上游换 key 无意义，所以**在流式路径上原地重试 1 次**（`same_key_retry < 1`），`await asyncio.sleep(3)` 后重发。

## 4. 失败分类与状态标记

### 分类（`relay/errors.py: classify_error(status_code, err_text)`）

返回 `(retry, mark, msg)` 三元组：是否换 key 重试、标记成什么状态、给用户的消息。

> ⚠️ 关键规则：**速率关键词优先于额度关键词**。429 响应体里常常同时出现「rate limit」和「quota」字样，误判成额度耗尽（`exhausted`）会让密钥被长时间冷落，实际只是瞬时限流。

### 标记路径（三条分支，粒度不同）

```python
if mark == rate_limited:
    mark_tier_rate_limited(tier.id)                       # 整层 60 s 冷却（同池共享 TPM）
    mark_key_model_status(..., model_name=native)         # 同时记 (key, 模型) 状态

elif mark == exhausted and is_model_pool_quota(err_text):
    mark_model_quota_limited(key.id, virtual)             # 只冷却 (key, 模型) 300 s
    mark_key_model_status(..., model_name=native)         # 不标死整把 key

elif mark == invalid and "no access to model" in err_text:
    mark_key_model_status(..., model_name=native)         # 密钥有效，只是没这个模型的权限

else:
    mark_key(key, mark)                                   # 整把 key 标这个状态
```

| 上游情况 | 标记范围 | 理由 |
| --- | --- | --- |
| 429 限流 | **整个 tier** + (key,模型) | 同一 provider 的 key 共享上游 TPM 池，一个限流全限流 |
| 429 且是模型池级额度耗尽 | **(key, 模型)** | 只是该模型的积分池空了，同 key 其它模型还能用 |
| 401/403 且 `no access to model` | **(key, 模型)** | 密钥本身有效，只是该模型无权限 |
| 401/403 其它 | 整把 key → `invalid` | 密钥真失效 |
| 密文解不开 | 整把 key → `invalid` | 换过 `SECRET_KEY`，只能人工重录 |
| 其它可重试错误 | 按 `mark` | — |

`is_model_pool_quota()` 还会识别 `entitlement` 关键词（如 SenseNova 的 token plan）。

## 5. 四级兜底总表（就是 README 里那张，带参数）

| 级别 | 触发条件 | 做什么 | 关键参数 |
| :---: | --- | --- | --- |
| **1** | 常规 | `active` 密钥参与调度；限流冷却到期的直接视为可用 | — |
| **2** | 全池 `exhausted` | 追加**最久未测**的 `exhausted` 密钥，赌额度窗口已滚动恢复 | `exhausted` 复测周期 **10 分钟** |
| **3** | 所有 key 的 `(key, 模型)` 积分池同时冷却 | 追加冷却中的组合做最后尝试，不再 503 | 模型池冷却 **300 秒** |
| **4** | 所有 tier 都在 TPM 冷却 | 无视冷却标记，取层内密钥试最后一轮 | tier TPM 冷却 **60 秒** |

### 成功时的额外收益

```python
async def _revive_if_exhausted(key, db):
    if key.status == KeyStatus.exhausted:
        await mark_key(key, KeyStatus.active, db, message="真实请求成功，自动恢复")
```

最后一次尝试拿到 **200** 说明额度窗口确实恢复了 —— 直接把这把 key 拉回 `active`，比等探针复测（最长 10 分钟巡检）快得多，且告警随 `mark_key` 自动 resolve。

### 流式路径的特殊性

```
200 到手 → 流已打开 → 故障转移窗口关闭
```

一旦拿到 200，即使**流中途出错也不再切换 key**（已经吐了一部分内容给客户端，换 key 会导致响应内容错乱）。stream 上出现的异常只记日志，不会重试。

## 6. 令牌桶：事前限速（`relay/token_bucket.py`）

与「事后冷却」互补，目标是从**源头**减少 429：

| 机制 | 时机 | 粒度 | 行为 |
| --- | --- | --- | --- |
| 事后冷却 | 已被上游 429 **之后** | (key,模型) / tier | 冷却期内跳过 |
| **令牌桶** | 请求发出**之前** | (key, 原生模型) | 无令牌 → 换下一把 key |

### 参数

| 环境变量 | 默认 | 说明 |
| --- | :---: | --- |
| `MTT_TOKEN_BUCKET_RPM` | `20` | 每把 key 每分钟请求数（补充速率 = RPM / 60 令牌/秒） |
| `MTT_TOKEN_BUCKET_BURST` | `10` | 突发容量（桶满时可连发几个请求不用等补充） |
| `MTT_TOKEN_BUCKET_MODEL_RPM` | 空 | 按原生模型名覆盖 RPM，格式 `glm-5.2:10,deepseek-v4-flash:15` |

默认值取 20 RPM 的理由（写在代码注释里）：官方接口上限 5 RPS = 300 RPM，免费层实际 RPM 限额未公布，取 20 留足余量。

### 三条设计原则（代码注释原文摘要）

1. **单进程 uvicorn，无锁** —— 事件循环内不存在 `await` 的算术操作没有竞态。
2. **令牌桶永不导致硬失败** —— 全部 key 都无令牌时，由 gateway 短暂等待后放行。
3. **速率可按模型覆盖** —— 不同模型配额不同。

> 这三条也解释了为什么多副本部署不成立：桶是**进程内全局**的，两个副本就是两份额度账本。

## 7. 缓存亲和路由

```python
_AFFINITY_PREFIX_BUDGET = 4096          # 字符预算
affinity = routing_affinity(virtual, body)
```

- 取 `虚拟模型 + 消息稳定前缀`（预算 4096 字符）做哈希。
- 同一会话随历史增长，**前缀不变** → 哈希稳定 → 固定同一把上游 key → **复用该 key 在上游的前缀缓存**，命中率与成本都受益。
- 共享同一 `system` + 开头消息的会话会得到**相同哈希** → 聚到同一 key，最大化共享前缀缓存。
- 与 `model_pool` / `exhausted` 的亲和：同池但被冷却时，亲和不再强制，交给正常调度。

## 8. 定时任务（`core/scheduler.py`）

APScheduler（`AsyncIOScheduler`），随 FastAPI `lifespan` 启动与关闭：

| 任务 | 周期 | 行为 |
| --- | --- | --- |
| `sweep_degraded_keys` 巡检 | **每 10 分钟** | 重测所有非 `active` 且非 `disabled` 的密钥，成功则转回 `active`；其中 `exhausted` 的距上次测试不足 **10 分钟**则跳过（避免频繁命中上游 429 quota） |
| 用量 rollup | **每日 02:30** | `CallLog` → `DailyUsage` 预聚合 |
| 启动回填 | 启动时一次 | 回填近 **60 天** 用量 |

### 探测保护期

```python
_DEGRADED_PROBE_GRACE_MINUTES = 5      # health_check.py
_DEGRADED_PROBE_GRACE_MINUTES = 5
_DEGRADED_PROBE_GRACE_MINUTES             # 非 active 密钥探测成功后的保护期
_DEGRADED_PROBE_GRACE_MINUTES
```

1-token 探针太轻量：刚被判 degraded 的 key 可能「小请求能过、大请求仍限流」，探测成功就恢复 `active` 会让状态来回横跳。保护期内探测成功**不恢复**。

### 探测自身的重试与并发

| 常量 | 值 | 说明 |
| --- | :---: | --- |
| `_PROBE_ATTEMPTS` | `3` | 网络层瞬时故障重试次数 |
| `_RETRY_BACKOFF` | `1.0` s | 退避间隔 |
| `_BATCH_CONCURRENCY` | `5` | 批量测试并发度 |

## 9. 全部常量速查

| 常量 | 值 | 位置 | 含义 |
| --- | :---: | --- | --- |
| `_RATE_LIMIT_COOLDOWN` | `60` s | `balancer.py` / `health_check.py` | 429 限流冷却 |
| `_MODEL_QUOTA_COOLDOWN` | `300` s | `balancer.py` | (key, 模型) 积分池冷却 |
| `_AFFINITY_PREFIX_BUDGET` | `4096` 字符 | `balancer.py` | 缓存亲和前缀预算 |
| `_NETWORK_RETRY` | `3` | `gateway.py` | 网络层原地重试次数 |
| `_NETWORK_BACKOFF` | `1.0` s | `gateway.py` | 网络层退避 |
| HTTP 超时 | `600` s 总 / `10` s 建连 | `gateway.py` | httpx Timeout |
| `same_key_retry` 上限 | `1`（sleep 3 s） | `gateway.py` | 上游 read body failed 原地重试 |
| `SWEEP_INTERVAL_MINUTES` | `10` | `scheduler.py` | 劣化密钥巡检间隔 |
| `EXHAUSTED_RETEST_MINUTES` | `10` | `scheduler.py` | exhausted 最小复测间隔 |
| `_DEGRADED_PROBE_GRACE_MINUTES` | `5` | `health_check.py` | 探测成功保护期 |
| `_PROBE_ATTEMPTS` / `_RETRY_BACKOFF` | `3` / `1.0` s | `health_check.py` | 探测重试 |
| `_BATCH_CONCURRENCY` | `5` | `health_check.py` | 批量探测并发 |
| `MTT_TOKEN_BUCKET_RPM` | `20` | `token_bucket.py` | 每 key RPM |
| `MTT_TOKEN_BUCKET_BURST` | `10` | `token_bucket.py` | 突发容量 |

## 10. 回归测试

改这一层之前先看测试，改完必须跑：

```bash
cd backend && python -m pytest tests/ -v
```

| 文件 | 用例 | 覆盖 |
| --- | :---: | --- |
| `test_balancer.py` | 17 | Tier 调度、三种策略、状态机标记 |
| `test_fallback_chain.py` | 12 | **四级兜底尝试序列**（核心回归防线） |
| `test_alert_auto_resolve.py` | 10 | 告警自动 resolve |
| `test_classify_error.py` | 7 | 错误分类（速率 vs 额度优先级） |
| `test_health_classify.py` | 6 | 探测结果分类 |

> 测试跑在内存 SQLite 上，`conftest.py` 里有 autouse 夹具会**清空 balancer 的进程内冷却状态** —— 否则用例之间会互相污染。

## 相关页面

- [architecture.md](architecture.md) —— 这些逻辑在整体链路中的位置
- [billing-and-usage.md](billing-and-usage.md) —— 成本与用量
- [troubleshooting.md](troubleshooting.md) —— 一直 429 / 一直 503 怎么查
- [api-reference.md](api-reference.md) —— 查看密钥状态与告警的接口
