# Roadmap / 路线图

[中文](#中文) | [English](#english)

## 中文

这份路线图只列**已经确定要做**的事。更长期的方向会在 issue 里公开讨论后再加进来，不在文件里提前承诺。

### 0.1.x：把已验证范围扩大

`0.1.0` 的真实模型验收只覆盖了一组环境：Windows、Python 3.12、Node 22、一个兼容服务、一个 Python 标准库命令行项目。它证明这条链路能跑通，不代表所有环境都通过了。接下来的首要工作是补上这些缺口：

- **macOS 与 Linux 的真实交付验收。** 目录选择依赖 `tkinter`，Linux 上可能缺少 `python3-tk`，需要确认并给出明确的环境要求。
- **更多兼容服务的端到端验收。** 目前只有一个服务跑过完整链路。
- **更多语言与验证命令模板。** 现在的验证命令模板围绕 Python 生态，需要覆盖其他语言。

### 0.2：降低上手成本

- 缩短从克隆到跑起来的步骤，减少必须手工执行的命令。
- 缩短后端全量测试的时间，让贡献者不必等几分钟才能验证改动。

### 不在范围内

以下不在本仓库范围内，也不会因为被提议就加入：

- 已有项目的系统性重建（社区版只做从空目录创建）
- 逐模块外部组件选型
- 私有团队扩展与托管服务

正确性说明：`0.1.x` 的目标是把验证过的范围写清楚、写诚实，而不是把未验证的东西说成已支持。

---

## English

This roadmap lists only what is **already decided**. Longer-term directions get discussed in issues first and are added here afterward; nothing is promised in this file ahead of a decision.

### 0.1.x: widen the validated range

The `0.1.0` real-model acceptance run covered one environment: Windows, Python 3.12, Node 22, one compatible provider, one Python standard-library CLI project. It shows the pipeline works; it does not claim every environment is covered. Closing those gaps comes first:

- **Real delivery acceptance on macOS and Linux.** Directory picking depends on `tkinter`, which may be missing on Linux (`python3-tk`); this needs confirming and documenting as an explicit requirement.
- **End-to-end acceptance with more compatible providers.** Only one provider has run the full pipeline so far.
- **More languages and verification command templates.** Current templates center on the Python ecosystem and need to cover others.

### 0.2: lower the cost of getting started

- Fewer steps and fewer manual commands between clone and a running instance.
- Shorter full backend suite so contributors do not wait several minutes to validate a change.

### Out of scope

These stay out of this repository and will not be added simply because they are proposed:

- Systematic reconstruction of existing projects (the community edition creates from an empty directory)
- Per-module external component selection
- Proprietary team extensions and hosted services

A note on honesty: the goal of `0.1.x` is to state the validated range precisely, not to describe unverified things as supported.
