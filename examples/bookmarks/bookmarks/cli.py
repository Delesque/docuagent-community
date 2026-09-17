"""命令行入口：把 ``add`` / ``list`` / ``search`` / ``delete`` 子命令映射到业务服务。

本模块是四层最小闭环（模型与校验 → JSON 文件存储 → 业务服务 → CLI 入口）的最上层，
只依赖下层模块，并承担两件职责：

- 解析命令行 ``python -m bookmarks <子命令> [--file 路径]``，缺省数据文件为
  ``./bookmarks.json``（``--file`` 既可以写在子命令之前，也可以写在子命令之后）；
- 把领域异常（``ValidationError`` / ``DuplicateURLError`` /
  ``BookmarkNotFoundError`` / ``CorruptStorageError``）映射为中文提示与非零退出码，
  而不是把异常调用栈抛给用户。

退出码语义：

- ``0``（:data:`EXIT_SUCCESS`）：命令成功；
- ``1``（:data:`EXIT_FAILURE`）：领域错误（字段校验失败、URL 重复、id 不存在、
  数据文件损坏）或数据文件读写故障；
- ``2``（:data:`EXIT_USAGE`）：用法错误（缺少子命令、缺少必需参数、参数取值非法、
  未知子命令或未知选项）。

对外契约只有 :func:`main`，其余名字都是本模块的实现细节。
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from typing import NoReturn

from bookmarks.model import Bookmark, ValidationError
from bookmarks.service import BookmarkNotFoundError, BookmarkService
from bookmarks.storage import BookmarkStore, CorruptStorageError

__all__ = ["main"]

EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2

_DEFAULT_DATA_FILE = "bookmarks.json"
_EMPTY_LIST_MESSAGE = "暂无书签。"
_EMPTY_SEARCH_MESSAGE = "未找到匹配的书签。"
_DOMAIN_ERRORS = (ValidationError, BookmarkNotFoundError, CorruptStorageError)


class _ArgumentParser(argparse.ArgumentParser):
    """把 argparse 的用法错误统一为中文前缀与退出码 :data:`EXIT_USAGE`。"""

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        print(f"错误：{message}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)


def main(argv: Sequence[str] | None = None) -> int:
    """解析命令行、执行对应子命令并返回进程退出码。

    ``argv`` 缺省时读取 ``sys.argv[1:]``（由 ``python -m bookmarks`` 触发）。
    用法错误由 argparse 以退出码 :data:`EXIT_USAGE` 直接退出；
    领域错误与数据文件读写故障打印中文提示并返回 :data:`EXIT_FAILURE`。
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    command = args.command
    if command is None:
        parser.print_help()
        return EXIT_USAGE
    data_file = (getattr(args, "file", None) or "").strip()
    if not data_file:
        return _report_usage_error("数据文件路径不能为空。")
    try:
        service = BookmarkService(BookmarkStore(data_file))
        return _HANDLERS[command](service, args)
    except _DOMAIN_ERRORS as error:
        return _report_error(str(error))
    except OSError as error:
        return _report_error(f"无法访问数据文件 {data_file}：{error}")


def _add(service: BookmarkService, args: argparse.Namespace) -> int:
    """新增书签并打印落库结果。"""
    if args.title is None or args.url is None:
        return _report_usage_error("add 需要同时提供 --title 与 --url。")
    bookmark = service.add(args.title, args.url)
    print(f"已添加书签 #{bookmark.id}：{bookmark.title} —— {bookmark.url}")
    return EXIT_SUCCESS


def _list(service: BookmarkService, _args: argparse.Namespace) -> int:
    """列出全部书签。"""
    return _print_bookmarks(service.list_all(), _EMPTY_LIST_MESSAGE)


def _search(service: BookmarkService, args: argparse.Namespace) -> int:
    """按关键字搜索标题与 URL 并打印命中的书签。"""
    if args.keyword is None:
        return _report_usage_error("search 需要关键字参数。")
    return _print_bookmarks(service.search(args.keyword), _EMPTY_SEARCH_MESSAGE)


