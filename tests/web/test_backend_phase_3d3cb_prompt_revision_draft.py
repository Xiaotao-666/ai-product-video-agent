from __future__ import annotations

import json
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch

import tests.web.test_backend_phase_3d2_shot_generation as phase3d2
from prompt_generator import PromptGenerationError, StructuredOutputExhaustedError
from storyboard import (
    CreativeBrief,
    PromptRevisionDraft,
    Storyboard,
    generate_prompt_revision_draft,
)
from tests.web.test_backend_phase_1b_projects import tree_snapshot, write_json
from web_backend.models.tasks import TaskOperation, TaskStatus


FEEDBACK = "增强电影感，提高产品质感"
DRAFT_CORE = "cinematic product close-up with premium studio light and controlled camera motion"
DRAFT_PROMPT = (
    DRAFT_CORE
    + "\n[Composition Constraint]\nNo dedicated subtitle-safe region is required for this shot."
    + "\n[Global Hard Constraints]\nNo additional global hard constraints."
    + "\n[Text Overlay Constraint]\nDo not generate artificial subtitles, captions, title cards, slogans, or UI text in the footage."
    + "\n[Audio Constraint]\nGenerate silent visual footage only; do not synthesize dialogue, voice-over, sound effects, soundtrack, or background music."
)


