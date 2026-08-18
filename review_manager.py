"""Command-line human approval gates and durable review records."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable, TypeVar
from pydantic import BaseModel

from task_state import TaskState, TaskStateManager
from task_logger import TaskLogger
from project_manager import ProjectPaths


T = TypeVar("T", bound=BaseModel)


class TaskCancelled(Exception):
    """Normal control-flow signal raised when a user cancels a review gate."""

    def __init__(self, cancel_stage: str) -> None:
        super().__init__(cancel_stage)
        self.cancel_stage = cancel_stage


class ReviewRecorder:
    def __init__(
        self,
        project: ProjectPaths,
        request_data: dict[str, Any],
        task_id: str,
        task_logger: TaskLogger | None = None,
        initial_state: TaskState = TaskState.PENDING,
    ) -> None:
        now = datetime.now()
        self.project = project
        self.task_id = task_id
        self.task_logger = task_logger
        self.path = project.review_file_path(task_id)
        self.state_manager = TaskStateManager(initial_state)
        self.data: dict[str, Any] = {
            "task_id": self.task_id,
            "cancel_stage": "",
            "timestamp": now.isoformat(timespec="seconds"),
            "user_action": "",
            "status": initial_state.value,
            "request": request_data,
            "stages": [],
        }
        self.save()

    def transition(self, state: TaskState) -> None:
        self.state_manager.transition(state)
        self.data["status"] = state.value
        self.data.setdefault("state_history", []).append(
            {
                "state": state.value,
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            }
        )
        self.save()

    def add_stage(self, stage_name: str, original_output: dict[str, Any]) -> int:
        self.transition(TaskState.WAITING_REVIEW)
        self.data["stages"].append(
            {
                "stage": stage_name,
                "original_ai_output": original_output,
                "interactions": [],
                "user_modification_comments": [],
                "final_confirmed_version": None,
            }
        )
        self.save()
        if self.task_logger:
            self.task_logger.set_stage(stage_name)
            self.task_logger.event(
                "WAITING_REVIEW", f"等待 {stage_name} 人工审核", stage=stage_name
            )
        return len(self.data["stages"]) - 1

    def record_interaction(
        self,
        stage_index: int,
        action: str,
        output: dict[str, Any],
        comment: str | None = None,
    ) -> None:
        stage = self.data["stages"][stage_index]
        stage["interactions"].append(
            {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "action": action,
                "user_comment": comment,
                "ai_output": output,
            }
        )
        if comment:
            stage["user_modification_comments"].append(comment)
        self.save()

    def confirm(self, stage_index: int, output: dict[str, Any]) -> None:
        self.record_interaction(stage_index, "confirm", output)
        self.data["stages"][stage_index]["final_confirmed_version"] = output
        self.data["user_action"] = "approve"
        self.transition(TaskState.APPROVED)
        if self.task_logger:
            stage = self.data["stages"][stage_index]["stage"]
            self.task_logger.review_action(stage, "approve")
            self.task_logger.event("APPROVED", f"用户确认 {stage}", stage=stage)

    def begin_revision(self) -> None:
        self.transition(TaskState.REVISING)

    def finish_revision(self) -> None:
        self.transition(TaskState.WAITING_REVIEW)
        if self.task_logger:
            self.task_logger.event(
                "WAITING_REVIEW",
                "等待修改或重新生成后的方案人工审核",
                stage=self.task_logger.current_stage,
            )

    def cancel(self, stage_index: int, cancel_stage: str, output: dict[str, Any]) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        self.record_interaction(stage_index, "cancel", output)
        self.data.update(
            {
                "cancel_stage": cancel_stage,
                "timestamp": now,
                "user_action": "cancel",
            }
        )
        self.transition(TaskState.CANCELLED)
        if self.task_logger:
            self.task_logger.review_action(cancel_stage, "cancel")
            self.task_logger.event("TASK_CANCELLED", stage=cancel_stage)

    def start_generating(self) -> None:
        self.transition(TaskState.GENERATING)

    def complete(self) -> None:
        self.transition(TaskState.COMPLETED)
        self.data["completed_at"] = datetime.now().isoformat(timespec="seconds")
        self.save()

    def record_shot_action(
        self,
        shot_id: int,
        action: str,
        feedback: str | None = None,
        **fields: Any,
    ) -> None:
        self.data.setdefault("shot_reviews", []).append(
            {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "shot_id": int(shot_id),
                "action": action,
                "feedback": feedback,
                **fields,
            }
        )
        self.save()
        if self.task_logger:
            self.task_logger.review_action(
                f"Shot {int(shot_id):02d}审核", action, feedback
            )

    def cancel_shot(self, shot_id: int, cancel_stage: str) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        self.record_shot_action(shot_id, "cancel")
        self.data.update(
            {
                "cancel_stage": cancel_stage,
                "cancel_shot_id": int(shot_id),
                "timestamp": now,
                "user_action": "cancel",
            }
        )
        self.transition(TaskState.CANCELLED)
        if self.task_logger:
            self.task_logger.event(
                "TASK_CANCELLED", stage=cancel_stage, shot_id=int(shot_id)
            )

    def save(self) -> None:
        self.project.save_json(self.path, self.data)


def display_output(title: str, output: BaseModel) -> None:
    print(f"\n========== {title} ==========")
    review_formatter = getattr(output, "to_review_text", None)
    if callable(review_formatter):
        print(review_formatter())
    else:
        print(json.dumps(output.model_dump(), ensure_ascii=False, indent=2))
    print("=" * (22 + len(title)))


def print_cancelled(cancel_stage: str) -> None:
    print("\n==========任务已取消==========")
    print("取消节点：")
    print(cancel_stage)
    print("\n任务结束。")
    print("============================")


def human_review_gate(
    title: str,
    stage_name: str,
    cancel_stage: str,
    initial_output: T,
    recorder: ReviewRecorder,
    revise: Callable[[T, str], T],
    regenerate: Callable[[], T],
    persist: Callable[[T], None] | None = None,
    on_waiting: Callable[[], None] | None = None,
    on_approved: Callable[[], None] | None = None,
    on_cancel: Callable[[], None] | None = None,
) -> T:
    if persist:
        persist(initial_output)
    if on_waiting:
        on_waiting()
    stage_index = recorder.add_stage(stage_name, initial_output.model_dump())
    current = initial_output
    while True:
        display_output(title, current)
        print("\n请选择：")
        print("1. 确认，继续下一步")
        print("2. 修改，根据意见重新生成")
        print("3. 重新生成当前方案")
        print("4. 取消，终止本次视频生成任务")
        choice = input("请输入 1、2、3 或 4: ").strip()
        if choice == "1":
            recorder.confirm(stage_index, current.model_dump())
            if persist:
                persist(current)
            if on_approved:
                on_approved()
            return current
        if choice == "2":
            comment = input("请输入修改意见: ").strip()
            if not comment:
                print("修改意见不能为空。")
                continue
            if recorder.task_logger:
                recorder.task_logger.review_action(stage_name, "revise", comment)
            recorder.begin_revision()
            current = revise(current, comment)
            if persist:
                persist(current)
            recorder.record_interaction(
                stage_index, "revise", current.model_dump(), comment
            )
            recorder.finish_revision()
            continue
        if choice == "3":
            if recorder.task_logger:
                recorder.task_logger.review_action(stage_name, "regenerate")
            recorder.begin_revision()
            current = regenerate()
            if persist:
                persist(current)
            recorder.record_interaction(
                stage_index, "regenerate", current.model_dump()
            )
            recorder.finish_revision()
            continue
        if choice == "4":
            recorder.cancel(stage_index, cancel_stage, current.model_dump())
            if persist:
                persist(current)
            if on_cancel:
                on_cancel()
            raise TaskCancelled(cancel_stage)
        print("无效选择，请输入 1、2、3 或 4。")
