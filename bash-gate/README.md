# Bash Gate (PreToolUse Hook)

在 Bash 命令执行前做黑/白名单拦截。与 Permission Hook 互补：

| 钩子 | 时机 | 职责 |
|------|------|------|
| Permission Hook | 操作前 | 控制是否弹窗 |
| Bash Gate | Bash 执行前 | 控制是否允许执行 |

## 安装

在 `.claude/settings.json` 中注册：

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "bash /path/to/bash-gate/pretooluse-bash-gate.sh",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

## 规则管理

**增删规则只改两个 `.txt` 文件，无需动脚本或 settings.json：**

- `trusted-commands.txt` — 每行一条正则，命中即自动放行
- `dangerous-commands.txt` — 每行一条正则，命中即拦截

规则即时生效，注释行以 `#` 开头。匹配引擎是 Python `re.search()`（macOS BSD grep 不认 `\b` 单词边界）。

## 决策流程

```
Bash 命令即将执行
       ↓
dangerous-commands.txt 命中？ → deny（拦截）
       ↓ 否
trusted-commands.txt 命中？   → allow（放行）
       ↓ 否
不输出，走 Claude Code 默认（弹窗）
```
