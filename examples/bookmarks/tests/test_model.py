"""bookmarks.model 的单元测试：字段校验、URL 规范化与领域异常。"""

import unittest
from datetime import datetime

from bookmarks.model import (
    Bookmark,
    DuplicateURLError,
    ValidationError,
    normalize_url,
)


class NormalizeURLTest(unittest.TestCase):
    """normalize_url 的规范化规则与非法输入处理。"""

    def test_strips_surrounding_whitespace(self) -> None:
        self.assertEqual(
            normalize_url("  https://example.com/a  "),
            "https://example.com/a",
        )

    def test_lowercases_scheme_and_host_only(self) -> None:
        self.assertEqual(
            normalize_url("HTTPS://Example.COM/Path/To/Page"),
            "https://example.com/Path/To/Page",
        )

    def test_drops_root_path_slash(self) -> None:
        self.assertEqual(normalize_url("https://example.com/"), "https://example.com")

    def test_keeps_query_and_fragment(self) -> None:
        self.assertEqual(
            normalize_url("https://example.com/s?q=Abc#Frag"),
            "https://example.com/s?q=Abc#Frag",
        )

    def test_keeps_userinfo_and_port(self) -> None:
        self.assertEqual(
            normalize_url("https://User@Example.com:8443/a"),
            "https://User@example.com:8443/a",
        )

    def test_keeps_text_without_scheme(self) -> None:
        self.assertEqual(normalize_url("example.com/Path"), "example.com/Path")

    def test_equivalent_urls_normalize_to_same_value(self) -> None:
        self.assertEqual(
            normalize_url("HTTPS://Example.com/"),
            normalize_url("https://example.com"),
        )

    def test_distinct_paths_stay_distinct(self) -> None:
        self.assertNotEqual(
            normalize_url("https://example.com/a"),
            normalize_url("https://example.com/b"),
        )

    def test_rejects_blank_input(self) -> None:
        for value in ("", "   ", "\t"):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    normalize_url(value)

    def test_rejects_non_text_input(self) -> None:
        with self.assertRaises(ValidationError):
            normalize_url(123)  # type: ignore[arg-type]

    def test_rejects_internal_whitespace(self) -> None:
        with self.assertRaises(ValidationError):
            normalize_url("https://example.com/a b")

    def test_rejects_unparsable_url(self) -> None:
        with self.assertRaises(ValidationError):
            normalize_url("https://[::1")


class BookmarkTest(unittest.TestCase):
    """Bookmark 的构造、默认值与校验。"""

    def test_defaults_leave_id_unset_and_stamp_created_at(self) -> None:
        bookmark = Bookmark(title="标题", url="https://example.com")
        self.assertIsNone(bookmark.id)
        parsed = datetime.fromisoformat(bookmark.created_at)
        self.assertIsNotNone(parsed.tzinfo)

    def test_normalizes_url_and_strips_title(self) -> None:
        bookmark = Bookmark(title="  标题  ", url="  HTTPS://Example.com/x  ")
        self.assertEqual(bookmark.title, "标题")
        self.assertEqual(bookmark.url, "https://example.com/x")

    def test_supports_record_order_positional_arguments(self) -> None:
        bookmark = Bookmark(7, "标题", "https://example.com", "2026-01-02T03:04:05+00:00")
        self.assertEqual(bookmark.id, 7)
        self.assertEqual(bookmark.title, "标题")
        self.assertEqual(bookmark.url, "https://example.com")
        self.assertEqual(bookmark.created_at, "2026-01-02T03:04:05+00:00")

    def test_supports_record_mapping_keywords(self) -> None:
        record = {
            "id": 3,
            "title": "文档",
            "url": "https://example.com/doc",
            "created_at": "2026-01-02T03:04:05+00:00",
        }
        bookmark = Bookmark(**record)
        self.assertEqual(bookmark.id, 3)
        self.assertEqual(bookmark.title, "文档")
        self.assertEqual(bookmark.url, "https://example.com/doc")
        self.assertEqual(bookmark.created_at, "2026-01-02T03:04:05+00:00")

    def test_repeated_url_stores_the_same_normalized_value(self) -> None:
        first = Bookmark(id=1, title="甲", url="HTTPS://Example.com/")
        second = Bookmark(id=2, title="乙", url="https://example.com")
        self.assertEqual(first.url, second.url)

    def test_rejects_blank_title(self) -> None:
        for value in ("", "   "):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    Bookmark(title=value, url="https://example.com")

    def test_rejects_blank_url(self) -> None:
        for value in ("", "   "):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    Bookmark(title="标题", url=value)

    def test_rejects_non_text_fields(self) -> None:
        with self.assertRaises(ValidationError):
            Bookmark(title=None, url="https://example.com")  # type: ignore[arg-type]
        with self.assertRaises(ValidationError):
            Bookmark(title="标题", url=None)  # type: ignore[arg-type]

    def test_rejects_blank_created_at(self) -> None:
        with self.assertRaises(ValidationError):
            Bookmark(title="标题", url="https://example.com", created_at="  ")

    def test_rejects_invalid_id(self) -> None:
        for value in (0, -1, "1", 1.5, True):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    Bookmark(id=value, title="标题", url="https://example.com")  # type: ignore[arg-type]

    def test_equality_uses_field_values(self) -> None:
        first = Bookmark(id=1, title="标题", url="https://example.com", created_at="2026-01-02T03:04:05+00:00")
        second = Bookmark(id=1, title="标题", url="https://example.com", created_at="2026-01-02T03:04:05+00:00")
        third = Bookmark(id=2, title="标题", url="https://example.com", created_at="2026-01-02T03:04:05+00:00")
        self.assertEqual(first, second)
        self.assertNotEqual(first, third)


class DomainErrorTest(unittest.TestCase):
    """领域异常的层级与消息语义。"""

    def test_validation_error_is_a_value_error(self) -> None:
        self.assertTrue(issubclass(ValidationError, ValueError))

    def test_duplicate_url_error_is_a_validation_error(self) -> None:
        self.assertTrue(issubclass(DuplicateURLError, ValidationError))

    def test_exception_message_is_preserved(self) -> None:
        with self.assertRaises(ValidationError) as context:
            normalize_url("   ")
        self.assertEqual(str(context.exception), "URL不能为空。")

        with self.assertRaises(DuplicateURLError) as duplicate_context:
            raise DuplicateURLError("URL 已存在：https://example.com")
        self.assertEqual(str(duplicate_context.exception), "URL 已存在：https://example.com")


if __name__ == "__main__":
    unittest.main()
