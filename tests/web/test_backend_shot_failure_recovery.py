from __future__ import annotations

import json
import unittest
from contextlib import ExitStack
from threading import Event
from unittest.mock import patch

from tests.web import test_backend_phase_3d2_shot_generation as fixture
from tests.web.test_backend_phase_1b_projects import tree_snapshot, write_json
from video_provider import DownloadResult, ProviderErrorCode, ProviderTask, ProviderTaskStatus, VideoProviderError
from web_backend.models.tasks import TaskError, TaskRecord, TaskOperation, TaskStatus


class ShotFailureRecoveryTests(unittest.TestCase):
    _write_project = fixture.WebBackendPhase3D2ShotGenerationTests._write_project
    _write_reference = staticmethod(fixture.WebBackendPhase3D2ShotGenerationTests._write_reference)
    payload = staticmethod(fixture.WebBackendPhase3D2ShotGenerationTests.payload)
    preflight = fixture.WebBackendPhase3D2ShotGenerationTests.preflight
    start = fixture.WebBackendPhase3D2ShotGenerationTests.start
    wait_terminal = fixture.WebBackendPhase3D2ShotGenerationTests.wait_terminal
    provider_patches = fixture.WebBackendPhase3D2ShotGenerationTests.provider_patches
    base = "/api/projects/project-a/shots/shot_01"

    @staticmethod
    def rejection():
        return VideoProviderError(
            ProviderErrorCode.INVALID_REQUEST, "创建视频任务失败（2061）：unsupported plan",
            raw_error={"base_resp": {"status_code": 2061, "status_msg": "private plan"}},
        )

    def setUp(self):
        fixture.WebBackendPhase3D2ShotGenerationTests.setUp(self)
        with self.provider_patches(submit_error=self.rejection()):
            response = self.start()
            self.assertEqual(response.status_code, 202, response.text)
            self.failed_task = self.wait_terminal(response.json()["task_id"])
        self.assertEqual(self.failed_task.status.value, "FAILED")
        self.assertEqual(self.failed_task.error.code, "VIDEO_PROVIDER_INVALID_REQUEST")
        self.submit_calls = self.poll_calls = self.download_calls = 0

    def config(self, **changes):
        return {**self.payload(), "intent": "FAILED_RETRY", "duration": 6, "resolution": "768P", **changes}

    def check(self, config=None):
        return self.client.post(self.base + "/generation/failed-retry/preflight", json=config or self.config())

    def retry(self, config=None, **changes):
        config = config or self.config()
        checked = self.check(config)
        self.assertEqual(checked.status_code, 200, checked.text)
        self.assertTrue(checked.json()["ready"], checked.text)
        return self.client.post(self.base + "/generation/failed-retry", json={
            **config, "preflight_fingerprint": checked.json()["preflight_fingerprint"],
            "confirm_external_video_call": True, **changes,
        })

    def recovery(self):
        response = self.client.get(self.base)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["failure_recovery"]

    def mutate_entry(self, **changes):
        project = json.loads((self.project_dir / "project.json").read_text(encoding="utf-8"))
        project["video_generation"]["shots"]["1"].update(changes)
        write_json(self.project_dir / "project.json", project)

    def provider(self, *, reject=False, pause=None, release=None, poll_error=None):
        from providers.minimax_hailuo_provider import MiniMaxHailuoProvider
        from providers.minimax_h3_provider import MiniMaxH3Provider
        def submit(adapter, request, task_logger=None):
            self.submit_calls += 1
            self.request_config = (request.duration, request.resolution, adapter.model_name, request.required_capability)
            if pause:
                pause.set()
                release.wait(3)
            if reject:
                raise self.rejection()
            return ProviderTask(adapter.provider_name, adapter.model_name, adapter.api_version,
                                adapter.generation_mode(request.required_capability), "hidden-provider-task")
        def poll(adapter, task, task_logger=None):
            self.poll_calls += 1
            if poll_error:
                raise poll_error
            return task.evolve(status=ProviderTaskStatus.COMPLETED, provider_file_id="hidden-file",
                               output_locator="https://private.invalid/video")
        def download(adapter, task, output_path, request, task_logger=None):
            self.download_calls += 1
            self.download_config = (request.duration, request.resolution, adapter.model_name, request.required_capability)
            output_path.write_bytes(b"fake-video")
            return DownloadResult(output_path, 10)
        stack = ExitStack()
        for adapter in (MiniMaxHailuoProvider, MiniMaxH3Provider):
            for method, fake in (("submit", submit), ("poll", poll), ("download", download)):
                stack.enter_context(patch.object(adapter, method, autospec=True, side_effect=fake))
        return stack

    def test_failed_metadata_is_not_video_ready_and_prompt_stays_ready(self):
        collection = self.client.get("/api/projects/project-a/shots").json()
        shot = collection["shots"][0]
        self.assertEqual(shot["prompt_status"], "READY")
        self.assertEqual(shot["video_status"], "FAILED")
        self.assertEqual(shot["generation_count"], 1)
        self.assertIsNone(shot["official_version"])
        self.assertEqual(self.recovery()["state"], "RETRY_ALLOWED")
        self.assertIn("当前套餐不支持", self.recovery()["safe_message"])

    def test_explicit_rejection_preflight_is_zero_write_zero_network(self):
        before = tree_snapshot(self.root)
        options = self.client.get(self.base + "/generation/failed-retry/options")
        self.assertEqual(options.status_code, 200, options.text)
        self.assertTrue(options.json()["eligible"])
        checked = self.check()
        self.assertEqual(checked.status_code, 200, checked.text)
        self.assertTrue(checked.json()["ready"], checked.text)
        self.assertEqual(checked.json()["shot"]["next_video_version"], 2)
        self.assertEqual(checked.json()["shot"]["prompt_version"], 2)
        self.assertEqual(checked.json()["intent"], "FAILED_RETRY")
        self.assertEqual(tree_snapshot(self.root), before)
        self.assertEqual((self.submit_calls, self.poll_calls, self.download_calls), (0, 0, 0))

    def test_model_duration_resolution_and_visual_can_be_selected_without_fallback(self):
        for mode in ("none", "reference_asset", "first_frame"):
            with self.subTest(mode=mode):
                config = self.config(model_selection="MANUAL", requested_model="MiniMax-H3",
                                     duration=8, resolution="2K",
                                     visual_input={"mode": mode, "asset_ids": [] if mode == "none" else [self.asset_id]})
                checked = self.check(config)
                self.assertEqual(checked.status_code, 200, checked.text)
                self.assertTrue(checked.json()["ready"], checked.text)
                self.assertEqual(checked.json()["resolved"]["model"], "MiniMax-H3")
                self.assertEqual(checked.json()["shot"]["duration_seconds"], 8)
                self.assertEqual(checked.json()["shot"]["resolution"], "2K")
        invalid = self.check(self.config(model_selection="MANUAL", requested_model="MiniMax-Hailuo-2.3", resolution="2K"))
        self.assertFalse(invalid.json()["ready"])
        self.assertIn("INVALID_RESOLUTION", [item["code"] for item in invalid.json()["issues"]])
        self.assertEqual(self.submit_calls, 0)

    def test_confirmation_false_missing_and_non_boolean_create_no_task(self):
        checked = self.check().json()
        before = tree_snapshot(self.root)
        for value in (False, None, "true"):
            body = {**self.config(), "preflight_fingerprint": checked["preflight_fingerprint"]}
            if value is not None:
                body["confirm_external_video_call"] = value
            response = self.client.post(self.base + "/generation/failed-retry", json=body)
            self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(tree_snapshot(self.root), before)
        self.assertEqual(self.submit_calls, 0)

    def test_success_preserves_failed_v1_creates_v2_and_waits_for_review(self):
        previous = tree_snapshot(self.project_dir / "shots" / "shot_01" / "v001")
        previous_record = json.loads((self.project_dir / "project.json").read_text(encoding="utf-8"))["video_generation"]["shots"]["1"]["generation_versions"][0]
        config = self.config(model_selection="MANUAL", requested_model="MiniMax-H3", duration=8, resolution="2K")
        with self.provider():
            response = self.retry(config)
            self.assertEqual(response.status_code, 202, response.text)
            self.assertEqual(response.json()["operation"], "SHOT_GENERATE")
            self.assertIn(response.json()["task_id"], response.headers["Location"])
            task = self.wait_terminal(response.json()["task_id"])
        self.assertEqual(task.status.value, "SUCCEEDED", task.error)
        self.assertEqual(self.request_config, (8, "2K", "MiniMax-H3", "none"))
        self.assertEqual((self.submit_calls, self.poll_calls, self.download_calls), (1, 1, 1))
        self.assertEqual(tree_snapshot(self.project_dir / "shots" / "shot_01" / "v001"), previous)
        bundle = self.project_dir / "shots" / "shot_01" / "v002"
        generation = json.loads((bundle / "generation.json").read_text(encoding="utf-8"))
        self.assertEqual((generation["duration"], generation["resolution"]), (8, "2K"))
        self.assertEqual(generation["generation_intent"], "FAILED_RETRY")
        self.assertEqual(generation["status"], "WAITING_REVIEW")
        self.assertTrue((bundle / "video.mp4").is_file())
        detail = self.client.get(self.base).json()
        self.assertEqual(detail["generation_count"], 2)
        self.assertEqual(detail["pending_review_version"], 2)
        self.assertIsNone(detail["official_version"])
        self.assertEqual(next(item for item in detail["versions"] if item["version"] == 1)["review_status"], "FAILED")
        self.assertEqual(json.loads((self.project_dir / "project.json").read_text(encoding="utf-8"))["video_generation"]["shots"]["1"]["generation_versions"][0], previous_record)
        collection = self.client.get("/api/projects/project-a/shots").json()
        self.assertEqual(collection["shots"][0]["video_status"], "READY")
        self.assertEqual(detail["failure_recovery"]["state"], "BUSINESS_ALREADY_COMPLETE")

    def test_explicit_failure_again_requires_new_preflight_for_v3_without_loop(self):
        with self.provider(reject=True):
            response = self.retry()
            task = self.wait_terminal(response.json()["task_id"])
        self.assertEqual(task.status.value, "FAILED", task.error)
        self.assertEqual(self.submit_calls, 1)
        self.assertEqual(self.recovery()["state"], "RETRY_ALLOWED")
        checked = self.check()
        self.assertEqual(checked.json()["shot"]["next_video_version"], 3)
        self.assertEqual(self.submit_calls, 1)
        generation = json.loads((self.project_dir / "shots/shot_01/v002/generation.json").read_text(encoding="utf-8"))
        self.assertEqual(generation["status"], "FAILED")

    def test_failed_retry_resume_uses_its_persisted_config_and_no_new_submit(self):
        config = self.config(model_selection="MANUAL", requested_model="MiniMax-H3", duration=8, resolution="2K",
                             visual_input={"mode": "first_frame", "asset_ids": [self.asset_id]})
        with self.provider(poll_error=VideoProviderError(ProviderErrorCode.PROVIDER_TEMPORARY_ERROR, "fake timeout")):
            response = self.retry(config)
            task = self.wait_terminal(response.json()["task_id"])
        self.assertEqual(task.status.value, "FAILED")
        self.assertEqual(self.request_config, (8, "2K", "MiniMax-H3", "first_frame"))
        self.assertEqual(self.recovery()["state"], "RESUME_AVAILABLE")
        previous = tree_snapshot(self.project_dir / "shots" / "shot_01" / "v001")
        with self.provider():
            resumed = self.client.post(self.base + "/generation/resume")
            self.assertEqual(resumed.status_code, 202, resumed.text)
            task = self.wait_terminal(resumed.json()["task_id"])
        self.assertEqual(task.status.value, "SUCCEEDED", task.error)
        self.assertEqual(self.submit_calls, 1)
        self.assertEqual(self.download_config, (8, "2K", "MiniMax-H3", "first_frame"))
        self.assertEqual(task.result.version, 2)
        self.assertEqual(tree_snapshot(self.project_dir / "shots" / "shot_01" / "v001"), previous)

    def test_unknown_submission_blocks_retry(self):
        self.mutate_entry(submission_unknown=True, generation_phase="SUBMISSION_UNKNOWN")
        recovery = self.recovery()
        self.assertEqual(recovery["state"], "RETRY_BLOCKED_SUBMISSION_UNKNOWN")
        self.assertFalse(recovery["can_retry"])
        self.assertIn("为避免重复收费", recovery["safe_message"])
        self.assertEqual(self.check().status_code, 409)
        self.assertEqual(self.submit_calls, 0)

    def test_provider_task_and_file_progress_only_allow_resume(self):
        for field, reason in (("provider_task_id", "PROVIDER_TASK_EXISTS"), ("file_id", "FILE_READY")):
            with self.subTest(field=field):
                values = {"provider_task_id": None, "file_id": None, field: "hidden-locator"}
                self.mutate_entry(**values)
                recovery = self.recovery()
                self.assertEqual(recovery["state"], "RESUME_AVAILABLE")
                self.assertEqual(recovery["reason_code"], reason)
                self.assertFalse(recovery["can_retry"])
                self.assertEqual(self.check().status_code, 409)
        self.assertEqual(self.submit_calls, 0)

    def test_complete_bundle_and_incomplete_local_video_never_allow_resubmit(self):
        video = self.project_dir / "shots/shot_01/v001/video.mp4"
        video.write_bytes(b"")
        self.assertFalse(self.recovery()["can_retry"])
        video.write_bytes(b"fake-video")
        self.assertEqual(self.recovery()["state"], "BUSINESS_ALREADY_COMPLETE")
        self.assertEqual(self.check().status_code, 409)
        self.assertEqual(self.submit_calls, 0)

    def test_active_task_attaches_instead_of_duplicate(self):
        task = self.failed_task.model_copy(update={
            "task_id": "task_" + "a" * 32, "status": TaskStatus.QUEUED,
            "started_at": None, "finished_at": None, "error": None,
        })
        self.application.state.task_repository.create(task)
        recovery = self.recovery()
        self.assertEqual(recovery["state"], "ACTIVE_TASK")
        self.assertEqual(recovery["active_task_id"], task.task_id)
        self.assertFalse(recovery["can_retry"])
        self.assertEqual(self.check().status_code, 409)

    def test_unproven_failure_never_becomes_retryable(self):
        self.application.state.task_repository.update(self.failed_task.model_copy(update={
            "error": TaskError(code="TASK_EXECUTION_FAILED", message="任务执行失败。", retryable=False),
        }))
        self.assertFalse(self.recovery()["can_retry"])
        self.assertEqual(self.check().status_code, 409)

    def test_stale_configuration_and_prompt_are_rejected_before_task(self):
        checked = self.check().json()
        response = self.client.post(self.base + "/generation/failed-retry", json={
            **self.config(duration=10), "preflight_fingerprint": checked["preflight_fingerprint"],
            "confirm_external_video_call": True,
        })
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "FAILED_RETRY_STALE")
        self.mutate_entry(active_prompt_version=999)
        self.assertEqual(self.check().status_code, 409)
        self.assertEqual(self.submit_calls, 0)
        self.assertEqual(len(self.application.state.task_repository.list_for_project("project-a")), 1)

    def test_worker_rechecks_stale_state_under_lock(self):
        pending = []
        runner = self.application.state.task_runner
        original_submit = runner.submit
        with patch.object(runner, "submit", side_effect=lambda *args, **kwargs: pending.append((args, kwargs))):
            response = self.retry()
        self.assertEqual(response.status_code, 202, response.text)
        self.mutate_entry(submission_unknown=True)
        with self.provider():
            args, kwargs = pending[0]
            original_submit(*args, **kwargs)
            task = self.wait_terminal(response.json()["task_id"])
        self.assertEqual(task.error.code, "FAILED_RETRY_STALE")
        self.assertEqual(self.submit_calls, 0)

    def test_duplicate_request_while_running_never_submits_twice(self):
        entered, release = Event(), Event()
        with self.provider(pause=entered, release=release):
            first = self.retry()
            self.assertTrue(entered.wait(2))
            try:
                self.assertEqual(self.check().status_code, 409)
            finally:
                release.set()
            task = self.wait_terminal(first.json()["task_id"])
        self.assertEqual(task.status.value, "SUCCEEDED", task.error)
        self.assertEqual(self.submit_calls, 1)

    def test_security_rejects_locator_and_version_inputs_and_only_returns_safe_error(self):
        for field in ("path", "provider_task_id", "file_id", "version", "credential"):
            with self.subTest(field=field):
                self.assertEqual(self.check({**self.config(), field: "secret-value"}).status_code, 422)
        content = self.client.get(self.base).text + self.check().text
        for forbidden in ("private plan", "mock-hailuo-key", "provider_task_id", "file_id", str(self.root)):
            self.assertNotIn(forbidden, content)
        self.assertEqual(self.submit_calls, 0)

    def test_batch_stays_initial_only_and_normal_initial_rejects_failed_attempt(self):
        options = self.client.get("/api/projects/project-a/shots/generation/options")
        self.assertEqual(options.status_code, 200, options.text)
        self.assertFalse(options.json()["shots"][0]["available"])
        self.assertFalse(self.preflight().json()["ready"])
        self.assertEqual(self.submit_calls, 0)


if __name__ == "__main__":
    unittest.main()
