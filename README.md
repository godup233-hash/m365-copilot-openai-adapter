# m365-copilot-openai-adapter

将 Microsoft 365 Copilot Chat API（Microsoft Graph `/beta/copilot`）封装为 OpenAI 兼容接口，用作 Sub2API 等网关的上游。

```
OpenAI 客户端 → Sub2API（用户密钥、计费、调度） → 本服务 → Microsoft Graph → M365 Copilot
```

## 前置条件

Copilot Chat API 的接入限制较严格，动手前请确认以下条件全部满足：

- 工作或学校账号（委派权限），个人 Microsoft 账号不受支持
- 账号已分配 Microsoft 365 Copilot 附加许可证，且底层具备 M365 E3/E5 或商业版订阅
- 租户管理员可为应用授予下文列出的 7 项 Graph 权限
- 租户非世纪互联运营的中国区（官方文档明确不支持）

该 API 目前处于 Graph beta 阶段，微软声明不支持生产环境使用，接口可能随时变更。

另请注意调用身份问题：所有请求均以同一个微软账号的身份执行，Copilot 仅能访问该账号权限范围内的企业数据，审计日志亦记录在该账号名下。本项目适用于自用或互信小团队内部使用，不适用于对外分发场景。

## 功能

- `GET /v1/models`、`POST /v1/chat/completions`（流式与非流式）
- MSAL 设备代码登录，令牌缓存落盘，文件权限 0600
- 会话映射：客户端通过 `metadata.session_id` 或 `user` 字段延续多轮对话
- 429、5xx 及网络错误采用指数退避重试，遵循 `Retry-After`，上限 3 次
- 日志脱敏：Bearer 令牌、JWT、API Key 均不会写入日志
- Docker 部署，非 root 运行，令牌缓存通过 volume 持久化

未实现 `/v1/responses`，此为有意设计，原因见「接入 Sub2API」一节。

## 本地运行

```bash
pip install -e ".[dev]"
cp .env.example .env
# 编辑 .env，填写两个必填项：
#   ADAPTER_API_KEY  自行生成的长随机串，即下游调用时使用的密钥
#   AZURE_CLIENT_ID  Entra 应用注册的 Application (client) ID

m365-adapter login     # 设备代码登录，按提示在浏览器中完成授权
m365-adapter serve     # 默认监听 127.0.0.1:8000
```

验证：

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer $ADAPTER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"m365-copilot-chat","messages":[{"role":"user","content":"你好"}]}'
```

## Entra 应用注册（一次性）

1. 在 [Entra 管理中心](https://entra.microsoft.com)注册应用，账户类型选择多租户或仅本组织，无需配置重定向 URI。
2. 记录 Application (client) ID，填入 `.env` 的 `AZURE_CLIENT_ID`。
3. 「身份验证 → 高级设置」中开启「允许公共客户端流」，设备代码流依赖此选项。
4. 添加 Microsoft Graph 委派权限，以下 7 项缺一不可：
   `Sites.Read.All`、`Mail.Read`、`People.Read.All`、`OnlineMeetingTranscript.Read.All`、`Chat.Read`、`ChannelMessage.Read.All`、`ExternalItem.Read.All`
5. 点击「授予管理员同意」，此步骤需要租户管理员权限。

## Docker 部署

```bash
cp .env.example .env
docker compose up -d --build
docker compose exec m365-copilot-adapter m365-adapter login
```

登录为交互式流程，需在容器内执行一次 `login`，`compose exec` 会分配 TTY。令牌缓存存储于 named volume `adapter-data`，该卷包含 refresh token，请注意备份等同于泄露。

## 接入 Sub2API

顺序要求：先启动本服务并完成登录，再在 Sub2API 中创建账户。

Sub2API 创建账户时会异步探测上游的 `/v1/responses`。本服务对该路径返回不含模型相关错误码的 404，Sub2API 探测后将标记「不支持 Responses」，后续流量全部走 `/v1/chat/completions` 直转，SSE 原样透传。上述行为已按 Sub2API v0.2.15 源码核实。若创建账户时本服务不在线，探测失败将默认按 Responses 路由，后续请求会全部返回 404，删除账户后重建即可。

账户配置：

| 字段 | 值 |
|---|---|
| 平台 / 类型 | OpenAI / API Key |
| Base URL | `http://m365-copilot-adapter:8000/v1`（同一 Docker 网络） |
| API Key | `.env` 中的 `ADAPTER_API_KEY` |
| 模型映射 | `copilot` → `m365-copilot-chat` |

Sub2API 与本服务不在同一网络时，请将 Base URL 替换为实际可达地址。若 Sub2API 启用了 `security.url_allowlist`，需将本服务的 host 加入 `upstream_hosts`（该白名单默认关闭）。

