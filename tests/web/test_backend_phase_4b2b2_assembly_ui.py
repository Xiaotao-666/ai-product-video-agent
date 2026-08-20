from __future__ import annotations

import json
import unittest
from pathlib import Path
from threading import Event

from tests.web import test_backend_phase_4b2b1_assembly_execution as execution_fixtures
from tests.web.web_response_assertions import assert_public_payload


class WebBackendPhase4B2B2AssemblyUiTests(unittest.TestCase):
    """HTTP contract tests for the Assembly UI workflow; FFmpeg stays mocked."""

    setUp = execution_fixtures.WebBackendPhase4B2B1AssemblyExecutionTests.setUp
    ready_project = execution_fixtures.WebBackendPhase4B2B1AssemblyExecutionTests.ready_project
    use_success_executor = execution_fixtures.WebBackendPhase4B2B1AssemblyExecutionTests.use_success_executor
    create_plan = execution_fixtures.WebBackendPhase4B2B1AssemblyExecutionTests.create_plan
    execute = execution_fixtures.WebBackendPhase4B2B1AssemblyExecutionTests.execute
    wait_terminal = execution_fixtures.WebBackendPhase4B2B1AssemblyExecutionTests.wait_terminal
    manifest = staticmethod(execution_fixtures.WebBackendPhase4B2B1AssemblyExecutionTests.manifest)
    result = staticmethod(execution_fixtures.WebBackendPhase4B2B1AssemblyExecutionTests.result)
    promote_shot_one = staticmethod(
        execution_fixtures.WebBackendPhase4B2B1AssemblyExecutionTests.promote_shot_one
    )

    def test_01_get_assembly_exposes_ready_plan_snapshot_without_a_task(self):
        self.ready_project("assembly-ui-plan")
        plan = self.create_plan("assembly-ui-plan")

        response = self.client.get("/api/projects/assembly-ui-plan/assembly")

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["current_plan"]["assembly_version"], plan["assembly_version"])
        self.assertEqual(payload["current_plan"]["status"], "READY")
        self.assertEqual(
            [(shot["shot_id"], shot["approved_video_version"], shot["prompt_version"])
             for shot in payload["current_plan"]["shots"]],
            [(1, 2, 3), (2, 1, 1), (3, 1, 2)],
        )
        self.assertEqual(
            self.client.get("/api/projects/assembly-ui-plan/tasks").json()["tasks"],
            [],
        )

    def test_02_execute_returns_one_durable_assembly_task(self):
        self.ready_project("assembly-ui-execute")
        plan = self.create_plan("assembly-ui-execute")

        task = self.execute("assembly-ui-execute", plan["assembly_version"])

        self.assertEqual(task["operation"], "ASSEMBLY_EXECUTE")
        self.assertEqual(task["target_id"], "assembly_v001")
        self.assertEqual(self.wait_terminal(task["task_id"])["status"], "SUCCEEDED")
        tasks = self.client.get("/api/projects/assembly-ui-execute/tasks").json()["tasks"]
        self.assertEqual([item["task_id"] for item in tasks], [task["task_id"]])

    def test_03_success_projects_final_video_version_and_exact_media(self):
        project_dir = self.ready_project("assembly-ui-final")
        plan = self.create_plan("assembly-ui-final")
        task = self.execute("assembly-ui-final", plan["assembly_version"])
        self.assertEqual(self.wait_terminal(task["task_id"])["status"], "SUCCEEDED")

        payload = self.client.get("/api/projects/assembly-ui-final/assembly").json()

        self.assertEqual(payload["current_version"], 1)
        self.assertEqual(payload["final_videos"][0]["final_video_version"], 1)
        self.assertEqual(payload["final_videos"][0]["assembly_version"], 1)
        self.assertTrue(payload["final_videos"][0]["is_current"])
        self.assertEqual(len(payload["final_videos"][0]["shots"]), 3)
        self.assertEqual(
            self.client.get(
                "/api/projects/assembly-ui-final/assembly/versions/1/video"
            ).content,
            b"mock-assembled-video",
        )
        self.assertTrue((project_dir / "assembly_outputs" / "v001").is_dir())

    def test_04_new_execution_preserves_and_serves_final_video_history(self):
        project_dir = self.ready_project("assembly-ui-history")
        first_plan = self.create_plan("assembly-ui-history")
        self.wait_terminal(self.execute("assembly-ui-history", first_plan["assembly_version"])["task_id"])
        self.promote_shot_one(project_dir)
        second_plan = self.create_plan("assembly-ui-history")
        self.use_success_executor(video=b"second-final-video")
        self.wait_terminal(self.execute("assembly-ui-history", second_plan["assembly_version"])["task_id"])

        payload = self.client.get("/api/projects/assembly-ui-history/assembly").json()

        self.assertEqual(
            [item["final_video_version"] for item in payload["final_videos"]],
            [2, 1],
        )
        self.assertEqual(
            [item["is_current"] for item in payload["final_videos"]],
            [True, False],
        )
        self.assertEqual(
            self.client.get(
                "/api/projects/assembly-ui-history/assembly/versions/1/video"
            ).content,
            b"mock-assembled-video",
        )
        self.assertEqual(
            self.client.get(
                "/api/projects/assembly-ui-history/assembly/versions/2/video"
            ).content,
            b"second-final-video",
        )

    def test_05_running_task_is_recoverable_by_project_task_list_without_repost(self):
        self.ready_project("assembly-ui-recovery")
        self.create_plan("assembly-ui-recovery")
        entered = Event()
        release = Event()
        calls = 0

        def blocked(_paths, *, output, **_kwargs):
            nonlocal calls
            calls += 1
            entered.set()
            release.wait(2)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"recovered-final")
            return self.result()

        self.application.state.assembly_execution_service._core_executor = blocked
        task = self.execute("assembly-ui-recovery", 1)
        self.assertTrue(entered.wait(2))

        tasks = self.client.get("/api/projects/assembly-ui-recovery/tasks").json()["tasks"]
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["task_id"], task["task_id"])
        self.assertIn(tasks[0]["status"], {"QUEUED", "RUNNING"})
        release.set()
        self.assertEqual(self.wait_terminal(task["task_id"])["status"], "SUCCEEDED")
        self.assertEqual(calls, 1)

    def test_06_outdated_plan_rejects_execution_and_keeps_old_final_video(self):
        project_dir = self.ready_project("assembly-ui-outdated")
        self.create_plan("assembly-ui-outdated")
        first = self.execute("assembly-ui-outdated", 1)
        self.assertEqual(self.wait_terminal(first["task_id"])["status"], "SUCCEEDED")
        self.promote_shot_one(project_dir)

        detail = self.client.get("/api/projects/assembly-ui-outdated/assembly")
        rejected = self.client.post(
            "/api/projects/assembly-ui-outdated/assembly/execute",
            json={"assembly_version": 1},
        )

        self.assertEqual(detail.json()["current_plan"]["status"], "OUTDATED")
        self.assertEqual(rejected.status_code, 409, rejected.text)
        self.assertEqual(rejected.json()["error"]["code"], "ASSEMBLY_PLAN_OUTDATED")
        self.assertEqual(
            self.client.get(
                "/api/projects/assembly-ui-outdated/assembly/versions/1/video"
            ).content,
            b"mock-assembled-video",
        )
        self.assertEqual(
            len(self.client.get("/api/projects/assembly-ui-outdated/tasks").json()["tasks"]),
            1,
        )

    def test_07_missing_or_unknown_version_returns_safe_media_error(self):
        project_dir = self.ready_project("assembly-ui-media-error")
        self.create_plan("assembly-ui-media-error")
        task = self.execute("assembly-ui-media-error", 1)
        self.wait_terminal(task["task_id"])
        (project_dir / "assembly_outputs" / "v001" / "final_video.mp4").unlink()

        missing = self.client.get(
            "/api/projects/assembly-ui-media-error/assembly/versions/1/video"
        )
        unknown = self.client.get(
            "/api/projects/assembly-ui-media-error/assembly/versions/99/video"
        )

        self.assertEqual(missing.status_code, 404)
        self.assertEqual(unknown.status_code, 404)
        self.assertEqual(missing.json()["error"]["code"], "ASSEMBLY_MEDIA_NOT_FOUND")

    def test_08_dto_and_openapi_expose_no_paths_commands_or_locators(self):
        self.ready_project("assembly-ui-safe")
        self.create_plan("assembly-ui-safe")
        task = self.execute("assembly-ui-safe", 1)
        self.wait_terminal(task["task_id"])

        payload = self.client.get("/api/projects/assembly-ui-safe/assembly").json()
        assert_public_payload(self, payload)
        rendered = json.dumps(payload).lower()
        for forbidden in (
            "absolute",
            "workspace",
            "ffmpeg",
            "provider_task",
            "file_id",
            "credential",
            "\\\\",
        ):
            self.assertNotIn(forbidden, rendered)
        schema = self.client.get("/openapi.json").json()
        self.assertIn(
            "/api/projects/{project_id}/assembly/versions/{version}/video",
            schema["paths"],
        )


if __name__ == "__main__":
    unittest.main()
