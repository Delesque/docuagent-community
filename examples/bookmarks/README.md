# Bookmarks —— 单人本地命令行书签管理器

一个只用 Python 3.12 标准库实现的本地书签管理器：书签保存在本地 JSON 文件里，
通过固定入口 `python -m bookmarks` 使用，无第三方依赖、无网络调用、无数据库、无登录。

## 环境要求

- Python 3.12 或更高版本（标准库 `argparse` / `json` / `pathlib` / `tempfile` 等）。
- 无其他依赖：项目不使用 `pip install`，也没有运行时第三方包。

## 快速开始

在仓库根目录执行（无需安装）：

```bash
python -m bookmarks add --title 示例 --url https://example.com
python -m bookmarks list
```

输出示例：

```
已添加书签 #1：示例 —— https://example.com
#1	示例	https://example.com	2026-01-01T00:00:00+00:00
```

## 子命令

| 命令 | 说明 |
| --- | --- |
| `python -m bookmarks add --title <标题> --url <URL>` | 新增一条书签，成功时打印分配到的 id。 |
| `python -m bookmarks list` | 按插入顺序列出全部书签。 |
| `python -m bookmarks search <关键字>` | 按关键字搜索标题与 URL（大小写不敏感的子串匹配）。 |
| `python -m bookmarks delete <id>` | 按 id 删除书签，成功时打印被删除的记录。 |

公共选项：

| 选项 | 说明 |
| --- | --- |
| `--file <路径>` | 指定数据文件，缺省 `./bookmarks.json`。可以写在子命令之前或之后。 |
| `-h` / `--help` | 查看主命令或任一子命令的中文帮助。 |

示例：

```bash
python -m bookmarks --file ./data/dev.json add --title Python 官方 --url https://python.org
python -m bookmarks list --file ./data/dev.json
python -m bookmarks search python --file ./data/dev.json
python -m bookmarks delete 1 --file ./data/dev.json
```

## 数据文件

默认数据文件为当前工作目录下的 `./bookmarks.json`，可用 `--file` 指向其他路径
（例如为不同场景建立互相隔离的数据文件）。文件格式：

```json
{
  "version": 1,
  "next_id": 2,
  "bookmarks": [
    {
      "id": 1,
      "title": "示例",
      "url": "https://example.com",
      "created_at": "2026-01-01T00:00:00+00:00"
    }
  ]
}
```

- `version`：数据文件格式版本，当前为 `1`。
- `next_id`：下一个将被分配的 id，与书签集合同文件保存，因此删除后的 id 不会被复用。
- `bookmarks`：书签记录数组，字段为 `id` / `title` / `url` / `created_at`（ISO8601，UTC）。
- 写入采用“先写同目录临时文件、再用 `os.replace` 原子替换”的方式，不会留下半写文件。
- 数据文件由存储层独占读写，其他代码请通过 `bookmarks.storage.BookmarkStore` 访问。

## 退出码语义

| 退出码 | 含义 |
| --- | --- |
| `0` | 命令成功（`list` / `search` 无匹配后打印中文提示也算成功）。 |
| `1` | 领域错误：标题或 URL 校验失败、URL 重复、id 不存在、数据文件损坏，或数据文件读写失败。 |
| `2` | 用法错误：缺少子命令、缺少必需参数、id 不是整数、数据文件路径为空、未知子命令或未知选项。 |

错误提示为中文，输出到标准错误；例如 URL 重复时：

```
错误：URL 已存在：https://example.com。
```

数据文件损坏时不会覆盖或截断该文件，而是提示错误并以退出码 `1` 结束，便于排查后修复或
用 `--file` 指向新的数据文件重新开始。

## 规则与约束

- 标题与 URL 去掉首尾空白后不得为空；URL 不得包含空白字符。
- URL 重复判定基于规范化结果：协议与主机名转小写、根路径 `/` 归一为空路径，
  路径 / 查询串 / 片段保持原样。
- 删除书签后其 id 不会被复用。
- 项目为单人本地工具，不含文件锁：多个进程同时写同一个数据文件可能互相覆盖。

## 开发与测试

```bash
python -m unittest discover -s tests -v
```

测试使用系统临时目录中的临时数据文件，结束后即删除，不会向仓库写入 `bookmarks.json`。

## 代码结构

四层最小闭环，导入方向单向（CLI → 服务 → 存储 → 模型）：

| 层 | 文件 | 职责 |
| --- | --- | --- |
| 模型与校验 | `bookmarks/model.py` | `Bookmark`、`ValidationError`、`DuplicateURLError`、`normalize_url`。 |
| JSON 文件存储 | `bookmarks/storage.py` | `BookmarkStore`（原子写入、损坏检测）、`CorruptStorageError`。 |
| 书签业务服务 | `bookmarks/service.py` | `BookmarkService`（新增 / 列出 / 搜索 / 删除）、`BookmarkNotFoundError`。 |
| CLI 入口 | `bookmarks/cli.py` | `main`：子命令解析、`--file`、中文提示与退出码。 |

`bookmarks/__main__.py` 只把控制权交给 `bookmarks.cli.main`，因此 `python -m bookmarks`
即为固定入口。
