# 网关 API（对外 `/v1/*`）

> 一句话定位：只做 OpenAI 兼容的四个端点，让任意「OpenAI 兼容客户端」零改造接入。所有端点合用一个令牌 `GATEWAY_TOKEN`。

相关页面：[api-reference.md](api-reference.md)（管理台端点清单）· [scheduling.md](scheduling.md)（失败后怎么兜底）· [billing-and-usage.md](billing-and-usage.md)（成本怎么算）

---

## 1. 接入信息

```
Base URL : http://<host>:<WEB_PORT>/v1        # 默认 8080；本仓库当前 8085
           本地后端直连：http://127.0.0.1:8082/v1
API Key  : <GATEWAY_TOKEN>                    # 根 .env，或管理台 Playground 页查看
Auth     : Authorization: Bearer <GATEWAY_TOKEN>
```

管理台 Playground 页会给出两个地址：

| 标签 | 取值 | 用途 |
| --- | --- | --- |
| 固定地址 | `http://<Tailscale-IP>:<WEB_PORT>/v1` | 跨网络可用，不随物理网络变化（推荐） |
| 本机地址 | `window.location.origin` + `/v1` | 跟随浏览器访问来源（dev = Vite 5175，prod = nginx 端口） |

## 2. 鉴权

| 项 | 说明 |
| --- | --- |
| 依赖 | `core/deps.py: require_gateway`，挂在整个 `/v1` 路由上 |
| 头 | `Authorization: Bearer <GATEWAY_TOKEN>` |
| 缺失 | `401 Missing gateway token` |
| 不匹配 | `401 Invalid gateway token`（`hmac.compare_digest` 常数时间比对） |
| 与管理台鉴权的关系 | **完全独立**。管理台用 `Bearer <session-token>`（`require_auth`）。网关令牌错**不会**登出管理台会话 —— 前端 `api/client.ts` 里有专门判断：URL 含 `/v1/` 的 401 不触发 logout。 |

## 3. 端点总览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/v1/chat/completions` | 对话补全（支持 `stream`）；生图渠道会自动分发到生图接口 |
| `POST` | `/v1/embeddings` | 向量嵌入 |
| `POST` | `/v1/images/generations` | 生图 |
| `GET` | `/v1/models` | 列出所有启用通道对应的虚拟模型（附 `context_length`） |

三个 POST 端点有共同的**前置校验**（`api/relay.py`）：

1. 请求体必须能解析为 JSON —— 否则 `400 请求体不是合法 JSON`
2. 必须有 `model` 字段 —— 否则 `400 缺少 model 字段`
3. `model` 必须是某个 `is_active=true` 的通道的 `virtual_model` —— 否则 `404 未找到 model '<model>' 对应的通道`

> `model` 填的是**虚拟模型名**（通道名），不是上游的原生模型名。**一个虚拟模型 = 一个通道 = 一套 Tier 结构**，所以换上游只需改管理台，客户端不动。

---

## 4. `POST /v1/chat/completions`

### 请求

标准 OpenAI 请求体，原样透传（除 `model` 会被替换成上游原生模型名）。

```json
{
  "model": "<通道虚拟模型名>",
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "用一句话介绍你自己"}
  ],
  "temperature": 0.7,
  "stream": false
}
```

| 字段 | 必填 | 说明 |
| --- | :---: | --- |
| `model` | ✅ | 虚拟模型名 |
| `messages` | ✅ | 原样透传 |
| `stream` | ⬜ | `true` 时走 SSE |
| 其它 | ⬜ | `temperature` / `max_tokens` / `tools` 等一律原样透传，网关不做字段白名单 |

### 响应（非流式）

上游 JSON 原样返回，含 `usage`（`prompt_tokens` / `completion_tokens` / `total_tokens`，以及可能的缓存命中计数，见 [billing-and-usage.md](billing-and-usage.md)）。

### 响应（流式 `stream=true`）

- `Content-Type: text/event-stream`，逐块透传上游 `data:` 行，以 `data: [DONE]` 结束。
- 链路各层都为流式做过配置：
  - nginx：`proxy_buffering off`、`proxy_cache off`、`proxy_read_timeout 900s`、`chunked_transfer_encoding on`
  - Vite dev proxy：`proxy.on('proxyRes')` 里删掉 `content-encoding`，不缓冲
  - `accounting.py` 用**独立 DB 会话**写日志 —— 客户端中途断开、主会话已关也能落库

### 生图渠道自动分发（本项目特有的行为）

如果该通道的 `virtual_model` 或 `name` 含生图特征词，`/v1/chat/completions` **不会**报错，而是自动改走生图接口，并把图片 URL 包装成聊天响应返回：

```python
_IMAGE_MODEL_HINTS = ("image", "img", "t2i", "dall-e", "sora", "flux", "midjourney")
```

设计意图：很多 agent 客户端只会调 `/chat/completions`，遇到生图模型却不知道要换端点。网关替它们识别并分发，**客户端无需改任何配置**。

对应地，健康探测也会识别生图模型名并改走「列模型接口」验证密钥（生图模型对 chat 请求会回 400）。

```bash
curl http://127.0.0.1:8085/v1/chat/completions \
  -H "Authorization: Bearer $GATEWAY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model":"agnes-image-2.5-flash","messages":[{"role":"user","content":"画一只猫"}]}'
# → 自动走 images/generations，返回里带图片 URL
```

