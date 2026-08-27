"""Offline contract for request/snapshot/resume generation configuration."""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from project_state import ProjectCheckpoint
from shot_generation_workflow import continue_shot_generation, resume_shot_generation
from tests import test_shot_generation_workflow as core_fixture
from video_generator import ProviderSubmissionUnknownError
from video_provider import ProviderErrorCode, VideoProviderError


class RecordingGenerator(core_fixture.FakeCoreVideoGenerator):
    def __call__(self, **kwargs):
        self.configuration = (kwargs["duration"], kwargs["resolution"])
        return super().__call__(**kwargs)


class ShotGenerationConfigTests(unittest.TestCase):
    setUp = core_fixture.ShotGenerationWorkflowTests.setUp
    generate = core_fixture.ShotGenerationWorkflowTests.generate

    def run_attempt(self, fake, *, resolution=None, duration=6, prepare=True):
        if prepare:
            self.checkpoint.prepare_shot_generation(1, generation_intent="FAILED_RETRY")
        kwargs = {} if resolution is None else {"resolution": resolution}
        with patch("requests.sessions.Session.request", side_effect=AssertionError("network forbidden")):
            return continue_shot_generation(
                paths=self.paths, checkpoint=self.checkpoint, plan=self.plan,
                shot=self.board.shots[0].model_copy(update={"duration": duration}), shot_id=1,
                deepseek_key="", provider_credentials={}, task_logger=self.logger,
                video_generate=fake,
                safety_review=lambda *_a, **_k: self.fail("saved safety must be reused"),
                **kwargs,
            )

    def snapshot(self, version=1):
        return json.loads(self.paths.shot_version_generation_path(1, version).read_text(encoding="utf-8"))

    def test_default_initial_request_and_snapshot_stay_768p(self):
        fake = RecordingGenerator()
        self.generate(fake)
        self.assertEqual(fake.configuration, (6, "768P"))
        self.assertEqual(self.snapshot()["resolution"], "768P")

    def test_default_shared_callable_stays_768p(self):
        fake = RecordingGenerator()
        self.run_attempt(fake)
        self.assertEqual(fake.configuration, (6, "768P"))
        self.assertEqual(self.snapshot()["resolution"], "768P")

    def test_explicit_configuration_matches_request_record_and_bundle(self):
        fake = RecordingGenerator()
        self.run_attempt(fake, resolution="2K", duration=10)
        record = self.checkpoint.shot_checkpoint(1)["generation_versions"][0]
        for value in (record, self.snapshot()):
            self.assertEqual((value["duration"], value["resolution"]), fake.configuration)
        self.assertEqual(fake.configuration, (10, "2K"))
        self.assertEqual(fake.submit_calls, 1)
        self.assertEqual(self.checkpoint.shot_checkpoint(1)["status"], "WAITING_REVIEW")

    def interrupted(self):
        with self.assertRaises(VideoProviderError):
            self.run_attempt(RecordingGenerator(fail_after_submit=True), resolution="2K", duration=10)
        self.checkpoint = ProjectCheckpoint.load(self.paths)

    def test_resume_after_reload_uses_persisted_duration_and_resolution(self):
        self.interrupted()
        fake = RecordingGenerator()
        resume_shot_generation(
            paths=self.paths, checkpoint=self.checkpoint, plan=self.plan,
            shot=self.board.shots[0], shot_id=1, deepseek_key="",
            provider_credentials={}, task_logger=self.logger, video_generate=fake,
        )
        self.assertEqual(fake.configuration, (10, "2K"))
        self.assertEqual(fake.submit_calls, 0)
        self.assertEqual(self.snapshot()["resolution"], "2K")

    def test_resume_ignores_new_caller_configuration(self):
        self.interrupted()
        fake = RecordingGenerator()
        self.run_attempt(fake, resolution="768P", duration=6, prepare=False)
        self.assertEqual(fake.configuration, (10, "2K"))
        self.assertEqual(fake.submit_calls, 0)

    def test_legacy_missing_resolution_means_768p_not_new_caller_value(self):
        self.interrupted()
        self.checkpoint.shot_checkpoint(1)["generation_versions"][0].pop("resolution")
        self.checkpoint.save()
        self.checkpoint = ProjectCheckpoint.load(self.paths)
        fake = RecordingGenerator()
        self.run_attempt(fake, resolution="2K", prepare=False)
        self.assertEqual(fake.configuration, (10, "768P"))
        self.assertEqual(fake.submit_calls, 0)

    def test_failed_history_is_unchanged_by_new_attempt(self):
        first = RecordingGenerator()
        original = first.__call__
        def reject(**kwargs):
            def rejected(_task):
                raise VideoProviderError(ProviderErrorCode.INVALID_REQUEST, "explicit rejection")
            kwargs["on_submitted"] = rejected
            return original(**kwargs)
        with self.assertRaises(VideoProviderError):
            self.run_attempt(reject)
        previous = {p.name: p.read_bytes() for p in self.paths.shot_version_dir(1, 1).iterdir()}
        self.run_attempt(RecordingGenerator(), resolution="2K")
        self.assertEqual(previous, {p.name: p.read_bytes() for p in self.paths.shot_version_dir(1, 1).iterdir()})
        self.assertEqual(self.checkpoint.shot_checkpoint(1)["generation_count"], 2)
        self.assertEqual(self.snapshot(2)["resolution"], "2K")

    def test_submission_unknown_stays_unknown_and_unresumable(self):
        with self.assertRaises(ProviderSubmissionUnknownError):
            self.run_attempt(RecordingGenerator(ambiguous=True), resolution="2K")
        self.assertTrue(self.checkpoint.shot_checkpoint(1)["submission_unknown"])
        from shot_generation_workflow import ShotGenerationResumeUnavailable
        with self.assertRaises(ShotGenerationResumeUnavailable):
            resume_shot_generation(
                paths=self.paths, checkpoint=self.checkpoint, plan=self.plan,
                shot=self.board.shots[0], shot_id=1, deepseek_key="",
                provider_credentials={}, task_logger=self.logger, video_generate=RecordingGenerator(),
            )

    def test_file_ready_resume_does_not_submit_again(self):
        first = RecordingGenerator()
        def failed_download(**kwargs):
            def fail(_task):
                raise VideoProviderError(ProviderErrorCode.DOWNLOAD_FAILED, "offline failure")
            kwargs["on_downloading"] = fail
            return first(**kwargs)
        with self.assertRaises(VideoProviderError):
            self.run_attempt(failed_download, resolution="2K")
        self.assertTrue(self.checkpoint.shot_checkpoint(1)["file_id"])
        self.checkpoint = ProjectCheckpoint.load(self.paths)
        second = RecordingGenerator()
        self.run_attempt(second, prepare=False)
        self.assertEqual(second.submit_calls, 0)
        self.assertEqual(second.configuration, (6, "2K"))

    def test_local_video_finalization_never_calls_generator(self):
        first = RecordingGenerator()
        def failed_finalize(**kwargs):
            def fail(_path):
                raise RuntimeError("offline finalization interruption")
            kwargs["on_downloaded"] = fail
            return first(**kwargs)
        with self.assertRaises(RuntimeError):
            self.run_attempt(failed_finalize, resolution="2K")
        self.checkpoint = ProjectCheckpoint.load(self.paths)
        second = RecordingGenerator()
        self.run_attempt(second, prepare=False)
        self.assertEqual(second.calls, 0)
        self.assertEqual(self.checkpoint.shot_checkpoint(1)["status"], "WAITING_REVIEW")
        self.assertEqual(self.snapshot()["resolution"], "2K")


if __name__ == "__main__":
    unittest.main()
