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
| `check-login` | 检查登录状态，返回用户信息 |
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

## 支持模型

`qwen3.6-plus`（默认）、`qwen3-max`、`qwen3-coder`、`qwen3-next` 等。

## License

MIT
