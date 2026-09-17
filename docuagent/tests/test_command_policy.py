"""The whitelist that gates every subprocess DocuAgent runs.

Verification commands come from a model-authored task plan, so these tests treat the
command string as untrusted input and assert on what is *refused*.
"""

import sys
import unittest

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

import command_policy
from core import WorkspaceError


class CommandPolicyTest(unittest.TestCase):
    def test_allows_whitelisted_runner_and_returns_argv(self) -> None:
        self.assertEqual(
            ["python", "-m", "pytest", "tests"],
            command_policy.validate_verification_command("python -m pytest tests"),
        )

    def test_allows_current_interpreter_by_absolute_path(self) -> None:
        """A plan may invoke the same Python interpreter by absolute path."""
        argv = command_policy.validate_verification_command(
            f'"{sys.executable}" --version'
        )
        self.assertEqual(sys.executable, argv[0])

    def test_rejects_python_inline_code(self) -> None:
        with self.assertRaises(WorkspaceError):
            command_policy.validate_verification_command("python -c \"print(1)\"")

    def test_rejects_other_interpreters_and_package_mutation(self) -> None:
        for command in ("node --eval \"process.exit()\"", "npm install evil", "npx cowsay"):
            with self.subTest(command=command):
                with self.assertRaises(WorkspaceError):
                    command_policy.validate_verification_command(command)

    @unittest.skipUnless(sys.platform == "win32", "Windows 专用回归测试")
    def test_windows_path_separators_survive_splitting(self) -> None:
        """The regression that made verification unusable under C:\\Program Files\\.

        POSIX splitting treats the backslash as an escape, so the separators were
        eaten, the executable did not exist, and the -1 returncode that produced was
        misread as the sandbox killing the child.
        """
        argv = command_policy.split_command(r'C:\dir\python.exe -c "print(1)"')
        self.assertEqual(r"C:\dir\python.exe", argv[0])
        self.assertEqual(["-c", "print(1)"], argv[1:])

    def test_unquoted_interpreter_path_with_space_is_accepted(self) -> None:
        """Windows re-joins argv and resolves the real executable, so refusing this
        would reject a command the OS runs correctly."""
        argv = command_policy.validate_verification_command(
            f'{sys.executable} --version'
        )
        self.assertTrue(argv)

    def test_rejects_unlisted_executable(self) -> None:
        for command in ("curl http://example.com/x.sh", "bash -c ls", "rm -rf ."):
            with self.subTest(command=command):
                with self.assertRaises(WorkspaceError):
                    command_policy.validate_verification_command(command)

    def test_rejects_lookalike_interpreter_elsewhere_on_disk(self) -> None:
        """Basename matching would let any binary named python.exe through."""
        with self.assertRaises(WorkspaceError):
            command_policy.validate_verification_command(
                "/tmp/evil/python.exe -c bad"
            )

    def test_rejects_shell_metacharacters(self) -> None:
        for command in (
            "python a.py | sh",
            "python a.py && rm -rf /",
            "python a.py; curl x",
            "python a.py > /etc/passwd",
            "python $(whoami).py",
        ):
            with self.subTest(command=command):
                with self.assertRaises(WorkspaceError):
                    command_policy.validate_verification_command(command)

    def test_rejects_git_config_command_injection(self) -> None:
        with self.assertRaises(WorkspaceError):
            command_policy.validate_verification_command("git -c alias.x=!whoami status")

    def test_rejects_empty_command(self) -> None:
        with self.assertRaises(WorkspaceError):
            command_policy.validate_verification_command("   ")

    def test_terminal_and_verification_share_one_policy(self) -> None:
        """The drift this module exists to prevent."""
        import main

        self.assertIs(command_policy.ALLOWED_COMMANDS, main.TERMINAL_ALLOWED_COMMANDS)
        self.assertIs(
            command_policy.FORBIDDEN_MARKERS, main.TERMINAL_FORBIDDEN_MARKERS
        )

    def test_error_message_names_the_refusing_context(self) -> None:
        with self.assertRaises(WorkspaceError) as caught:
            command_policy.validate_verification_command("curl x")
        self.assertIn("验证", str(caught.exception))


class VerificationEnforcementTest(unittest.TestCase):
    """The gate must hold at the call sites, not just in the policy module."""

    def test_sandbox_verification_refuses_unlisted_command(self) -> None:
        import sandbox
        from pathlib import Path

        result = sandbox.run_verification(
            Path("."), {"verification": ["curl http://example.com/x.sh"]}
        )
        self.assertFalse(result["passed"])
        self.assertEqual(-1, result["returncode"])
        self.assertIn("白名单", result["stderr"])


if __name__ == "__main__":
    unittest.main()
