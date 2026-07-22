# qwen-cli

通过 [kimi-webbridge](https://www.kimi.team/features/webbridge) 自动化 [chat.qwen.ai](https://chat.qwen.ai/)（Alibaba 通义千问）的命令行工具。复用浏览器真实登录会话，无需 API Key。

## 前置依赖

- Chrome 已安装 Kimi WebBridge 扩展并登录 chat.qwen.ai
- `~/.kimi-webbridge/bin/kimi-webbridge status` 显示 `running: true`
- Python 3.10+

## 快速开始

```bash
python3 cli.py check-login
python3 cli.py chat --content "用一句话解释什么是熵"
```

## 命令一览

| 命令 | 用途 |
|------|------|
| `captcha-status` | 检查云盾就绪状态 / 是否正挂着滑块验证 |
| `check-login` | 检查登录状态，返回用户信息 |
| `list-models` | 列出当前账号可用模型 |
| `list-conversations` | 列出历史对话 |
| `get-conversation --chat-id ID` | 获取完整消息记录 |
| `delete-conversation --chat-id ID` | 删除对话 |
| `new-chat [--model] [--project-id]` | 新建对话 |
| `chat --content "..." [--chat-id] [--model] [--thinking] [--search] [--project-id]` | 发消息（支持流式、思考、联网） |
| `list-projects` | 列出所有项目 |
| `get-project --project-id ID` | 获取项目详情 |
| `create-project --name N [--instruction\|--instruction-file]` | 新建项目（含自定义指令） |
| `update-project --project-id ID [--name] [--instruction\|--instruction-file]` | 修改项目 |
| `delete-project --project-id ID` | 删除项目 |
| `list-project-chats --project-id ID` | 列出项目内对话 |
| `list-project-files --project-id ID` | 列出项目文件 |
| `upload-file --file PATH [--project-id ID]` | 上传文件（走 OSS v4 签名，直接 Python → OSS，绕过浏览器） |
| `delete-project-file --project-id ID --file-id ID` | 从项目移除文件 |

所有命令返回 `{"ok": true, "data": ...}` 或 `{"ok": false, "error": {...}}`。
退出码：`0` 成功 / `1` 业务失败 / `2` 运行时错误 / `3` 需人工过滑块验证。

## 滑块验证规避

chat.qwen.ai 用阿里云盾（baxia + AWSC）给 `/api/v2/chat/completions`、`/api/v2/chats*`、
`/api/v{1,2}/files/getstsToken` 等接口注入 `bx-ua` / `bx-umidtoken` 签名头。

**页面冷加载后约 1.3s 内发出的请求签名是退化的**（云盾等不到 AWSC 就绪会照发占位
token），这是滑块的主要诱因。注意 `document.readyState === "complete"` 和
`window.baxiaInitialized === true` 都不足以判断就绪——必须等到 uid token 拿到。

被拦时的请求**不会返回错误码，而是一直挂着**等人过验证，表现为"卡住直到超时"。

`qwen/antibot.py` 复刻了站点自己的就绪判定并在每次调用前门控，同时检测滑块 DOM
（`#waf_nc_block` 全屏滑块 / `.nc_wrapper` 内联滑块）并在超时后回查页面状态，
命中即以 `captcha_required`（exit 3）快速失败。**exit 3 不要重试**，
滑块必须人工完成；批量任务请复用同一个热 tab 并在调用间留 2–3s 间隔。

### 压测

`tests/stress_captcha.py` 是带熔断的联网压测（检测到滑块立即中止），验证门控在负载下
挡得住滑块：

```bash
python3 tests/stress_captcha.py            # 冷启动 + 热连打（不烧 token）
python3 tests/stress_captcha.py --phase c  # 真实 chat 负载（消耗 token）
```

无滑块退出 0，触发则退出 1。这是对真实站点的联网测试、会占用账号会话，勿高频反复跑。

## 支持模型

用 `list-models` 查当前账号实际可用的模型，不要凭记忆猜 id。

默认 `qwen3.8-max-preview`；另有 `qwen3.7-max` / `qwen3.7-plus` / `qwen3.6-plus` /
`qwen3.5-397b-a17b` / `qwen3-coder-plus` / `qwen3-vl-plus` 等。

模型 meta 里的 `think_skip.enable === false` 表示**不允许关闭思考模式**（目前只有默认的
`qwen3.8-max-preview`），此时传 `--no-thinking` 会被服务端拒为 `invalid_input`，
CLI 会在发请求前预检拦下。需要关思考请用 `qwen3.7-max`。

## License

MIT
