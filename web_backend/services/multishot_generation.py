"""Project-level planning over independent existing Shot generation tasks."""

from __future__ import annotations

from web_backend.models.generation import (
    GenerationIntent,
    GenerationPreflightRequest,
    GenerationStartRequest,
    GenerationVisualInputRequest,
    GenerationVisualInputMode,
    ModelSelectionMode,
)
from web_backend.models.multishot_generation import (
    MultiShotGenerationAggregation,
    MultiShotGenerationOption,
    MultiShotGenerationOptionsResponse,
    MultiShotGenerationPlanItem,
    MultiShotGenerationPlanResponse,
    MultiShotGenerationStartRequest,
    MultiShotPlanStatus,
)
from web_backend.models.tasks import TaskOperation, TaskRecord, TaskStatus
from web_backend.repositories.shot_repository import (
    ShotNotFound,
    ShotRepository,
    normalize_shot_id,
)
from web_backend.services.shot_generation import (
    PaidCallConfirmationRequired,
    ShotGenerationActionService,
)
from web_backend.services.shot_generation_preflight import (
    ShotGenerationPreflightService,
)
from web_backend.services.tasks import TaskService


class MultiShotGenerationNotAllowed(RuntimeError):
    pass


_GENERATION_OPERATIONS = {
    TaskOperation.SHOT_GENERATE,
    TaskOperation.SHOT_REGENERATE,
    TaskOperation.SHOT_PROMPT_VERSION_GENERATE,
    TaskOperation.SHOT_RESUME,
}


