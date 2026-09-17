"""bookmarks.storage 的单元测试：文档解析/序列化、ID 分配与原子落盘。

测试全部使用系统临时目录中的临时数据文件，结束即由 ``TemporaryDirectory`` 清理，
不会向仓库写入 ``bookmarks.json``。
"""

import json
import tempfile
import unittest
from pathlib import Path

from bookmarks.model import Bookmark, ValidationError
from bookmarks.storage import BookmarkStore, CorruptStorageError

_DATA_FILE_NAME = "data.json"
_CREATED_AT = "2026-01-02T03:04:05+00:00"


def _bookmark(bookmark_id: int = 1, title: str = "示例", url: str = "https://example.com") -> Bookmark:
    """构造一条已落库的书签记录。"""
    return Bookmark(id=bookmark_id, title=title, url=url, created_at=_CREATED_AT)


def _document(bookmarks, next_id: int = 1, version: int = 1) -> str:
    """按数据文件约定拼装文档文本。"""
    return json.dumps(
        {"version": version, "next_id": next_id, "bookmarks": list(bookmarks)},
        ensure_ascii=False,
    )


def _record(bookmark_id: int = 1, title: str = "示例", url: str = "https://example.com"):
    """构造一条记录字典。"""
    return {"id": bookmark_id, "title": title, "url": url, "created_at": _CREATED_AT}


