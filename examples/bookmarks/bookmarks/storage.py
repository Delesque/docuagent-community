"""JSON 文件存储层：以 :class:`BookmarkStore` 独占数据文件的读写。

本模块是四层最小闭环（模型与校验 → JSON 文件存储 → 业务服务 → CLI 入口）中的存储层，
是数据文件 ``bookmarks.json``（默认 ``./bookmarks.json``，可由 ``--file`` 覆盖）的唯一所有者：

- 文档形状：``{"version": 1, "next_id": <int>, "bookmarks": [<记录>...]}``，
  记录形状：``{"id": <int>, "title": <str>, "url": <str>, "created_at": <ISO8601>}``；
- 文件不存在视为空集合（首次使用不报错）；文件损坏或格式非法时抛
  :class:`CorruptStorageError`；
- 写入先写同目录临时文件再以 :func:`os.replace` 原子替换；写入前会先解析现有文件，
  因此损坏的文件不会被覆盖或截断；
- ``next_id`` 与书签集合同文件持久化：保存全量集合时取“现有文件中的 next_id”与
  “本次集合中最大 id + 1”的较大者，因此删除书签后 id 不会被复用。

读取时会忽略未知的文档/记录字段（向前兼容），但已知字段的取值必须合法；
写回时按固定字段顺序重新序列化，未知字段不会保留。

对外契约只有 :class:`BookmarkStore` 与 :class:`CorruptStorageError`，其余名字都是
本模块的实现细节。真实 I/O 故障（权限、磁盘、目标是目录等）不包装成
:class:`CorruptStorageError`，而是原样抛出 :class:`OSError`，便于调用方区分
“内容损坏”与“环境故障”。
"""

from __future__ import annotations

import json
import os
import tempfile
import warnings
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bookmarks.model import Bookmark, ValidationError

__all__ = ["BookmarkStore", "CorruptStorageError"]

_STORAGE_VERSION = 1
_FIRST_ID = 1
_DEFAULT_DATA_FILE = "bookmarks.json"
_RECORD_FIELDS = ("id", "title", "url", "created_at")


class CorruptStorageError(Exception):
    """数据文件不是合法文档：内容损坏、格式非法或版本不受支持。"""


@dataclass(frozen=True)
class _StoredDocument:
    """数据文件的解析结果：ID 分配状态与全量书签集合。"""

    next_id: int
    bookmarks: list[Bookmark]


class BookmarkStore:
    """独占读写单个数据文件的存储对象。

    ``path`` 为数据文件路径，缺省 ``bookmarks.json``（即当前工作目录下的
    ``./bookmarks.json``，与 CLI 的 ``--file`` 缺省值一致），必须是 ``str`` 或
    ``os.PathLike``。本对象不缓存文件内容，每次操作都读取磁盘，因此多个
    :class:`BookmarkStore` 实例看到同一份 ID 分配状态。
    """

    def __init__(self, path: str | os.PathLike[str] = _DEFAULT_DATA_FILE) -> None:
        if not isinstance(path, (str, os.PathLike)):
            raise TypeError("数据文件路径必须是 str 或 os.PathLike。")
        resolved = Path(path)
        if not resolved.name:
            raise ValueError("数据文件路径不能为空，也不能是目录。")
        self._path = resolved

    @property
    def path(self) -> Path:
        """数据文件路径（只读）。"""
        return self._path

    def load(self) -> list[Bookmark]:
        """读取全量书签集合。

        文件不存在时返回空列表；文件损坏或格式非法时抛 :class:`CorruptStorageError`，
        并保持原文件不变。返回的是新列表，调用方增删不影响存储。
        """
        if not self._path.exists():
            return []
        return list(self._read_document().bookmarks)

    def next_id(self) -> int:
        """返回下一个将被分配的书签 id。

        文件不存在时返回 ``1``；删除书签后 id 不会被复用，因为该值取“文件中的
        next_id”与“现有最大 id + 1”的较大者。
        """
        document = self._read_document()
        return _next_id_for(document.next_id, _validated_ids(document.bookmarks))

    def save(self, bookmarks: Iterable[Bookmark]) -> None:
        """把全量书签集合原子写入数据文件。

        写入前先解析现有文件：文件损坏时抛 :class:`CorruptStorageError`，原文件保持不变。
        集合中若存在未分配 ``id`` 或 ``id`` 重复的书签，抛
        :class:`bookmarks.model.ValidationError`，同样不触碰磁盘。
        """
        items = _as_bookmark_list(bookmarks)
        bookmark_ids = _validated_ids(items)
        existing = self._read_document()
        document = _StoredDocument(
            next_id=_next_id_for(existing.next_id, bookmark_ids),
            bookmarks=items,
        )
        _write_atomically(self._path, _serialize_document(document))

    def _read_document(self) -> _StoredDocument:
        """解析数据文件；文件不存在时返回空文档（首个可用 id 为 1）。"""
        if not self._path.exists():
            return _StoredDocument(next_id=_FIRST_ID, bookmarks=[])
        try:
            raw_text = self._path.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            raise CorruptStorageError(f"数据文件不是 UTF-8 文本：{error}") from error
        return _parse_document(raw_text)


def _as_bookmark_list(bookmarks: Iterable[Bookmark]) -> list[Bookmark]:
    """把传入选集转换为列表，并拒绝非 ``Bookmark`` 元素。"""
    if isinstance(bookmarks, (str, bytes)) or not isinstance(bookmarks, Iterable):
        raise TypeError("书签集合必须是可迭代的 Bookmark 集合。")
    items = list(bookmarks)
    for bookmark in items:
        if not isinstance(bookmark, Bookmark):
            raise TypeError(f"书签集合只能包含 Bookmark，实际为 {type(bookmark).__name__}。")
    return items


