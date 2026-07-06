#!/usr/bin/env python3
"""Claude Code Permission Request Hook

自动放行绝大多数操作，仅对计划文件编辑弹窗确认。
配合 settings.json 的 allowlist 使用：allowlist 中的条目不会触发此钩子。

支持 macOS (AppleScript dialog) 和 Windows (ctypes MessageBox)。
"""

import json
import os
import subprocess
import sys
import tempfile
import time

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"

LOG_DIR = tempfile.gettempdir()
LOG = os.path.join(LOG_DIR, "claude-permission-hook.log")
DIALOG_TIMEOUT = 120

# 这些工具的操作基本安全，一律自动放行
# （正常情况下 settings.json allowlist 已覆盖，这里是兜底）
SAFE_TOOLS = {
    # 只读类 — 无副作用
    "Read", "LSP", "WebSearch", "WebFetch",
    # 内部状态管理 — 无外部副作用
    "TaskCreate", "TaskUpdate", "TaskList", "TaskGet",
    "TaskOutput", "TaskStop",
    "CronCreate", "CronDelete", "CronList",
    "ScheduleWakeup",
    "EnterPlanMode", "ExitPlanMode",
    "EnterWorktree", "ExitWorktree",
    "AskUserQuestion",
    # MCP 工具 — 已在 allowlist 中逐一授权
    # Agent — 子代理继承主会话权限
    "Agent", "Workflow",
    # Notebook 编辑
    "NotebookEdit",
    # Skill 调用
    "Skill",
}


def log(msg):
    try:
        with open(LOG, "a") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    except Exception:
        pass


# ── 平台抽象层 ──────────────────────────────────────────────

