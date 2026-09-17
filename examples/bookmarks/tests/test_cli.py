"""CLI 层测试：``python -m bookmarks`` 的子命令行为、中文提示与退出码语义。

测试以 ``main(argv)`` 的返回值为进程退出码的代理，并用临时目录中的数据文件实现隔离，
因此不会读写仓库根目录的 ``./bookmarks.json``。覆盖范围：

- ``add`` / ``list`` / ``search`` / ``delete`` 的成功路径与输出；
- Title/URL 校验失败、URL 重复、id 不存在、数据文件损坏的退出码与中文提示；
- 用法错误（缺少子命令、缺少必需参数、id 非整数）返回退出码 2；
- ``--file`` 写在子命令之前或之后都生效，缺省值为 ``./bookmarks.json``。
"""

from __future__ import annotations

import importlib.util
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from bookmarks.cli import EXIT_FAILURE, EXIT_SUCCESS, EXIT_USAGE, main

_ADD_TITLE_OPTION = "--title"
_ADD_URL_OPTION = "--url"
_FILE_OPTION = "--file"


class CliTestBase(unittest.TestCase):
    """公共夹具：临时数据文件路径与命令行执行辅助。"""

    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary_directory.cleanup)
        self.data_file = str(
            Path(self._temporary_directory.name) / "bookmarks.json"
        )

    def run_cli(self, *arguments: str) -> tuple[int, str, str]:
        """执行 ``main`` 并捕获标准输出与标准错误，返回 (退出码, stdout, stderr)。"""
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = main(list(arguments))
        return exit_code, stdout.getvalue(), stderr.getvalue()

    def add_bookmark(self, title: str, url: str) -> tuple[int, str, str]:
        """经 CLI 新增一条书签，供其他用例准备数据。"""
        return self.run_cli(
            _FILE_OPTION,
            self.data_file,
            "add",
            _ADD_TITLE_OPTION,
            title,
            _ADD_URL_OPTION,
            url,
        )


class EntryPointTest(CliTestBase):
    """入口可调用性与固定入口模块的存在性。"""

    def test_main_is_callable(self) -> None:
        self.assertTrue(callable(main))

    def test_module_entry_point_module_exists(self) -> None:
        self.assertIsNotNone(importlib.util.find_spec("bookmarks.__main__"))


class AddCommandTest(CliTestBase):
    """``add`` 子命令。"""

    def test_add_persists_bookmark_and_prints_record(self) -> None:
        exit_code, stdout, _stderr = self.add_bookmark("示例", "https://example.com")

        self.assertEqual(exit_code, EXIT_SUCCESS)
        self.assertIn("已添加书签 #1", stdout)
        self.assertIn("示例", stdout)
        self.assertTrue(Path(self.data_file).exists())

    def test_add_uses_title_before_subcommand_file_option(self) -> None:
        exit_code, stdout, _stderr = self.run_cli(
            "add",
            _ADD_TITLE_OPTION,
            "后置文件选项",
            _ADD_URL_OPTION,
            "https://example.com/late",
            _FILE_OPTION,
            self.data_file,
        )

        self.assertEqual(exit_code, EXIT_SUCCESS)
        self.assertIn("已添加书签 #1", stdout)
        self.assertTrue(Path(self.data_file).exists())

    def test_add_rejects_duplicate_url(self) -> None:
        self.add_bookmark("第一次", "https://example.com")

        exit_code, stdout, stderr = self.add_bookmark("第二次", "https://example.com")

        self.assertEqual(exit_code, EXIT_FAILURE)
        self.assertEqual(stdout, "")
        self.assertIn("URL 已存在", stderr)

    def test_add_rejects_blank_title(self) -> None:
        exit_code, _stdout, stderr = self.run_cli(
            _FILE_OPTION,
            self.data_file,
            "add",
            _ADD_TITLE_OPTION,
            "   ",
            _ADD_URL_OPTION,
            "https://example.com",
        )

        self.assertEqual(exit_code, EXIT_FAILURE)
        self.assertIn("标题不能为空", stderr)

    def test_add_requires_title_and_url_options(self) -> None:
        exit_code, _stdout, stderr = self.run_cli(_FILE_OPTION, self.data_file, "add")

        self.assertEqual(exit_code, EXIT_USAGE)
        self.assertIn("--title", stderr)


class ListCommandTest(CliTestBase):
    """``list`` 子命令。"""

    def test_list_prints_empty_hint_for_missing_file(self) -> None:
        exit_code, stdout, _stderr = self.run_cli(_FILE_OPTION, self.data_file, "list")

        self.assertEqual(exit_code, EXIT_SUCCESS)
        self.assertEqual(stdout.strip(), "暂无书签。")

    def test_list_prints_all_bookmarks(self) -> None:
        self.add_bookmark("Python 官方", "https://python.org")
        self.add_bookmark("本地文档", "https://example.com/docs")

        exit_code, stdout, _stderr = self.run_cli(_FILE_OPTION, self.data_file, "list")

        self.assertEqual(exit_code, EXIT_SUCCESS)
        self.assertIn("#1", stdout)
        self.assertIn("Python 官方", stdout)
        self.assertIn("#2", stdout)
        self.assertIn("本地文档", stdout)


