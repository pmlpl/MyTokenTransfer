# 文档目录 · MyTokenTransfer Wiki

> 全部文档的索引页。阅读方法见 [README.md](README.md)，版本变更见 [changelog.md](changelog.md)。

**项目**：MyTokenTransfer —— 自研轻量版 one-api/new-api，API 密钥管理台 + OpenAI 兼容转发网关
**版本**：`0.1.0`（`backend/pyproject.toml` / `frontend/package.json` / `FastAPI(version=)`）
**规模**：43 个 HTTP 端点 + `/health` · 12 张数据表 · 59 个单元测试 · 8 个迁移版本 · 11 个前端页面

---

## 入门

| 文档 | 内容 | 适合 |
| --- | --- | --- |
| [README.md](README.md) | **wiki 阅读方法**、文件约定、维护规则 | 第一次进 wiki |
| [../README.md](../README.md) | 项目主 README：定位、核心能力、快速开始、使用示例、贡献指南 | 第一次接触项目 |
| [architecture.md](architecture.md) | 模块划分、请求数据流、12 张表的数据模型 | 想搞懂整体结构 |

## 核心机制

| 文档 | 内容 | 关键代码 |
| --- | --- | --- |
| [scheduling.md](scheduling.md) | Tier 调度、三种策略、**四级兜底**、冷却参数、令牌桶事前限速 | `app/relay/balancer.py`、`app/relay/token_bucket.py` |
| [billing-and-usage.md](billing-and-usage.md) | 计费维度、峰谷双档窗口、用量聚合链路、时间口径 | `app/relay/accounting.py`、`app/services/usage.py` |
| [deployment.md](deployment.md) | 三种部署方式、环境变量全表、端口口径、备份与迁移 | `docker-compose*.yml`、`deploy.ps1`、`dev.ps1` |

## 参考

| 文档 | 内容 |
| --- | --- |
| [gateway-api.md](gateway-api.md) | 对外 `/v1/*` 四个端点的契约、鉴权、流式、生图自动分发、接入示例 |
| [api-reference.md](api-reference.md) | 43 个端点的清单速查（管理台 + 网关） |

## 运维

| 文档 | 内容 |
| --- | --- |
| [troubleshooting.md](troubleshooting.md) | 现象 → 原因 → 处理；含已知的文档/代码不一致点 |
| [changelog.md](changelog.md) | 按日期记录的变更日志（当前 36 次提交，2026-09-01 起） |

---

## 图示导航

```
README.md        项目是什么 + 怎么跑起来
   │
   ├─ wiki/README.md ──── 这份 wiki 怎么读（入口）
   │
   ├─ 入门 ──────── architecture.md      模块与数据流
   │
   ├─ 核心机制 ──── scheduling.md        调度 / 四级兜底 ★ 最核心
   │                billing-and-usage.md 计费 / 用量
   │                deployment.md        部署 / 配置
   │
   ├─ 参考 ──────── gateway-api.md       对外 /v1/* 契约
   │                api-reference.md     43 个端点清单
   │
   └─ 运维 ──────── troubleshooting.md   排错
                    changelog.md         变更日志
```

---

## 三条主线速查

**① 一次请求怎么走完**（细节 → [architecture.md](architecture.md)）

```
客户端 /v1/chat/completions
  → relay.py 查通道（virtual_model → Channel）
  → gateway.route_chat_completions 编排 (tier, key) 尝试序列
  → token_bucket 事前检查速率（无令牌换 key）
  → balancer 生成候选队列 + 处理失败标记
  → httpx 直连上游 OpenAI 兼容端点
  → accounting 写 CallLog + 概算成本
  → 返回（stream=true 时 SSE 透传）
```

**② 四级兜底**（细节 → [scheduling.md](scheduling.md)）

```
T0 正常 active key
 → 全池 exhausted：赌额度窗口已滚动恢复（复测周期 10 min）
 → 模型池冷却：(key, 模型) 组合只冷却 300 s，不标死整把 key
 → tier TPM 冷却：整层 60 s，全层冷却时无视冷却试最后一轮
 → 仍失败 → 503；网络瞬时故障原地重试 3 次
```

**③ 一次调用的成本怎么算出来**（细节 → [billing-and-usage.md](billing-and-usage.md)）

```
usage 里的 prompt / completion / cached_tokens
  × 定价表单价（高峰档 or 空闲档，按北京时间判定）
  → call_logs.cost
  → DailyUsage 预聚合（每日 02:30 rollup + 当天实时重算）
  → 用量页读聚合表出图
```

---

## 维护这份目录

新增页面时，请同时更新：

1. 本文件的对应分组表格；
2. [README.md](README.md) 的「命名与职责」表；
3. `../README.md` 末尾的「文档地图」表。

删除 / 重命名页面时，记得全仓库搜一遍相对链接（`wiki/`、`../wiki/`）。