class MultiShotGenerationService:
    """Build a batch plan while keeping execution and recovery Shot-scoped."""

    def __init__(
        self,
        shot_repository: ShotRepository,
        preflight_service: ShotGenerationPreflightService,
        action_service: ShotGenerationActionService,
        task_service: TaskService,
        *,
        max_parallel: int = 2,
    ) -> None:
        self._shot_repository = shot_repository
        self._preflight_service = preflight_service
        self._action_service = action_service
        self._task_service = task_service
        self._max_parallel = max(1, int(max_parallel))

    @staticmethod
    def _preflight_payload() -> GenerationPreflightRequest:
        return GenerationPreflightRequest(
            intent=GenerationIntent.INITIAL,
            model_selection=ModelSelectionMode.AUTO,
            requested_model=None,
            visual_input=GenerationVisualInputRequest(
                mode=GenerationVisualInputMode.NONE,
                asset_ids=[],
            ),
        )

    def options(self, project_id: str) -> MultiShotGenerationOptionsResponse:
        collection = self._shot_repository.list_shots(project_id)
        tasks = self._task_service.list_for_project(collection.project_id).tasks
        project_has_active_task = any(
            task.status in {TaskStatus.QUEUED, TaskStatus.RUNNING}
            for task in tasks
        )
        latest_by_target: dict[str, TaskRecord] = {}
        for task in tasks:
            if (
                task.operation in _GENERATION_OPERATIONS
                and task.target_id is not None
                and task.target_id not in latest_by_target
            ):
                latest_by_target[task.target_id] = task

        options: list[MultiShotGenerationOption] = []
        for shot in collection.shots:
            task = latest_by_target.get(shot.shot_id)
            if task is not None and task.status in {TaskStatus.QUEUED, TaskStatus.RUNNING}:
                status = task.status.value
                available = False
            elif (
                task is not None
                and task.operation is TaskOperation.SHOT_GENERATE
                and task.status in {TaskStatus.FAILED, TaskStatus.INTERRUPTED}
                and shot.status not in {"WAITING_REVIEW", "APPROVED"}
            ):
                status = "FAILED"
                available = False
            else:
                status = shot.status
                available = False
                if (
                    not project_has_active_task
                    and shot.status == "NOT_STARTED"
                    and shot.prompt_status == "READY"
                ):
                    try:
                        checked = self._preflight_service.preflight(
                            collection.project_id,
                            shot.shot_id,
                            self._preflight_payload(),
                        )
                    except ShotNotFound:
                        # Phase 4A can project a Storyboard-only legacy Shot.
                        # It remains visible but is not generation-ready until
                        # Core has established its checkpoint state.
                        checked = None
                    available = bool(
                        checked is not None
                        and checked.ready
                        and checked.preflight_fingerprint is not None
                    )
                    if available:
                        status = "READY"
            options.append(
                MultiShotGenerationOption(
                    shot_id=shot.shot_id,
                    order=shot.order,
                    title=shot.title,
                    status=status,
                    prompt_ready=shot.prompt_status == "READY",
                    video_status=shot.video_status,
                    available=available,
                )
            )

        aggregation = self._aggregate(options)
        return MultiShotGenerationOptionsResponse(
            project_id=collection.project_id,
            status=self._status(aggregation),
            max_parallel=self._max_parallel,
            aggregation=aggregation,
            shots=options,
        )

    def start(
        self,
        project_id: str,
        payload: MultiShotGenerationStartRequest,
        *,
        correlation_id: str | None,
    ) -> MultiShotGenerationPlanResponse:
        if not payload.confirm_paid_call:
            raise PaidCallConfirmationRequired("paid calls were not confirmed")
        current = self.options(project_id)
        by_id = {item.shot_id: item for item in current.shots}
        selected: list[str] = []
        for raw_shot_id in payload.shots:
            shot_id, _number = normalize_shot_id(raw_shot_id)
            option = by_id.get(shot_id)
            if option is None or not option.available:
                raise MultiShotGenerationNotAllowed(
                    "one or more selected Shots are not generation-ready"
                )
            selected.append(shot_id)

        prepared: list[tuple[str, GenerationStartRequest]] = []
        for shot_id in selected:
            preflight_payload = self._preflight_payload()
            checked = self._preflight_service.preflight(
                current.project_id,
                shot_id,
                preflight_payload,
            )
            if not checked.ready or checked.preflight_fingerprint is None:
                raise MultiShotGenerationNotAllowed("generation plan became stale")
            prepared.append(
                (
                    shot_id,
                    GenerationStartRequest(
                        **preflight_payload.model_dump(),
                        preflight_fingerprint=checked.preflight_fingerprint,
                        confirm_paid_call=True,
                    ),
                )
            )

        tasks = self._action_service.submit_batch_starts(
            current.project_id,
            prepared,
            correlation_id=correlation_id,
        )
        after = self.options(current.project_id)
        return MultiShotGenerationPlanResponse(
            project_id=current.project_id,
            status=after.status,
            max_parallel=self._max_parallel,
            shots=[
                MultiShotGenerationPlanItem(
                    shot_id=str(task.target_id),
                    task_id=task.task_id,
                    operation=task.operation.value,
                    status=task.status,
                )
                for task in tasks
            ],
            aggregation=after.aggregation,
        )

    @staticmethod
    def _aggregate(
        shots: list[MultiShotGenerationOption],
    ) -> MultiShotGenerationAggregation:
        counts = {
            "QUEUED": 0,
            "RUNNING": 0,
            "WAITING_REVIEW": 0,
            "APPROVED": 0,
            "FAILED": 0,
            "NOT_STARTED": 0,
        }
        for shot in shots:
            key = shot.status if shot.status in counts else "NOT_STARTED"
            counts[key] += 1
        return MultiShotGenerationAggregation(
            total=len(shots),
            queued=counts["QUEUED"],
            running=counts["RUNNING"],
            waiting_review=counts["WAITING_REVIEW"],
            approved=counts["APPROVED"],
            failed=counts["FAILED"],
            not_started=counts["NOT_STARTED"],
        )

    @staticmethod
    def _status(aggregation: MultiShotGenerationAggregation) -> MultiShotPlanStatus:
        if aggregation.total == 0:
            return MultiShotPlanStatus.NOT_STARTED
        progressed = (
            aggregation.waiting_review + aggregation.approved + aggregation.failed
        )
        active = aggregation.queued + aggregation.running
        if active and progressed:
            return MultiShotPlanStatus.PARTIAL_PROGRESS
        if active:
            return MultiShotPlanStatus.IN_PROGRESS
        if aggregation.failed and (
            aggregation.approved or aggregation.waiting_review or aggregation.not_started
        ):
            return MultiShotPlanStatus.PARTIAL_PROGRESS
        if aggregation.approved == aggregation.total:
            return MultiShotPlanStatus.COMPLETED
        if aggregation.waiting_review and not aggregation.not_started:
            return MultiShotPlanStatus.WAITING_REVIEW
        if any(shot_count for shot_count in (aggregation.not_started, aggregation.failed)):
            return MultiShotPlanStatus.READY
        return MultiShotPlanStatus.PARTIAL_PROGRESS