class StorageTestCase(unittest.TestCase):
    """公共夹具：隔离的临时目录与一个指向临时数据文件的存储对象。"""

    def setUp(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory(prefix="bookmarks-storage-")
        self.addCleanup(temporary_directory.cleanup)
        self.root = Path(temporary_directory.name)
        self.data_file = self.root / _DATA_FILE_NAME
        self.store = BookmarkStore(self.data_file)

    def write_raw(self, content: str | bytes) -> None:
        """直接写入数据文件原始内容（模拟外部损坏或手工编辑）。"""
        if isinstance(content, bytes):
            self.data_file.write_bytes(content)
        else:
            self.data_file.write_text(content, encoding="utf-8")

    def read_raw(self) -> str:
        return self.data_file.read_text(encoding="utf-8")

    def read_payload(self) -> dict:
        return json.loads(self.read_raw())

    def entries(self) -> set[str]:
        return {entry.name for entry in self.root.iterdir()}

    def assert_no_temporary_leftover(self) -> None:
        leftovers = [name for name in self.entries() if name.endswith(".tmp")]
        self.assertEqual(leftovers, [], "原子写入不应留下临时文件")

    def assert_corrupt(self, content: str | bytes, keyword: str) -> CorruptStorageError:
        """写入损坏内容，断言 load 抛 CorruptStorageError 且原文件逐字节不变。"""
        self.write_raw(content)
        before = self.data_file.read_bytes()
        with self.assertRaises(CorruptStorageError) as context:
            self.store.load()
        message = str(context.exception)
        self.assertIn(keyword, message)
        self.assertEqual(self.data_file.read_bytes(), before, "损坏文件不得被修改")
        self.assert_no_temporary_leftover()
        return context.exception


class EmptyFileTest(StorageTestCase):
    """数据文件不存在时的行为：等价于空集合，且不产生副作用。"""

    def test_load_returns_empty_list(self) -> None:
        self.assertEqual(self.store.load(), [])

    def test_next_id_starts_at_one(self) -> None:
        self.assertEqual(self.store.next_id(), 1)

    def test_read_only_operations_do_not_create_the_file(self) -> None:
        self.store.load()
        self.store.next_id()
        self.assertFalse(self.data_file.exists())

    def test_load_missing_file_inside_missing_directory(self) -> None:
        store = BookmarkStore(self.root / "nested" / "deeper" / _DATA_FILE_NAME)
        self.assertEqual(store.load(), [])


class PathTest(StorageTestCase):
    """路径处理：接受 str / Path，属性只读，非法路径立即失败。"""

    def test_accepts_string_path(self) -> None:
        store = BookmarkStore(str(self.data_file))
        store.save([_bookmark()])
        self.assertEqual(len(store.load()), 1)

    def test_exposes_path_property(self) -> None:
        self.assertEqual(self.store.path, self.data_file)

    def test_default_path_matches_documented_data_file(self) -> None:
        self.assertEqual(BookmarkStore("bookmarks.json").path, Path("bookmarks.json"))

    def test_rejects_non_path_like_value(self) -> None:
        with self.assertRaises(TypeError):
            BookmarkStore(123)  # type: ignore[arg-type]

    def test_rejects_empty_path(self) -> None:
        with self.assertRaises(ValueError):
            BookmarkStore("")


class RoundTripTest(StorageTestCase):
    """保存与读取的往返：字段、顺序与文档形状。"""

    def test_saves_and_loads_bookmark_fields(self) -> None:
        original = _bookmark(1, "Python 文档", "https://docs.python.org/3/")
        self.store.save([original])
        self.assertEqual(self.store.load(), [original])

    def test_writes_documented_document_shape(self) -> None:
        self.store.save([_bookmark(1), _bookmark(2, "乙", "https://example.com/b")])
        payload = self.read_payload()
        self.assertEqual(list(payload), ["version", "next_id", "bookmarks"])
        self.assertEqual(payload["version"], 1)
        self.assertEqual(payload["next_id"], 3)
        self.assertEqual(list(payload["bookmarks"][0]), ["id", "title", "url", "created_at"])

    def test_preserves_collection_order(self) -> None:
        second = _bookmark(2, "乙", "https://example.com/b")
        first = _bookmark(1, "甲", "https://example.com/a")
        self.store.save([second, first])
        self.assertEqual([item.id for item in self.store.load()], [2, 1])

    def test_accepts_arbitrary_iterable(self) -> None:
        self.store.save(iter([_bookmark(1), _bookmark(2, "乙", "https://example.com/b")]))
        self.assertEqual(len(self.store.load()), 2)

    def test_saving_empty_collection_writes_valid_document(self) -> None:
        self.store.save([])
        self.assertEqual(self.read_payload(), {"version": 1, "next_id": 1, "bookmarks": []})
        self.assertEqual(self.store.load(), [])

    def test_overwrites_previous_content_completely(self) -> None:
        self.store.save([_bookmark(1, "甲", "https://example.com/a")])
        self.store.save([_bookmark(2, "乙", "https://example.com/b")])
        loaded = self.store.load()
        self.assertEqual([item.title for item in loaded], ["乙"])

    def test_load_returns_independent_list(self) -> None:
        self.store.save([_bookmark(1)])
        loaded = self.store.load()
        loaded.clear()
        self.assertEqual(len(self.store.load()), 1)

    def test_file_ends_with_newline_and_keeps_chinese_readable(self) -> None:
        self.store.save([_bookmark(1, "中文标题", "https://example.com/a")])
        raw = self.read_raw()
        self.assertTrue(raw.endswith("\n"))
        self.assertIn("中文标题", raw)

    def test_creates_missing_parent_directories(self) -> None:
        nested = self.root / "nested" / "deeper" / _DATA_FILE_NAME
        BookmarkStore(nested).save([_bookmark(1)])
        self.assertTrue(nested.is_file())
        self.assertEqual(self.entries(), {"nested"})

    def test_ignores_unknown_fields_when_loading(self) -> None:
        self.write_raw(
            json.dumps(
                {
                    "version": 1,
                    "next_id": 2,
                    "extra": "多余字段",
                    "bookmarks": [dict(_record(1), extra="多余字段")],
                },
                ensure_ascii=False,
            )
        )
        self.assertEqual(self.store.load(), [_bookmark(1)])

    def test_rewrite_drops_unknown_fields(self) -> None:
        self.write_raw(
            json.dumps(
                {
                    "version": 1,
                    "next_id": 2,
                    "extra": "多余字段",
                    "bookmarks": [dict(_record(1), extra="多余字段")],
                },
                ensure_ascii=False,
            )
        )
        self.store.save(self.store.load())
        self.assertNotIn("多余字段", self.read_raw())


class NextIdTest(StorageTestCase):
    """ID 分配状态：单调递增、删除后不复用、与集合同文件持久化。"""

    def test_next_id_advances_after_save(self) -> None:
        self.store.save([_bookmark(1)])
        self.assertEqual(self.store.next_id(), 2)

    def test_deleted_id_is_not_reused(self) -> None:
        self.store.save([_bookmark(1), _bookmark(2, "乙", "https://example.com/b")])
        self.store.save([_bookmark(1)])
        self.assertEqual(self.store.next_id(), 3)

    def test_next_id_survives_emptying_the_collection(self) -> None:
        self.store.save([_bookmark(1), _bookmark(2, "乙", "https://example.com/b")])
        self.store.save([])
        self.assertEqual(self.store.next_id(), 3)

    def test_updating_existing_bookmark_keeps_next_id(self) -> None:
        self.store.save([_bookmark(1)])
        self.store.save([_bookmark(1, "改过的标题")])
        self.assertEqual(self.store.next_id(), 2)

    def test_next_id_follows_larger_external_id(self) -> None:
        self.store.save([_bookmark(9)])
        self.assertEqual(self.read_payload()["next_id"], 10)

    def test_persisted_next_id_wins_when_larger_than_records(self) -> None:
        self.write_raw(_document([_record(1)], next_id=100))
        self.assertEqual(self.store.next_id(), 100)

    def test_next_id_covers_records_when_persisted_value_lags(self) -> None:
        self.write_raw(_document([_record(5), _record(7)], next_id=1))
        self.assertEqual(self.store.next_id(), 8)
        self.store.save(self.store.load())
        self.assertEqual(self.read_payload()["next_id"], 8)


class CorruptionTest(StorageTestCase):
    """损坏或格式非法的文件必须抛 CorruptStorageError，且绝不被覆盖。"""

    def test_reports_corrupt_storage_error_type(self) -> None:
        self.assertTrue(issubclass(CorruptStorageError, Exception))
        self.assertFalse(issubclass(CorruptStorageError, OSError), "损坏提示不应与 I/O 故障混淆")

    def test_rejects_invalid_json(self) -> None:
        self.assert_corrupt("{不是 JSON", "JSON")

    def test_rejects_non_object_root(self) -> None:
        self.assert_corrupt("[]", "数据文件根节点")
        self.assert_corrupt("123", "数据文件根节点")
        self.assert_corrupt('"文本"', "数据文件根节点")

    def test_rejects_missing_or_unsupported_version(self) -> None:
        self.assert_corrupt(_document([], next_id=1).replace('"version": 1, ', ""), "版本")
        self.assert_corrupt(_document([], next_id=1, version=2), "版本")
        self.assert_corrupt(_document([], next_id=1).replace('"version": 1', '"version": "1"'), "版本")
        self.assert_corrupt(_document([], next_id=1).replace('"version": 1', '"version": true'), "版本")

    def test_rejects_missing_next_id(self) -> None:
        self.assert_corrupt('{"version": 1, "bookmarks": []}', "next_id")

    def test_rejects_invalid_next_id(self) -> None:
        for value in ("0", "-3", '"1"', "true", "1.5", "null"):
            with self.subTest(next_id=value):
                self.assert_corrupt(
                    f'{{"version": 1, "next_id": {value}, "bookmarks": []}}',
                    "next_id",
                )

    def test_rejects_missing_bookmarks_field(self) -> None:
        self.assert_corrupt('{"version": 1, "next_id": 1}', "bookmarks")

    def test_rejects_non_list_bookmarks(self) -> None:
        self.assert_corrupt('{"version": 1, "next_id": 1, "bookmarks": {}}', "bookmarks")
        self.assert_corrupt('{"version": 1, "next_id": 1, "bookmarks": "文本"}', "bookmarks")

    def test_rejects_non_object_record(self) -> None:
        self.assert_corrupt(_document(["不是对象"], next_id=2), "第 1 条书签记录")

    def test_rejects_record_with_missing_field(self) -> None:
        incomplete = {"id": 1, "title": "标题", "url": "https://example.com"}
        self.assert_corrupt(_document([incomplete], next_id=2), "created_at")

    def test_rejects_record_with_invalid_id(self) -> None:
        for value in (0, "1", True, None):
            with self.subTest(identifier=value):
                self.assert_corrupt(
                    _document([dict(_record(1), id=value)], next_id=2),
                    "id",
                )

    def test_rejects_record_with_invalid_fields(self) -> None:
        cases = (
            (dict(_record(1), title="   "), "标题"),
            (dict(_record(1), url=""), "URL"),
            (dict(_record(1), url="https://[::1"), "URL"),
            (dict(_record(1), created_at="  "), "创建时间"),
        )
        for record, keyword in cases:
            with self.subTest(keyword=keyword):
                self.assert_corrupt(_document([record], next_id=2), keyword)

    def test_rejects_duplicate_record_ids(self) -> None:
        self.assert_corrupt(
            _document([_record(1), _record(1, "乙", "https://example.com/b")], next_id=3),
            "重复",
        )

    def test_rejects_non_utf8_content(self) -> None:
        self.assert_corrupt(b"\xff\xfe\x00\x01", "UTF-8")

    def test_save_refuses_to_overwrite_corrupt_file(self) -> None:
        self.write_raw("{损坏")
        before = self.data_file.read_bytes()
        with self.assertRaises(CorruptStorageError):
            self.store.save([_bookmark(1)])
        self.assertEqual(self.data_file.read_bytes(), before)
        self.assert_no_temporary_leftover()

    def test_next_id_also_rejects_corrupt_file(self) -> None:
        self.write_raw("{损坏")
        with self.assertRaises(CorruptStorageError):
            self.store.next_id()


class SaveValidationTest(StorageTestCase):
    """写入前的集合校验：未分配 id、重复 id、非 Bookmark 元素都不得落盘。"""

    def test_rejects_bookmark_without_id(self) -> None:
        with self.assertRaises(ValidationError):
            self.store.save([Bookmark(title="标题", url="https://example.com")])
        self.assertFalse(self.data_file.exists())

    def test_rejects_duplicate_ids(self) -> None:
        with self.assertRaises(ValidationError):
            self.store.save([_bookmark(1, "甲", "https://example.com/a"), _bookmark(1, "乙", "https://example.com/b")])
        self.assertFalse(self.data_file.exists())

    def test_rejects_non_bookmark_element(self) -> None:
        with self.assertRaises(TypeError):
            self.store.save([_bookmark(1), "不是书签"])  # type: ignore[list-item]
        self.assertFalse(self.data_file.exists())

    def test_rejects_non_iterable_argument(self) -> None:
        with self.assertRaises(TypeError):
            self.store.save(None)  # type: ignore[arg-type]

    def test_rejected_save_keeps_existing_content(self) -> None:
        self.store.save([_bookmark(1)])
        before = self.data_file.read_bytes()
        with self.assertRaises(ValidationError):
            self.store.save([_bookmark(2), _bookmark(2, "乙", "https://example.com/b")])
        self.assertEqual(self.data_file.read_bytes(), before)
        self.assert_no_temporary_leftover()


class AtomicWriteTest(StorageTestCase):
    """原子落盘：不留临时文件，目标不可写时按 OSError 上抛且不破坏数据。"""

    def test_no_temporary_leftover_after_successful_save(self) -> None:
        self.store.save([_bookmark(1)])
        self.store.save([_bookmark(1), _bookmark(2, "乙", "https://example.com/b")])
        self.assertEqual(self.entries(), {_DATA_FILE_NAME})

    def test_directory_target_raises_os_error_without_leftover(self) -> None:
        store = BookmarkStore(self.root / "作为目录的目标")
        (self.root / "作为目录的目标").mkdir()
        with self.assertRaises(OSError):
            store.save([_bookmark(1)])
        self.assert_no_temporary_leftover()
        with self.assertRaises(OSError):
            store.load()


if __name__ == "__main__":
    unittest.main()