创建账户后将其挂入分组并为用户密钥授权，客户端请求时 model 填写映射前名称（如 `copilot`）即可。

## 在个人项目中直接调用

Sub2API 不是必须的中间层。本服务本身是标准的 OpenAI 兼容接口，个人项目可以直接用官方 SDK 调用，适合自用脚本、小工具、后端服务等场景。

Python：

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="<ADAPTER_API_KEY>",
)

resp = client.chat.completions.create(
    model="m365-copilot-chat",
    messages=[{"role": "user", "content": "总结一下我今天未读的邮件"}],
)
print(resp.choices[0].message.content)

# 流式
for chunk in client.chat.completions.create(
    model="m365-copilot-chat",
    messages=[{"role": "user", "content": "我明天有什么会？"}],
    stream=True,
):
    print(chunk.choices[0].delta.content or "", end="")

# 多轮会话：通过 metadata.session_id 延续同一个 Copilot 会话
resp = client.chat.completions.create(
    model="m365-copilot-chat",
    messages=[{"role": "user", "content": "接着上面说"}],
    extra_body={"metadata": {"session_id": "my-project-session-001"}},
)
```

Node.js：

```js
import OpenAI from "openai";

const client = new OpenAI({
  baseURL: "http://127.0.0.1:8000/v1",
  apiKey: "<ADAPTER_API_KEY>",
});

const resp = await client.chat.completions.create({
  model: "m365-copilot-chat",
  messages: [{ role: "user", content: "你好" }],
});
console.log(resp.choices[0].message.content);
```

默认配置只监听 `127.0.0.1`，即仅本机项目可调。项目部署到其他机器时，将 `.env` 中 `HOST` 改为 `0.0.0.0`（或直接用 Docker 部署到服务器），客户端填写对应地址即可。跨机器调用建议放在内网或加反向代理 HTTPS，不要把端口直接暴露到公网。

## 参数支持情况

| OpenAI 参数 | 状态 | 说明 |
|---|---|---|
| `messages` | 支持 | system/developer 并入 additionalContext，最后一条 user 作为 message.text |
| `stream` | 支持 | 对 Graph 的 SSE 累积快照做差分，转换为 chat.completion.chunk |
| `metadata.session_id` / `user` | 支持 | 用作会话键，详见下节 |
| `tools` / `functions` | 返回 400 | Copilot Chat API 不支持工具调用 |
| `temperature` / `top_p` / `max_tokens` / `stop` 等 | 忽略 | Copilot 无对应能力 |
| `stream_options.include_usage` | 容忍但忽略 | Sub2API 会注入该字段；Graph 不提供 token 计数，响应中不含 usage，不做伪造 |
| `model` | 透传 | 仅为别名，不能切换微软内部模型 |

## 会话与多用户

会话键优先级：`metadata.session_id` > `user` > 默认桶 `default`。Sub2API 不透传自定义请求头（其 Chat Completions 直转路径的请求头白名单仅含 accept-language 与 user-agent，见源码），因此 `X-Session-ID` 等请求头不可用，只能依赖请求体字段。

同一会话键复用同一 Copilot 会话，同会话请求串行执行。会话映射存储于内存，TTL 默认 24 小时，为单进程设计；多副本部署需自行替换为共享存储。

关于多用户隔离需要说明：Sub2API 不会向上游传递终端用户身份，本服务无法区分 Sub2API 背后的终端用户。`session_id` 与 `user` 依赖客户端自觉提供且可被伪造，不构成可靠的多用户隔离。

会话失效（上游 404）时默认返回 `session_expired` 错误，由客户端更换 session_id 重新开始，不会静默丢弃历史。将 `SESSION_RECOVERY` 设为 `auto_new` 可自动新建会话，代价是丢失历史上下文。

## 测试

```bash
pip install -e ".[dev]"
pytest -q
```

共 27 项，全部为 mock 测试（HTTPX MockTransport 模拟 Graph 上游）：响应映射、SSE 增量正确性（无重复、无伪造截断）、多轮会话与隔离、认证及 403 错误报告、429/5xx 重试、日志脱敏、Sub2API 探测兼容。

尚未进行真实 Graph 联调。官方文档与实际行为之间可能存在出入，首次接入真实账号如遇问题，欢迎在 issue 中反馈报错详情。

## 已知限制

- Graph beta API，微软不保证稳定性
- 仅返回文本，不支持工具调用、代码解释器与图片输入输出
- 无 token 用量统计，下游网关无法按 token 精确计费，只能按次或包月
- 流式传输中断后不续传，以避免重复提交
- 上游会话无查询与删除接口，过期清理仅为本地移除映射

## License

MIT
