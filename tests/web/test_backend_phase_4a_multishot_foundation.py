from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from tests.web.test_backend_phase_1b_projects import tree_snapshot
from tests.web.test_backend_phase_2d4_shots import project_payload, write_json
from tests.web.web_response_assertions import assert_public_payload


def shot_checkpoint(
    shot_id: int,
    status: str,
    *,
    official: int | None = None,
    pending: int | None = None,
) -> dict:
    prompt_version = 1
    generation_versions = []
    if official is not None:
        generation_versions.append(
            {
                "video_version": official,
                "prompt_version": prompt_version,
                "status": "APPROVED",
            }
        )
    if pending is not None:
        generation_versions.append(
            {
                "video_version": pending,
                "prompt_version": prompt_version,
                "status": "WAITING_REVIEW",
            }
        )
    return {
        "shot_id": shot_id,
        "status": status,
        "generation_count": len(generation_versions),
        "active_prompt_version": prompt_version,
        "approved_prompt_version": prompt_version if official is not None else None,
        "active_video_version": pending or official,
        "approved_video_version": official,
        "prompt_versions": [
            {
                "version": prompt_version,
                "source": "ai_generated",
                "prompt": f"prompt {shot_id}",
            }
        ],
        "generation_versions": generation_versions,
        "candidate": {
            "status": "WAITING_REVIEW" if pending is not None else "NONE",
            "video_version": pending,
            "prompt_version": prompt_version if pending is not None else None,
        },
    }