def _record_id(bookmark: Bookmark) -> int:
    """返回已落库书签的 id；未分配 id 的书签不允许写入数据文件。"""
    if bookmark.id is None:
        raise ValidationError("书签必须先分配 id 才能写入数据文件。")
    return bookmark.id


def _validated_ids(bookmarks: Iterable[Bookmark]) -> list[int]:
    """校验集合中的 id 均已分配且互不重复，并按顺序返回。"""
    ids: list[int] = []
    seen_ids: set[int] = set()
    for bookmark in bookmarks:
        bookmark_id = _record_id(bookmark)
        if bookmark_id in seen_ids:
            raise ValidationError(f"书签集合中存在重复的 id：{bookmark_id}。")
        seen_ids.add(bookmark_id)
        ids.append(bookmark_id)
    return ids


def _next_id_for(previous_next_id: int, bookmark_ids: list[int]) -> int:
    """计算新的 ID 分配状态：删除后不复用，也绝不小于 ``现有最大 id + 1``。"""
    if not bookmark_ids:
        return previous_next_id
    return max(previous_next_id, max(bookmark_ids) + 1)


def _require_object(value: Any, description: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CorruptStorageError(f"{description}必须是 JSON 对象。")
    return value


def _require_positive_int(value: Any, description: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < _FIRST_ID:
        raise CorruptStorageError(f"{description}必须是不小于 {_FIRST_ID} 的整数。")
    return value


def _parse_record(raw_record: Any, index: int) -> tuple[int, Bookmark]:
    """解析单条记录，返回其 id 与模型对象。"""
    label = f"第 {index + 1} 条书签记录"
    record = _require_object(raw_record, label)
    missing = [name for name in _RECORD_FIELDS if name not in record]
    if missing:
        raise CorruptStorageError(f"{label}缺少字段：{'、'.join(missing)}。")
    bookmark_id = _require_positive_int(record["id"], f"{label}的 id")
    try:
        bookmark = Bookmark(
            id=bookmark_id,
            title=record["title"],
            url=record["url"],
            created_at=record["created_at"],
        )
    except ValidationError as error:
        raise CorruptStorageError(f"{label}非法：{error}") from error
    return bookmark_id, bookmark


def _parse_bookmarks(raw_bookmarks: Any) -> list[Bookmark]:
    """解析 bookmarks 数组，并拒绝重复 id（否则按 id 定位会失去确定性）。"""
    if not isinstance(raw_bookmarks, list):
        raise CorruptStorageError("数据文件的 bookmarks 字段必须是 JSON 数组。")
    bookmarks: list[Bookmark] = []
    seen_ids: set[int] = set()
    for index, raw_record in enumerate(raw_bookmarks):
        bookmark_id, bookmark = _parse_record(raw_record, index)
        if bookmark_id in seen_ids:
            raise CorruptStorageError(f"数据文件存在重复的书签 id：{bookmark_id}。")
        seen_ids.add(bookmark_id)
        bookmarks.append(bookmark)
    return bookmarks


def _parse_document(raw_text: str) -> _StoredDocument:
    """把数据文件文本解析为 :class:`_StoredDocument`，任何非法内容都抛错。"""
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as error:
        raise CorruptStorageError(f"数据文件不是合法的 JSON：{error}") from error
    document = _require_object(payload, "数据文件根节点")
    version = document.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version != _STORAGE_VERSION:
        raise CorruptStorageError(
            f"不支持的数据文件版本：{version!r}，本项目只支持 {_STORAGE_VERSION}。"
        )
    if "next_id" not in document:
        raise CorruptStorageError("数据文件缺少 next_id 字段。")
    if "bookmarks" not in document:
        raise CorruptStorageError("数据文件缺少 bookmarks 字段。")
    next_id = _require_positive_int(document["next_id"], "数据文件的 next_id")
    return _StoredDocument(next_id=next_id, bookmarks=_parse_bookmarks(document["bookmarks"]))


def _record_of(bookmark: Bookmark) -> dict[str, Any]:
    """把书签序列化为记录字典，字段顺序与文档约定一致。"""
    return {
        "id": _record_id(bookmark),
        "title": bookmark.title,
        "url": bookmark.url,
        "created_at": bookmark.created_at,
    }


def _serialize_document(document: _StoredDocument) -> str:
    """把文档序列化为 UTF-8 文本，中文不转义，末尾保留换行。"""
    payload = {
        "version": _STORAGE_VERSION,
        "next_id": document.next_id,
        "bookmarks": [_record_of(bookmark) for bookmark in document.bookmarks],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _remove_temporary_file(path: Path) -> None:
    """尽力删除临时文件；删除失败只发警告，不掩盖原始异常。"""
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError as error:
        warnings.warn(f"清理临时文件失败：{path}（{error}）", RuntimeWarning, stacklevel=2)


def _write_atomically(path: Path, text: str) -> None:
    """先写同目录临时文件，再以 :func:`os.replace` 原子替换目标文件。

    目标是新路径时自动创建缺失的父目录；替换失败时删除临时文件并让原始异常继续上抛，
    因此已有数据文件不会被半写内容覆盖或截断。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        _remove_temporary_file(temporary_path)
        raise
