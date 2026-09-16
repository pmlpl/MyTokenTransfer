# MyTokenTransfer Wiki · 阅读方法

这份 wiki 是 `README.md` 的**展开版**。README 回答「这东西是什么、怎么跑起来」，wiki 回答「为什么这么设计、细节参数在哪、出问题怎么定位」。

---

## 一、这份 wiki 怎么读

### 推荐路径（按身份选）

| 你的身份 | 建议顺序 |
| --- | --- |
| **第一次接触** | `README.md` 的「快速开始」→ [architecture.md](architecture.md) → [gateway-api.md](gateway-api.md) |
| **要把它跑起来** | `README.md` 的「快速开始」→ [deployment.md](deployment.md) → 「常见问题排错」 |
| **要接自己的客户端** | [gateway-api.md](gateway-api.md)（四个端点 + 鉴权）→ [billing-and-usage.md](billing-and-usage.md)（成本怎么看） |
| **要改调度 / 兜底逻辑** | [scheduling.md](scheduling.md) → `backend/app/relay/balancer.py` → `backend/tests/test_fallback_chain.py` |
| **要写脚本调管理台 API** | [api-reference.md](api-reference.md) |
| **遇到报错** | [troubleshooting.md](troubleshooting.md) |
| **想知道最近改了什么** | [changelog.md](changelog.md) |
| **找某个文档** | [index.md](index.md)（全量目录） |

### 三分钟速读版

1. 它是个 **API Key 池 + OpenAI 兼容网关**：客户端只认「一个地址 + 一个令牌」。
2. 数据流：客户端 → nginx → FastAPI `/v1/*` → `relay/gateway` 编排尝试序列 → `relay/balancer` 选 key → httpx 直连上游 → `relay/accounting` 落库计费。
3. 最核心的设计是**四级兜底**（[scheduling.md](scheduling.md)）：正常 → 全池耗尽赌恢复 → 模型池级冷却 → tier TPM 级冷却。四级都试过才 503。
4. 所有状态（冷却、令牌桶）都在**单进程内存**里，Redis 起了但没接线。

---

## 二、文档文件约定

### 命名与职责

| 文件 | 职责 | 什么时候改它 |
| --- | --- | --- |
| `README.md`（wiki 根目录下这个） | 阅读方法与导航入口 | 新增 wiki 页面时 |
| `index.md` | 全量目录 + 维护规则 | 新增 / 删除 / 重命名页面时 |
| `changelog.md` | 按日期记录的变更日志 | **每次功能或修复提交后追加一条** |
| `architecture.md` | 模块划分、数据流、数据模型 | 改动模块边界或表结构时 |
| `deployment.md` | 部署、环境变量、备份 | 改动 compose / 脚本 / 配置项时 |
| `gateway-api.md` | 对外 `/v1/*` 端点契约 | 改动网关行为时 |
| `scheduling.md` | Tier 调度与四级兜底 | 改动 `relay/balancer.py`、`token_bucket.py` 时 |
| `billing-and-usage.md` | 计费口径与用量聚合 | 改动 `relay/accounting.py`、`services/usage.py` 时 |
| `api-reference.md` | 43 个端点的清单速查 | 增删路由时 |
| `troubleshooting.md` | 现象 → 原因 → 处理 | 踩到新坑并解决后 |

### 正文页统一结构

建议每页按这个骨架写，便于检索：

1. **一句话定位** —— 这页解决什么问题
2. **设计 / 行为说明** —— 为什么这么做
3. **参数与常量表** —— 具体数值、环境变量、默认值
4. **代码位置** —— 相关文件路径，最好带函数名
5. **相关页面** —— 交叉引用

### 写作规范

- **中文正文，代码块内一律 ASCII 直引号**（`"` `'`），避免渲染器把中文引号带进命令。
- 命令块标注 Shell 类型：Windows 用 ```powershell```，跨平台用 ```bash```。
- 数值必须来自代码，不写「大概」「可能」。不确定就标 `⚠️ 未验证`。
- 相对链接用仓库内路径（如 `../backend/app/relay/balancer.py`），方便在 GitHub / VS Code / Obsidian 里都能点开。
- 已知不一致（文档与代码对不上）**不要默默改文档**，在 [troubleshooting.md](troubleshooting.md) 或对应页面的「已知问题」段落里明确写出。

---

## 三、本地怎么浏览这份 wiki

三种方式，任选：

**1) VS Code / 编辑器预览（最简单）**

- 打开任意 `.md` 文件 → `Ctrl+Shift+V` 开启 Markdown 预览；
- 内部链接可直接跳转。

**2) GitHub / Gitee 网页（协作最方便）**

- 推送后在仓库里点进 `wiki/` 目录即可，`index.md` 会作为目录页；
- 注意：GitHub 的 **Wiki 功能**（独立 `wiki.git` 仓库）和这里的 `wiki/` **目录**是两回事，本项目用的是目录方案，跟着代码一起版本化。

**3) 本地 Markdown 服务器（有渲染目录树时好用）**

```bash
# 任选其一
npx markserv ./wiki        # 起一个本地服务，自动刷新
npx docsify-cli serve ./wiki
```

> 💡 顺序阅读时，用 [index.md](index.md) 当目录页，它按「入门 → 核心机制 → 参考 → 运维」分组。

---

## 四、维护规则（给未来的自己）

1. **改了行为 → 当天改文档。** 具体映射见上面的「命名与职责」表。
2. **每次提交后往 [changelog.md](changelog.md) 追加一条。** 格式：

   ```markdown
   ### YYYY-MM-DD
   - `类型(范围): 一句话说明` —— 影响面 / 需要注意的兼容性
   ```

   类型沿用 commit 前缀：`feat` / `fix` / `test` / `docs` / `refactor` / `chore` / `build` / `config`。
3. **新增页面必须同时更新 [index.md](index.md) 和本文件的「命名与职责」表。**
4. **页面超过 ~300 行就拆分**，并在两侧页面加交叉引用。
5. 常量（冷却秒数、窗口时段、桶参数）**只在一处写死值**（[scheduling.md](scheduling.md) / [billing-and-usage.md](billing-and-usage.md)），其它页面只放链接，避免改一次要同步五处。

---

## 相关页面

- [index.md](index.md) —— 全量目录
- [changelog.md](changelog.md) —— 更新日志
- [../README.md](../README.md) —— 项目主 README