class SearchCommandTest(CliTestBase):
    """``search`` 子命令。"""

    def test_search_matches_title_case_insensitively(self) -> None:
        self.add_bookmark("Python 官方", "https://python.org")

        exit_code, stdout, _stderr = self.run_cli(
            _FILE_OPTION, self.data_file, "search", "python"
        )

        self.assertEqual(exit_code, EXIT_SUCCESS)
        self.assertIn("Python 官方", stdout)

    def test_search_matches_url_substring(self) -> None:
        self.add_bookmark("示例站点", "https://example.com/section")

        exit_code, stdout, _stderr = self.run_cli(
            _FILE_OPTION, self.data_file, "search", "SECTION"
        )

        self.assertEqual(exit_code, EXIT_SUCCESS)
        self.assertIn("示例站点", stdout)

    def test_search_without_match_prints_empty_hint(self) -> None:
        self.add_bookmark("Python 官方", "https://python.org")

        exit_code, stdout, _stderr = self.run_cli(
            _FILE_OPTION, self.data_file, "search", "不存在的关键字"
        )

        self.assertEqual(exit_code, EXIT_SUCCESS)
        self.assertEqual(stdout.strip(), "未找到匹配的书签。")

    def test_search_requires_keyword(self) -> None:
        exit_code, _stdout, stderr = self.run_cli(_FILE_OPTION, self.data_file, "search")

        self.assertEqual(exit_code, EXIT_USAGE)
        self.assertIn("关键字", stderr)


class DeleteCommandTest(CliTestBase):
    """``delete`` 子命令。"""

    def test_delete_removes_bookmark(self) -> None:
        self.add_bookmark("待删除", "https://example.com/doomed")

        exit_code, stdout, _stderr = self.run_cli(
            _FILE_OPTION, self.data_file, "delete", "1"
        )

        self.assertEqual(exit_code, EXIT_SUCCESS)
        self.assertIn("已删除书签 #1", stdout)

        list_code, list_stdout, _list_stderr = self.run_cli(
            _FILE_OPTION, self.data_file, "list"
        )
        self.assertEqual(list_code, EXIT_SUCCESS)
        self.assertEqual(list_stdout.strip(), "暂无书签。")

    def test_delete_unknown_id_fails(self) -> None:
        self.add_bookmark("保留", "https://example.com/keep")

        exit_code, _stdout, stderr = self.run_cli(
            _FILE_OPTION, self.data_file, "delete", "42"
        )

        self.assertEqual(exit_code, EXIT_FAILURE)
        self.assertIn("未找到", stderr)

    def test_deleted_id_is_not_reused(self) -> None:
        self.add_bookmark("第一条", "https://example.com/one")
        self.add_bookmark("第二条", "https://example.com/two")
        self.run_cli(_FILE_OPTION, self.data_file, "delete", "1")

        exit_code, stdout, _stderr = self.add_bookmark(
            "第三条", "https://example.com/three"
        )

        self.assertEqual(exit_code, EXIT_SUCCESS)
        self.assertIn("已添加书签 #3", stdout)

    def test_delete_rejects_non_integer_id(self) -> None:
        exit_code, _stdout, stderr = self.run_cli(
            _FILE_OPTION, self.data_file, "delete", "abc"
        )

        self.assertEqual(exit_code, EXIT_USAGE)
        self.assertIn("整数", stderr)


class CorruptStorageTest(CliTestBase):
    """数据文件损坏时的退出码、中文提示与“不覆盖原文件”不变式。"""

    def test_corrupt_file_fails_without_overwriting(self) -> None:
        corrupt_payload = '{ "version": 1, "next_id": '
        data_path = Path(self.data_file)
        data_path.write_text(corrupt_payload, encoding="utf-8")

        exit_code, stdout, stderr = self.run_cli(
            _FILE_OPTION, self.data_file, "list"
        )

        self.assertEqual(exit_code, EXIT_FAILURE)
        self.assertEqual(stdout, "")
        self.assertIn("数据文件", stderr)
        self.assertEqual(data_path.read_text(encoding="utf-8"), corrupt_payload)

    def test_add_on_corrupt_file_fails_without_overwriting(self) -> None:
        corrupt_payload = "不是 JSON"
        data_path = Path(self.data_file)
        data_path.write_text(corrupt_payload, encoding="utf-8")

        exit_code, _stdout, stderr = self.add_bookmark("示例", "https://example.com")

        self.assertEqual(exit_code, EXIT_FAILURE)
        self.assertIn("数据文件", stderr)
        self.assertEqual(data_path.read_text(encoding="utf-8"), corrupt_payload)


class UsageTest(CliTestBase):
    """用法错误与选项解析。"""

    def test_without_subcommand_prints_help_and_returns_usage_code(self) -> None:
        exit_code, stdout, _stderr = self.run_cli()

        self.assertEqual(exit_code, EXIT_USAGE)
        self.assertIn("add", stdout)
        self.assertIn("delete", stdout)

    def test_empty_file_option_is_rejected(self) -> None:
        exit_code, _stdout, stderr = self.run_cli(
            _FILE_OPTION, "   ", "list"
        )

        self.assertEqual(exit_code, EXIT_USAGE)
        self.assertIn("数据文件路径", stderr)

    def test_unknown_subcommand_returns_usage_code(self) -> None:
        with self.assertRaises(SystemExit) as context:
            self.run_cli("frobnicate")

        self.assertEqual(context.exception.code, EXIT_USAGE)


if __name__ == "__main__":
    unittest.main()
