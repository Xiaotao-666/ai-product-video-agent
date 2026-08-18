"""One-off local mock probe for the migrated Durian copy. No external API."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main
from project_manager import create_project_paths
from project_state import ProjectCheckpoint
from prompt_generator import PromptSafetyReview, ProductVideoRequest
from task_logger import TaskLogger


class LocalMiniMax:
    def __init__(self) -> None:
        self.shot_ids: list[int] = []

    def __call__(self, **kwargs) -> Path:
        shot_id = int(kwargs["shot_id"])
        self.shot_ids.append(shot_id)
        if shot_id == 1:
            raise AssertionError("Resume 不得重新调用已生成的 Shot 01")
        kwargs["on_submitted"]("mock-shot2-task")
        kwargs["on_task_updated"]("mock-shot2-file")
        output = kwargs["output_path"]
        output.write_bytes(b"local-mock-shot-02")
        return output


def safety(prompt: str, *args, **kwargs) -> PromptSafetyReview:
    return PromptSafetyReview(
        is_safe=True, risk_notes=[], reviewed_video_prompt=f"SAFE::{prompt}"
    )


def run(root: Path) -> dict:
    paths = create_project_paths(root)
    checkpoint = ProjectCheckpoint.load(paths)
    request = ProductVideoRequest.model_validate(checkpoint.data["request"])
    mock = LocalMiniMax()
    with (
        patch.object(main, "generate_video", side_effect=mock),
        patch.object(main, "review_prompt_safety", side_effect=safety),
        patch("builtins.input", side_effect=["1", "1"]),
    ):
        main.run_pipeline(
            paths,
            request,
            checkpoint,
            "deepseek-mock",
            "minimax-mock",
            TaskLogger(paths, "durian-resume-mock"),
        )
    return {
        "mock_api_shot_ids": mock.shot_ids,
        "shot_01_status": checkpoint.shot_status(1).value,
        "shot_01_approved_version": checkpoint.shot_checkpoint(1).get(
            "approved_video_version"
        ),
        "shot_02_status": checkpoint.shot_status(2).value,
        "shot_02_approved_version": checkpoint.shot_checkpoint(2).get(
            "approved_video_version"
        ),
        "shot_02_video": str(paths.shot_version_video_path(2, 1)),
        "all_approved": checkpoint.all_shots_approved([1, 2]),
    }


if __name__ == "__main__":
    print(json.dumps(run(Path(sys.argv[1])), ensure_ascii=False, indent=2))
