import tempfile
import unittest
from pathlib import Path

import task_jobs


class TaskJobsTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_job_is_persisted_and_finished(self) -> None:
        handle = task_jobs.begin_job(
            self.root,
            ["a"],
            kind="generate-wave",
            transport="sync",
        )

        jobs = task_jobs.read_jobs(self.root)
        self.assertEqual(1, len(jobs))
        self.assertEqual("running", jobs[0]["status"])
        self.assertEqual(["a"], jobs[0]["task_ids"])
        self.assertEqual("sync", jobs[0]["transport"])

        task_jobs.finish_job(handle, "completed")

        jobs = task_jobs.read_jobs(self.root)
        self.assertEqual("completed", jobs[0]["status"])
        self.assertTrue(jobs[0]["finished_at"])

    def test_cancel_is_scoped_to_one_project(self) -> None:
        with tempfile.TemporaryDirectory() as other:
            other_root = Path(other)
            first = task_jobs.begin_job(
                self.root,
                ["a"],
                kind="generate-wave",
                transport="sync",
            )
            second = task_jobs.begin_job(
                other_root,
                ["a"],
                kind="generate-wave",
                transport="sync",
            )

            self.assertEqual(
                1,
                task_jobs.cancel_jobs(["a"], project_root=self.root),
            )
            self.assertTrue(task_jobs.task_cancelled(self.root, "a"))
            self.assertFalse(task_jobs.task_cancelled(other_root, "a"))

            task_jobs.finish_job(first, "cancelled")
            task_jobs.finish_job(second, "completed")

    def test_task_cancel_hits_only_the_requested_task(self) -> None:
        handle = task_jobs.begin_job(
            self.root,
            ["a", "b"],
            kind="generate-wave",
            transport="stream",
        )

        self.assertEqual(
            1,
            task_jobs.cancel_jobs(["a"], project_root=self.root),
        )
        self.assertTrue(handle.cancelled("a"))
        self.assertFalse(handle.cancelled("b"))
        self.assertEqual(
            2,
            task_jobs.cancel_jobs(None, project_root=self.root),
        )
        self.assertTrue(handle.cancelled("b"))

        task_jobs.finish_job(handle, "cancelled")

    def test_reconcile_marks_other_process_jobs_as_orphaned(self) -> None:
        handle = task_jobs.begin_job(
            self.root,
            ["a"],
            kind="generate-wave",
            transport="sync",
        )
        document = task_jobs._read_document(self.root)
        document["jobs"][0]["owner_id"] = "dead-process"
        task_jobs._write_document(self.root, document)
        task_jobs._HANDLES.pop(
            (str(self.root.resolve()), handle.job_id),
            None,
        )

        self.assertEqual(1, task_jobs.reconcile_jobs(self.root))

        job = task_jobs.read_jobs(self.root)[0]
        self.assertEqual("orphaned", job["status"])
        self.assertIn("进程已退出", job["last_error"])

    def test_legacy_registry_still_cancels_without_project(self) -> None:
        handle = task_jobs.begin_job(
            None,
            ["legacy"],
            kind="legacy",
            transport="memory",
        )

        self.assertEqual(1, task_jobs.cancel_jobs(["legacy"]))
        self.assertTrue(task_jobs.any_task_cancelled("legacy"))

        task_jobs.finish_job(handle, "cancelled")


if __name__ == "__main__":
    unittest.main()
