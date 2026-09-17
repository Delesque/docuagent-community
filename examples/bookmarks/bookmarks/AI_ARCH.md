# bookmarks/ — 本地书签管理器包

## 职责

`bookmarks` 是 Bookmarks 项目唯一的 Python 包（仓库根布局，不使用 `src/`），承载单人本地命令行书签管理器的全部运行时代码：解析 `add` / `list` / `search` / `delete` 子命令，把书签集合持久化到本地 JSON 文件（默认 `./bookmarks.json`，可用 `--file` 指定隔离数据文件），并把领域异常映射为中文提示与非零退出码。运行形态为跨平台本地命令行（Windows/macOS/Linux），固定使用 CPython 3.12 解释器直接运行，无服务端、无容器、无网络调用、无数据库。

## 目录内直接条目

本目录是扁平包，只有直接文件，没有子目录，因此不存在需要下钻的子 `AI_ARCH.md`。

- `__init__.py` — 包初始化文件，使 `bookmarks` 成为可被 `python -m bookmarks` 与 `tests/` 导入的包。当前不导入任何模块，保持零 import 的轻量初始化，避免导入包时产生副作用。
- `__main__.py` — 模块执行入口，唯一导入是 `from bookmarks.cli import main`，使 `python -m bookmarks` 可用；自身不含业务逻辑。
- `cli.py` — CLI 层实现（契约 `bookmark-cli`）：用 `argparse` 构建 `add` / `list` / `search` / `delete` 子命令与 `--file` 选项，装配 `BookmarkStore` 与 `BookmarkService`，捕获 `ValidationError`、`DuplicateURLError`、`BookmarkNotFoundError`、`CorruptStorageError` 转为中文提示与非零退出码。导出符号 `main`。
- `main.py` — 包内直接文件，自身没有任何导入，也没有被包内其他模块导入；包的实际命令入口是 `cli.py` 的 `main`，由 `__main__.py` 触发。
- `model.py` — 模型与校验层（契约 `bookmark-model`）：用 `dataclasses` 定义 `Bookmark`，用 `urllib.parse` 的 `urlsplit` / `urlunsplit` 与 `datetime`、`timezone` 实现字段校验、URL 规范化与时间戳。导出 `Bookmark`、`ValidationError`、`DuplicateURLError`、`normalize_url`。
- `service.py` — 业务服务层（契约 `bookmark-service`）：`BookmarkService` 实现 `add` / `list_all` / `search` / `delete`，维护稳定唯一整数 ID 并拒绝重复 URL；依赖 `model.py` 与 `storage.py`。导出 `BookmarkService`、`BookmarkNotFoundError`。
- `storage.py` — JSON 文件存储层（契约 `bookmark-storage`）：`BookmarkStore` 独占数据文件的读写，用 `json` 解析/序列化 `{version,next_id,bookmarks[]}`，用 `tempfile` + `os.replace` 原子落盘，损坏文件抛错且不覆盖原数据；依赖 `model.py`。导出 `BookmarkStore`、`CorruptStorageError`。

## 公开符号（契约声明，状态均为 active，since 0.1.0）

- `bookmark-model`（`bookmarks/model.py`）：`Bookmark`、`ValidationError`、`DuplicateURLError`、`normalize_url`。
- `bookmark-storage`（`bookmarks/storage.py`）：`BookmarkStore`、`CorruptStorageError`。
- `bookmark-service`（`bookmarks/service.py`）：`BookmarkService`、`BookmarkNotFoundError`。
- `bookmark-cli`（`bookmarks/cli.py`）：`main`。

## 分层与数据形状

四层最小闭环：模型与校验 → JSON 文件存储 → 业务服务 → CLI 入口，导入方向单向（`cli.py` → `service.py` → `storage.py` → `model.py`），无反向导入。

- 数据文件 `bookmarks.json`（默认 `./bookmarks.json`，可由 `--file` 覆盖）由 `bookmark-storage` 独占所有权，其余层次只能通过 `BookmarkStore` 访问；存储格式为 `{"version":1,"next_id":<int>,"bookmarks":[{"id":<int>,"title":<str>,"url":<str>,"created_at":<ISO8601>}]}`。
- ID 分配状态（`next_id`）与书签集合同文件持久化，删除后 ID 不复用，保证跨进程稳定唯一。
- 跨层数据形状：`Bookmark`（`id` 可空表示未落库）；`storage.py` 与 `service.py` 之间传递的是 `List[Bookmark]` 全量集合；服务层对外返回单个 `Bookmark` 或 `List[Bookmark]`，CLI 只消费这两种形状。

## 命令与退出码

- `python -m bookmarks add --title <标题> --url <URL>`：新增书签。
- `python -m bookmarks list`：列出全部书签。
- `python -m bookmarks search <关键字>`：按关键字搜索书签。
- `python -m bookmarks delete <id>`：按 ID 删除书签。
- `--file <路径>`：指定数据文件，缺省为 `./bookmarks.json`。

退出码语义：校验失败（`ValidationError`）、URL 重复（`DuplicateURLError`）、ID 不存在（`BookmarkNotFoundError`）与存储损坏（`CorruptStorageError`）均以非零退出码失败退出并给出中文提示，而不是抛出调用栈。

## 依赖约束

- 只允许 Python 3.12 标准库（argparse / json / dataclasses / pathlib / tempfile / os / unittest）；禁止第三方依赖、禁止联网、禁止数据库。
- `__init__.py` 保持无导入；`__main__.py` 只做 `cli.main()` 调用。
- 标题与 URL 去空格后不得为空；URL 重复判定基于 `normalize_url` 的结果。
- 写入必须先写同目录临时文件再用 `os.replace` 原子替换，避免半写文件；存储文件损坏或格式非法时抛 `CorruptStorageError`，且不得覆盖或截断原文件。
- 测试统一放在仓库根的 `tests/` 下；各模块测试使用的临时数据文件位于系统临时目录、测试结束即删除，不写入仓库。
- 无文件锁，多进程并发写同一数据文件可能互相覆盖；本包定位为单人本地工具，不解决并发。
- 用户可见文案与 README 使用中文。

## 验证

- `python -m unittest discover -s tests -v`
- `python -m unittest discover -s tests -p test_model.py -v`
- `python -m unittest discover -s tests -p test_storage.py -v`
- `python -m unittest discover -s tests -p test_service.py -v`
- `python -m unittest discover -s tests -p test_cli.py -v`
- `python -m bookmarks list`
- `python -m bookmarks add --title 示例 --url https://example.com`
