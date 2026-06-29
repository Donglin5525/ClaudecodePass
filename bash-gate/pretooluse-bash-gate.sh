#!/usr/bin/env bash
# PreToolUse Bash gate —— Claude Code 每次跑 Bash 前调用此脚本做权限决策
#   命中 dangerous-commands.txt → deny（拦截）
#   命中 trusted-commands.txt   → allow（放行，不弹窗）
#   都不命中                    → 不输出，走默认（弹窗问用户）
#
# 增删规则只改同目录的两个 .txt，无需动本脚本或 settings.json。
# 注意：匹配用 Python re（不是 grep），因为 macOS BSD grep 不认 \b 单词边界。

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 从 stdin 读 Claude Code 注入的 JSON，提取 tool_input.command，存入环境变量
CMD="$(python3 -c 'import sys,json
try:
    print(json.load(sys.stdin).get("tool_input",{}).get("command",""))
except Exception:
    pass' 2>/dev/null)"

# 解析失败或无命令 → 走默认流程（不干预）
[ -z "$CMD" ] && exit 0
export CMD

# Python 做正则匹配 + 决策输出（CMD 经环境变量传入，避免 argv 引号/换行问题）
python3 - "$HOOK_DIR" <<'PY'
import sys, json, re, os
hook_dir, cmd = sys.argv[1], os.environ.get("CMD", "")

def matched(path):
    if not os.path.isfile(path):
        return False
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            pat = line.strip()
            if not pat or pat.startswith("#"):
                continue
            try:
                if re.search(pat, cmd):
                    return True
            except re.error:
                continue  # 正则本身非法 → 跳过该行，不连累其它规则
    return False

out = {"hookSpecificOutput": {"hookEventName": "PreToolUse"}}
if matched(os.path.join(hook_dir, "dangerous-commands.txt")):
    out["hookSpecificOutput"].update({
        "permissionDecision": "deny",
        "permissionDecisionReason": "命中危险操作黑名单，已拦截"
    })
elif matched(os.path.join(hook_dir, "trusted-commands.txt")):
    out["hookSpecificOutput"]["permissionDecision"] = "allow"
else:
    sys.exit(0)  # 都不命中 → 不输出，由 Claude Code 走默认弹窗

print(json.dumps(out))
PY
