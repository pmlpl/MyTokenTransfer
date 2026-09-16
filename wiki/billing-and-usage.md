# 计费与用量

> 一句话定位：把每次上游调用返回的 `usage` 换算成钱写进明细表，再按日预聚合成用量页读的小表。核心是「缓存命中单独计价 + 高峰/空闲双档」这两件事。

相关页面：[gateway-api.md](gateway-api.md)（`usage` 从哪来）· [scheduling.md](scheduling.md)（调用为什么失败）

**代码位置**：`backend/app/relay/accounting.py`（计费与落库）· `backend/app/services/usage.py`（按日预聚合）· `backend/app/models/pricing.py`（单价表）· `backend/app/api/dashboard.py`（用量查询）

---

## 1. 计费三维度

一次调用的成本由三类 token 各自的单价构成：

| 维度 | 来源字段 | 单价列 |
| --- | --- | --- |
| 缓存命中输入 | `prompt_tokens_details.cached_tokens`（OpenAI 格式）或 `prompt_cache_hit_tokens`（DeepSeek 格式） | `cached_input_price` |
| 未命中输入 | `prompt_tokens − cached_tokens` | `input_price`（或空闲档 `off_peak_input_price`） |
| 输出 | `completion_tokens` | `output_price`（或空闲档 `off_peak_output_price`） |

### 计算式（`accounting.py: write_call_log`）

```python
uncached = max(0, pt - cached)

cost = round(
    cached   * cached_input_price / 1_000_000
  + uncached * in_price           / 1_000_000
  + ct       * out_price          / 1_000_000,
    6,
)
```

要点：

- **单位是「每 1M tokens」**，单价列类型 `Numeric(12, 6)`。
- 结果 `round(..., 6)`，保留 6 位小数。**不会出现科学计数法**（曾经有这个显示 bug，已在 `b71d9fe` 修复）。
- `cached` 为空时退化为「全部输入按未命中价」计费，不会算错。
- 缓存命中来源兼容两种上游格式：先看 OpenAI 的 `prompt_tokens_details.cached_tokens`，为 0 再看 DeepSeek 的 `prompt_cache_hit_tokens`。

### 用哪个模型名查价

```python
model_for_price = native_model or virtual_model
```

优先用**原生模型名**查价表 —— 因为同一虚拟模型可能映射到不同供应商的不同模型，价格不同。原生名为空时才退回虚拟名。

> 所以定价表里 `model_name` 建议填**上游原生模型名**。若你只填了虚拟模型名，只有在 `native_model` 为空时才会命中。

### 落库

用**独立 DB 会话**写 `call_logs`，理由写在文件 docstring 里：

> 用独立会话写，即使流式响应中途客户端断开、主会话已关闭，日志也能落库。

写入字段：`created_at`(UTC)、`channel_id`、`tier_order`、`provider_id`、`key_id`、`virtual_model`、`native_model`、`prompt_tokens`、`completion_tokens`、`total_tokens`、`cached_tokens`、`cost`、`latency_ms`、`status`、`error`（截断到 500 字符）、`stream`。

> 两个运维价值很高的字段：`tier_order`（这次请求最终由第几层兜底服务的）和 `status`/`error`（失败原因）。Playground 页调试时也会显示「本次由 key #N（T<层> 层）服务」。

---

## 2. 高峰 / 空闲双档定价

### 时段定义

```python
_PEAK_WINDOWS = ((time(9, 0), time(12, 0)), (time(14, 0), time(18, 0)))
```

判定逻辑（`_in_peak`）：

```
now_utc + 8h  →  北京时间
  ├─ 周六 / 周日      →  全天空闲
  └─ 周一 ~ 周五      →  09:00 ≤ t < 12:00  或  14:00 ≤ t < 18:00  →  高峰
                          其余时间（含 12:00–14:00 午休）        →  空闲
```

