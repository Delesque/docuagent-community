import unittest

from bookmarks.main import main


class SmokeTest(unittest.TestCase):
    def test_entry_point_is_callable(self) -> None:
        self.assertTrue(callable(main))


if __name__ == "__main__":
    unittest.main()
