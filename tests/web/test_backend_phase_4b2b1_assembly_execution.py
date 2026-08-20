from __future__ import annotations

import json
import subprocess
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch

from fastapi.testclient import TestClient

from tests.web import test_backend_phase_4b2a_assembly_planning as planning_fixtures
from tests.web.web_response_assertions import assert_public_payload
from video_assembly import AssemblyError, AssemblyExecutionResult


class WebBackendPhase4B2B1AssemblyExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        from web_backend.app import create_app
        from web_backend.settings import BackendSettings

        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.projects_root = self.root / "projects"
        self.runtime_root = self.root / "runtime"
        self.network_guard = patch(
            "requests.sessions.Session.request",
            side_effect=AssertionError("real provider/network call"),
        )
        self.network_guard.start()
        self.addCleanup(self.network_guard.stop)
        self.application = create_app(
            settings=BackendSettings(
                projects_root=self.projects_root,
                runtime_root=self.runtime_root,
                task_workers=2,
            )
        )
        self.client = TestClient(self.application, raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        self.addCleanup(self.application.state.task_runner.shutdown)
        self.executor_calls: list[list[tuple[int, str]]] = []
        self.use_success_executor()

    def ready_project(self, project_id: str = "assembly-execution") -> Path:
        fixtures = planning_fixtures.WebBackendPhase4B2AAssemblyPlanningTests
        project_dir = fixtures.write_project(
            self,
            project_id,
            [1, 2, 3],
            approved_versions={1: 2, 2: 1, 3: 1},
        )
        fixtures.write_bundle(
            project_dir, 1, 2, prompt_version=3, duration=6
        )
        fixtures.write_bundle(
            project_dir, 2, 1, prompt_version=1, duration=8
        )
        fixtures.write_bundle(
            project_dir, 3, 1, prompt_version=2, duration=5
        )
        project_state = project_dir / "project.json"
        payload = json.loads(project_state.read_text(encoding="utf-8"))
        payload["created_at"] = "2026-08-20T00:00:00+08:00"
        bindings = {1: (2, 3), 2: (1, 1), 3: (1, 2)}
        payload["video_generation"]["completed_shots"] = [1, 2, 3]
        for shot_id, (video_version, prompt_version) in bindings.items():
            checkpoint = payload["video_generation"]["shots"][str(shot_id)]
            checkpoint.update(
                generation_count=video_version,
                active_prompt_version=prompt_version,
                approved_prompt_version=prompt_version,
                active_video_version=video_version,
                approved_video_version=video_version,
                prompt_versions=[
                    {
                        "version": prompt_version,
                        "source": "ai_generated",
                        "prompt": f"safe prompt {shot_id}",
                    }
                ],
                generation_versions=[
                    {
                        "video_version": video_version,
                        "prompt_version": prompt_version,
                        "status": "APPROVED",
                        "duration": {1: 6, 2: 8, 3: 5}[shot_id],
                        "resolution": "768P",
                    }
                ],
            )
        project_state.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return project_dir

    @staticmethod
    def result() -> AssemblyExecutionResult:
        return AssemblyExecutionResult(
            mode="concat_copy",
            total_duration=19.0,
            width=1280,
            height=720,
            fps=25.0,
            codec="h264",
            pixel_format="yuv420p",
        )

    def use_success_executor(self, *, video: bytes = b"mock-assembled-video") -> None:
        def execute(_paths, *, sources, output, **_kwargs):
            self.executor_calls.append(
                [(item["shot_id"], Path(item["path"]).name) for item in sources]
            )
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(video)
            return self.result()

        self.application.state.assembly_execution_service._core_executor = execute

    def create_plan(self, project_id: str) -> dict:
        response = self.client.post(f"/api/projects/{project_id}/assembly/plan")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    @staticmethod
    def promote_shot_one(project_dir: Path) -> None:
        fixtures = planning_fixtures.WebBackendPhase4B2AAssemblyPlanningTests
        fixtures.write_bundle(project_dir, 1, 3, prompt_version=4, duration=6)
        fixtures.set_approved_version(project_dir, 1, 3, 4)
        project_path = project_dir / "project.json"
        payload = json.loads(project_path.read_text(encoding="utf-8"))
        checkpoint = payload["video_generation"]["shots"]["1"]
        checkpoint["prompt_versions"].append(
            {"version": 4, "source": "ai_revision", "prompt": "safe prompt 4"}
        )
        checkpoint["generation_versions"][-1].update(
            duration=6,
            resolution="768P",
        )
        project_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def execute(self, project_id: str, version: int) -> dict:
        response = self.client.post(
            f"/api/projects/{project_id}/assembly/execute",
            json={"assembly_version": version},
        )
        self.assertEqual(response.status_code, 202, response.text)
        self.assertTrue(response.headers["Location"].startswith("/api/tasks/task_"))
        return response.json()

    def wait_terminal(self, task_id: str, timeout: float = 4.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            payload = self.client.get(f"/api/tasks/{task_id}").json()
            if payload["status"] in {
                "SUCCEEDED",
                "FAILED",
                "INTERRUPTED",
                "CANCELLED",
            }:
                return payload
            time.sleep(0.01)
        self.fail(f"task did not finish: {task_id}")

    @staticmethod
    def manifest(project_dir: Path) -> dict:
        return json.loads(
            (project_dir / "videos" / "assembly_manifest.json").read_text(
                encoding="utf-8"
            )
        )

    def test_01_one_plan_creates_one_assembly_task_with_plan_target(self):
        self.ready_project("task-model")
        plan = self.create_plan("task-model")
        queued = self.execute("task-model", plan["assembly_version"])
        self.assertEqual(queued["operation"], "ASSEMBLY_EXECUTE")
        self.assertEqual(queued["target_id"], "assembly_v001")
        terminal = self.wait_terminal(queued["task_id"])
        self.assertEqual(terminal["status"], "SUCCEEDED")
        tasks = self.client.get("/api/projects/task-model/tasks").json()["tasks"]
        self.assertEqual(len(tasks), 1)
        self.assertNotIn("PROJECT_GENERATE_ALL", {item["operation"] for item in tasks})

    def test_02_success_creates_versioned_final_bundle_and_read_api_media(self):
        project_dir = self.ready_project("final-version")
        task = self.execute("final-version", self.create_plan("final-version")["assembly_version"])
        terminal = self.wait_terminal(task["task_id"])
        self.assertEqual(terminal["result"]["version"], 1)
        bundle = project_dir / "assembly_outputs" / "v001"
        self.assertEqual(
            {item.name for item in bundle.iterdir()},
            {"final_video.mp4", "assembly.json", "source_manifest.json", "review.json"},
        )
        detail = self.client.get("/api/projects/final-version/assembly")
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertTrue(detail.json()["video_available"])
        self.assertEqual(
            self.client.get("/api/projects/final-version/assembly/video").content,
            b"mock-assembled-video",
        )

    def test_03_executor_receives_exact_plan_order_and_output_is_validated(self):
        self.ready_project("exact-snapshot")
        plan = self.create_plan("exact-snapshot")
        task = self.execute("exact-snapshot", plan["assembly_version"])
        self.assertEqual(self.wait_terminal(task["task_id"])["status"], "SUCCEEDED")
        self.assertEqual(
            self.executor_calls,
            [[(1, "video.mp4"), (2, "video.mp4"), (3, "video.mp4")]],
        )

        project_dir = self.ready_project("empty-output")
        self.create_plan("empty-output")
        self.application.state.assembly_execution_service._core_executor = (
            lambda *_args, **_kwargs: self.result()
        )
        failed = self.wait_terminal(self.execute("empty-output", 1)["task_id"])
        self.assertEqual(failed["status"], "FAILED")
        self.assertEqual(failed["error"]["code"], "ASSEMBLY_EXECUTION_FAILED")
        self.assertFalse((project_dir / "assembly_outputs" / "v001").exists())
        self.assertEqual(list((project_dir / "assembly_outputs").glob(".staging_*")), [])

    def test_04_source_manifest_tracks_plan_versions_without_internal_paths(self):
        project_dir = self.ready_project("source-manifest")
        self.wait_terminal(
            self.execute("source-manifest", self.create_plan("source-manifest")["assembly_version"])["task_id"]
        )
        payload = json.loads(
            (project_dir / "assembly_outputs" / "v001" / "source_manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(payload["assembly_version"], 1)
        self.assertEqual(
            [(item["shot_id"], item["approved_video_version"], item["prompt_version"])
             for item in payload["shots"]],
            [(1, 2, 3), (2, 1, 1), (3, 1, 2)],
        )
        assert_public_payload(self, payload)

    def test_05_ffmpeg_failure_is_isolated_and_persisted_as_resumable(self):
        project_dir = self.ready_project("ffmpeg-failure")
        self.create_plan("ffmpeg-failure")

        def fail(*_args, **_kwargs):
            raise AssemblyError("mock ffmpeg failure")

        self.application.state.assembly_execution_service._core_executor = fail
        task = self.wait_terminal(self.execute("ffmpeg-failure", 1)["task_id"])
        self.assertEqual(task["status"], "FAILED")
        execution = self.manifest(project_dir)["executions"][0]
        self.assertEqual(execution["status"], "FAILED")
        self.assertEqual(execution["final_video_version"], 1)
        self.assertFalse((project_dir / "assembly_outputs" / "v001").exists())

    def test_06_resume_reuses_final_version_and_does_not_duplicate_execution(self):
        project_dir = self.ready_project("resume")
        self.create_plan("resume")
        self.application.state.assembly_execution_service._core_executor = (
            lambda *_args, **_kwargs: (_ for _ in ()).throw(AssemblyError("mock"))
        )
        self.assertEqual(
            self.wait_terminal(self.execute("resume", 1)["task_id"])["status"],
            "FAILED",
        )
        self.use_success_executor(video=b"resumed-video")
        response = self.client.post(
            "/api/projects/resume/assembly/resume",
            json={"assembly_version": 1},
        )
        self.assertEqual(response.status_code, 202, response.text)
        terminal = self.wait_terminal(response.json()["task_id"])
        self.assertEqual(terminal["status"], "SUCCEEDED")
        manifest = self.manifest(project_dir)
        self.assertEqual(len(manifest["executions"]), 1)
        self.assertEqual(manifest["executions"][0]["final_video_version"], 1)
        self.assertEqual(
            (project_dir / "assembly_outputs" / "v001" / "final_video.mp4").read_bytes(),
            b"resumed-video",
        )

    def test_07_resume_reconciles_published_bundle_without_second_ffmpeg_call(self):
        project_dir = self.ready_project("reconcile")
        self.create_plan("reconcile")
        self.application.state.assembly_execution_service._core_executor = (
            lambda *_args, **_kwargs: (_ for _ in ()).throw(AssemblyError("mock"))
        )
        self.wait_terminal(self.execute("reconcile", 1)["task_id"])
        output = project_dir / "assembly_outputs" / "v001"
        output.mkdir(parents=True)
        (output / "final_video.mp4").write_bytes(b"already-published")
        metadata = {
            "mode": "concat_copy", "total_duration": 19, "width": 1280,
            "height": 720, "fps": 25, "codec": "h264", "pixel_format": "yuv420p",
        }
        for name, payload in (
            ("assembly.json", metadata),
            ("source_manifest.json", {"shots": []}),
            ("review.json", {"status": "NOT_STARTED"}),
        ):
            path = output / name
            path.write_text(json.dumps(payload), encoding="utf-8")

        def forbidden(*_args, **_kwargs):
            raise AssertionError("FFmpeg must not replay after bundle publication")

        self.application.state.assembly_execution_service._core_executor = forbidden
        response = self.client.post(
            "/api/projects/reconcile/assembly/resume", json={"assembly_version": 1}
        )
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(self.wait_terminal(response.json()["task_id"])["status"], "SUCCEEDED")

    def test_08_new_plan_creates_next_version_and_preserves_old_bundle(self):
        project_dir = self.ready_project("history")
        self.wait_terminal(self.execute("history", self.create_plan("history")["assembly_version"])["task_id"])
        first = project_dir / "assembly_outputs" / "v001" / "final_video.mp4"
        self.assertEqual(first.read_bytes(), b"mock-assembled-video")
        self.promote_shot_one(project_dir)
        second_plan = self.create_plan("history")
        self.assertEqual(second_plan["assembly_version"], 2)
        self.use_success_executor(video=b"second-final")
        second = self.wait_terminal(self.execute("history", 2)["task_id"])
        self.assertEqual(second["result"]["version"], 2)
        self.assertEqual(first.read_bytes(), b"mock-assembled-video")
        self.assertEqual(
            (project_dir / "assembly_outputs" / "v002" / "final_video.mp4").read_bytes(),
            b"second-final",
        )

    def test_09_outdated_plan_is_rejected_before_task_or_ffmpeg(self):
        project_dir = self.ready_project("outdated-execute")
        self.create_plan("outdated-execute")
        self.promote_shot_one(project_dir)
        response = self.client.post(
            "/api/projects/outdated-execute/assembly/execute",
            json={"assembly_version": 1},
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"]["code"], "ASSEMBLY_PLAN_OUTDATED")
        self.assertEqual(
            self.client.get("/api/projects/outdated-execute/tasks").json()["tasks"], []
        )
        self.assertEqual(self.executor_calls, [])

    def test_10_running_plan_is_project_busy_and_never_double_executes(self):
        self.ready_project("busy-execute")
        self.create_plan("busy-execute")
        entered = Event()
        release = Event()

        def blocked(_paths, *, output, **_kwargs):
            entered.set()
            release.wait(2)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"one-call")
            return self.result()

        self.application.state.assembly_execution_service._core_executor = blocked
        first = self.execute("busy-execute", 1)
        self.assertTrue(entered.wait(2))
        second = self.client.post(
            "/api/projects/busy-execute/assembly/execute",
            json={"assembly_version": 1},
        )
        self.assertEqual(second.status_code, 409, second.text)
        self.assertEqual(second.json()["error"]["code"], "PROJECT_BUSY")
        release.set()
        self.assertEqual(self.wait_terminal(first["task_id"])["status"], "SUCCEEDED")
        self.assertEqual(
            len(self.client.get("/api/projects/busy-execute/tasks").json()["tasks"]), 1
        )

    def test_11_public_dto_and_openapi_hide_paths_commands_and_locators(self):
        self.ready_project("safe-dto")
        plan = self.create_plan("safe-dto")
        queued = self.execute("safe-dto", plan["assembly_version"])
        assert_public_payload(self, queued)
        terminal = self.wait_terminal(queued["task_id"])
        assert_public_payload(self, terminal)
        schema = self.client.get("/openapi.json").json()
        for path in (
            "/api/projects/{project_id}/assembly/execute",
            "/api/projects/{project_id}/assembly/resume",
        ):
            example = schema["paths"][path]["post"]["responses"]["202"]["content"]["application/json"]["example"]
            self.assertEqual(example["operation"], "ASSEMBLY_EXECUTE")
            assert_public_payload(self, example)
        rendered = json.dumps(terminal).lower()
        for forbidden in ("ffmpeg", "provider_task", "file_id", "workspace", "\\\\"):
            self.assertNotIn(forbidden, rendered)

    def test_12_core_snapshot_uses_mock_probe_and_silent_concat(self):
        from project_manager import create_project_paths
        from task_logger import TaskLogger
        from video_assembly import execute_assembly_snapshot

        paths = create_project_paths(self.root / "core-snapshot")
        sources = []
        for shot_id in (1, 2):
            source = paths.shot_version_video_path(shot_id, 1)
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(b"mock-source")
            sources.append({"shot_id": shot_id, "path": source})
        output = paths.assembly_staged_output_path("mock-core")
        commands: list[list[str]] = []

        def runner(command, **_kwargs):
            commands.append(command)
            if "-version" in command:
                return subprocess.CompletedProcess(command, 0, "mock version\n", "")
            if command[0] == "ffprobe":
                payload = {
                    "format": {"duration": "12"},
                    "streams": [
                        {
                            "index": 0,
                            "codec_type": "video",
                            "codec_name": "h264",
                            "width": 1280,
                            "height": 720,
                            "pix_fmt": "yuv420p",
                            "r_frame_rate": "25/1",
                            "avg_frame_rate": "25/1",
                        }
                    ],
                }
                return subprocess.CompletedProcess(
                    command, 0, json.dumps(payload), ""
                )
            Path(command[-1]).write_bytes(b"mock-concat-output")
            return subprocess.CompletedProcess(command, 0, "", "")

        with patch("video_assembly.shutil.which", side_effect=lambda name: name):
            result = execute_assembly_snapshot(
                paths,
                task_id="mock-core",
                sources=sources,
                output=output,
                task_logger=TaskLogger(paths, task_id="mock-core"),
                runner=runner,
            )
        self.assertEqual(result.mode, "concat_copy")
        concat = next(command for command in commands if "concat" in command)
        self.assertIn("-an", concat)
        self.assertTrue(output.is_file())


if __name__ == "__main__":
    unittest.main()
