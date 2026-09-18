# Contributing to DocuAgent Community / 参与贡献

[中文](#中文) | [English](#english)

## 中文

感谢你愿意花时间在这个仓库上。这一节说明怎么把代码跑起来、怎么验证改动、以及合并前需要满足什么。

### 社区版边界

本仓库只覆盖**从空目录创建个人新项目**这条链路。已有项目的系统性重建、逐模块外部组件选型、私有团队扩展不在本仓库范围内。如果你的提议落在这个边界外，请先开一个 issue 讨论，再动手写代码——避免做完了才发现不在射程内。

### 环境要求

- Python 3.12 或更高版本
- Node.js 22.12 或更高版本及 npm
- 一个兼容 OpenAI API 格式的模型服务和你自己的 API Key

后端运行时**只使用 Python 标准库**。测试唯一依赖是 `pytest`。

### 从源码运行

```bash
npm --prefix docuagent/frontend ci
npm --prefix docuagent/frontend run build
python docuagent/docuagent.py --host 127.0.0.1 --port 8765 --no-browser
```

打开 `http://127.0.0.1:8765`，在“设置”里配置模型服务，然后选一个空项目目录。

### 验证改动

提交 PR 前，以下三项都必须通过：

```bash
# 后端
python -m pip install -r docuagent/requirements-dev.txt
cd docuagent && python -m pytest -q

# 前端（在 docuagent/frontend 下）
npm ci
npm test
npm run build
```

后端全量测试需要几分钟，这是正常的——套件里有真实子进程与文件系统的用例。自动化测试**不需要连接真实模型服务**，也不会产生模型调用费用。

CI 在 Ubuntu 上跑同样的三步。你的改动在 Linux 上失败但在 Windows 上通过（或反过来）是可能的，请留意跨平台用例。

### 代码约定

这几条是硬约束，不是建议：

1. **后端运行时不得引入第三方依赖。** 只允许标准库。新增依赖必须先在 issue 里说明理由。
2. **代码智能（`docuagent/codeintel/`）是隔离包。** 只有 `main_routes_codeintel.py` 可以 import 它；`agent_tools.py` 和其他核心模块一律不得 import。能力通过环境变量 `DOCUAGENT_CODE_INTEL` 显式开启，默认关闭。
3. **项目状态写入必须走原子写入。** 不要直接 `open(path, "w")` 写 `.docuagent/` 下的状态文件，用 `workspace.py` 里的原子写入函数。
4. **界面文案是中文。** 面向用户的字符串、错误提示、面板标签都用中文；代码标识符、注释、提交信息用英文。
5. **提交信息用英文，动词开头**，例如 `fix:`、`add:`、`chore:`。一次提交只做一件事。

### 提交 PR

1. Fork 仓库，从 `main` 建分支。
2. 改动 + 补测试。修 bug 请先写一个能复现它的失败用例。
3. 跑通上面三项验证。
4. 开 PR，说明：改了什么、为什么、怎么验证的。
5. 如果改动涉及用户可见行为，请附上运行前后的对比（截图或终端输出）。

我们没有 CLA 流程。你贡献的代码会以同样的 Apache 2.0 许可证发布。

### 报告问题

开 issue 时请附上：

- 操作系统与版本、Python 版本、Node 版本
- 使用的模型服务（不必提供 Key 或端点地址）
- 复现步骤，以及 `.docuagent/` 之外的相关日志片段

安全漏洞不要开公开 issue，请看 [SECURITY.md](SECURITY.md)。

---

## English

Thanks for spending time on this repository. This section covers running the code, verifying changes, and what a pull request must satisfy before it can merge.

### Community scope

This repository covers **creating new personal projects from an empty directory**. Systematic reconstruction of existing projects, per-module external component selection, and proprietary team extensions are out of scope. If your idea falls outside that boundary, open an issue first so we can discuss it before you write code.

### Prerequisites

- Python 3.12 or newer
- Node.js 22.12 or newer and npm
- An OpenAI-compatible model provider and your own API key

The backend runtime uses **only the Python standard library**. `pytest` is the sole test dependency.

### Run from source

```bash
npm --prefix docuagent/frontend ci
npm --prefix docuagent/frontend run build
python docuagent/docuagent.py --host 127.0.0.1 --port 8765 --no-browser
```

Open `http://127.0.0.1:8765`, configure the provider in Settings, and pick an empty project directory.

### Verify a change

All three must pass before you open a PR:

```bash
# Backend
python -m pip install -r docuagent/requirements-dev.txt
cd docuagent && python -m pytest -q

# Frontend, from docuagent/frontend
npm ci
npm test
npm run build
```

The full backend suite takes several minutes; that is expected because it exercises real subprocesses and the filesystem. The automated suite **does not need a live model provider** and incurs no model cost.

CI runs the same three steps on Ubuntu. A change can pass on Windows and fail on Linux, or the reverse. Watch for cross-platform cases.

### Code conventions

These are hard constraints, not preferences:

1. **The backend runtime must not gain third-party dependencies.** Standard library only. Propose any addition in an issue first, with a reason.
2. **Code intelligence (`docuagent/codeintel/`) is an isolated package.** Only `main_routes_codeintel.py` may import it; `agent_tools.py` and the other core modules must not. The capability is gated behind `DOCUAGENT_CODE_INTEL` and is off by default.
3. **Project state must be written atomically.** Do not write `.docuagent/` state files with a bare `open(path, "w")`; use the atomic write helpers in `workspace.py`.
4. **User-facing strings are Chinese.** Labels, error messages, and panel text are Chinese; identifiers, comments, and commit messages are English.
5. **Commit messages are English and start with a verb**, for example `fix:`, `add:`, `chore:`. One commit does one thing.

### Submitting a pull request

1. Fork the repository and branch from `main`.
2. Make the change and add tests. For a bug fix, start with a failing test that reproduces it.
3. Run all three verification steps above.
4. Open the PR describing what changed, why, and how you verified it.
5. If the change is user-visible, include a before/after comparison, screenshot or terminal output.

There is no CLA. Your contribution ships under the same Apache 2.0 license.

### Reporting issues

When opening an issue, include:

- OS and version, Python version, Node version
- The model provider you used (no key or endpoint needed)
- Reproduction steps, plus any relevant log excerpts outside `.docuagent/`

Do not open a public issue for security vulnerabilities. See [SECURITY.md](SECURITY.md).
