# AI_ARCH.md —— Bookmarks 仓库根

## 职责

本目录是 Bookmarks 项目的仓库根，承载单人本地命令行书签管理器的全部交付物：`bookmarks` 包（Python 3.12 纯标准库实现）、`pyproject.toml` 项目元数据、`README.md` 中文使用文档与 `tests` 测试目录。运行形态为跨平台本地命令行（Windows/macOS/Linux），由 CPython 3.12 解释器直接运行，无服务端、无容器、无网络调用、无数据库、无登录。固定入口为 `python -m bookmarks`，可用 `--file` 指定隔离的数据文件（缺省 `./bookmarks.json`）。

代码组织为四层最小闭环：模型与校验 → JSON 文件存储 → 业务服务 → CLI 入口与交付文档；导入方向单向，`bookmarks` 包不得导入 `tests`。

## 目录内直接条目

### `bookmarks`（目录）

项目唯一的 Python 包，采用仓库根布局（不使用 `src/` 布局），承载全部运行时代码。包内直接文件为 `__init__.py`（零导入的包初始化）、`__main__.py`（`from bookmarks.cli import main`，支撑 `python -m bookmarks`）、`cli.py`（CLI 层，公开 `main`）、`model.py`（模型与校验层）、`service.py`（业务服务层）、`storage.py`（JSON 存储层），以及无任何导入的残留直接文件 `main.py`。

子导航文档：`bookmarks/AI_ARCH.md`。

### `pyproject.toml`（文件）

项目元数据文件，其中声明的 Python 版本要求为 `requires-python = ">=3.12"`，即确切取值为 `>=3.12`。项目不使用第三方运行时依赖（依赖集合为空）；测试使用标准库 `unittest`。

### `README.md`（文件）

面向使用者的中文交付文档，说明 `python -m bookmarks` 的运行方式、`add` / `list` / `search` / `delete` 子命令、`--file` 选项、数据文件格式、退出码语义、URL 规范化与 ID 不复用规则，以及测试命令与四层代码结构。

### `tests`（目录）

存放全部自动化测试，使用标准库 `unittest` 编写，按被测模块逐文件组织：`test_model.py`、`test_storage.py`、`test_service.py`、`test_cli.py` 与 `test_smoke.py`。测试数据一律使用系统临时目录中的临时文件，结束即删除，不向仓库写入 `bookmarks.json`。

子导航文档：`tests/AI_ARCH.md`。

## 公开符号与命令

各层公开符号（按依赖方向）：

- 模型层（`bookmarks/model.py`）：`Bookmark`、`ValidationError`、`DuplicateURLError`、`normalize_url`。
- 存储层（`bookmarks/storage.py`）：`BookmarkStore`、`CorruptStorageError`。
- 服务层（`bookmarks/service.py`）：`BookmarkService`、`BookmarkNotFoundError`。
- CLI 层（`bookmarks/cli.py`）：`main`，以及退出码常量 `EXIT_SUCCESS`、`EXIT_FAILURE`、`EXIT_USAGE`（`tests/test_cli.py` 直接导入这四个符号）。

命令行接口：

- `python -m bookmarks add --title <标题> --url <URL>`：新增书签。
- `python -m bookmarks list`：列出全部书签。
- `python -m bookmarks search <关键字>`：按关键字搜索书签。
- `python -m bookmarks delete <id>`：按 ID 删除书签。
- `--file <路径>`：指定数据文件，缺省 `./bookmarks.json`。

退出码语义：校验失败（`ValidationError`）、URL 重复（`DuplicateURLError`）、ID 不存在（`BookmarkNotFoundError`）与存储损坏（`CorruptStorageError`）均以非零退出码失败退出并给出中文提示，而不是抛出调用栈；具体为 `0` 成功、`1` 领域错误或数据文件读写失败、`2` 用法错误。

## 数据与所有权

数据文件 `bookmarks.json`（默认 `./bookmarks.json`，可由 `--file` 覆盖）格式为：
`{"version":1,"next_id":<int>,"bookmarks":[{"id":<int>,"title":<str>,"url":<str>,"created_at":<ISO8601>}]}`。
该文件由存储层独占所有权，其他模块只能通过 `BookmarkStore` 访问。ID 分配状态（`next_id`）与书签集合同文件持久化，删除后 ID 不复用，保证跨进程稳定唯一。跨层数据形状：模型层提供 `Bookmark`（`id` 可空表示未落库）；存储层与业务层之间传递 `List[Bookmark]` 全量集合；业务层对外返回单个 `Bookmark` 或 `List[Bookmark]`，CLI 只消费这两种形状。

## 依赖约束

- 仅使用 Python 3.12 标准库（argparse / json / dataclasses / pathlib / tempfile / os / unittest 等）；禁止第三方依赖、禁止联网、禁止数据库。
- 包布局统一为仓库根的 `bookmarks` 包，测试统一放 `tests`。
- 包内导入方向单向：`cli.py` → `service.py` → `storage.py` → `model.py`，不允许反向导入；`bookmarks` 包不得导入 `tests`。
- 写入必须先写同目录临时文件再以 `os.replace` 原子替换，避免半写文件。
- 存储文件损坏或格式非法时抛 `CorruptStorageError`，且不得覆盖或截断原文件。
- 标题与 URL 去空格后不得为空；URL 重复判定基于 `normalize_url` 的结果。
- 用户可见文案与 `README.md` 使用中文。
- 无文件锁，多进程并发写同一数据文件可能互相覆盖；本项目定位为单人本地工具，不解决并发。
- 搜索语义（标题与 URL、大小写不敏感、子串匹配）由服务层实现固定，并以服务层测试固化。

## 入口一致性说明

命令入口由 `bookmarks/cli.py` 的 `main` 提供，并由 `bookmarks/__main__.py` 的 `from bookmarks.cli import main` 触发，因此 `python -m bookmarks` 可用。仓库根仍存在直接文件 `bookmarks/main.py`（无任何导入）与 `tests/test_smoke.py`（`from bookmarks.main import main`），二者引用的是 `bookmarks.main` 而非 `bookmarks.cli`；修改入口模块名时需同步这两处。

## 验证

- `python -m unittest discover -s tests -v` —— 运行 `tests` 下全部测试（项目级验证命令）。
- `python -m unittest discover -s tests -p test_model.py -v` —— 模型层测试。
- `python -m unittest discover -s tests -p test_storage.py -v` —— 存储层测试。
- `python -m unittest discover -s tests -p test_service.py -v` —— 服务层测试。
- `python -m unittest discover -s tests -p test_cli.py -v` —— CLI 层测试。
- `python -m bookmarks list` —— 验证命令行入口可运行并列出书签。
- `python -m bookmarks add --title 示例 --url https://example.com` —— 验证新增书签路径。
