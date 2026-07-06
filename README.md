# Claude Code Permission Hook

减少 Claude Code 弹窗打扰的权限钩子。自动放行安全操作，只在真正需要确认时才弹窗。

## 做了什么

- **只读命令自动放行** — grep、find、git log、cat、ls 等 100+ 只读命令不弹窗
- **已知安全命令自动放行** — xcodebuild、swift、npm、git、curl 等构建/开发工具
- **MCP 工具自动放行** — 所有 `mcp__*` 工具不弹窗
- **仅弹窗确认** — 计划文件编辑、未知/危险 Bash 命令
- **管道引用感知** — 正确解析引号内的 `|`，不会误判 `grep 'a\|b'` 为管道
- **cd 前缀剥离** — 识别 `cd /path && command` 形式，匹配已知安全命令
- **macOS 原生弹窗** — AppleScript 中文确认对话框，默认按钮「拒绝」防误触；超时不放行，回退终端持续等用户

## 安装

### 1. 下载脚本

```bash
mkdir -p ~/.claude/scripts
curl -o ~/.claude/scripts/permission-hook.py https://raw.githubusercontent.com/<user>/<repo>/main/permission-hook.py
chmod +x ~/.claude/scripts/permission-hook.py
```

### 2. 注册钩子

在 `~/.claude/settings.json`（全局）或项目 `.claude/settings.json` 中添加：

```json
{
  "hooks": {
    "PermissionRequest": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/.claude/scripts/permission-hook.py"
          }
        ]
      }
    ]
  }
}
```

### 3. 验证

下次 Claude Code 执行操作时，检查日志：

```bash
tail -f /tmp/claude-permission-hook.log
```

看到 `Auto-allowed` 说明钩子生效。

## 自定义

### 调整放行策略

编辑脚本中的四个配置集合：

| 集合 | 说明 |
|------|------|
| `SAFE_TOOLS` | 工具名白名单（Read、Agent、Skill 等） |
| `READONLY_BASH_PREFIXES` | 只读 Bash 命令前缀 |
| `KNOWN_SAFE_COMMANDS` | cd 前缀后仍放行的安全命令 |
| `DANGEROUS_KEYWORDS` | 绝对拦截的危险关键词 |

### 调整弹窗超时

修改 `DIALOG_TIMEOUT` 变量（默认 120 秒）。

### 关闭计划文件弹窗

如果不想对计划文件编辑弹窗，修改 `auto_allow()` 中的 Edit/Write 分支：

```python
if tool in ("Edit", "Write"):
    return True  # 永远放行
```

## 工作原理

```
Claude Code 执行操作
       ↓
PermissionRequest 钩子触发
       ↓
permission-hook.py 分析操作
       ↓
┌──────────────────────────────────┐
│ SAFE_TOOLS?        → 自动放行    │
│ MCP 工具?          → 自动放行    │
│ 只读 Bash?         → 自动放行    │
│ 已知安全命令?      → 自动放行    │
│ 计划文件编辑?      → 弹窗确认    │
│ 未知/危险命令?     → 弹窗确认    │
└──────────────────────────────────┘
```

## 配套：Bash Gate

本仓库还包含可选的 PreToolUse Bash Gate（`bash-gate/` 目录），在命令执行前做黑/白名单拦截，与 Permission Hook 互补：

- **Permission Hook** — 控制是否弹窗问用户
- **Bash Gate** — 控制命令是否允许执行（deny/allow/默认）

详见 `bash-gate/README.md`。

## 许可

MIT