def play_sound():
    """播放提示音。"""
    if IS_MAC:
        try:
            subprocess.Popen(
                ["afplay", "/System/Library/Sounds/Glass.aiff"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass
    elif IS_WIN:
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except Exception:
            pass


def send_notification(title, text):
    """发送系统通知（弹窗失败时的降级方案）。"""
    if IS_MAC:
        try:
            subprocess.run(
                ["osascript", "-e",
                 f'display notification "{text}" with title "{title}" sound name "Glass"'],
                capture_output=True, timeout=5,
            )
        except Exception:
            pass
    elif IS_WIN:
        try:
            from win10toast import ToastNotifier
            ToastNotifier().show_toast(title, text, duration=5, threaded=True)
        except Exception:
            pass


def activate_terminal():
    """把终端窗口带到前台。"""
    if IS_MAC:
        try:
            subprocess.run(
                ["osascript", "-e", 'tell application "Ghostty" to activate'],
                capture_output=True, timeout=5,
            )
        except Exception:
            pass
    elif IS_WIN:
        try:
            import ctypes
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception:
            pass


# ── 弹窗 ────────────────────────────────────────────────────

def _dialog_mac(msg):
    """macOS: AppleScript dialog，三按钮 + 超时。"""
    fd, msg_file = tempfile.mkstemp(prefix=".claude_hook_msg_", suffix=".txt")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(msg)
    except Exception:
        return ""

    script = f'''
set f to POSIX file "{msg_file}"
set msg to read f as «class utf8»
try
    display dialog msg ¬
        with title "Claude Code 需要授权" ¬
        buttons {{"拒绝", "查看详情", "授权"}} ¬
        default button "拒绝" ¬
        giving up after {DIALOG_TIMEOUT}
on error number -128
    return "cancelled"
end try
'''
    result = ""
    try:
        r = subprocess.run(
            ["osascript"], input=script, capture_output=True,
            text=True, timeout=DIALOG_TIMEOUT + 10,
        )
        result = r.stdout.strip()
    except subprocess.TimeoutExpired:
        log("Dialog timed out")
    except Exception as e:
        log(f"Dialog error: {e}")
        send_notification("Claude Code", "需要你的授权")

    try:
        os.unlink(msg_file)
    except Exception:
        pass
    return result


def _dialog_win(msg):
    """Windows: ctypes MessageBox，三按钮（Abort/Retry/Ignore 映射为 拒绝/查看详情/授权）。"""
    import ctypes
    # MB_ABORTRETRYIGNORE = 2, MB_ICONQUESTION = 32, MB_SYSTEMMODAL = 0x1000
    flags = 2 | 32 | 0x1000
    rc = ctypes.windll.user32.MessageBoxW(
        0, msg, "Claude Code 需要授权", flags
    )
    # IDABORT=3 → 拒绝, IDRETRY=4 → 查看详情, IDIGNORE=5 → 授权
    return {3: "拒绝", 4: "查看详情", 5: "授权"}.get(rc, "")


def show_dialog(msg):
    """显示确认弹窗，返回结果字符串。"""
    if IS_MAC:
        return _dialog_mac(msg)
    elif IS_WIN:
        return _dialog_win(msg)
    return ""


# ── 消息组装 ────────────────────────────────────────────────

def build_message(tool, inp, cwd):
    parts = [f"工具: {tool}"]
    if tool == "Bash":
        detail = inp.get("command", "")[:200]
        if detail:
            parts.append(f"命令: {detail}")
    elif tool in ("Edit", "Write", "Read"):
        detail = inp.get("file_path", "")
        if detail:
            parts.append(f"文件: {detail}")
    elif tool == "Agent":
        detail = inp.get("description", "")
        if detail:
            parts.append(f"任务: {detail}")
    if cwd:
        parts.append(f"目录: {cwd}")
    return "\n".join(parts)


def is_plan_file(file_path):
    """判断文件路径是否为计划文件（plan mode 写入的目标）。"""
    if not file_path:
        return False
    has_plan_dir = "/plans/" in file_path or "/.claude/plans/" in file_path
    has_indicator = "/.claude/plans/" in file_path or "/docs/" in file_path
    return has_plan_dir and has_indicator


# ── 只读 Bash 命令前缀 ─────────────────────────────────────

READONLY_BASH_PREFIXES = (
    # 文件内容搜索 — 只读
    "grep ",
    "rg ",
    "egrep ",
    "fgrep ",
    # 文件系统查找 — 只读（find 的 -delete/-exec 会被 settings.json 拦截）
    "find ",
    "mdfind ",
    "fd ",
    "fdfind ",
    # 文件内容查看 — 只读
    "cat ",
    "head ",
    "tail ",
    "less ",
    "more ",
    "file ",
    "xxd ",
    # 统计与格式化 — 只读
    "wc ",
    "wc -",
    "sort ",
    "uniq ",
    "diff ",
    "comm ",
    "cmp ",
    "column ",
    # sed 只读模式（-n 打印特定行，不含 -i 就不会修改文件）
    "sed -n ",
    # 系统信息 — 只读
    "ls ",
    "ls -",
    "pwd",
    "whoami",
    "uname ",
    "sw_vers",
    "echo ",
    "printf ",
    "date ",
    "hostname",
    "env",
    "printenv",
    "id ",
    "which ",
    "type ",
    "arch",
    "seq ",
    # 磁盘/进程 — 只读
    "du ",
    "df ",
    "ps ",
    "top ",
    "lsof ",
    # git 只读子命令
    "git log",
    "git diff",
    "git show",
    "git status",
    "git branch",
    "git tag ",
    "git remote",
    "git ls-files",
    "git ls-remote",
    "git rev-parse",
    "git describe",
    "git stash list",
    "git reflog",
    "git shortlog",
    "git cat-file",
    "git for-each-ref",
    "git worktree list",
    "git config --get",
    "git blame ",
    # gh 只读子命令
    "gh pr view",
    "gh pr list",
    "gh pr diff",
    "gh pr checks",
    "gh pr status",
    "gh issue view",
    "gh issue list",
    "gh issue status",
    "gh run view",
    "gh run list",
    "gh workflow list",
    "gh workflow view",
    "gh repo view",
    "gh release view",
    "gh release list",
    "gh auth status",
    # docker 只读
    "docker ps",
    "docker images",
    "docker logs",
    "docker inspect",
    # 网络检查 — 只读
    "ping ",
    "curl -I ",
    "curl --head ",
    "nc -z ",
)

# 已知安全命令前缀 — 子代理常用 "cd /path && command" 形式
# 这些命令已在 settings.json allowlist 中授权，但 cd 前缀导致 settings 匹配不到
# 这里补一层兜底：去掉 cd 前缀后，如果首命令在这些前缀中，自动放行
KNOWN_SAFE_COMMANDS = (
    # 构建工具 — 用户已明确信任
    "xcodebuild",
    "xcrun",
    "swift",
    # 包管理 — 无破坏性
    "npm",
    "pip",
    "pip3",
    "brew",
    "bun",
    "pnpm",
    "npx",
    "node",
    # 运行时 — 用户已明确信任
    "python3",
    "bash",
    # 网络 — 用户已明确信任
    "curl",
    "ssh",
    "scp",
    "rsync",
    "ping",
    # 容器 — 用户已明确信任
    "docker",
    # 版本控制 — 用户已明确信任
    "git",
    "gh",
    # 系统工具 — 用户已明确信任
    "open",
    "claude",
    "sqlite3",
    "osascript",
    "plutil",       # plist 查看器 — 只读
    "sips",         # 图片处理
    "md5",
    "defaults",
    "log show",
    # 环境管理
    "fnm",
    "nvm",
    "export",
    # gstack skill 脚本
    "~/.claude/skills/gstack/bin/",
)

# 绝对危险的命令关键词 — 即使出现在管道中也不自动放行
DANGEROUS_KEYWORDS = (
    "rm -rf /",
    "rm -rf ~",
    "> /dev/sd",
    "mkfs.",
    "dd if=",
    ":(){:|:&};:",
)


# ── 命令解析 ────────────────────────────────────────────────

def _split_pipes(cmd):
    """按管道符 | 拆分命令，但尊重引号内的 | 不拆分。

    解决核心问题：grep 正则中的 \\|（OR 操作符）在单引号内，
    naive split("|") 会误拆，导致只读命令被判定为不安全。
    """
    segments = []
    current = []
    in_single = False
    in_double = False
    escaped = False
    i = 0
    while i < len(cmd):
        c = cmd[i]
        if escaped:
            current.append(c)
            escaped = False
        elif c == '\\' and not in_single:
            escaped = True
            current.append(c)
        elif c == "'" and not in_double:
            in_single = not in_single
            current.append(c)
        elif c == '"' and not in_single:
            in_double = not in_double
            current.append(c)
        elif c == '|' and not in_single and not in_double:
            segments.append(''.join(current))
            current = []
        else:
            current.append(c)
        i += 1
    if current:
        segments.append(''.join(current))
    return segments


def _strip_cd_prefix(cmd):
    """去掉 cd 前缀（子代理常用 "cd /path && command" 形式）。
    支持多层 cd ... && cd ... && command。
    """
    while cmd.startswith("cd "):
        and_pos = cmd.find(" && ")
        if and_pos == -1:
            break
        cmd = cmd[and_pos + 4:].strip()
    return cmd


def _strip_env_prefix(seg):
    """去掉环境变量前缀（如 ARCH=xxx command），返回实际命令。"""
    parts = seg.split()
    if not parts:
        return seg
    idx = 0
    while idx < len(parts) and "=" in parts[idx] and not parts[idx].startswith(("sed", "grep", "awk")):
        idx += 1
    if idx > 0 and idx < len(parts):
        return " ".join(parts[idx:])
    return seg


def is_known_safe(cmd):
    """判断 cd 前缀后的命令是否在 KNOWN_SAFE_COMMANDS 中。"""
    stripped = _strip_cd_prefix(cmd)
    if stripped == cmd and not cmd.startswith("cd "):
        return False

    first_seg = stripped.split("&&")[0].split("|")[0].split(";")[0].strip()
    first_seg = _strip_env_prefix(first_seg)
    first_token = first_seg.split()[0] if first_seg.split() else ""

    for prefix in KNOWN_SAFE_COMMANDS:
        if first_token.startswith(prefix) or first_seg.startswith(prefix):
            return True
    return False


def is_readonly_bash(cmd):
    """判断 Bash 命令是否为只读安全操作。

    策略：
    1. 先排除危险命令
    2. 提取管道中的每段命令，逐一判断
    3. 含 cd 前缀的，去掉 cd 部分后判断剩余命令
    4. 任一段不识别 → 不放行（安全优先）
    """
    if not cmd:
        return False

    for kw in DANGEROUS_KEYWORDS:
        if kw in cmd:
            return False

    cmd = _strip_cd_prefix(cmd)
    if cmd.startswith("cd "):
        return False

    segments = _split_pipes(cmd)
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        seg = _strip_env_prefix(seg)
        if not seg:
            continue

        # xargs 后接只读命令也算只读（find ... | xargs grep 这类常见组合）
        # xargs rm 因 rm 不在只读清单而不会放行 —— 安全优先
        check_seg = seg[6:].strip() if seg.startswith("xargs ") else seg

        matched = False
        for prefix in READONLY_BASH_PREFIXES:
            if check_seg.startswith(prefix) or (check_seg + " ").startswith(prefix):
                matched = True
                break
        if not matched:
            return False

    return True


def auto_allow(tool, inp):
    """判断是否应该自动放行（不弹对话框）。

    策略：
    - 只读/内部工具 → 一律放行
    - Edit/Write 非计划文件 → 放行
    - Edit/Write 计划文件 → 弹窗确认
    - Bash 只读命令 → 放行
    - Bash 写入/未知命令 → 弹窗确认
    - 其他未知工具 → 弹窗确认
    """
    if tool in SAFE_TOOLS:
        return True

    if tool.startswith("mcp__"):
        return True

    if tool in ("Edit", "Write"):
        file_path = inp.get("file_path", "")
        return not is_plan_file(file_path)

    if tool == "Bash":
        cmd = inp.get("command", "")
        if is_readonly_bash(cmd):
            return True
        if is_known_safe(cmd):
            return True

    return False


# ── 主入口 ──────────────────────────────────────────────────

def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        log("Failed to read input JSON")
        sys.exit(0)

    tool = data.get("tool_name", "Unknown")
    inp = data.get("tool_input", {})
    cwd = data.get("cwd", "")

    detail = ""
    if tool in ("Edit", "Write"):
        detail = inp.get("file_path", "")
    elif tool == "Bash":
        detail = inp.get("command", "")[:120]
    log(f"Request: tool={tool}, detail={detail}, cwd={cwd}")

    if auto_allow(tool, inp):
        log(f"-> Auto-allowed {tool}")
        resp = {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {"behavior": "allow"},
            }
        }
        print(json.dumps(resp))
        sys.exit(0)

    # 需要用户确认的操作 — 弹窗
    msg = build_message(tool, inp, cwd)
    play_sound()
    result = show_dialog(msg)
    log(f"Result: {result}")

    if "授权" in result:
        log("-> Authorized")
        resp = {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {"behavior": "allow"},
            }
        }
        print(json.dumps(resp))
    elif "查看详情" in result:
        log("-> View details")
        activate_terminal()
    elif "拒绝" in result:
        log("-> Denied")
        resp = {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {"behavior": "deny", "reason": "用户拒绝授权"},
            }
        }
        print(json.dumps(resp))
    else:
        # 浮窗超时或异常关闭 —— 绝不替用户放行（堵死"没看到就默认同意"）
        # 也不直接 deny（deny=操作失败要重发，不符合"停下来等我"）
        # 不输出 decision → 回退终端原生权限弹窗，持续等用户，不超时、不放行
        log("-> Timeout/abnormal, NOT allowing; falling back to terminal")
        activate_terminal()
        send_notification("Claude Code 等待确认", "有操作等你确认，已切回终端，请回终端处理")

    sys.exit(0)


if __name__ == "__main__":
    main()
