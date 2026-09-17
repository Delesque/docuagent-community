"""书签业务服务层测试：覆盖新增、列出、搜索、删除四条业务规则。

测试对象是 :class:`bookmarks.service.BookmarkService`，只通过
:class:`bookmarks.storage.BookmarkStore` 访问数据文件。所有数据文件都位于系统临时
目录，测试结束即随 ``TemporaryDirectory`` 删除，不向仓库写入 ``bookmarks.json``。
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

from bookmarks.model import Bookmark, DuplicateURLError, ValidationError
from bookmarks.service import BookmarkNotFoundError, BookmarkService
from bookmarks.storage import BookmarkStore, CorruptStorageError

_VALID_DOCUMENT = '{"version": 1, "next_id": 1, "bookmarks": []}'


class ServiceTestCase(unittest.TestCase):
    """为每个测试准备独立的临时数据文件与 BookmarkService。"""

    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary_directory.cleanup)
        self.data_file = Path(self._temporary_directory.name) / "bookmarks.json"
        self.store = BookmarkStore(self.data_file)
        self.service = BookmarkService(self.store)

    def write_raw(self, text: str) -> None:
        """直接写入数据文件原始文本，用于构造“存储损坏”场景。"""
        self.data_file.write_text(text, encoding="utf-8")

    def read_raw(self) -> str:
        """读取数据文件原始文本。"""
        return self.data_file.read_text(encoding="utf-8")

    def add(self, title: str, url: str) -> Bookmark:
        """新增一条书签并断言其已落库。"""
        bookmark = self.service.add(title, url)
        self.assertIsNotNone(bookmark.id)
        return bookmark


class BookmarkServiceInitTests(ServiceTestCase):
    """:class:`BookmarkService` 的构造约束。"""

    def test_rejects_non_store_argument(self) -> None:
        with self.assertRaises(TypeError):
            BookmarkService(object())  # type: ignore[arg-type]

    def test_accepts_bookmark_store(self) -> None:
        service = BookmarkService(BookmarkStore(self.data_file))
        self.assertEqual(service.list_all(), [])


class ListAllTests(ServiceTestCase):
    """list_all 返回全量书签的插入顺序。"""

    def test_empty_when_data_file_missing(self) -> None:
        self.assertFalse(self.data_file.exists())
        self.assertEqual(self.service.list_all(), [])

    def test_returns_bookmarks_in_insertion_order(self) -> None:
        first = self.add("Python", "https://python.org")
        second = self.add("PEP 8", "https://peps.python.org/pep-0008/")
        listed = self.service.list_all()
        self.assertEqual([item.id for item in listed], [first.id, second.id])
        self.assertEqual([item.title for item in listed], ["Python", "PEP 8"])

    def test_returns_new_list_instances(self) -> None:
        self.add("Python", "https://python.org")
        first_read = self.service.list_all()
        second_read = self.service.list_all()
        self.assertIsNot(first_read, second_read)
        self.assertEqual(first_read, second_read)

    def test_propagates_corrupt_storage_error(self) -> None:
        self.write_raw("{ not json")
        with self.assertRaises(CorruptStorageError):
            self.service.list_all()


class AddTests(ServiceTestCase):
    """add 的校验、去重、ID 分配与持久化规则。"""

    def test_first_bookmark_gets_id_one(self) -> None:
        bookmark = self.add("Python 官网", "https://python.org")
        self.assertEqual(bookmark.id, 1)
        self.assertEqual(bookmark.title, "Python 官网")
        self.assertEqual(bookmark.url, "https://python.org")
        self.assertTrue(bookmark.created_at)

    def test_persists_added_bookmark(self) -> None:
        bookmark = self.add("Python", "https://python.org")
        self.assertEqual(self.service.list_all(), [bookmark])
        document = json.loads(self.read_raw())
        self.assertEqual(document["bookmarks"][0]["id"], bookmark.id)

    def test_assigns_increasing_unique_ids(self) -> None:
        ids = [self.add(f"书签 {index}", f"https://example.com/{index}").id for index in range(3)]
        self.assertEqual(ids, [1, 2, 3])

    def test_strips_surrounding_whitespace(self) -> None:
        bookmark = self.add("  Python  ", "  https://python.org  ")
        self.assertEqual(bookmark.title, "Python")
        self.assertEqual(bookmark.url, "https://python.org")

    def test_rejects_blank_title(self) -> None:
        with self.assertRaises(ValidationError):
            self.service.add("   ", "https://python.org")
        self.assertFalse(self.data_file.exists())

    def test_rejects_blank_url(self) -> None:
        with self.assertRaises(ValidationError):
            self.service.add("Python", "   ")
        self.assertFalse(self.data_file.exists())

    def test_rejects_non_text_title(self) -> None:
        with self.assertRaises(ValidationError):
            self.service.add(None, "https://python.org")  # type: ignore[arg-type]

    def test_rejects_duplicate_url(self) -> None:
        self.add("Python", "https://python.org")
        with self.assertRaises(DuplicateURLError):
            self.service.add("另一个标题", "https://python.org")
        self.assertEqual(len(self.service.list_all()), 1)

    def test_duplicate_detection_ignores_host_case(self) -> None:
        self.add("Python", "https://python.org")
        with self.assertRaises(DuplicateURLError):
            self.service.add("Python 大写主机", "https://PYTHON.ORG")

    def test_duplicate_detection_ignores_scheme_case(self) -> None:
        self.add("Python", "https://python.org")
        with self.assertRaises(DuplicateURLError):
            self.service.add("Python 大写协议", "HTTPS://python.org")

    def test_duplicate_detection_ignores_root_path_slash(self) -> None:
        self.add("Python", "https://python.org")
        with self.assertRaises(DuplicateURLError):
            self.service.add("Python 带斜杠", "https://python.org/")

    def test_duplicate_detection_ignores_surrounding_whitespace(self) -> None:
        self.add("Python", "https://python.org")
        with self.assertRaises(DuplicateURLError):
            self.service.add("Python 带空格", "  https://python.org  ")

    def test_distinct_paths_are_not_duplicates(self) -> None:
        self.add("Python", "https://python.org")
        self.add("Python 文档", "https://python.org/doc")
        self.assertEqual(len(self.service.list_all()), 2)

    def test_duplicate_error_is_validation_error(self) -> None:
        self.add("Python", "https://python.org")
        self.assertTrue(issubclass(DuplicateURLError, ValidationError))

    def test_does_not_reuse_ids_after_delete(self) -> None:
        first = self.add("Python", "https://python.org")
        self.service.delete(first.id)
        second = self.add("Rust", "https://www.rust-lang.org")
        self.assertEqual(second.id, first.id + 1)

    def test_ids_stay_unique_across_service_instances(self) -> None:
        first = self.add("Python", "https://python.org")
        other_service = BookmarkService(BookmarkStore(self.data_file))
        second = other_service.add("Rust", "https://www.rust-lang.org")
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(second.id, first.id + 1)

    def test_propagates_corrupt_storage_error(self) -> None:
        self.write_raw("这不是 JSON")
        with self.assertRaises(CorruptStorageError):
            self.service.add("Python", "https://python.org")
        self.assertEqual(self.read_raw(), "这不是 JSON")

    def test_failed_add_keeps_existing_data(self) -> None:
        self.add("Python", "https://python.org")
        before = self.read_raw()
        with self.assertRaises(DuplicateURLError):
            self.service.add("重复", "https://python.org")
        self.assertEqual(self.read_raw(), before)


class SearchTests(ServiceTestCase):
    """search 的大小写不敏感子串匹配语义。"""

    def setUp(self) -> None:
        super().setUp()
        self.add("Python 官网", "https://python.org")
        self.add("Rust 官网", "https://www.rust-lang.org/docs?topic=ABC")

    def test_matches_title_substring(self) -> None:
        titles = [bookmark.title for bookmark in self.service.search("Python")]
        self.assertEqual(titles, ["Python 官网"])

    def test_matches_url_substring(self) -> None:
        titles = [bookmark.title for bookmark in self.service.search("rust-lang")]
        self.assertEqual(titles, ["Rust 官网"])

    def test_match_is_case_insensitive(self) -> None:
        self.assertEqual(len(self.service.search("python")), 1)
        self.assertEqual(len(self.service.search("PYTHON")), 1)
        self.assertEqual(len(self.service.search("docs?topic=abc")), 1)

    def test_returns_empty_list_when_no_match(self) -> None:
        self.assertEqual(self.service.search("Go 语言"), [])

    def test_strips_keyword_whitespace(self) -> None:
        self.assertEqual(len(self.service.search("  Python  ")), 1)

    def test_rejects_blank_keyword(self) -> None:
        with self.assertRaises(ValidationError):
            self.service.search("   ")

    def test_rejects_non_text_keyword(self) -> None:
        with self.assertRaises(ValidationError):
            self.service.search(123)  # type: ignore[arg-type]

    def test_search_on_empty_store_returns_empty_list(self) -> None:
        empty_service = BookmarkService(BookmarkStore(self.data_file.parent / "other.json"))
        self.assertEqual(empty_service.search("Python"), [])

    def test_propagates_corrupt_storage_error(self) -> None:
        self.write_raw("")
        with self.assertRaises(CorruptStorageError):
            self.service.search("Python")


class DeleteTests(ServiceTestCase):
    """delete 的定位、返回与被删除数据的持久化。"""

    def test_returns_removed_bookmark(self) -> None:
        bookmark = self.add("Python", "https://python.org")
        removed = self.service.delete(bookmark.id)
        self.assertEqual(removed, bookmark)
        self.assertEqual(self.service.list_all(), [])

    def test_removes_only_target_bookmark(self) -> None:
        first = self.add("Python", "https://python.org")
        second = self.add("Rust", "https://www.rust-lang.org")
        self.service.delete(first.id)
        self.assertEqual(self.service.list_all(), [second])

    def test_persists_removal_to_disk(self) -> None:
        bookmark = self.add("Python", "https://python.org")
        self.service.delete(bookmark.id)
        document = json.loads(self.read_raw())
        self.assertEqual(document["bookmarks"], [])

    def test_raises_not_found_for_unknown_id(self) -> None:
        self.add("Python", "https://python.org")
        with self.assertRaises(BookmarkNotFoundError):
            self.service.delete(99)

    def test_not_found_error_is_lookup_error(self) -> None:
        self.assertTrue(issubclass(BookmarkNotFoundError, LookupError))

    def test_raises_not_found_on_empty_store(self) -> None:
        with self.assertRaises(BookmarkNotFoundError):
            self.service.delete(1)

    def test_rejects_non_integer_id(self) -> None:
        with self.assertRaises(ValidationError):
            self.service.delete("1")  # type: ignore[arg-type]

    def test_rejects_boolean_id(self) -> None:
        with self.assertRaises(ValidationError):
            self.service.delete(True)

    def test_propagates_corrupt_storage_error(self) -> None:
        self.write_raw("[]")
        with self.assertRaises(CorruptStorageError):
            self.service.delete(1)

    def test_failed_delete_keeps_existing_data(self) -> None:
        self.add("Python", "https://python.org")
        before = self.read_raw()
        with self.assertRaises(BookmarkNotFoundError):
            self.service.delete(7)
        self.assertEqual(self.read_raw(), before)


class EndToEndFlowTests(ServiceTestCase):
    """四类业务规则串起来的最小闭环。"""

    def test_add_search_delete_flow(self) -> None:
        python = self.add("Python 官网", "https://python.org")
        rust = self.add("Rust 官网", "https://www.rust-lang.org")
        self.assertEqual([item.id for item in self.service.search("官网")], [python.id, rust.id])
        self.service.delete(python.id)
        self.assertEqual([item.id for item in self.service.search("官网")], [rust.id])
        self.assertEqual([item.id for item in self.service.list_all()], [rust.id])

    def test_deleted_url_can_be_added_again_with_new_id(self) -> None:
        first = self.add("Python", "https://python.org")
        self.service.delete(first.id)
        second = self.add("Python 重新添加", "https://python.org")
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(len(self.service.list_all()), 1)
        self.assertTrue(os.path.exists(self.data_file))


if __name__ == "__main__":
    unittest.main()
