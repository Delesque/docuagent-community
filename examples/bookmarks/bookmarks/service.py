"""书签业务服务层：以 :class:`BookmarkService` 固化新增、列出、搜索、删除的业务规则。

本模块是四层最小闭环（模型与校验 → JSON 文件存储 → 业务服务 → CLI 入口）中的服务层，
只依赖下层模块（模型层与存储层），并把业务规则叠在
:class:`bookmarks.storage.BookmarkStore` 之上：

- :meth:`BookmarkService.add`：先经模型层校验标题与 URL，再按
  :func:`bookmarks.model.normalize_url` 与全量集合比对以拒绝重复 URL，
  然后向存储层申请下一个 id 并把全量集合原子落库；
- :meth:`BookmarkService.list_all`：按插入顺序返回全量书签；
- :meth:`BookmarkService.search`：标题与 URL 的大小写不敏感子串匹配；
- :meth:`BookmarkService.delete`：按 id 删除并返回被删除的书签，id 不存在时抛
  :class:`BookmarkNotFoundError`；删除后 id 不复用（由存储层的 ID 分配状态保证）。

服务层不缓存书签集合，每次操作都通过存储层读取磁盘，因此多个服务实例共享同一份数据。

对外契约只有 :class:`BookmarkService` 与 :class:`BookmarkNotFoundError`，其余名字都是
本模块的实现细节。服务层不吞掉下层异常：字段校验失败抛
:class:`bookmarks.model.ValidationError`，URL 重复抛
:class:`bookmarks.model.DuplicateURLError`，数据文件损坏抛
:class:`bookmarks.storage.CorruptStorageError`，由 CLI 统一映射为中文提示与非零退出码。
"""

from __future__ import annotations

from dataclasses import replace

from bookmarks.model import Bookmark, DuplicateURLError, ValidationError, normalize_url
from bookmarks.storage import BookmarkStore

__all__ = ["BookmarkNotFoundError", "BookmarkService"]


class BookmarkNotFoundError(LookupError):
    """按 id 定位书签时未找到对应记录。"""


class BookmarkService:
    """把书签业务规则叠加在 :class:`~bookmarks.storage.BookmarkStore` 之上。

    ``store`` 必须是 :class:`~bookmarks.storage.BookmarkStore` 实例；数据文件的读写
    完全交给该对象，本类不直接接触文件。
    """

    def __init__(self, store: BookmarkStore) -> None:
        if not isinstance(store, BookmarkStore):
            raise TypeError("BookmarkService 需要 BookmarkStore 实例。")
        self._store = store

    def add(self, title: str, url: str) -> Bookmark:
        """新增书签：校验字段、拒绝重复 URL、分配 id 并持久化。

        返回已落库的书签（含分配到的 id）。标题与 URL 去掉首尾空白后不得为空；
        URL 的重复判定基于 :func:`bookmarks.model.normalize_url` 的结果，
        重复时抛 :class:`bookmarks.model.DuplicateURLError` 且不写入任何内容。
        """
        pending = _pending_bookmark(title, url)
        bookmarks = self._store.load()
        _reject_duplicate_url(bookmarks, pending)
        bookmark = replace(pending, id=self._store.next_id())
        self._store.save([*bookmarks, bookmark])
        return bookmark

    def list_all(self) -> list[Bookmark]:
        """按插入顺序返回全量书签；数据文件不存在时返回空列表。"""
        return self._store.load()

    def search(self, keyword: str) -> list[Bookmark]:
        """按关键字搜索标题与 URL（大小写不敏感的子串匹配）。

        关键字去掉首尾空白后不得为空，否则抛
        :class:`bookmarks.model.ValidationError`；无匹配时返回空列表。
        """
        needle = _require_keyword(keyword)
        return [bookmark for bookmark in self._store.load() if _matches(bookmark, needle)]

    def delete(self, bookmark_id: int) -> Bookmark:
        """按 id 删除书签并返回被删除的书签；id 不存在时抛 :class:`BookmarkNotFoundError`。

        id 必须是整数（``bool`` 除外），否则抛
        :class:`bookmarks.model.ValidationError`。删除后 id 不会被复用，因为 ID 分配
        状态与书签集合同文件持久化。
        """
        target_id = _require_id(bookmark_id)
        bookmarks = self._store.load()
        for index, bookmark in enumerate(bookmarks):
            if bookmark.id == target_id:
                removed = bookmarks.pop(index)
                self._store.save(bookmarks)
                return removed
        raise BookmarkNotFoundError(f"未找到 id 为 {target_id} 的书签。")


def _pending_bookmark(title: str, url: str) -> Bookmark:
    """构造未落库书签（``id`` 为 ``None``），借此复用模型层的字段校验与 URL 规范化。"""
    return Bookmark(id=None, title=title, url=url)


def _reject_duplicate_url(bookmarks: list[Bookmark], pending: Bookmark) -> None:
    """规范化 URL 已存在时拒绝新增。"""
    candidate = normalize_url(pending.url)
    for bookmark in bookmarks:
        if normalize_url(bookmark.url) == candidate:
            raise DuplicateURLError(f"URL 已存在：{candidate}。")


def _require_keyword(keyword: str) -> str:
    """校验搜索关键字并返回用于比较的规范化形式。"""
    if not isinstance(keyword, str):
        raise ValidationError("搜索关键字必须是文本。")
    text = keyword.strip()
    if not text:
        raise ValidationError("搜索关键字不能为空。")
    return text.casefold()


def _require_id(bookmark_id: int) -> int:
    """校验待删除书签的 id 必须是整数（拒绝 ``bool``）。"""
    if isinstance(bookmark_id, bool) or not isinstance(bookmark_id, int):
        raise ValidationError("书签 id 必须是整数。")
    return bookmark_id


def _matches(bookmark: Bookmark, needle: str) -> bool:
    """标题或 URL 的小写形式包含关键字即命中。"""
    return needle in bookmark.title.casefold() or needle in bookmark.url.casefold()