class WebBackendPhase4AMultiShotFoundationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.projects_root = Path(self.temp.name) / "projects"
        from web_backend.app import create_app
        from web_backend.settings import BackendSettings

        self.client = TestClient(
            create_app(settings=BackendSettings(projects_root=self.projects_root)),
            raise_server_exceptions=False,
        )
        self.addCleanup(self.client.close)

    def write_project(
        self,
        project_id: str,
        checkpoints: list[dict],
        *,
        storyboard: list[dict] | None = None,
        prompts: list[int] | None = None,
    ) -> Path:
        project_dir = self.projects_root / project_id
        payload = project_payload(project_id)
        payload["video_generation"]["shots"] = {
            str(item["shot_id"]): item for item in checkpoints
        }
        write_json(project_dir / "project.json", payload)
        if storyboard is not None:
            write_json(
                project_dir / "storyboard" / "storyboard.json",
                {"total_duration": len(storyboard) * 6, "shots": storyboard},
            )
        if prompts is not None:
            write_json(
                project_dir / "storyboard" / "video_prompts.json",
                {
                    "shots": [
                        {
                            "shot_id": number,
                            "visual_prompt_core": f"core {number}",
                            "video_prompt": f"prompt {number}",
                        }
                        for number in prompts
                    ]
                },
            )
        return project_dir

    @staticmethod
    def storyboard_shots() -> list[dict]:
        return [
            {"shot_id": 1, "purpose": "建立产品", "visual": "visual 1"},
            {"shot_id": 2, "purpose": "展示卖点", "visual": "visual 2"},
            {"shot_id": 3, "purpose": "品牌收束", "visual": "visual 3"},
        ]

    def get_shots(self, project_id: str):
        return self.client.get(f"/api/projects/{project_id}/shots")

    def test_01_legacy_single_shot_project_is_compatible(self):
        self.write_project(
            "legacy-single",
            [shot_checkpoint(1, "APPROVED", official=1)],
        )
        response = self.get_shots("legacy-single")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "COMPLETED")
        self.assertEqual(payload["aggregation"]["total"], 1)
        self.assertEqual(
            payload["shots"][0],
            {
                "shot_id": "shot_01",
                "order": 1,
                "title": "Shot 01",
                "status": "APPROVED",
                "prompt_status": "READY",
                "video_status": "READY",
                "review_status": "APPROVED",
                "official_version": 1,
                "pending_review_version": None,
                "version_count": 1,
                "generation_count": 1,
            },
        )

    def test_02_storyboard_is_the_complete_multi_shot_collection(self):
        project_dir = self.write_project(
            "multi-read",
            [
                shot_checkpoint(2, "NOT_STARTED"),
                shot_checkpoint(1, "APPROVED", official=1),
            ],
            storyboard=self.storyboard_shots(),
            prompts=[1, 2, 3],
        )
        before = tree_snapshot(project_dir)
        response = self.get_shots("multi-read")
        self.assertEqual(response.status_code, 200, response.text)
        shots = response.json()["shots"]
        self.assertEqual(
            [item["shot_id"] for item in shots],
            ["shot_01", "shot_02", "shot_03"],
        )
        self.assertEqual([item["order"] for item in shots], [1, 2, 3])
        self.assertEqual(
            [item["title"] for item in shots],
            ["建立产品", "展示卖点", "品牌收束"],
        )
        self.assertEqual(shots[2]["status"], "NOT_STARTED")
        self.assertEqual(shots[2]["prompt_status"], "READY")
        planned_detail = self.client.get(
            "/api/projects/multi-read/shots/shot_03"
        )
        self.assertEqual(planned_detail.status_code, 200, planned_detail.text)
        self.assertEqual(planned_detail.json()["shot_id"], "shot_03")
        self.assertEqual(tree_snapshot(project_dir), before)

    def test_03_order_is_stable_and_independent_of_checkpoint_mapping_order(self):
        self.write_project(
            "stable-order",
            [
                shot_checkpoint(3, "NOT_STARTED"),
                shot_checkpoint(1, "NOT_STARTED"),
                shot_checkpoint(2, "NOT_STARTED"),
            ],
            storyboard=self.storyboard_shots(),
        )
        first = self.get_shots("stable-order").json()["shots"]
        second = self.get_shots("stable-order").json()["shots"]
        expected = [("shot_01", 1), ("shot_02", 2), ("shot_03", 3)]
        self.assertEqual(
            [(item["shot_id"], item["order"]) for item in first], expected
        )
        self.assertEqual(first, second)

    def test_04_project_status_aggregation_uses_effective_shot_state(self):
        self.write_project(
            "aggregate",
            [
                shot_checkpoint(1, "APPROVED", official=1),
                shot_checkpoint(2, "APPROVED", official=1, pending=2),
                shot_checkpoint(3, "GENERATING"),
            ],
            storyboard=self.storyboard_shots(),
            prompts=[1, 2, 3],
        )
        payload = self.get_shots("aggregate").json()
        self.assertEqual(payload["status"], "GENERATING")
        self.assertEqual(
            payload["aggregation"],
            {
                "total": 3,
                "approved": 1,
                "waiting_review": 1,
                "generating": 1,
                "not_started": 0,
                "failed": 0,
            },
        )
        self.assertEqual(
            [item["status"] for item in payload["shots"]],
            ["APPROVED", "WAITING_REVIEW", "GENERATING"],
        )

    def test_05_missing_shot_returns_the_existing_safe_not_found_error(self):
        self.write_project(
            "missing-shot",
            [shot_checkpoint(1, "NOT_STARTED")],
        )
        response = self.client.get(
            "/api/projects/missing-shot/shots/shot_02"
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "SHOT_NOT_FOUND")

    def test_06_collection_dto_is_path_and_provider_safe(self):
        checkpoint = shot_checkpoint(1, "APPROVED", official=1)
        checkpoint.update(
            provider_task_id="provider-secret",
            file_id="file-secret",
            credential_env_name="MINIMAX_API_KEY",
            video_path=r"D:\private\video.mp4",
        )
        self.write_project(
            "safe-dto",
            [checkpoint],
            storyboard=[
                {
                    "shot_id": 1,
                    "purpose": r"D:\private\API_KEY.txt",
                    "visual": "visual",
                }
            ],
        )
        response = self.get_shots("safe-dto")
        self.assertEqual(response.status_code, 200, response.text)
        assert_public_payload(self, response.json())
        rendered = json.dumps(response.json(), ensure_ascii=False).lower()
        for forbidden in (
            "provider_task_id",
            "file_id",
            "credential_env_name",
            "provider-secret",
            "d:\\private",
        ):
            self.assertNotIn(forbidden, rendered)


if __name__ == "__main__":
    unittest.main()
