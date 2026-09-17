"""Regression test for child-process decoding of non-UTF-8 output.

On a Chinese Windows install, tools such as `ipconfig` print GBK. Captured output must
not kill the reader thread; it has to come back (with replacement characters) so the
command still reports its real output and return code. Before the fix,
`subprocess.run(..., text=True)` raised `UnicodeDecodeError` in its reader thread and the
verification result silently lost all output.
"""

import sys
import tempfile
import unittest
from pathlib import Path

import sandbox

from command_fixtures import verification_command

GBK_SCRIPT = (
    "import sys\n"
    "sys.stdout.buffer.write('中文输出'.encode('gbk'))\n"
    "sys.stdout.buffer.write(b'\\n')\n"
    "sys.stdout.flush()\n"
)


class ChildDecodingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        (self.root / "src").mkdir(parents=True)
        (self.root / "src" / "core.py").write_text("print('core')\n", encoding="utf-8")

    def test_verification_tolerates_non_utf8_output(self) -> None:
        command = verification_command(self.root, GBK_SCRIPT, name="gbk_output.py")
        result = sandbox.run_verification(self.root, {"verification": [command]})
        self.assertTrue(result["passed"], result)
        self.assertEqual(0, result["returncode"], result)
        # The GBK bytes cannot become valid text, but the call must not crash and the
        # output must survive as replacement characters instead of disappearing.
        self.assertIn("\ufffd", result["stdout"])
        self.assertNotEqual("", result["stdout"].strip())

    def test_helper_run_tolerates_non_utf8_output(self) -> None:
        script = self.root / "gbk_output.py"
        script.write_text(GBK_SCRIPT, encoding="utf-8")
        completed = sandbox._run([sys.executable, "gbk_output.py"], self.root)
        self.assertEqual(0, completed.returncode)
        self.assertNotEqual("", completed.stdout.strip())
        self.assertIn("\ufffd", completed.stdout)


if __name__ == "__main__":
    unittest.main()
