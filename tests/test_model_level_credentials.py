from __future__ import annotations

import json
import os
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

import requests

from project_manager import create_project_paths
from project_state import ProjectCheckpoint
from prompt_generator import ProductVideoRequest
from providers.minimax_common import request_json
from providers.minimax_h3_provider import MiniMaxH3Provider
from providers.minimax_hailuo_provider import MiniMaxHailuoProvider
from reference_assets import ReferenceAssetManager
from shot_review import create_prompt_version
from shot_storage import read_bundle_json
from storyboard import ShotVideoPrompt, VideoPromptPlan
from task_logger import TaskLogger
from video_generation_request import ProviderSelection, VideoGenerationRequest
from video_generator import generate_video
from video_history import video_version_history
from video_model_selection import choose_and_confirm_video_generation
from video_provider import (
    DownloadResult,
    ProviderCapabilities,
    ProviderErrorCode,
    ProviderTask,
    ProviderTaskStatus,
    VideoProvider,
    VideoProviderError,
)
from video_provider_registry import (
    VideoProviderRegistry,
    create_default_registry,
    load_provider_credentials_from_env,
)
from visual_input import (
    first_frame_visual_input,
    none_visual_input,
    reference_asset_visual_input,
)


def write_png(path: Path, width: int = 256, height: int = 256) -> None:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return (
            struct.pack(">I", len(payload))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    row = b"\x00" + bytes([240, 180, 20]) * width
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(row * height))
        + chunk(b"IEND", b"")
    )


class RecordingProvider(VideoProvider):
    def __init__(
        self,
        provider: str,
        model: str,
        modes: set[str],
        *,
        credential_env_name: str | None = None,
        credential_value: str | None = None,
    ) -> None:
        self.provider_name = provider
        self.model_name = model
        self.api_version = "v-test"
        self.capabilities = ProviderCapabilities(
            frozenset(modes),
            supported_resolutions=frozenset({"768P"}),
            min_duration=4,
            max_duration=15,
        )
        self.generation_mode_by_visual_mode = {
            mode: f"{model}-{mode}" for mode in modes
        }
        self.credential_env_name = credential_env_name
        self.credential_value = credential_value
        self.submit_calls = 0
        self.poll_calls = 0

    def submit(self, request, task_logger=None) -> ProviderTask:
        self.submit_calls += 1
        return ProviderTask(
            self.provider_name,
            self.model_name,
            self.api_version,
            self.generation_mode(request.required_capability),
            f"task-{self.model_name}-{self.submit_calls}",
        )

    def poll(self, task, task_logger=None) -> ProviderTask:
        self.poll_calls += 1
        return task.evolve(
            status=ProviderTaskStatus.COMPLETED,
            provider_file_id="file-local",
            raw_status="completed",
        )

    def download(self, task, output_path, request, task_logger=None) -> DownloadResult:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(f"{self.model_name}:{task.provider_task_id}".encode())
        return DownloadResult(output_path, output_path.stat().st_size)


class Fake400Response:
    status_code = 400
    text = json.dumps(
        {
            "type": "error",
            "error": {
                "type": "bad_request_error",
                "message": "TokenPlan 或 Credit 暂不支持 MiniMax-H3 系列模型 (2013)",
            },
            "request_id": "request-safe-123",
        },
        ensure_ascii=False,
    )

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        raise requests.HTTPError("400", response=self)


class ModelLevelCredentialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.paths = create_project_paths(Path(self.temp.name) / "project")
        source = Path(self.temp.name) / "ref.png"
        write_png(source)
        self.asset = ReferenceAssetManager(self.paths).import_image(source)
        self.reference = reference_asset_visual_input(self.asset)
        self.first_frame = first_frame_visual_input(self.asset)
        self.credentials = {
            "minimax": {
                "MiniMax-Hailuo-2.3": "hailuo-secret",
                "MiniMax-H3": "h3-secret",
            }
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def request(self, visual=None, selection=None) -> VideoGenerationRequest:
        return VideoGenerationRequest(
            shot_id=1,
            prompt="safe prompt",
            duration=6,
            resolution="768P",
            visual_input=visual or none_visual_input(),
            project=self.paths,
            provider_selection=selection,
        )

    def state(self) -> tuple[ProjectCheckpoint, VideoPromptPlan]:
        product = ProductVideoRequest(
            product_name="P",
            product_description="D",
            duration_seconds=6,
            video_style="S",
            video_purpose="U",
        )
        checkpoint = ProjectCheckpoint.create(
            self.paths, "P", product.model_dump()
        )
        checkpoint.ensure_shots([1])
        plan = VideoPromptPlan(
            shots=[ShotVideoPrompt(shot_id=1, video_prompt="prompt-v1")]
        )
        create_prompt_version(
            self.paths,
            checkpoint,
            plan,
            1,
            "prompt-v1",
            "ai_generated",
            TaskLogger(self.paths, "prompt"),
            parent_version=None,
        )
        return checkpoint, plan

    def test_A_none_auto_routes_hailuo(self):
        route = create_default_registry(self.credentials).preflight(self.request())
        self.assertEqual(route.adapter.model_name, "MiniMax-Hailuo-2.3")
        self.assertEqual(route.selection_mode, "auto")

    def test_B_first_frame_auto_routes_hailuo(self):
        route = create_default_registry(self.credentials).preflight(
            self.request(self.first_frame)
        )
        self.assertEqual(route.adapter.model_name, "MiniMax-Hailuo-2.3")

    def test_C_reference_asset_auto_routes_h3(self):
        route = create_default_registry(self.credentials).preflight(
            self.request(self.reference)
        )
        self.assertEqual(route.adapter.model_name, "MiniMax-H3")

    def test_D_hailuo_reads_original_env_slot(self):
        route = create_default_registry(self.credentials).preflight(self.request())
        self.assertEqual(route.credential_env_name, "MINIMAX_API_KEY")
        self.assertEqual(route.adapter.credential_value, "hailuo-secret")

    def test_E_h3_reads_dedicated_env_slot(self):
        route = create_default_registry(self.credentials).preflight(
            self.request(self.reference)
        )
        self.assertEqual(route.credential_env_name, "MINIMAX_H3_API_KEY")
        self.assertEqual(route.adapter.credential_value, "h3-secret")

    def test_F_missing_h3_key_blocks_before_http(self):
        registry = create_default_registry(
            {"minimax": {"MiniMax-Hailuo-2.3": "hailuo", "MiniMax-H3": ""}}
        )
        with patch("providers.minimax_common.requests.request") as http:
            with self.assertRaises(VideoProviderError) as raised:
                generate_video(
                    {},
                    "prompt",
                    self.paths,
                    output_path=self.paths.shot_version_video_path(1, 1),
                    visual_input=self.reference,
                    provider_registry=registry,
                )
        self.assertEqual(raised.exception.code, ProviderErrorCode.AUTH_ERROR)
        self.assertIn("MINIMAX_H3_API_KEY", str(raised.exception))
        http.assert_not_called()

    def test_G_h3_never_falls_back_to_hailuo_env(self):
        with patch.dict(
            os.environ,
            {"MINIMAX_API_KEY": "hailuo-only", "MINIMAX_H3_API_KEY": ""},
            clear=False,
        ):
            loaded = load_provider_credentials_from_env()
        self.assertEqual(
            loaded["minimax"]["MiniMax-Hailuo-2.3"], "hailuo-only"
        )
        self.assertEqual(loaded["minimax"]["MiniMax-H3"], "")

    def test_H_manual_h3_selection_is_recorded(self):
        outputs: list[str] = []
        answers = iter(["2", "2", "1"])
        decision = choose_and_confirm_video_generation(
            create_default_registry(self.credentials),
            self.request(self.reference),
            prompt_version=1,
            input_func=lambda _prompt: next(answers),
            output=outputs.append,
        )
        self.assertEqual(decision.action, "generate")
        self.assertEqual(decision.provider_selection.selection_mode, "manual")
        self.assertEqual(decision.metadata["provider_model"], "MiniMax-H3")

    def test_I_manual_incompatible_model_is_rejected(self):
        answers = iter(["2", "1", "2"])
        decision = choose_and_confirm_video_generation(
            create_default_registry(self.credentials),
            self.request(self.reference),
            prompt_version=1,
            input_func=lambda _prompt: next(answers),
            output=lambda _text: None,
        )
        self.assertEqual(decision.action, "change_visual")

    def test_J_auto_still_shows_final_confirmation(self):
        outputs: list[str] = []
        answers = iter(["1", "1"])
        decision = choose_and_confirm_video_generation(
            create_default_registry(self.credentials),
            self.request(),
            prompt_version=2,
            input_func=lambda _prompt: next(answers),
            output=outputs.append,
        )
        rendered = "\n".join(outputs)
        self.assertEqual(decision.metadata["selection_mode"], "auto")
        self.assertIn("视频生成确认", rendered)
        self.assertIn("MINIMAX_API_KEY", rendered)
        self.assertNotIn("hailuo-secret", rendered)

    def test_K_cancel_at_confirmation_has_zero_api_calls(self):
        answers = iter(["1", "4"])
        with patch("providers.minimax_common.requests.request") as http:
            decision = choose_and_confirm_video_generation(
                create_default_registry(self.credentials),
                self.request(),
                prompt_version=1,
                input_func=lambda _prompt: next(answers),
                output=lambda _text: None,
            )
        self.assertEqual(decision.action, "cancel")
        http.assert_not_called()

    def test_L_resume_locks_historical_model_and_task(self):
        old = RecordingProvider("old", "MiniMax-H3", {"reference_asset"})
        new = RecordingProvider("new", "hailuo-default", {"reference_asset"})
        registry = VideoProviderRegistry(
            {
                "default_models": {
                    "reference_asset": {
                        "provider": "new",
                        "model": "hailuo-default",
                    }
                }
            }
        )
        registry.register(old)
        registry.register(new)
        resume = ProviderTask(
            "old", "MiniMax-H3", "v-test", "reference", "historical-task"
        )
        generate_video(
            {},
            "prompt",
            self.paths,
            output_path=self.paths.shot_version_video_path(1, 1),
            visual_input=self.reference,
            provider_registry=registry,
            resume_task=resume,
        )
        self.assertEqual((old.submit_calls, old.poll_calls), (0, 1))
        self.assertEqual((new.submit_calls, new.poll_calls), (0, 0))

    def test_M_regeneration_can_select_another_compatible_model(self):
        registry = create_default_registry(self.credentials)
        future = RecordingProvider("future", "future-reference", {"reference_asset"})
        registry.register(future)
        answers = iter(["3", "3", "1"])
        decision = choose_and_confirm_video_generation(
            registry,
            self.request(self.reference),
            prompt_version=2,
            regeneration=True,
            previous_metadata={
                "provider": "minimax",
                "provider_model": "MiniMax-H3",
            },
            input_func=lambda _prompt: next(answers),
            output=lambda _text: None,
        )
        self.assertEqual(decision.provider_selection.model, "future-reference")

    def test_N_candidate_model_is_independent_from_approved(self):
        checkpoint, _plan = self.state()
        checkpoint.prepare_shot_generation(1)
        checkpoint.mark_shot_submitted(
            1,
            ProviderTask(
                "minimax", "MiniMax-Hailuo-2.3", "v1", "text", "approved-task"
            ),
        )
        video = self.paths.shot_version_video_path(1, 1)
        video.write_bytes(b"approved")
        checkpoint.mark_shot_ready_for_review(1)
        checkpoint.approve_shot(1)
        checkpoint.begin_candidate_editing(1, None)
        checkpoint.candidate_checkpoint(1)["prompt_version"] = 1
        checkpoint.save()
        checkpoint.prepare_candidate_generation(1)
        checkpoint.mark_candidate_submitted(
            1,
            ProviderTask(
                "minimax", "MiniMax-H3", "v2", "reference", "candidate-task"
            ),
        )
        entry = checkpoint.shot_checkpoint(1)
        self.assertEqual(entry["provider_model"], "MiniMax-Hailuo-2.3")
        self.assertEqual(entry["candidate"]["provider_model"], "MiniMax-H3")

    def test_O_bundle_records_route_names_but_not_secret(self):
        checkpoint, _plan = self.state()
        checkpoint.prepare_shot_generation(1)
        checkpoint.mark_shot_submitted(
            1,
            ProviderTask(
                "minimax",
                "MiniMax-H3",
                "v2",
                "reference_generation",
                "task-safe",
                selection_mode="manual",
                credential_env_name="MINIMAX_H3_API_KEY",
            ),
        )
        bundle = read_bundle_json(self.paths, 1, 1, "generation.json")
        self.assertEqual(bundle["selection_mode"], "manual")
        self.assertEqual(bundle["credential_env_name"], "MINIMAX_H3_API_KEY")
        self.assertNotIn("h3-secret", json.dumps(bundle))

    def test_P_provider_400_exposes_safe_message_and_request_id(self):
        with patch(
            "providers.minimax_common.requests.request",
            return_value=Fake400Response(),
        ):
            with self.assertRaises(VideoProviderError) as raised:
                request_json(
                    "POST", "https://mock.invalid", {}, model="MiniMax-H3", json={}
                )
        self.assertIn("TokenPlan", str(raised.exception))
        self.assertEqual(raised.exception.request_id, "request-safe-123")
        self.assertEqual(raised.exception.http_status, 400)

    def test_Q_api_log_never_contains_credential_value(self):
        secret = "test-secret-value"
        provider = RecordingProvider(
            "future",
            "future-video",
            {"none"},
            credential_env_name="FUTURE_VIDEO_KEY",
            credential_value=secret,
        )
        registry = VideoProviderRegistry(
            {
                "credential_env": {
                    "future": {"future-video": "FUTURE_VIDEO_KEY"}
                },
                "default_models": {
                    "none": {"provider": "future", "model": "future-video"}
                },
            }
        )
        registry.register(provider)
        logger = TaskLogger(self.paths, "safe-log")
        logger.register_secret(secret)
        generate_video(
            {},
            "prompt",
            self.paths,
            output_path=self.paths.shot_version_video_path(1, 1),
            provider_registry=registry,
            task_logger=logger,
        )
        log = logger.api_log_path.read_text(encoding="utf-8")
        self.assertNotIn(secret, log)
        self.assertIn("FUTURE_VIDEO_KEY", log)

    def test_R_base64_reference_is_not_written_to_normal_logs(self):
        logger = TaskLogger(self.paths, "no-base64")
        logger.register_secret("h3-secret")
        captured: dict = {}

        def fake_request(_method, _url, **kwargs):
            captured.update(kwargs.get("json") or {})
            return Fake400Response()

        with patch("providers.minimax_common.requests.request", side_effect=fake_request):
            with self.assertRaises(VideoProviderError):
                generate_video(
                    self.credentials,
                    "prompt",
                    self.paths,
                    output_path=self.paths.shot_version_video_path(1, 1),
                    visual_input=self.reference,
                    provider_registry=create_default_registry(self.credentials),
                    task_logger=logger,
                )
        self.assertTrue(captured["content"][1]["image_url"]["url"].startswith("data:image"))
        log = logger.api_log_path.read_text(encoding="utf-8")
        self.assertNotIn("data:image", log)
        self.assertNotIn(captured["content"][1]["image_url"]["url"], log)

    def test_S_old_schema2_bundle_without_new_fields_still_loads(self):
        checkpoint, _plan = self.state()
        checkpoint.prepare_shot_generation(1)
        checkpoint.mark_shot_submitted(
            1,
            ProviderTask("minimax", "MiniMax-H3", "v2", "reference", "old-task"),
        )
        generation_path = self.paths.shot_version_generation_path(1, 1)
        generation = json.loads(generation_path.read_text(encoding="utf-8"))
        generation.pop("selection_mode", None)
        generation.pop("credential_env_name", None)
        generation_path.write_text(
            json.dumps(generation, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        project_path = self.paths.project_state_path()
        project = json.loads(project_path.read_text(encoding="utf-8"))
        mirror = project["video_generation"]["shots"]["1"]["generation_versions"][0]
        mirror.pop("selection_mode", None)
        mirror.pop("credential_env_name", None)
        self.paths.save_json(project_path, project)
        loaded = ProjectCheckpoint.load(self.paths)
        task = loaded.generation_provider_task(1, 1)
        self.assertEqual(task.provider_task_id, "old-task")
        self.assertIsNone(task.selection_mode)
        history = video_version_history(self.paths, loaded, 1)
        self.assertEqual(history[0].selection_mode, "legacy")

    def test_T_future_provider_uses_own_model_credential_without_business_change(self):
        provider = RecordingProvider(
            "future",
            "future-v1",
            {"none"},
            credential_env_name="FUTURE_VIDEO_KEY",
            credential_value="future-secret",
        )
        registry = VideoProviderRegistry(
            {
                "credential_env": {
                    "future": {"future-v1": "FUTURE_VIDEO_KEY"}
                },
                "default_models": {
                    "none": {"provider": "future", "model": "future-v1"}
                },
            }
        )
        registry.register(provider)
        output = generate_video(
            {},
            "prompt",
            self.paths,
            output_path=self.paths.shot_version_video_path(1, 1),
            provider_registry=registry,
        )
        self.assertTrue(output.is_file())
        self.assertEqual(provider.submit_calls, 1)


if __name__ == "__main__":
    unittest.main()
