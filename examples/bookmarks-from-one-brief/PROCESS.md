# 一次真实交付的完整过程

这份记录来自本项目的一次真实运行：给一句话需求，看它交回什么、中途卡在哪里、最后怎么验证。
数字都是这次运行的实测值，不是估计。

- 被交付的项目：[`examples/bookmarks/`](bookmarks/)（命令行书签管理器，Python 标准库）
- 模型：本机配置的 OpenAI 兼容服务（未记录提供方与端点）
- 总耗时：约 12 分钟；64 次模型调用；1,330,209 token（模型侧缓存命中率 0.60）

## 一句话需求

> 做一个单人本地的命令行书签管理器，用 Python 3.12 标准库实现，数据存本地 JSON。
> 支持新增书签（标题 + URL）、列出全部书签、按关键字搜索、删除书签。稳定唯一的整数 ID；
> 标题和 URL 都不能为空，重复 URL 要拒绝并给出明确错误。进程重启后数据保留；
> 存储文件损坏时必须明确报错并失败退出，不能覆盖原始数据。入口固定为 `python -m bookmarks`，
> 支持 `--file` 指定隔离的数据文件。交付中文使用说明 README、覆盖全部业务行为的测试，
> 以及 `AI_ARCH.md` 文档树。建议四个职责模块：模型与校验、JSON 文件存储、业务服务、
> CLI 与端到端测试。

## 阶段一：访谈（4 个固定问题 + 1 轮模型追问）

固定问题覆盖交付标准、验收方式、技术边界、交付清单，答案只影响后续访谈的上下文。之后模型追问一轮，
问出的是会真正改变实现细节的事：ID 怎么生成、数据文件默认放哪、测试用什么框架。
这一步结束的标志是产出 `review` 状态的架构草案，共 4 个模块：

| 模块 | 目标文件 |
| --- | --- |
| `bookmark-model` | `bookmarks/model.py`, `tests/test_model.py` |
| `bookmark-storage` | `bookmarks/storage.py`, `tests/test_storage.py` |
| `bookmark-service` | `bookmarks/service.py`, `tests/test_service.py` |
| `bookmark-cli` | `bookmarks/cli.py`, `bookmarks/__main__.py`, `tests/test_cli.py` |

耗时：固定问题 0.0–0.03 秒，模型那一轮 29.6 秒。

## 阶段二：架构预检拒绝了模型自己的验证命令

架构草案里每个模块都带一条**验证命令**，它是「这个模块算不算做完」的判定依据。
模型第一次给的是：

```
python -m bookmarks add --title 示例 --url https://example.com && python -m bookmarks list
```

预检直接拒绝，理由是：

> 验证只允许单条白名单命令，不支持管道、重定向或命令拼接。

产品没有把问题放过去，也没有自己替换成一条看似合理的命令，而是把拒绝原因退回给模型要求修订。
修订后模型改成：

```
python -m unittest discover -s tests -p test_cli.py -v
```

这才允许进入 `confirmed`。修订耗时 10.6 秒，初始化（生成脚手架、契约与文档骨架）48.4 秒。

这一点值得单独说：**模型给出不可执行的验证命令是常态**，如果预检只是「看起来像命令就放行」，
那么生成阶段会一路跑下去，最后交付一个验证形同虚设的项目。

## 阶段三：逐模块实现（4 轮）

每一轮的结构是：生成补丁 → 人工审阅通过 → 应用 → 运行该模块的验证命令。

| 轮次 | 耗时 | 产出 |
| --- | --- | --- |
| 第 1 轮 | 63.5 秒 | `bookmark-model` 进入 review |
| 第 2 轮 | 161.6 秒 | `bookmark-storage` 进入 review |
| 第 3 轮 | 63.2 秒 | `bookmark-service` 进入 review |
| 第 4 轮 | 322.1 秒 | `bookmark-cli` 进入 review |

四轮全部结束后，4 个模块状态都是 `verified`，每个模块记录的验证命令退出码都是 0：