这套窗口是 **DeepSeek 官方口径**（`fix(pricing): 峰谷窗口改为 DeepSeek 官方定义并按官方价校准价表`），版本历史里改过一版。

### 档位选择

```python
if _in_peak(now):
    in_price, out_price = input_price, output_price                     # 高峰/标准档
else:
    in_price  = off_peak_input_price  or input_price                    # 空闲档
    out_price = off_peak_output_price or output_price
```

| 情形 | 行为 |
| --- | --- |
| 供应商无分时定价 | 两列 `off_peak_*` 留 `0` → 全天按标准（高峰）价计费 |
| 供应商有分时折扣 | 填上 `off_peak_*`，空闲时段自动用折扣价 |

> ⚠️ `off_peak_*` 填 `0` 表示「未设置」而非「免费」—— 计费时会 `or` 回退到标准价。代码注释明确写了这一点。缓存命中价 `cached_input_price` **没有**空闲档，全天一个价。

### 用量页的成本会自动切换档位

`call_logs.cost` 是**写入时刻**按当时档位算好的固定值，不会随后重算。所以看历史数据时，成本反映的是当时是否处于高峰 —— 这是有意的（账单口径），不是 bug。

---

## 3. 单价表（`model_pricings`）

| 列 | 类型 | 含义 |
| --- | --- | --- |
| `model_name` | `String(128)` **唯一，带索引** | 模型名（建议填上游原生模型名） |
| `input_price` | `Numeric(12,6)` | 每 1M 输入 tokens（高峰/标准档） |
| `cached_input_price` | `Numeric(12,6)` | 每 1M **缓存命中**输入 tokens |
| `output_price` | `Numeric(12,6)` | 每 1M 输出 tokens（高峰/标准档） |
| `off_peak_input_price` | `Numeric(12,6)` | 每 1M 输入 tokens（空闲档），`0` = 未设置 |
| `off_peak_output_price` | `Numeric(12,6)` | 每 1M 输出 tokens（空闲档），`0` = 未设置 |
| `context_length` | `Integer` | 上下文长度（tokens），`0` = 未设置；会出现在 `/v1/models` 响应里 |
| `currency` | `String(8)` | 默认 `CNY` |
| `note` | `String(255)` | 备注 |

`context_length` 的用途：支持百万级上下文中转时，客户端需要用 `/v1/models` 得知模型能吃多少上下文 —— 所以这个值会一并返回。

定价表不配的后果：`cost` 恒为 `0`，用量页成本图是平的，但 **token 计数照常统计**（因为那是上游 `usage` 直接给的）。

管理接口：`GET/POST /pricing`、`PUT/DELETE /pricing/{id}`。见 [api-reference.md](api-reference.md)。

---

## 4. 用量聚合链路

```
call_logs（明细，每次调用一行）
    │  services/usage.py: rollup_date(db, day) —— 先 DELETE 该天旧行，再聚合 INSERT
    ▼
daily_usage（每天 × 每 key × 每模型 一行）
    │  Dashboard / Usage 页直接读这张小表
    ▼
前端图表（ECharts）
```

### 为什么要有预聚合

`call_logs` 会随使用量无上限增长。用量页如果每次扫明细做 `GROUP BY`，随着表变大查询会越来越慢。所以维护一张预聚合小表，读路径只碰它。

### `rollup_date` 的关键性质

| 性质 | 说明 |
| --- | --- |
| **幂等** | 先 `DELETE` 该天的行再重新聚合插入，重复调用结果一致 |
| **可补录** | 新增/补录的明细会被算进来（重算即可） |
| **读路径可安全调用** | 单用户场景下不会与写冲突 |

模型维度用 `func.coalesce(func.nullif(CallLog.native_model, ""), CallLog.virtual_model)` —— **`native_model` 为空时回退到 `virtual_model`**，与计费侧的 `model_for_price` 口径一致。

### 触发时机

