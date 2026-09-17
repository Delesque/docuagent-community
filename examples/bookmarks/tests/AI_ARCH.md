# tests/ —— 测试目录

## 职责

`tests/` 存放 Bookmarks 项目的全部自动化测试，是四层最小闭环（模型 → 存储 → 服务 → CLI）
的验证入口。测试统一使用标准库 `unittest` 编写，按被测模块逐文件组织，覆盖书签字段校验、
JSON 存储读写与损坏处理、业务规则以及命令行端到端行为。

测试使用系统临时目录中的临时数据文件，结束即删除，不向仓库写入 `bookmarks.json`。
该目录不属于 `bookmarks/` 包的分层依赖：测试可以导入任意层，产品代码不得反向导入 `tests/`。

## 目录内容

| 名称 | 类型 | 说明 |
| --- | --- | --- |
| `test_model.py` | 文件 | 模型层单元测试，验证 `bookmarks.model` 的数据结构、字段校验与 URL 规范化。 |
| `test_storage.py` | 文件 | 存储层单元测试，验证 `bookmarks.storage` 的 JSON 读写、原子落盘与损坏文件处理。 |
| `test_service.py` | 文件 | 服务层测试，验证 `bookmarks.service` 的新增/列出/搜索/删除业务规则与异常路径。 |
| `test_cli.py` | 文件 | CLI 端到端测试，验证 `bookmarks.cli` 的子命令输出与退出码语义。 |
| `test_smoke.py` | 文件 | 冒烟测试，验证命令行入口可被导入。 |

`tests/` 下没有子目录，也没有其他直接文件。

## 各文件职责与所测符号

- `test_model.py`：导入 `Bookmark`、`DuplicateURLError`、`ValidationError`、`normalize_url`
  以及标准库 `unittest`、`datetime`，是模型与校验契约的固化点。
- `test_storage.py`：导入 `BookmarkStore`、`CorruptStorageError`、`Bookmark`、`ValidationError`
  以及 `json`、`tempfile`、`unittest`、`pathlib.Path`，用临时数据文件验证读写与损坏文件不被覆盖。
- `test_service.py`：导入 `BookmarkService`、`BookmarkNotFoundError`、`BookmarkStore`、
  `CorruptStorageError`、`Bookmark`、`DuplicateURLError`、`ValidationError` 以及
  `json`、`os`、`tempfile`、`unittest`、`pathlib.Path`，覆盖跨存储与模型的业务组合行为。
- `test_cli.py`：导入 `main`、`EXIT_SUCCESS`、`EXIT_FAILURE`、`EXIT_USAGE` 以及
  `importlib.util`、`io`、`tempfile`、`unittest`、`contextlib.redirect_stderr`、
  `contextlib.redirect_stdout`、`pathlib.Path`，通过重定向标准输出/错误捕获命令行表现。
- `test_smoke.py`：仅导入 `unittest` 与 `from bookmarks.main import main`，是最轻量的
  入口可用性检查。

## 重要公开符号

被测产品侧的公开契约（测试断言目标）：

- 模型层：`Bookmark`、`ValidationError`、`DuplicateURLError`、`normalize_url`。
- 存储层：`BookmarkStore`、`CorruptStorageError`。
- 服务层：`BookmarkService`、`BookmarkNotFoundError`。
- CLI 层：`main`，以及退出码常量 `EXIT_SUCCESS`、`EXIT_FAILURE`、`EXIT_USAGE`。

## 依赖约束

- 仅使用 Python 3.12 标准库（测试侧为 `unittest`、`tempfile`、`json`、`os`、`io`、
  `importlib.util`、`contextlib`、`pathlib`、`datetime`），禁止第三方依赖、禁止联网、禁止数据库。
- 包布局统一为仓库根的 `bookmarks/` 包（不使用 `src/` 布局），测试统一放在仓库根的 `tests/`。
- 涉及存储的测试必须使用临时数据文件，避免污染默认的 `./bookmarks.json`。
- 存储文件损坏或格式非法时产品代码抛 `CorruptStorageError`，CLI 以非零退出码失败退出；
  测试应断言退出码与错误提示，而不是异常栈。
- 测试可以导入任意产品层，产品代码（`bookmarks/` 包）不得反向导入 `tests/`。

## 验证命令

- `python -m unittest discover -s tests -v` —— 运行 `tests/` 下全部测试（项目级验证命令）。
- `python -m unittest discover -s tests -p test_model.py -v` —— 只运行模型层测试。
- `python -m unittest discover -s tests -p test_storage.py -v` —— 只运行存储层测试。
- `python -m unittest discover -s tests -p test_service.py -v` —— 只运行服务层测试。
- `python -m unittest discover -s tests -p test_cli.py -v` —— 只运行 CLI 端到端测试。

## 注意事项

- `test_smoke.py` 的导入路径为 `from bookmarks.main import main`，即它期望入口模块
  为 `bookmarks.main`；CLI 层契约声明的入口为 `bookmarks/cli.py` 的 `main`，并由
  `bookmarks/__main__.py` 支撑 `python -m bookmarks`。修改入口模块名时需同步这两处。
- 搜索语义（大小写不敏感、子串匹配范围）由服务层实现固定，必须由 `test_service.py`
  固化，后续不得静默变更。
- 数据文件无文件锁，多进程并发写同一文件可能互相覆盖；该项目定位为单人本地工具，
  测试不覆盖并发场景。
- 本目录新增、删除或重命名测试文件后，本文件需同步更新。