class WebBackendPhase3D3CBPromptRevisionDraftTests(unittest.TestCase):
    setUp = phase3d2.WebBackendPhase3D2ShotGenerationTests.setUp
    _write_project = phase3d2.WebBackendPhase3D2ShotGenerationTests._write_project
    _write_reference = staticmethod(
        phase3d2.WebBackendPhase3D2ShotGenerationTests._write_reference
    )
    wait_terminal = phase3d2.WebBackendPhase3D2ShotGenerationTests.wait_terminal

    def _prepare(self) -> None:
        write_json(
            self.project_dir / "concepts" / "creative_brief.json",
            {
                "creative_concept": "cinematic product reveal",
                "target_audience": "adult consumers",
                "key_message": "premium product identity",
                "visual_direction": "clean premium studio",
                "narrative_arc": "reveal and close",
                "global_constraints": {
                    "must": ["preserve product color"],
                    "must_not": ["people"],
                },
            },
        )
        project = json.loads(
            (self.project_dir / "project.json").read_text(encoding="utf-8")
        )
        prompt = project["video_generation"]["shots"]["1"]["prompt_versions"][0]
        prompt["visual_prompt_core"] = "approved core"
        write_json(self.project_dir / "project.json", project)

    @staticmethod
    def _result() -> PromptRevisionDraft:
        return PromptRevisionDraft(
            visual_prompt_core=DRAFT_CORE,
            prompt=DRAFT_PROMPT,
        )

    def _post(self, payload: dict | None = None):
        return self.client.post(
            "/api/projects/project-a/shots/shot_01/prompt/revision/draft",
            json={"feedback": FEEDBACK} if payload is None else payload,
            headers={"X-Correlation-ID": "req_phase3d3cb"},
        )

    def _successful_task(self, *, prepare: bool = True):
        if prepare:
            self._prepare()
        with patch(
            "web_backend.services.prompt_revision.generate_prompt_revision_draft",
            return_value=self._result(),
        ) as shared:
            response = self._post()
            self.assertEqual(response.status_code, 202, response.text)
            task = self.wait_terminal(response.json()["task_id"])
        self.assertEqual(task.status, TaskStatus.SUCCEEDED)
        shared.assert_called_once()
        return response, task, shared

    def test_01_shared_core_callable_uses_scoped_context_and_deterministic_blocks(self):
        self._prepare()
        project = json.loads(
            (self.project_dir / "project.json").read_text(encoding="utf-8")
        )
        request_payload = project["request"]
        from prompt_generator import ProductVideoRequest

        request = ProductVideoRequest.model_validate(request_payload)
        brief = CreativeBrief.model_validate_json(
            (self.project_dir / "concepts" / "creative_brief.json").read_text(
                encoding="utf-8"
            )
        )
        board = Storyboard.model_validate_json(
            (self.project_dir / "storyboard" / "storyboard.json").read_text(
                encoding="utf-8"
            )
        )
        captured: dict[str, object] = {}

        def deepseek(_key, system, user, **kwargs):
            captured.update(system=system, user=user, kwargs=kwargs)
            return {"visual_prompt_core": DRAFT_CORE}

        with patch("storyboard.deepseek_json_request", side_effect=deepseek) as provider:
            draft = generate_prompt_revision_draft(
                request=request,
                brief=brief,
                shot=board.shots[0],
                current_prompt="approved core\n[Composition Constraint]\nold",
                current_prompt_version=2,
                feedback=FEEDBACK,
                api_key="mock-key",
            )
        provider.assert_called_once()
        context = json.loads(str(captured["user"]))
        self.assertEqual(context["current_prompt_version"], 2)
        self.assertEqual(context["current_visual_prompt_core"], "approved core")
        self.assertEqual(context["revision_request"], FEEDBACK)
        self.assertEqual(context["current_shot"]["purpose"], "product closeup")
        self.assertEqual(context["current_shot"]["camera"], "static")
        self.assertEqual(context["current_shot"]["motion"], "static")
        self.assertNotIn("shots", context)
        self.assertNotIn("[Composition Constraint]", DRAFT_CORE)
        for marker in (
            "[Composition Constraint]",
            "[Global Hard Constraints]",
            "[Text Overlay Constraint]",
            "[Audio Constraint]",
        ):
            self.assertEqual(draft.prompt.count(marker), 1)

    def test_02_post_returns_dedicated_202_task_and_safe_result_reference(self):
        response, task, _shared = self._successful_task()
        self.assertEqual(response.headers["location"], f"/api/tasks/{task.task_id}")
        self.assertEqual(response.json()["operation"], "SHOT_PROMPT_REVISION_DRAFT")
        self.assertEqual(response.json()["target_id"], "shot_01")
        self.assertEqual(task.result.resource_type, "PROMPT_REVISION_DRAFT")
        self.assertEqual(task.result.resource_id, "shot_01")
        self.assertIsNone(task.result.version)

    def test_03_get_returns_safe_draft_and_f5_reads_without_post(self):
        self._successful_task()
        with patch.object(
            self.application.state.prompt_revision_draft_service,
            "submit",
        ) as submit:
            first = self.client.get(
                "/api/projects/project-a/shots/shot_01/prompt/revision/draft"
            )
            second = self.client.get(
                "/api/projects/project-a/shots/shot_01/prompt/revision/draft"
            )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["base_prompt_version"], 2)
        self.assertEqual(first.json()["original_prompt"], "approved active prompt")
        self.assertEqual(first.json()["draft_prompt"], DRAFT_PROMPT)
        self.assertEqual(first.json()["feedback"], FEEDBACK)
        submit.assert_not_called()

    def test_04_draft_does_not_mutate_prompt_versions_video_or_generation_count(self):
        self._prepare()
        before_tree = tree_snapshot(self.project_dir)
        before_project = (self.project_dir / "project.json").read_bytes()
        before_prompt = (self.project_dir / "storyboard" / "video_prompts.json").read_bytes()
        self._successful_task(prepare=False)
        self.assertEqual(tree_snapshot(self.project_dir), before_tree)
        self.assertEqual((self.project_dir / "project.json").read_bytes(), before_project)
        self.assertEqual(
            (self.project_dir / "storyboard" / "video_prompts.json").read_bytes(),
            before_prompt,
        )
        project = json.loads(before_project)
        entry = project["video_generation"]["shots"]["1"]
        self.assertEqual(entry["generation_count"], 0)
        self.assertEqual(len(entry["prompt_versions"]), 1)
        self.assertFalse(any(self.project_dir.rglob("*.mp4")))

    def test_05_draft_storage_is_runtime_only_and_task_json_has_no_content(self):
        _response, task, _shared = self._successful_task()
        draft_file = (
            self.runtime_root
            / "prompt_revision_drafts"
            / "project-a"
            / "shot_01.json"
        )
        self.assertTrue(draft_file.is_file())
        self.assertFalse(any(self.project_dir.rglob("*revision*draft*")))
        task_text = (
            self.runtime_root / "tasks" / f"{task.task_id}.json"
        ).read_text(encoding="utf-8")
        for forbidden in (
            FEEDBACK,
            DRAFT_CORE,
            "approved active prompt",
            "api_key",
            "credential",
            "provider raw",
            str(self.project_dir),
        ):
            self.assertNotIn(forbidden, task_text)

    def test_06_feedback_validation_rejects_blank_long_and_extra_without_task(self):
        self._prepare()
        before = len(
            self.application.state.task_repository.list_for_project("project-a")
        )
        for payload in (
            {"feedback": "   "},
            {"feedback": "x" * 2001},
            {"feedback": FEEDBACK, "prompt_version": 2},
            {"feedback": FEEDBACK, "provider": "deepseek"},
        ):
            with self.subTest(keys=list(payload)):
                self.assertEqual(self._post(payload).status_code, 422)
        self.assertEqual(
            len(self.application.state.task_repository.list_for_project("project-a")),
            before,
        )

    def test_07_invalid_output_and_provider_failure_are_distinct_safe_errors(self):
        self._prepare()
        cases = (
            (
                StructuredOutputExhaustedError("raw invalid output"),
                "PROMPT_REVISION_OUTPUT_INVALID",
            ),
            (PromptGenerationError("raw provider secret"), "PROVIDER_FAILED"),
        )
        for error, code in cases:
            with self.subTest(code=code), patch(
                "web_backend.services.prompt_revision.generate_prompt_revision_draft",
                side_effect=error,
            ):
                response = self._post()
                self.assertEqual(response.status_code, 202)
                task = self.wait_terminal(response.json()["task_id"])
                self.assertEqual(task.status, TaskStatus.FAILED)
                self.assertEqual(task.error.code, code)
                task_text = (
                    self.runtime_root / "tasks" / f"{task.task_id}.json"
                ).read_text(encoding="utf-8")
                self.assertNotIn("raw", task_text)

    def test_08_same_shot_active_task_blocks_duplicate_deepseek(self):
        self._prepare()
        entered = Event()
        release = Event()

        def controlled(**_kwargs):
            entered.set()
            release.wait(timeout=3)
            return self._result()

        with patch(
            "web_backend.services.prompt_revision.generate_prompt_revision_draft",
            side_effect=controlled,
        ) as provider:
            first = self._post()
            self.assertEqual(first.status_code, 202)
            self.assertTrue(entered.wait(timeout=2))
            second = self._post()
            self.assertEqual(second.status_code, 409)
            self.assertEqual(second.json()["error"]["code"], "PROJECT_BUSY")
            release.set()
            self.wait_terminal(first.json()["task_id"])
        provider.assert_called_once()

    def test_09_active_task_is_recoverable_from_project_tasks_without_repost(self):
        self._prepare()
        entered = Event()
        release = Event()

        def controlled(**_kwargs):
            entered.set()
            release.wait(timeout=3)
            return self._result()

        with patch(
            "web_backend.services.prompt_revision.generate_prompt_revision_draft",
            side_effect=controlled,
        ) as provider:
            response = self._post()
            self.assertTrue(entered.wait(timeout=2))
            listed = self.client.get("/api/projects/project-a/tasks")
            active = [
                item
                for item in listed.json()["tasks"]
                if item["operation"] == "SHOT_PROMPT_REVISION_DRAFT"
                and item["target_id"] == "shot_01"
                and item["status"] in {"QUEUED", "RUNNING"}
            ]
            self.assertEqual(len(active), 1)
            release.set()
            self.wait_terminal(response.json()["task_id"])
        provider.assert_called_once()

    def test_10_stale_draft_is_not_returned_after_base_prompt_changes(self):
        self._successful_task()
        project_path = self.project_dir / "project.json"
        project = json.loads(project_path.read_text(encoding="utf-8"))
        project["video_generation"]["shots"]["1"]["prompt_versions"][0][
            "prompt"
        ] = "changed canonical prompt"
        write_json(project_path, project)
        response = self.client.get(
            "/api/projects/project-a/shots/shot_01/prompt/revision/draft"
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json()["error"]["code"],
            "PROMPT_REVISION_DRAFT_NOT_FOUND",
        )

    def test_11_different_shot_draft_tasks_can_execute_in_parallel(self):
        from web_backend.repositories.project_repository import ProjectRepository
        from web_backend.repositories.task_repository import TaskRepository
        from web_backend.services.task_runner import TaskRunner
        from web_backend.services.tasks import TaskService
        from web_backend.locking import ProjectLockManager

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            # Reuse the already valid project repository root from this fixture.
            repository = TaskRepository(root / "runtime")
            runner = TaskRunner(repository, ProjectLockManager(), max_workers=2)
            service = TaskService(
                repository,
                runner,
                ProjectRepository(self.projects_root),
            )
            entered_one = Event()
            entered_two = Event()
            release = Event()

            def first():
                entered_one.set()
                release.wait(timeout=3)

            def second():
                entered_two.set()
                release.wait(timeout=3)

            try:
                service.submit(
                    project_id="project-a",
                    operation=TaskOperation.SHOT_PROMPT_REVISION_DRAFT,
                    target_id="shot_01",
                    correlation_id="req_parallel_one",
                    callable_=first,
                    allow_parallel_targets=True,
                    acquire_project_lock=False,
                )
                service.submit(
                    project_id="project-a",
                    operation=TaskOperation.SHOT_PROMPT_REVISION_DRAFT,
                    target_id="shot_02",
                    correlation_id="req_parallel_two",
                    callable_=second,
                    allow_parallel_targets=True,
                    acquire_project_lock=False,
                )
                self.assertTrue(entered_one.wait(timeout=2))
                self.assertTrue(entered_two.wait(timeout=2))
            finally:
                release.set()
                runner.shutdown()

    def test_12_openapi_and_http_surface_are_draft_only(self):
        schema = self.client.get("/openapi.json").json()
        path = "/api/projects/{project_id}/shots/{shot_id}/prompt/revision/draft"
        self.assertEqual(set(schema["paths"][path]), {"get", "post"})
        example = schema["paths"][path]["post"]["responses"]["202"]["content"][
            "application/json"
        ]["example"]
        self.assertEqual(example["operation"], "SHOT_PROMPT_REVISION_DRAFT")
        self.assertEqual(
            self.client.delete(
                "/api/projects/project-a/shots/shot_01/prompt/revision/draft"
            ).status_code,
            405,
        )


if __name__ == "__main__":
    unittest.main()