def _delete(service: BookmarkService, args: argparse.Namespace) -> int:
    """按 id 删除书签并打印被删除的记录。"""
    raw_id = args.bookmark_id
    if raw_id is None:
        return _report_usage_error("delete 需要 id 参数。")
    bookmark_id = _parse_id(raw_id)
    if bookmark_id is None:
        return _report_usage_error(f"书签 id 必须是整数，实际收到：{raw_id}。")
    removed = service.delete(bookmark_id)
    print(f"已删除书签 #{removed.id}：{removed.title} —— {removed.url}")
    return EXIT_SUCCESS


def _print_bookmarks(bookmarks: list[Bookmark], empty_message: str) -> int:
    """打印书签列表；集合为空时打印中文提示，两种情况都以成功码结束。"""
    if not bookmarks:
        print(empty_message)
        return EXIT_SUCCESS
    for bookmark in bookmarks:
        print(f"#{bookmark.id}\t{bookmark.title}\t{bookmark.url}\t{bookmark.created_at}")
    return EXIT_SUCCESS


def _parse_id(raw_id: str) -> int | None:
    """把命令行给出的 id 文本解析为整数；无法解析时返回 ``None``。"""
    try:
        return int(raw_id.strip())
    except ValueError:
        return None


def _report_error(message: str) -> int:
    """打印领域错误的中文提示并返回 :data:`EXIT_FAILURE`。"""
    print(f"错误：{message}", file=sys.stderr)
    return EXIT_FAILURE


def _report_usage_error(message: str) -> int:
    """打印用法错误的中文提示并返回 :data:`EXIT_USAGE`。"""
    print(f"错误：{message}", file=sys.stderr)
    return EXIT_USAGE


def _build_parser() -> argparse.ArgumentParser:
    """构造带中文帮助文本的命令行解析器。"""
    parser = _ArgumentParser(
        prog="python -m bookmarks",
        description="本地命令行书签管理器：书签保存在本地 JSON 文件，缺省 ./bookmarks.json。",
    )
    _add_data_file_option(parser)
    subparsers = parser.add_subparsers(dest="command", metavar="子命令")

    add_parser = subparsers.add_parser("add", help="新增书签。", description="新增一条书签。")
    add_parser.add_argument("--title", metavar="标题", help="书签标题（去空格后不得为空）。")
    add_parser.add_argument(
        "--url", metavar="URL", help="书签 URL（去空格后不得为空；重复 URL 会被拒绝）。"
    )
    _add_data_file_option(add_parser, suppress_default=True)

    list_parser = subparsers.add_parser("list", help="列出全部书签。", description="列出全部书签。")
    _add_data_file_option(list_parser, suppress_default=True)

    search_parser = subparsers.add_parser(
        "search",
        help="按关键字搜索书签。",
        description="按关键字搜索标题与 URL（大小写不敏感的子串匹配）。",
    )
    search_parser.add_argument("keyword", nargs="?", metavar="关键字", help="搜索关键字。")
    _add_data_file_option(search_parser, suppress_default=True)

    delete_parser = subparsers.add_parser(
        "delete", help="按 id 删除书签。", description="按 id 删除书签；删除后的 id 不会被复用。"
    )
    delete_parser.add_argument("bookmark_id", nargs="?", metavar="ID", help="要删除的书签 id。")
    _add_data_file_option(delete_parser, suppress_default=True)
    return parser


def _add_data_file_option(
    parser: argparse.ArgumentParser, *, suppress_default: bool = False
) -> None:
    """给主解析器或子解析器添加 ``--file`` 选项。

    子解析器以 :data:`argparse.SUPPRESS` 为缺省值，因此子命令未给出 ``--file`` 时
    不会覆盖主解析器已解析出的取值，使 ``--file`` 写在子命令前后都有效。
    """
    parser.add_argument(
        "--file",
        metavar="路径",
        default=argparse.SUPPRESS if suppress_default else None,
        help=f"数据文件路径，缺省 ./{_DEFAULT_DATA_FILE}。",
    )


_HANDLERS = {
    "add": _add,
    "list": _list,
    "search": _search,
    "delete": _delete,
}