```
bookmark-model     verified  rc=0  python -m unittest discover -s tests -p test_model.py -v
bookmark-storage   verified  rc=0  python -m unittest discover -s tests -p test_storage.py -v
bookmark-service   verified  rc=0  python -m unittest discover -s tests -p test_service.py -v
bookmark-cli       verified  rc=0  python -m unittest discover -s tests -p test_cli.py -v
```

之后同步文档树（34.1 秒），最终 `docs_in_sync=true`、`delivery_status=ready`。

## 阶段四：独立复验（不看产品自报）

产品说「4 个模块 verified」不算证据。所以复验直接跑产出的程序，31 项检查：

**交付物结构**：README、`AI_ARCH.md`、四个模块文件、四个测试文件齐全，另有分层文档树。

**跑它自己的测试**（由我方执行）：

```
python -m unittest discover -s tests -v
Ran 144 tests in 0.775s
OK
```

**按需求逐条驱动 CLI**：

| 验收标准 | 结果 |
| --- | --- |
| 新增书签并返回 ID | 通过（输出 `已添加书签 #1：Python 官网 —— https://python.org`） |
| 列出全部书签 | 两条都在 |
| 按关键字搜索 | 只命中目标，不返回无关条目 |
| 重复 URL | 被拒绝，stderr 有明确错误、非 0 退出 |
| 空标题（只有空格） | 被拒绝 |
| 删除书签 | 成功，且列表不再包含 |
| 删除不存在的 ID | 报错、非 0 退出 |

**持久化**：数据落本地 JSON，重新读取正常。

**损坏数据**（这条最关键）：把存储文件内容改成非法 JSON，再跑一次 —— 明确报错、非 0 退出，
并且**原始文件字节完全没被覆盖**。这是需求里明确写死的行为，也是最容易被忽略的一条：
很多实现遇到坏数据会「先清空再重建」，那等于吃掉用户的数据。

**文档**：README 含入口命令 `python -m bookmarks`；存在分层 `AI_ARCH.md`。

## 用量归属

同一批统计按四个维度累积，项目路径只存 SHA-256 短键，不保存原文：

| 维度 | 值 |
| --- | --- |
| 项目 | 64 次调用 / 1,330,209 token / 缓存命中率 0.5954 |
| 模块 `bookmark-cli` | 17 次 / 477,085 token / 0.5224 |
| 模块 `bookmark-service` | 16 次 / 319,452 token / 0.6268 |
| 模块 `bookmark-storage` | 13 次 / 296,151 token / 0.6200 |
| 模块 `bookmark-model` | 9 次 / 161,132 token / 0.7501 |
| 功能 `agent` | 55 次 / 1,253,820 token |
| 功能 `documentation` | 7 次 / 55,515 token |
| 功能 `architecture` | 2 次 / 20,874 token |

## 这次运行暴露的两个真问题

**一、模型写错验证命令是常态**（见阶段二）。产品的处理是拒绝并要求修订，而不是自行改写。

**二、非 UTF-8 输出会让验证丢结果**。运行日志里出现过
`Exception in thread _readerthread` + `UnicodeDecodeError`：子进程捕获按区域编码解码，
遇到非 UTF-8 字节（中文 Windows 上 `ipconfig` 之类命令输出 GBK）时读取线程崩溃，
**捕获的输出全部丢失**，验证命令于是被误记为失败。修复方式是对所有捕获子进程输出的调用
显式使用 `encoding="utf-8", errors="replace"`，并加了回归测试（修复前该测试失败）。

## 复现方式

样例项目就在 [`examples/bookmarks/`](bookmarks/)，可以直接跑：

```bash
cd examples/bookmarks
python -m unittest discover -s tests -v          # 144 个测试
python -m bookmarks --file /tmp/demo.json add --title "Python" --url https://python.org
python -m bookmarks --file /tmp/demo.json list
```

需要 Python 3.12 或更高，无第三方依赖。
