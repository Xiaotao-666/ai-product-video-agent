from __future__ import annotations

import copy
import json
import unittest
from unittest.mock import patch

import tests.web.test_backend_phase_3d3cb_prompt_revision_draft as phase3d3cb
from tests.web.test_backend_phase_1b_projects import tree_snapshot, write_json


class WebBackendPhase3D3CB2APromptRevisionAdoptTests(unittest.TestCase):
    setUp = phase3d3cb.WebBackendPhase3D3CBPromptRevisionDraftTests.setUp
    _write_project = phase3d3cb.WebBackendPhase3D3CBPromptRevisionDraftTests._write_project
    _write_reference = staticmethod(
        phase3d3cb.WebBackendPhase3D3CBPromptRevisionDraftTests._write_reference
    )
    wait_terminal = phase3d3cb.WebBackendPhase3D3CBPromptRevisionDraftTests.wait_terminal
    _prepare = phase3d3cb.WebBackendPhase3D3CBPromptRevisionDraftTests._prepare
    _result = staticmethod(
        phase3d3cb.WebBackendPhase3D3CBPromptRevisionDraftTests._result
    )

    def _prepare_adoptable(self) -> None:
        self._prepare()
        project_path = self.project_dir / "project.json"
        project = json.loads(project_path.read_text(encoding="utf-8"))
        entry = project["video_generation"]["shots"]["1"]
        entry.update(
            {
                "status": "APPROVED",
                "generation_count": 1,
                "active_video_version": 1,
                "approved_video_version": 1,
                "approved_prompt_version": 2,
            }
        )
        entry["generation_versions"] = [
            {
                "video_version": 1,
                "prompt_version": 2,
                "status": "APPROVED",
                "review_result": "APPROVED",
                "is_active": True,
                "is_approved": True,
            }
        ]
        write_json(project_path, project)
        bundle = self.project_dir / "shots" / "shot_01" / "v001"
        bundle.mkdir(parents=True, exist_ok=True)
        (bundle / "video.mp4").write_bytes(b"existing-video")
        write_json(
            bundle / "prompt.json",
            {
                "prompt_version": 2,
                "prompt_source": "ai_revision",
                "prompt_text": "approved active prompt",
            },
        )
        write_json(
            bundle / "generation.json",
            {"video_version": 1, "prompt_version": 2, "status": "APPROVED"},
        )
        write_json(bundle / "review.json", {"review_result": "APPROVED"})
        # Establish the same normalized checkpoint baseline a real resumed project has.
        from project_manager import create_project_paths
        from project_state import ProjectCheckpoint

        ProjectCheckpoint.load(
            create_project_paths(self.project_dir, ensure_directories=False)
        )

    def _create_draft(self) -> None:
        with patch(
            "web_backend.services.prompt_revision.generate_prompt_revision_draft",
            return_value=self._result(),
        ):
            response = self.client.post(
                "/api/projects/project-a/shots/shot_01/prompt/revision/draft",
                json={"feedback": phase3d3cb.FEEDBACK},
            )
            self.assertEqual(response.status_code, 202, response.text)
            task = self.wait_terminal(response.json()["task_id"])
        self.assertEqual(task.status.value, "SUCCEEDED")

    def _adopt(self):
        return self.client.post(
            "/api/projects/project-a/shots/shot_01/prompt/revision/draft/adopt"
        )

    def _draft_path(self):
        return (
            self.runtime_root
            / "prompt_revision_drafts"
            / "project-a"
            / "shot_01.json"
        )

    def _convert_draft_to_legacy_b1_schema(self) -> None:
        path = self._draft_path()
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["fingerprint_schema_version"], 2)
        payload["base_fingerprint"] = payload.pop("content_fingerprint")
        payload.pop("state_fingerprint")
        payload.pop("fingerprint_schema_version")
        write_json(path, payload)

    def test_01_adopt_uses_shared_core_and_creates_one_immutable_ai_revision(self):
        self._prepare_adoptable()
        self._create_draft()
        project_path = self.project_dir / "project.json"
        before = json.loads(project_path.read_text(encoding="utf-8"))
        before_entry = before["video_generation"]["shots"]["1"]
        original_v2 = copy.deepcopy(before_entry["prompt_versions"][0])
        generation_snapshot = copy.deepcopy(before_entry["generation_versions"])
        bundle = self.project_dir / "shots" / "shot_01" / "v001"
        bundle_snapshot = tree_snapshot(bundle)
        task_count = len(
            self.application.state.task_repository.list_for_project("project-a")
        )

        from shot_review import adopt_prompt_revision_draft as shared_callable

        with (
            patch(
                "web_backend.services.prompt_revision.adopt_prompt_revision_draft",
                wraps=shared_callable,
            ) as shared,
            patch(
                "web_backend.services.prompt_revision.generate_prompt_revision_draft",
                side_effect=AssertionError("DeepSeek draft generation called"),
            ) as deepseek,
        ):
            response = self._adopt()

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["prompt_version"], 3)
        self.assertEqual(response.json()["parent_version"], 2)
        self.assertEqual(response.json()["source"], "ai_revision")
        self.assertEqual(response.json()["active_prompt_version"], 3)
        self.assertEqual(response.json()["approved_prompt_version"], 2)
        shared.assert_called_once()
        deepseek.assert_not_called()

        after = json.loads(project_path.read_text(encoding="utf-8"))
        entry = after["video_generation"]["shots"]["1"]
        self.assertEqual([item["version"] for item in entry["prompt_versions"]], [2, 3])
        self.assertEqual(entry["prompt_versions"][0], original_v2)
        revised = entry["prompt_versions"][1]
        self.assertEqual(revised["parent_version"], 2)
        self.assertEqual(revised["source"], "ai_revision")
        self.assertEqual(revised["user_feedback"], phase3d3cb.FEEDBACK)
        self.assertEqual(
            revised["revision_metadata"]["kind"],
            "ai_prompt_revision_draft_adoption",
        )
        self.assertTrue(revised["diff_metadata"]["changed"])
        self.assertEqual(revised["diff_metadata"]["base_prompt_version"], 2)
        self.assertEqual(entry["active_prompt_version"], 3)
        self.assertEqual(entry["approved_prompt_version"], 2)
        self.assertEqual(entry["generation_count"], 1)
        self.assertEqual(entry["generation_versions"], generation_snapshot)
        self.assertEqual(tree_snapshot(bundle), bundle_snapshot)
        self.assertEqual(
            len(self.application.state.task_repository.list_for_project("project-a")),
            task_count,
        )

    def test_02_rapid_duplicate_adopt_creates_only_one_new_version(self):
        self._prepare_adoptable()
        self._create_draft()
        first = self._adopt()
        second = self._adopt()
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 409, second.text)
        self.assertEqual(second.json()["error"]["code"], "ACTION_NOT_ALLOWED")
        project = json.loads(
            (self.project_dir / "project.json").read_text(encoding="utf-8")
        )
        versions = project["video_generation"]["shots"]["1"]["prompt_versions"]
        self.assertEqual([item["version"] for item in versions], [2, 3])

    def test_03_missing_draft_is_rejected_without_prompt_task_or_provider(self):
        self._prepare_adoptable()
        before_project = (self.project_dir / "project.json").read_bytes()
        with patch(
            "web_backend.services.prompt_revision.generate_prompt_revision_draft",
            side_effect=AssertionError("DeepSeek called"),
        ) as provider:
            response = self._adopt()
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json()["error"]["code"],
            "PROMPT_REVISION_DRAFT_NOT_FOUND",
        )
        provider.assert_not_called()
        self.assertEqual(
            (self.project_dir / "project.json").read_bytes(),
            before_project,
        )
        self.assertFalse(self.runtime_root.exists())

    def test_04_base_prompt_change_makes_draft_stale_without_new_version(self):
        self._prepare_adoptable()
        self._create_draft()
        project_path = self.project_dir / "project.json"
        project = json.loads(project_path.read_text(encoding="utf-8"))
        entry = project["video_generation"]["shots"]["1"]
        entry["prompt_versions"].append(
            {
                "shot_id": 1,
                "version": 3,
                "prompt": "another current prompt",
                "source": "manual_edit",
            }
        )
        entry["active_prompt_version"] = 3
        entry["prompt_version_count"] = 3
        write_json(project_path, project)

        response = self._adopt()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "ACTION_NOT_ALLOWED")
        after = json.loads(project_path.read_text(encoding="utf-8"))
        self.assertEqual(
            [
                item["version"]
                for item in after["video_generation"]["shots"]["1"]["prompt_versions"]
            ],
            [2, 3],
        )

    def test_05_safe_shot_state_change_is_viewable_but_adopt_is_strictly_rejected(self):
        self._prepare_adoptable()
        self._create_draft()
        project_path = self.project_dir / "project.json"
        project = json.loads(project_path.read_text(encoding="utf-8"))
        entry = project["video_generation"]["shots"]["1"]
        entry["status"] = "WAITING_REVIEW"
        write_json(project_path, project)
        before = project_path.read_bytes()

        readable = self.client.get(
            "/api/projects/project-a/shots/shot_01/prompt/revision/draft"
        )
        self.assertEqual(readable.status_code, 200, readable.text)
        response = self._adopt()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "ACTION_NOT_ALLOWED")
        self.assertEqual(project_path.read_bytes(), before)

    def test_06_openapi_adopt_is_synchronous_200_and_not_a_task(self):
        schema = self.client.get("/openapi.json").json()
        path = "/api/projects/{project_id}/shots/{shot_id}/prompt/revision/draft/adopt"
        self.assertEqual(set(schema["paths"][path]), {"post"})
        self.assertIn("200", schema["paths"][path]["post"]["responses"])
        self.assertNotIn("202", schema["paths"][path]["post"]["responses"])

    def test_07_new_draft_persists_versioned_content_and_state_fingerprints(self):
        self._prepare_adoptable()
        self._create_draft()
        payload = json.loads(self._draft_path().read_text(encoding="utf-8"))
        self.assertEqual(payload["fingerprint_schema_version"], 2)
        self.assertRegex(payload["content_fingerprint"], r"^[0-9a-f]{64}$")
        self.assertRegex(payload["state_fingerprint"], r"^[0-9a-f]{64}$")
        self.assertNotIn("base_fingerprint", payload)

    def test_08_legacy_b1_content_fingerprint_draft_remains_readable(self):
        self._prepare_adoptable()
        self._create_draft()
        self._convert_draft_to_legacy_b1_schema()

        response = self.client.get(
            "/api/projects/project-a/shots/shot_01/prompt/revision/draft"
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["base_prompt_version"], 2)
        self.assertTrue(response.json()["original_prompt"])
        self.assertTrue(response.json()["draft_prompt"])

    def test_09_legacy_b1_draft_can_adopt_without_another_provider_call(self):
        self._prepare_adoptable()
        self._create_draft()
        self._convert_draft_to_legacy_b1_schema()
        readable = self.client.get(
            "/api/projects/project-a/shots/shot_01/prompt/revision/draft"
        )
        self.assertEqual(readable.status_code, 200, readable.text)

        with patch(
            "web_backend.services.prompt_revision.generate_prompt_revision_draft",
            side_effect=AssertionError("DeepSeek called during legacy adoption"),
        ) as provider:
            adopted = self._adopt()
        self.assertEqual(adopted.status_code, 200, adopted.text)
        self.assertEqual(adopted.json()["prompt_version"], 3)
        self.assertEqual(adopted.json()["parent_version"], 2)
        provider.assert_not_called()
        project = json.loads(
            (self.project_dir / "project.json").read_text(encoding="utf-8")
        )
        versions = project["video_generation"]["shots"]["1"]["prompt_versions"]
        self.assertEqual([item["version"] for item in versions], [2, 3])


if __name__ == "__main__":
    unittest.main()