| 时机 | 行为 |
| --- | --- |
| 每日 **02:30** | APScheduler 定时 rollup 前一天 |
| 启动时 | 回填近 **60 天**（`rollup_recent`） |
| 打开用量页时 | `GET /stats/usage` 内部先对**今天**调一次 `rollup_date`，保证当天数据新鲜 |
| 打开仪表盘时 | 同上 |

> 所以「今天的用量」不是实时流，而是**每次查询时重算当天**，最多滞后到你上次打开页面。

---

## 5. 时间口径

| 位置 | 口径 | 说明 |
| --- | --- | --- |
| `call_logs.created_at` | **UTC**（`datetime.now(timezone.utc).replace(tzinfo=None)`） | 存储层统一 UTC |
| `GET /logs` 返回 | **东八区** | `logs.py` 里 `_UTC_OFFSET = timedelta(hours=8)`，返回前 `+ _UTC_OFFSET` 再 `isoformat()` |
| 峰谷判定 | **东八区** | UTC + 8 后判 weekday / time |
| `daily_usage.day` | 自然日 | 按 UTC 日边界聚合 |
| 「近 24 小时波形」 | **滚动窗口** | 不是自然日 —— `feat(usage): 今日波形改滚动近 24 小时窗口，跨午夜连续` 把跨午夜断档修掉了 |

> `GET /stats/usage` 的 `days` 参数范围 `1..365`，默认 `14`（读预聚合表，不再扫明细）。

---

## 6. 统计接口返回什么

### `GET /stats/overview`

```json
{
  "providers": 3,
  "keys": 12,
  "active": 9,
  "by_status": {"active": 9, "rate_limited": 1, "exhausted": 2}
}
```

### `GET /stats/usage?days=14`

按日聚合，每天一行：

```json
{
  "date": "2026-09-16",
  "calls": 128,
  "prompt_tokens": 45210,
  "completion_tokens": 18300,
  "total_tokens": 63510,
  "cached_tokens": 12000,
  "cost": 1.234567
}
```

另有「按日 × 按模型」维度（供趋势页画按模型堆叠面积图，支持指标切换）与「各密钥命中率统计」维度。

### `GET /stats/key-model-statuses`

返回 (key, 模型) 级细粒度状态 —— 对应「某模型积分池被冷却但密钥其它模型仍可用」这套机制。仪表盘上能看到。

### `GET /logs?limit=&offset=&channel_id=&status=`

调用日志分页（`limit` 上限 500，默认 100）。单条字段见第 1 节「落库」。这是排查「为什么这次请求慢/贵/失败」的第一手材料。

---

## 7. 计费口径的已知取舍

| 取舍 | 说明 | 影响 |
| --- | --- | --- |
| 成本按**写入时档位**快照 | 不随后重算 | 历史成本不可比；账单口径正确，分析口径需注意 |
| 定价表需手工维护 | 上游改价不会自动同步 | 不填 = 成本恒 0 |
| 查价用原生模型名 | 同一虚拟模型映射多家时价格各自独立 | 定价表要按原生名逐条填 |
| 单次调用最贵的那条会算进成本 | 重试失败的尝试**不写** `call_logs`（只有最终结果写一条） | 兜底试错产生的上游消耗**不计入成本** —— 成本偏低 |
| `latency_ms` 是**最终成功那次**的耗时 | 包含前置失败尝试的时间吗？会（`t0` 在循环内每次尝试重置） | 反映的是单次尝试耗时，不含队列等待 |

> 最后两条值得注意：这套计费是**自用估算**，不是与上游账单对账的工具。上游实际扣费会包含你兜底试错时的消耗。

## 相关页面

- [gateway-api.md](gateway-api.md) —— `usage` 字段的来源与格式
- [scheduling.md](scheduling.md) —— 失败尝试为什么不计成本
- [architecture.md](architecture.md) —— 聚合链路在整体架构中的位置
- [api-reference.md](api-reference.md) —— 统计与日志接口
