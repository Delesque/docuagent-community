"""Read-only round guard for the native sub-agent loop.

Real-model pilots showed the sub-agent looping on read-only tools for ten-plus
rounds without writing. This pins the 5/7/10 escalation ladder and the reset
behaviour when a write tool finally appears.
"""

import unittest

import agent_tools
from agent_tools import _read_only_round_reminders


def _turn(tool: str | None) -> dict:
    calls = [{"tool": tool, "args": {}}] if tool else []
    return {"turn": 1, "tool_calls": calls, "results": []}


class ReadOnlyRoundGuardTest(unittest.TestCase):
    def test_no_reminder_below_five(self) -> None:
        history = [_turn("read_file") for _ in range(4)]
        self.assertEqual(_read_only_round_reminders(history), [])

    def test_five_rounds_ask_to_stop_reading(self) -> None:
        history = [_turn("list_dir") for _ in range(5)]
        notice = _read_only_round_reminders(history)
        self.assertEqual(len(notice), 1)
        self.assertIn("不要再继续读取", notice[0])
        self.assertIn("write_file", notice[0])

    def test_seven_rounds_require_a_write_call(self) -> None:
        history = [_turn("task_status") for _ in range(7)]
        notice = _read_only_round_reminders(history)
        self.assertEqual(len(notice), 1)
        self.assertIn("必须调用 write_file 或 edit_file", notice[0])

    def test_ten_rounds_are_forbidden_read_only(self) -> None:
        history = [_turn("read_file") for _ in range(10)]
        notice = _read_only_round_reminders(history)
        self.assertEqual(len(notice), 1)
        self.assertIn("禁止再调用任何只读工具", notice[0])
        self.assertIn("任务将判为失败", notice[0])

    def test_write_resets_the_counter(self) -> None:
        history = (
            [_turn("read_file") for _ in range(5)]
            + [_turn("write_file")]
            + [_turn("read_file") for _ in range(4)]
        )
        self.assertEqual(_read_only_round_reminders(history), [])

    def test_write_then_five_more_reads_triggers_again(self) -> None:
        history = (
            [_turn("read_file") for _ in range(5)]
            + [_turn("edit_file")]
            + [_turn("read_file") for _ in range(5)]
        )
        notice = _read_only_round_reminders(history)
        self.assertEqual(len(notice), 1)
        self.assertIn("不要再继续读取", notice[0])

    def test_empty_history_is_silent(self) -> None:
        self.assertEqual(_read_only_round_reminders([]), [])
