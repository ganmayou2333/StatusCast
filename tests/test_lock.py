from __future__ import annotations

import unittest

from core.lock import AlreadyRunningError, SingleInstance


class SingleInstanceTests(unittest.TestCase):
    def test_second_instance_is_blocked_until_release(self) -> None:
        first = SingleInstance()
        second = SingleInstance()
        third = SingleInstance()
        first.acquire()
        try:
            with self.assertRaises(AlreadyRunningError):
                second.acquire()
        finally:
            first.release()
        third.acquire()
        third.release()


if __name__ == "__main__":
    unittest.main()