---

## 5. `POST /v1/embeddings`

```bash
curl http://127.0.0.1:8085/v1/embeddings \
  -H "Authorization: Bearer $GATEWAY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model":"<embedding 虚拟模型名>","input":["hello","world"]}'
```

请求体原样透传（`input` 可为字符串或字符串数组）。同样要求 `model` 是已启用通道。

---

## 6. `POST /v1/images/generations`

```bash
curl http://127.0.0.1:8085/v1/images/generations \
  -H "Authorization: Bearer $GATEWAY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model":"<生图虚拟模型名>","prompt":"一只戴宇航头盔的柴犬","n":1}'
```

与 chat 端点的区别：**不做生图特征词判断**，直接按生图路径转发（因为调用方已经明确知道这是生图端点）。

---

## 7. `GET /v1/models`

```bash
curl http://127.0.0.1:8085/v1/models -H "Authorization: Bearer $GATEWAY_TOKEN"
```

响应结构（OpenAI 兼容）：

```json
{
  "object": "list",
  "data": [
    {
      "id": "<虚拟模型名>",
      "object": "model",
      "owned_by": "mytokentransfer",
      "context_length": 1000000
    }
  ]
}
```

- 数据来源：`channels` 表中 `is_active=true` 的记录，按 `id` 升序。
- `context_length` 从 `model_pricings` 表按模型名取；定价表里没有该模型时为 `0`。

---

## 8. 客户端接入示例

### curl

```bash
export GATEWAY_TOKEN=<你的令牌>
export BASE=http://127.0.0.1:8085/v1

curl $BASE/models -H "Authorization: Bearer $GATEWAY_TOKEN"

curl $BASE/chat/completions \
  -H "Authorization: Bearer $GATEWAY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model":"<虚拟模型名>","messages":[{"role":"user","content":"你好"}]}'
```

### Python（openai SDK）

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8085/v1", api_key="<GATEWAY_TOKEN>")

# 非流式
r = client.chat.completions.create(
    model="<虚拟模型名>",
    messages=[{"role": "user", "content": "你好"}],
)
print(r.choices[0].message.content, r.usage)

# 流式
for chunk in client.chat.completions.create(
    model="<虚拟模型名>",
    messages=[{"role": "user", "content": "讲个冷笑话"}],
    stream=True,
):
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)
```

### 其它 OpenAI 兼容客户端

因为只依赖 `/v1` 标准路径，以下都只需填 `Base URL` + `API Key` + `Model` 三项：Cline、Cursor、Continue、Dify、LangChain 的 `ChatOpenAI`、自研 Agent。

```
API Base / Base URL : http://<host>:<WEB_PORT>/v1
API Key             : <GATEWAY_TOKEN>
Model               : <通道虚拟模型名>
```

### 跨设备 / 远程

配 Tailscale 之类的虚拟组网，让接入地址不随物理网络（WiFi / 热点）变化：

```
http://<Tailscale-IP>:<WEB_PORT>/v1
```

前提：宿主机 `WEB_PORT` 可被组网内设备访问（nginx 绑 `0.0.0.0`），且该设备已加入同一组网。

---

## 9. 端到端配置流程

网关不会自动认识任何模型，先把数据配进去。全部在管理台 Web UI 完成：

```
① 密钥页      录入多账号 Key（可批量）→ 点「测试」确认可用
                 ↓ provider_id
② 供应商页    新建供应商：base_url + 探测方式 + 模型列表
                 ↓
③ 转发通道页  建通道：
                · virtual_model = 对外暴露的虚拟模型名（客户端就填这个）
                · 把 ① 的 key 组成池挂到 T0 主池，选策略
                  round_robin / weighted / priority
                · 可选再加 T1+ 跨供应商兜底层
                · 配置模型映射：虚拟模型 → 各供应商原生模型名
                 ↓
④ 定价页      给模型填价格 → 用量页才有成本数据
                 ↓
⑤ Playground  核对地址 + 令牌，做端到端调试
```

配好后客户端只需统一地址 + 统一令牌，**换上游改管理台即可，客户端零改动**。

---

## 10. 错误码对照

| HTTP | 报错文本 | 触发点 | 处理 |
| --- | --- | --- | --- |
| `400` | `请求体不是合法 JSON` | `api/relay.py` | 检查 Content-Type 与 body |
| `400` | `缺少 model 字段` | `api/relay.py` | 加 `model` |
| `401` | `Missing gateway token` | `require_gateway` | 加 `Authorization` 头 |
| `401` | `Invalid gateway token` | `require_gateway` | 令牌对不上根 `.env` 的 `GATEWAY_TOKEN` |
| `404` | `未找到 model '<x>' 对应的通道` | `_load_channel` | 通道未建 / `is_active=false` / 名字不一致 |
| `503` | 上游全部候选耗尽 | `relay/gateway.py` | 四级兜底全试过，见 [scheduling.md](scheduling.md)；查告警与日志页 |
| 上游原样 | 上游 4xx/5xx 响应体 | 单次尝试失败但整体成功时不会暴露 | 见调用日志 |

## 相关页面

- [scheduling.md](scheduling.md) —— 503 之前网关都做了什么
- [billing-and-usage.md](billing-and-usage.md) —— `usage` 里的 token 怎么变成钱
- [troubleshooting.md](troubleshooting.md) —— 接入不通的排查顺序
