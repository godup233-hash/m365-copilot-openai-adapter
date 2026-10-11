# m365-copilot-openai-adapter

把 Microsoft 365 Copilot Chat API（Microsoft Graph `/beta/copilot`）包装成 OpenAI 兼容接口，给 Sub2API 这类网关当上游。

```
OpenAI 客户端 → Sub2API（用户 key、计费、调度） → 本服务 → Microsoft Graph → M365 Copilot
```

## 先看门槛，免得白折腾

这个 API 不是有个微软账号就能调。官方文档的限制写得很死：

- 只支持工作/学校账号的委派权限，个人账号（含家庭订阅）明确不行
- 账号要分配了 Microsoft 365 Copilot 附加许可证，底层还要 M365 E3/E5 或商业版
- 需要租户管理员同意 7 个 Graph 权限
- 世纪互联租户不支持
- API 目前是 Graph beta，微软明说不支持生产使用，随时可能改

还有一点要想清楚：所有请求都以同一个微软账号的身份执行，Copilot 只能看到这个账号有权访问的企业数据，审计日志也记在这个账号名下。适合自己用或互信小团队内部用，不适合对外分发。

## 功能

- `GET /v1/models`，`POST /v1/chat/completions`（流式、非流式都有）
- MSAL 设备代码登录，token cache 落盘，文件权限 0600
- 会话映射：客户端传 `metadata.session_id` 或 `user` 字段即可延续多轮对话
- 429、5xx、网络错误指数退避重试，尊重 `Retry-After`，最多 3 次
- 日志脱敏：Bearer token、JWT、API key 都不会进日志
- Docker 部署，非 root 运行，token cache 走 volume

没有实现 `/v1/responses`，这是故意的，原因见下面 Sub2API 一节。

## 本地跑起来

```bash
pip install -e ".[dev]"
cp .env.example .env
# 编辑 .env，填两个必填项：
#   ADAPTER_API_KEY  自己生成一个长随机串，这就是下游要用的 key
#   AZURE_CLIENT_ID  Entra 应用注册的 Application ID

m365-adapter login     # 设备代码登录，按提示在浏览器里授权
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

1. 在 [Entra 管理中心](https://entra.microsoft.com) 注册应用，账户类型选多租户或仅本组织，不需要重定向 URI。
2. 记下 Application (client) ID，填进 `.env` 的 `AZURE_CLIENT_ID`。
3. 身份验证 → 高级设置 → 打开「允许公共客户端流」，设备代码流需要这个。
4. API 权限 → 添加 Microsoft Graph 委派权限，7 个缺一不可：
   `Sites.Read.All`、`Mail.Read`、`People.Read.All`、`OnlineMeetingTranscript.Read.All`、`Chat.Read`、`ChannelMessage.Read.All`、`ExternalItem.Read.All`
5. 点「授予管理员同意」，需要租户管理员操作。

## Docker

```bash
cp .env.example .env
docker compose up -d --build
docker compose exec m365-copilot-adapter m365-adapter login
```

登录是交互式的，必须在容器里执行一次 `login`，compose exec 会分配 TTY。token cache 存在 named volume `adapter-data` 里，注意这个卷里有 refresh token，备份等于泄露。

## 接 Sub2API

顺序不能反：先启动本服务并完成 login，再去 Sub2API 建账户。

原因是 Sub2API 建账户时会异步探测上游的 `/v1/responses`。本服务对这个路径返回干净的 404，Sub2API 探测后会落标「不支持 Responses」，之后所有流量走 `/v1/chat/completions` 直转，SSE 原样透传。这套行为是按 Sub2API v0.2.15 源码核实过的，不是猜的。如果建账户时本服务不在线，探测失败会默认按 Responses 路由，之后的请求全部 404，删掉账户重建即可。

账户配置：

| 字段 | 值 |
|---|---|
| 平台 / 类型 | OpenAI / API Key |
| Base URL | `http://m365-copilot-adapter:8000/v1`（同一 Docker 网络） |
| API Key | `.env` 里的 `ADAPTER_API_KEY` |
| 模型映射 | `copilot` → `m365-copilot-chat` |

Sub2API 和本服务不在一个网络的话，把 base_url 换成实际可达地址。如果 Sub2API 开了 `security.url_allowlist`，要把本服务的 host 加进 `upstream_hosts`，默认是关闭的。

建完账户挂进分组，给用户 key 授权，客户端请求 model 填映射前的名字（比如 `copilot`）就能用了。

## 参数支持情况

| OpenAI 参数 | 状态 | 说明 |
|---|---|---|
| `messages` | 支持 | system/developer 进 additionalContext，最后一条 user 作为 message.text |
| `stream` | 支持 | Graph 的 SSE 快照做差分，转成 chat.completion.chunk |
| `metadata.session_id` / `user` | 支持 | 会话键，见下节 |
| `tools` / `functions` | 返回 400 | Copilot Chat API 不支持工具调用 |
| `temperature` / `top_p` / `max_tokens` / `stop` 等 | 忽略 | Copilot 没有对应能力 |
| `stream_options.include_usage` | 容忍但忽略 | Sub2API 会注入这个字段；Graph 不提供 token 计数，响应里也没有 usage，不伪造 |
| `model` | 透传 | 只是别名，不能切换微软内部模型 |

## 会话和多用户

会话键的优先级：`metadata.session_id` > `user` > 默认桶 `default`。Sub2API 不透传自定义 header（它的 CC 直转路径 header 白名单只有 accept-language 和 user-agent，源码可查），所以 `X-Session-ID` 之类的请求头没用，只能靠请求体字段。

同一个会话键复用同一个 Copilot 会话，同会话的请求串行执行。会话映射存在内存里，TTL 默认 24 小时，单进程设计，多副本部署需要自己换共享存储。

多用户隔离要说实话：Sub2API 不会把终端用户身份传给上游，本服务区分不了 Sub2API 背后的终端用户。`session_id` 和 `user` 靠客户端自觉提供，可以伪造，不能当成可靠的隔离手段。

会话失效（上游 404）默认返回 `session_expired` 错误，让客户端换个 session_id 重开，不会静默丢历史。设 `SESSION_RECOVERY=auto_new` 可以自动新建会话，代价是历史丢失。

## 测试

```bash
pip install -e ".[dev]"
pytest -q
```

27 项，全部是 mock 档（HTTPX MockTransport 模拟 Graph）：响应映射、SSE 增量不重复不截断、多轮会话、隔离、认证和 403 报告、429/5xx 重试、日志脱敏、Sub2API 探测兼容。

没做真实 Graph 联调。官方文档和实际行为之间通常会有出入，第一次接真账号时如果遇到问题，issue 里贴报错。

## 已知限制

- Graph beta API，微软不承诺稳定性
- 只能返回文本，不支持工具调用、代码解释器、图片
- 没有 token 用量，下游网关没法按 token 精确计费，只能按次或包月
- 流式中途断线不续传，避免重复提交
- 上游会话没有查询和删除接口，过期清理只是本地丢映射

## License

MIT
