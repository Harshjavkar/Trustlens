import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

from reel_lab.jobs import CreatorJobs, canonical_handle


class CreatorJobsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "library"
        self.jobs = CreatorJobs(self.root)

    def test_handle_and_profile_url_share_one_canonical_creator(self):
        values = ["FinanceWithSharan", " @FINANCEWITHSHARAN ",
                  "https://www.instagram.com/FinanceWithSharan",
                  "https://www.instagram.com/financewithsharan/",
                  "https://instagram.com/FINANCEWITHSHARAN/"]
        results = [self.jobs.request(value) for value in values]
        self.assertEqual({result["handle"] for result in results}, {"financewithsharan"})
        self.assertEqual(len({result["job_id"] for result in results}), 1)
        self.assertEqual(results[0]["platform"], "instagram")
        self.assertFalse(results[0]["coverage"]["profile_complete"])
        self.assertEqual([result["reused"] for result in results], [False, True, True, True, True])

    def test_invalid_urls_and_nonprofile_routes_are_rejected(self):
        values = ["", ".", "..", "@@creator", "creator/name", "a" * 31, ".creator", "creator.",
                  "two..dots", "https://evil.test/creator/", "http://www.instagram.com/creator/",
                  "https://www.instagram.com.evil.test/creator/", "https://evil@www.instagram.com/creator/",
                  "https://www.instagram.com:443/creator/", "https://www.instagram.com/reel/CODE/",
                  "https://www.instagram.com/p/CODE/", "https://www.instagram.com/reel/",
                  "https://www.instagram.com/creator/?next=evil", "https://www.instagram.com/creator/#x",
                  "https://www.instagram.com/creator/extra/", "https://www.instagram.com/%63reator/"]
        for value in values:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.jobs.request(value)
        self.assertEqual(self.jobs.list(), [])

    def test_concurrent_requests_from_independent_instances_deduplicate(self):
        stores = [CreatorJobs(self.root) for _ in range(12)]
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(lambda store: store.request("@SHARED"), stores))
        self.assertEqual(len({result["job_id"] for result in results}), 1)
        self.assertEqual({result["status"] for result in results}, {"queued"})
        self.assertEqual(sum(not result["reused"] for result in results), 1)
        self.assertEqual(len(self.jobs.list()), 1)
        with closing(sqlite3.connect(self.jobs.database_path)) as database:
            self.assertEqual(database.execute("SELECT count(*) FROM jobs").fetchone()[0], 1)

    def test_ready_creator_is_reused_and_coverage_updates_without_new_job(self):
        requested = self.jobs.request("creator")
        self.jobs.claim_next()
        ready = self.jobs.finish("creator", "ready", coverage={"posts": 3, "speech": 2})
        requested_again = self.jobs.request("CREATOR")
        self.assertTrue(requested_again.pop("reused"))
        self.assertEqual(requested_again, ready)
        self.jobs.sync_ready([{"handle": "creator", "posts": 4, "speech": 3, "profile_complete": True}])
        updated = self.jobs.get("creator")
        self.assertEqual(updated["job_id"], requested["job_id"])
        self.assertEqual(updated["coverage"]["posts"], 4)
        self.assertFalse(updated["coverage"]["profile_complete"])
        self.assertIn("unverified", updated["coverage"]["coverage_note"])
        self.assertIsNone(self.jobs.claim_next())

    def test_sync_existing_library_creates_ready_creator_and_is_idempotent(self):
        creators = [{"handle": "already_stored", "posts": 11, "captions": 11,
                     "speech": 0, "ocr": 0, "duration_seconds": 760.64}]
        self.jobs.sync_ready(creators)
        ready = self.jobs.get("already_stored")
        self.assertEqual(ready["status"], "ready")
        self.assertEqual(ready["coverage"]["posts"], 11)
        self.assertEqual(ready["coverage"]["duration_seconds"], 760.64)
        self.jobs.sync_ready(creators)
        requested = self.jobs.request("already_stored")
        self.assertTrue(requested.pop("reused"))
        self.assertEqual(requested, ready)
        self.assertIsNone(self.jobs.claim_next())

    def test_sync_does_not_mark_media_only_creator_ready(self):
        self.jobs.request("queued_creator")
        self.jobs.sync_ready([
            {"handle": "no_text", "posts": 11, "captions": 0, "speech": 0, "ocr": 0},
            {"handle": "queued_creator", "posts": 2, "captions": 0, "speech": 0, "ocr": 0},
        ])
        self.assertIsNone(self.jobs.get("no_text"))
        self.assertEqual(self.jobs.get("queued_creator")["status"], "queued")

    def test_sync_updates_active_coverage_without_finishing_worker_job(self):
        processing = self.jobs.request("processing_creator")
        self.jobs.claim_next()
        processing = self.jobs.update_progress("processing_creator", "Still extracting speech.")
        queued = self.jobs.request("queued_creator")
        self.jobs.sync_ready([
            {"handle": "processing_creator", "posts": 2, "captions": 2},
            {"handle": "queued_creator", "posts": 1, "speech": 1},
        ])
        for before in (processing, queued):
            after = self.jobs.get(before["handle"])
            self.assertEqual(after["status"], before["status"])
            self.assertEqual(after["job_id"], before["job_id"])
            self.assertEqual(after["detail"], before["detail"])
            self.assertGreater(after["coverage"]["posts"], 0)
        finished = self.jobs.finish("processing_creator", "ready")
        self.assertEqual(finished["status"], "ready")
        self.assertEqual(finished["coverage"]["captions"], 2)

    def test_failed_and_missing_links_require_explicit_retry(self):
        for status in ("failed", "needs_source_links"):
            with self.subTest(status=status):
                handle = "creator_" + status
                first = self.jobs.request(handle)
                self.jobs.claim_next()
                terminal = self.jobs.finish(handle, status, "Needs an explicit operator action.")
                for _ in range(3):
                    repeated = self.jobs.request(handle)
                    self.assertTrue(repeated.pop("reused"))
                    self.assertEqual(repeated, terminal)
                self.assertIsNone(self.jobs.claim_next())
                retry = self.jobs.retry(handle)
                self.assertEqual(retry["status"], "queued")
                self.assertFalse(retry["reused"])
                self.assertNotEqual(retry["job_id"], first["job_id"])
                self.assertEqual(self.jobs.retry(handle)["job_id"], retry["job_id"])
                self.jobs.finish(handle, "ready")

    def test_concurrent_retry_creates_only_one_new_attempt(self):
        old = self.jobs.request("creator")
        self.jobs.finish("creator", "failed")
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.jobs.retry("creator"), range(16)))
        self.assertEqual(len({result["job_id"] for result in results}), 1)
        self.assertEqual(sum(not result["reused"] for result in results), 1)
        self.assertNotEqual(results[0]["job_id"], old["job_id"])
        with closing(sqlite3.connect(self.jobs.database_path)) as database:
            self.assertEqual(database.execute("SELECT count(*) FROM jobs").fetchone()[0], 2)

    def test_claim_next_is_atomic_and_never_claims_one_job_twice(self):
        first = self.jobs.request("first")
        second = self.jobs.request("second")
        with ThreadPoolExecutor(max_workers=8) as pool:
            claims = list(pool.map(lambda _: self.jobs.claim_next(), range(16)))
        claimed = [claim for claim in claims if claim is not None]
        self.assertEqual(len(claimed), 2)
        self.assertEqual({claim["job_id"] for claim in claimed}, {first["job_id"], second["job_id"]})
        self.assertEqual({claim["status"] for claim in claimed}, {"processing"})

    def test_restart_recovery_is_explicit_and_retains_same_job(self):
        initial = self.jobs.request("interrupted")
        self.jobs.claim_next()
        self.jobs.request("still_queued")
        self.jobs.request("failed")
        self.jobs.finish("failed", "failed")
        restarted = CreatorJobs(self.root)
        self.assertEqual(restarted.get("interrupted")["status"], "processing")
        self.assertEqual(restarted.recover_interrupted(), 1)
        self.assertEqual(restarted.recover_interrupted(), 0)
        self.assertEqual(restarted.get("interrupted")["status"], "queued")
        self.assertEqual(restarted.get("interrupted")["job_id"], initial["job_id"])
        self.assertEqual(restarted.get("failed")["status"], "failed")
        self.assertEqual(restarted.claim_next()["handle"], "interrupted")

    def test_live_progress_persists_and_does_not_reopen_terminal_job(self):
        initial = self.jobs.request("creator")
        progress = self.jobs.update_progress("creator", "Waiting for saved source links.")
        self.assertEqual(progress["status"], "queued")
        self.assertEqual(progress["job_id"], initial["job_id"])
        self.jobs.claim_next()
        self.jobs.update_progress("creator", "Transcribing the saved audio.")
        self.assertEqual(CreatorJobs(self.root).get("creator")["detail"], "Transcribing the saved audio.")
        self.jobs.finish("creator", "needs_source_links", "Provide source links.")
        final = self.jobs.get("creator")
        self.assertEqual(self.jobs.update_progress("creator", "Stale worker update"), final)

    def test_detail_redaction_and_coverage_validation(self):
        self.jobs.request("creator")
        finished = self.jobs.finish(
            "creator", "failed", "Authorization: private-token\nFailed URL https://host/?token=private",
        )
        self.assertNotIn("private", json.dumps(finished))
        self.assertNotIn("https://", finished["detail"])
        with self.assertRaises(ValueError):
            self.jobs.sync_ready([{"handle": "creator", "posts": float("nan")}])
        self.assertEqual(self.jobs.get("creator")["status"], "failed")

    def test_terminal_jobs_cannot_be_reopened_by_finish_or_retry_ready(self):
        self.jobs.request("creator")
        ready = self.jobs.finish("creator", "ready")
        retried = self.jobs.retry("creator")
        self.assertTrue(retried.pop("reused"))
        self.assertEqual(retried, ready)
        self.assertEqual(self.jobs.finish("creator", "ready"), ready)
        with self.assertRaises(ValueError):
            self.jobs.finish("creator", "failed")
        with self.assertRaises(ValueError):
            self.jobs.finish("unknown", "ready")
        with self.assertRaises(ValueError):
            self.jobs.finish("creator", "processing")

    def test_get_unknown_and_sorted_list(self):
        self.assertIsNone(self.jobs.get("unknown"))
        self.jobs.request("z_creator")
        self.jobs.request("a_creator")
        self.assertEqual([row["handle"] for row in self.jobs.list()], ["a_creator", "z_creator"])


if __name__ == "__main__":
    unittest.main()
