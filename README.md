# DocuAgent Community / 社区版

[中文](#中文) | [English](#english)

## 中文

DocuAgent 是一个本地优先、围绕架构图构建的 AI 工程工作台。架构图让 AI 便捷地了解公共接口，让一百次局部最优的生成成为一个整体。

这里的健康工程规范是模型必须通过的硬性门槛，而不是它自觉遵守的一个 skill。DocuAgent 把需求确认、架构设计、模块契约、Agent 分工、代码审阅、验证和项目文档组织成一条可检查的工程流程。

它解决的重点不是让模型进行更长的对话，而是让模型在明确的工程边界内工作：代码 Agent 不读取完整对话历史，而是使用经过筛选的项目事实；架构图决定模块与依赖；契约定义公共接口；目录文档提供代码导航；任务状态、错误记忆和工作日志负责跨轮次延续目标。

### 主要创新

#### 1. 架构图是 AI 工作的基底

DocuAgent 先通过结构化访谈确认需求，再生成可审阅、可修改的架构图。架构图不只是展示结果，它同时参与：

- 划分模块职责和依赖关系；
- 派生模块契约与工作单元；
- 决定子 Agent 的读取范围和编辑范围；
- 组织实现顺序、跨模块转交和变更追踪；
- 为后续验证和文档同步提供事实来源。

用户可以在代码生成前审阅架构，避免错误边界直接扩散到整个项目。

#### 2. 用项目事实替代完整对话历史

代码 Agent 不读取完整聊天记录。每次工作只装配当前任务所需的项目事实，包括架构、模块契约、目录文档、当前工作单、相关接口、错误记忆和工作日志。

子 Agent 按模块持久化分区，并具有明确的读取权限与编辑权限。任务目标和历史问题被写入结构化状态，不依赖模型从长对话中自行回忆。这个设计可以减少重复上下文，形成更稳定的输入前缀以利于缓存命中，并把模型注意力集中在当前模块和相关接口上。

#### 3. 由文档 Agent 维护分层项目事实

每个未排除的受管理目录都有一份 `AI_ARCH.md`。它说明目录职责、直接文件和子目录、公共接口、依赖约束以及验证入口，让后续 Agent 不必先遍历全部源码。

所有正式 `AI_ARCH.md` 内容都由文档 Agent 编写。代码工作结束后，DocuAgent 对需要更新的目录逐个调用文档 Agent：先处理深层目录，再汇总父目录，最后生成根文档。程序只负责发现目录、采集磁盘事实、调度调用、检查文件覆盖与契约一致性，并在全部结果通过后原子写入。任一目录文档失败都会阻塞交付，不会使用程序模板代替正式文档。

### 细分功能创新

- **架构契约注册表：** 集中记录模块公开接口、共享符号、命令和依赖。Agent 在实现前检索契约，降低重复定义、接口漂移和隐式耦合风险。
- **持久化模块 Agent：** 不同模块拥有独立的上下文、错误记忆、工作日志和状态，后续任务可以沿用已确认的工程事实。
- **严格的工作区权限：** 读取范围与编辑范围分开控制。Agent 可以读取必要的相邻接口，但只能修改自己负责的文件；越界需求进入转交流程。
- **隔离实现与人工审阅：** 实现 Agent 在试运行工作区中修改文件。差异通过契约检查并经用户审阅后，才会写入主项目。
- **可停止、可恢复的长任务：** 系统保存工具循环、沙箱文件和基线信息。任务停止后可以从检查点恢复，同时检查项目是否已经发生冲突性变化。
- **错误记忆与修复闭环：** 生成、验证和文档错误会进入持久化记录；修复 Agent 可以依据错误、契约和验证输出继续处理。
- **代码与文档共同决定交付：** 代码验证通过只表示实现已通过代码门。目录文档覆盖、事实一致性和契约检查也必须通过，项目才会进入可交付状态。
- **显式授权的开源检索：** 架构确认后可以提出一次项目级 GitHub 检索。只有用户批准查询词后才会访问公开仓库，采用决定也不会自动下载或执行第三方代码。

### 工作流程

1. 选择一个空目录并描述项目目标。
2. 回答需求访谈，审阅并确认架构图。
3. DocuAgent 从架构生成模块契约和按依赖排序的工作单元。
4. 模块 Agent 在隔离工作区中实现任务，并提交差异供审阅。
5. 用户应用改动后运行声明的验证命令；失败时进入诊断和修复流程。
6. 文档 Agent 按目录更新 `AI_ARCH.md` 文档树，程序执行事实与契约门禁。
7. 只有代码任务和文档任务都通过，整体交付状态才会变为就绪。

### 社区版范围

社区版用于从空目录创建个人新项目，包含：

- 需求访谈和架构图审阅；
- 模块契约、任务依赖和持久化子 Agent；
- 隔离实现、补丁审阅、验证、诊断、修复和检查点恢复；
- 分层 `AI_ARCH.md` 文档树及独立文档交付门；
- 经用户批准的项目级 GitHub 开源检索；
- React 架构工作台、本地 Python 服务和 Electron 桌面外壳源码。

已有项目的系统性重建、逐模块外部组件选型和私有团队扩展不包含在本仓库中。本仓库提供源码，不包含托管服务、预构建桌面安装包、编译后的前端或私有扩展实现。

### 已验证范围

真实模型交付验收使用 Windows build 26100、Python 3.12.3、Node.js 22.13.1、npm 10.9.2 和 DeepSeek 官方 `deepseek-flash`。验收从空目录生成了一个 Python 标准库 CLI 项目，覆盖真实访谈、架构确认、模块实现、人工审阅、停止与检查点恢复、代码验证、文档失败隔离、文档重试和服务重启。

该结果只证明这一组环境和用例已通过，不代表所有模型、操作系统、语言和项目规模都已完成验证。

### 环境要求

- Python 3.12 或更高版本
- Node.js 22.12 或更高版本及 npm
- 一个兼容 OpenAI API 格式的模型服务和用户自己的 API Key

Python 应用运行时只使用标准库。前端和开发依赖从各自的软件包仓库安装。

### 从源码运行

在仓库根目录执行：

```bash
npm --prefix docuagent/frontend ci
npm --prefix docuagent/frontend run build
python docuagent/docuagent.py --host 127.0.0.1 --port 8765 --no-browser
```

打开 `http://127.0.0.1:8765`，在“设置”中配置模型服务，然后选择一个空项目目录。模型调用费用由所选服务商独立收取。

桌面外壳使用相同的本地后端和环境要求：

- Windows：运行 `DocuAgent-Desktop.bat`
- macOS 或 Linux：运行 `./DocuAgent-Desktop.sh`

启动脚本会在需要时安装前端或 Electron 依赖并构建前端。本源码发行版不包含预构建桌面程序。

### 验证源码

安装测试依赖并运行后端、前端测试和生产构建：

```bash
python -m pip install -r docuagent/requirements-dev.txt
PYTHONPATH=docuagent python -m pytest -q docuagent/tests
npm --prefix docuagent/frontend test
npm --prefix docuagent/frontend run build
```

PowerShell 中使用：

```powershell
$env:PYTHONPATH = "docuagent"
python -m pytest -q docuagent/tests
```

自动化测试不需要连接真实模型服务。

### 数据与安全边界

项目状态保存在项目自己的 `.docuagent/` 目录中。模型服务密钥保存在用户主目录配置中，并在配置接口响应里脱敏。完成任务所需的项目上下文会发送给用户配置的模型服务，因此“本地优先”不等于“完全离线”。

匿名使用统计默认只保存在本机，用于查看任务失败率、缓存命中率和 token 消耗。系统“用量”页同时显示整个项目、每个架构模块和每个功能模块的缓存率与 token，架构节点也会在放大后显示对应数据。统计不包含项目路径、代码、提示词、模型回复、模型名称、服务地址或 API Key。只有用户在设置中明确同意后，应用才会每 24 小时批量上传一次聚合数据；上传内容仅增加平台、CPU 架构和应用版本，用户也可以随时清除本地统计。完整说明见 [SECURITY.md](SECURITY.md)。

公开仓库检索只发送用户批准的查询词。搜索结果和模型输出均应视为不可信内容，也不会自动授予执行或复用第三方代码的许可。Agent 工作区隔离文件改动，但不是操作系统级安全沙箱。请把 HTTP 服务绑定到本机回环地址，并在运行前审阅命令、扩展、MCP 服务和生成的程序。

安全边界和漏洞报告方式见 [SECURITY.md](SECURITY.md)。

### 参与贡献

贡献方式、代码约定和 PR 要求见 [CONTRIBUTING.md](CONTRIBUTING.md)。已经确定要做的事见 [ROADMAP.md](ROADMAP.md)。参与本项目即视为接受 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。

### 仓库结构

- `docuagent/`：Python 后端、Agent 工作流、项目状态、契约和本地集成
- `docuagent/frontend/`：React 和 Vite 架构工作台
- `docuagent/desktop/`：本地服务的 Electron 外壳
- `docuagent/tests/`：后端与工作流测试
- `docuagent/AI_ARCH.md`：社区版源码导航入口
- `CONTRIBUTING.md`、`ROADMAP.md`、`CODE_OF_CONDUCT.md`：参与方式与范围

### 许可证与署名

DocuAgent Community 使用 Apache License 2.0，详见 [LICENSE](LICENSE) 和 [NOTICE](NOTICE)。Copyright 2026 Delesque。

社区许可证覆盖本源码发行版中的文件，不覆盖独立维护的私有扩展或私有开发历史。第三方软件包继续适用各自许可证。依赖范围见 [THIRD_PARTY.md](THIRD_PARTY.md)，应用图标来源见 [ASSET_PROVENANCE.md](ASSET_PROVENANCE.md)。

计划公开仓库为 `https://github.com/Delesque/docuagent-community`。如果源码树中存在 `PREVIEW-ONLY.txt`，当前目录仍是未公开的审查快照。

## English

DocuAgent is a local-first AI engineering workbench built around the architecture graph. The graph lets the model see public interfaces without digging through code, and turns a hundred locally optimal generations into one coherent whole.

Here, healthy engineering practice is a hard gate the model has to pass, not a skill it is trusted to follow on its own. DocuAgent connects requirements, architecture, module contracts, agent ownership, code review, verification, and project documentation into one inspectable engineering workflow.

Its goal is not to keep a model inside an ever-growing conversation. Coding agents work within explicit engineering boundaries: they receive selected project facts instead of the full chat transcript; the architecture graph defines modules and dependencies; contracts define public interfaces; directory documents provide code navigation; and persisted tasks, error memory, and work logs carry intent across turns.

### Major Innovations

#### 1. The architecture graph is the foundation for AI work

DocuAgent starts with a structured interview and produces a reviewable, editable architecture graph. The graph is more than a visualization. It is used to:

- define module responsibilities and dependencies;
- derive module contracts and work items;
- determine each sub-agent's read and write scopes;
- order implementation, route cross-module handoffs, and track changes;
- provide facts for verification and documentation synchronization.

Users can review the architecture before code generation, preventing an incorrect boundary from spreading through the project.

#### 2. Project facts replace full conversation history

Coding agents do not read the complete chat transcript. Each task receives only the project facts it needs: architecture, module contracts, directory documents, the current work item, related interfaces, error memory, and work logs.

Persistent sub-agents are partitioned by module and have explicit read and edit permissions. Task goals and previous failures live in structured state instead of relying on the model to remember a long conversation. This reduces repeated context, creates more stable prompt prefixes that are favorable to caching, and directs attention toward the current module and its interfaces.

#### 3. A documentation agent maintains hierarchical project facts

Every non-excluded managed directory has an `AI_ARCH.md` describing its responsibility, direct files and child directories, public interfaces, dependency constraints, and verification entry points. Future agents can navigate from these documents instead of scanning the entire codebase first.

All formal `AI_ARCH.md` content is written by the documentation agent. After code work, DocuAgent calls that agent once for each directory that needs an update: deeper directories first, then parents, and finally the root document. Program logic only discovers directories, gathers disk facts, schedules calls, checks file coverage and contract consistency, and atomically writes the batch after every result passes. A failure in any directory blocks delivery; no program-generated template is substituted for formal documentation.

### Detailed Innovations

- **Architecture contract registry:** Records module APIs, shared symbols, commands, and dependencies in one place. Agents search it before implementation, reducing duplicate definitions, interface drift, and implicit coupling.
- **Persistent module agents:** Each module keeps separate context, error memory, work logs, and state so later work can continue from confirmed engineering facts.
- **Strict workspace permissions:** Read scope and edit scope are controlled separately. Agents may inspect required neighboring interfaces but can modify only the files they own; cross-module work enters a handoff flow.
- **Isolated implementation and human review:** Implementation agents edit trial workspaces. Diffs reach the main project only after contract checks and user review.
- **Stoppable and resumable long-running work:** The system persists tool-loop history, sandbox files, and baseline information. A stopped task can resume from its checkpoint after checking the project for conflicting changes.
- **Error memory and repair loop:** Generation, verification, and documentation failures are persisted. Repair agents continue from the error, contract, and verification evidence.
- **Code and documentation jointly gate delivery:** Passing code verification completes only the code gate. Directory-document coverage, factual consistency, and contract checks must also pass before the project becomes deliverable.
- **Explicitly authorized open-source discovery:** After architecture confirmation, DocuAgent may propose one project-level GitHub search. It contacts public repositories only after the user approves the query, and an adoption decision never downloads or executes third-party code automatically.

### Workflow

1. Select an empty directory and describe the project goal.
2. Complete the requirements interview, then review and confirm the architecture graph.
3. DocuAgent derives module contracts and dependency-ordered work items.
4. Module agents implement tasks in isolated workspaces and submit diffs for review.
5. After the user applies a change, DocuAgent runs the declared verification commands and enters diagnosis and repair when needed.
6. The documentation agent updates the `AI_ARCH.md` tree directory by directory while program logic enforces fact and contract gates.
7. Delivery becomes ready only after both code work and documentation work pass.

### Community Edition Scope

The community edition creates personal new projects from an empty directory. It includes:

- requirements interviews and architecture graph review;
- module contracts, task dependencies, and persistent sub-agents;
- isolated implementation, patch review, verification, diagnosis, repair, and checkpoint recovery;
- a hierarchical `AI_ARCH.md` tree with an independent documentation delivery gate;
- user-approved project-level GitHub discovery;
- source code for the React workbench, local Python service, and Electron desktop shell.

Systematic reconstruction of existing projects, per-module external component selection, and proprietary team extensions are outside this repository. This is a source distribution; it does not contain a hosted service, prebuilt desktop installer, compiled frontend, or proprietary extension implementation.

### Validated Scope

The real-model delivery run used Windows build 26100, Python 3.12.3, Node.js 22.13.1, npm 10.9.2, and DeepSeek's official `deepseek-flash`. Starting from an empty directory, it produced a Python standard-library CLI project and covered a real interview, architecture confirmation, module implementation, human review, cancellation and checkpoint recovery, code verification, documentation failure isolation, documentation retry, and service restart.

This result validates that environment and scenario only. It does not claim complete coverage of every model, operating system, language, or project scale.

### Requirements

- Python 3.12 or newer
- Node.js 22.12 or newer and npm
- An OpenAI-compatible model provider and your own API key

The Python application runtime uses only the standard library. Frontend and development dependencies are installed from their package registries.

### Run From Source

From the repository root:

```bash
npm --prefix docuagent/frontend ci
npm --prefix docuagent/frontend run build
python docuagent/docuagent.py --host 127.0.0.1 --port 8765 --no-browser
```

Open `http://127.0.0.1:8765`, configure the model provider in Settings, and choose an empty project directory. Model usage is billed independently by the selected provider.

The desktop shell uses the same local backend and prerequisites:

- Windows: run `DocuAgent-Desktop.bat`
- macOS or Linux: run `./DocuAgent-Desktop.sh`

The launchers install missing frontend or Electron dependencies and build the frontend when needed. No prebuilt desktop application is included in this source release.

### Verify The Source

Install the test dependency, then run the backend suite, frontend suite, and production build:

```bash
python -m pip install -r docuagent/requirements-dev.txt
PYTHONPATH=docuagent python -m pytest -q docuagent/tests
npm --prefix docuagent/frontend test
npm --prefix docuagent/frontend run build
```

PowerShell equivalent:

```powershell
$env:PYTHONPATH = "docuagent"
python -m pytest -q docuagent/tests
```

The automated test suite does not require a live model provider.

### Data And Security Boundary

Project state is stored under the managed project's `.docuagent/` directory. Provider keys stay in the user's home configuration and are masked in configuration responses. Project context required for a task is sent to the configured model provider, so local-first operation is not the same as offline operation.

Anonymous usage counters are stored locally by default for task failure rates, cache hit rates and token usage. The Usage page shows totals for the whole project, each architecture module and each functional module; zoomed graph nodes show their own cache and token figures. Counters exclude project paths, code, prompts, model responses, model names, provider URLs and API keys. The application uploads aggregate batches at most once every 24 hours only after explicit user consent in Settings; uploaded batches add only platform, CPU architecture and app version. Users can clear local counters at any time. See [SECURITY.md](SECURITY.md) for the full boundary.

Public repository discovery sends only the query approved by the user. Search results and model output must be treated as untrusted and do not grant permission to execute or reuse third-party code. Agent workspaces isolate file changes but are not operating-system security sandboxes. Keep the HTTP service bound to loopback, and review commands, extensions, MCP servers, and generated applications before running them.

See [SECURITY.md](SECURITY.md) for the security boundary and vulnerability reporting process.

### Contributing

How to contribute, code conventions, and PR requirements are in [CONTRIBUTING.md](CONTRIBUTING.md). Work that is already decided is listed in [ROADMAP.md](ROADMAP.md). Participating in this project means accepting [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

### Repository Map

- `docuagent/`: Python backend, agent workflow, project state, contracts, and local integrations
- `docuagent/frontend/`: React and Vite architecture workbench
- `docuagent/desktop/`: Electron shell for the local service
- `docuagent/tests/`: backend and workflow tests
- `docuagent/AI_ARCH.md`: entry point for navigating the community source
- `CONTRIBUTING.md`, `ROADMAP.md`, `CODE_OF_CONDUCT.md`: how to take part and what is in scope

### License And Attribution

DocuAgent Community is licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE). Copyright 2026 Delesque.

The community license covers files in this source distribution. It does not cover separately maintained proprietary extensions or private development history. Third-party packages retain their own licenses. See [THIRD_PARTY.md](THIRD_PARTY.md) for dependency scope and [ASSET_PROVENANCE.md](ASSET_PROVENANCE.md) for the application icon source.

The intended public repository is `https://github.com/Delesque/docuagent-community`. If `PREVIEW-ONLY.txt` is present, the current tree is still an unpublished review snapshot.
