from __future__ import annotations

import io
import json
import socket
import subprocess
import threading
import unittest
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import requests
from fastapi.testclient import TestClient

from music_assets import MusicAssetManager
from music_generation import add_local_music
from music_provider import MusicProviderError
from post_production import PostProductionPipeline, ProjectCompletionStatus
from project_manager import create_project_paths
from project_state import AssemblyStatus, ProjectCheckpoint
from providers.local_music_provider import LocalMusicProvider
from tests.web.web_response_assertions import assert_public_payload


def silent_wav(duration: float = 0.08, sample_rate: int = 8000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(sample_rate)
        stream.writeframes(b"\x00\x00" * int(duration * sample_rate))
    return buffer.getvalue()


class WebBackendPhase4C3MusicWebSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.projects_root = self.root / "projects"
        self.project_dir = self.projects_root / "music-project"
        self.paths = create_project_paths(self.project_dir)
        checkpoint = ProjectCheckpoint.create(
            self.paths,
            "Music Web",
            {
                "product_name": "背景音乐",
                "product_description": "纯本地上传",
                "user_notes": "",
                "duration_seconds": 12,
            },
        )
        checkpoint.assembly_checkpoint().update(
            {
                "status": AssemblyStatus.COMPLETED.value,
                "needs_update": False,
                "final_video_path": "videos/final_video.mp4",
                "final_video_version": 1,
                "total_duration": 12.0,
            }
        )
        checkpoint.data["completion_status"] = (
            ProjectCompletionStatus.VIDEO_ASSEMBLY_COMPLETED.value
        )
        checkpoint.save()
        self.project_id = str(checkpoint.data["project_id"])
        self.paths.final_video_path().write_bytes(b"video")

        from web_backend.app import create_app
        from web_backend.locking import ProjectLockManager
        from web_backend.settings import BackendSettings

        self.lock_manager = ProjectLockManager()
        self.app = create_app(
            settings=BackendSettings(
                projects_root=self.projects_root,
                runtime_root=self.root / "runtime",
                task_workers=1,
            ),
            lock_manager=self.lock_manager,
        )
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        self.addCleanup(self.app.state.task_runner.shutdown)

    @property
    def base(self) -> str:
        return f"/api/projects/{self.project_id}/post-production/music"

    def options(self) -> dict:
        response = self.client.get(f"{self.base}/options")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def upload(
        self,
        *,
        content: bytes | None = None,
        filename: str = "music.wav",
        content_type: str = "audio/wav",
        expected_active: int | None | object = ...,
        expected_next: int | None = None,
    ):
        current = self.options()
        active = current["active_version"] if expected_active is ... else expected_active
        next_version = current["next_version"] if expected_next is None else expected_next
        data = {"expected_next_version": str(next_version)}
        if active is not None:
            data["expected_active_version"] = str(active)
        return self.client.post(
            f"{self.base}/upload",
            data=data,
            files={
                "file": (
                    filename,
                    silent_wav() if content is None else content,
                    content_type,
                )
            },
        )

    def upload_two(self) -> None:
        self.assertEqual(self.upload().status_code, 200)
        self.assertEqual(self.upload(content=silent_wav(0.1)).status_code, 200)

    def music_manifest(self) -> dict:
        return MusicAssetManager(self.paths).load_manifest()

    def task_count(self) -> int:
        return len(self.client.get(f"/api/projects/{self.project_id}/tasks").json()["tasks"])

    def staging_children(self) -> list[Path]:
        root = self.root / "runtime" / "music_uploads"
        return list(root.iterdir()) if root.exists() else []

    def test_01_options_without_music(self):
        payload = self.options()
        self.assertFalse(payload["has_music"])
        self.assertIsNone(payload["active_version"])

    def test_02_options_with_active_music(self):
        self.upload()
        payload = self.options()
        self.assertTrue(payload["has_music"])
        self.assertEqual(payload["active_version"], 1)

    def test_03_options_next_version(self):
        self.assertEqual(self.options()["next_version"], 1)
        self.upload()
        self.assertEqual(self.options()["next_version"], 2)

    def test_04_options_allowed_extensions_match_core(self):
        self.assertEqual(
            self.options()["allowed_extensions"],
            ["aac", "flac", "m4a", "mp3", "ogg", "wav"],
        )

    def test_05_loop_capability_is_false(self):
        self.assertFalse(self.options()["capabilities"]["loop"])

    def test_06_multipart_upload_succeeds(self):
        response = self.upload()
        self.assertEqual(response.status_code, 200, response.text)

    def test_07_upload_is_synchronous_200(self):
        self.assertEqual(self.upload().status_code, 200)
        self.assertEqual(self.task_count(), 0)

    def test_08_upload_creates_v001(self):
        self.assertEqual(self.upload().json()["version"], 1)
        self.assertTrue(self.paths.music_version_audio_path(1, "wav").is_file())

    def test_09_upload_sets_active_one(self):
        self.upload()
        self.assertEqual(self.music_manifest()["active_version"], 1)

    def test_10_replace_creates_v002(self):
        self.upload_two()
        self.assertEqual(self.music_manifest()["active_version"], 2)

    def test_11_replace_preserves_v001(self):
        self.upload()
        before = self.paths.music_version_audio_path(1, "wav").read_bytes()
        self.upload(content=silent_wav(0.1))
        self.assertEqual(self.paths.music_version_audio_path(1, "wav").read_bytes(), before)

    def test_12_upload_reuses_add_local_music(self):
        with patch("web_backend.services.music.add_local_music", wraps=add_local_music) as reused:
            self.assertEqual(self.upload().status_code, 200)
        reused.assert_called_once()

    def test_13_upload_reuses_core_provider_preflight(self):
        original = LocalMusicProvider.preflight
        calls: list[object] = []

        def tracked(provider, request):
            calls.append(request)
            return original(provider, request)

        with patch.object(LocalMusicProvider, "preflight", tracked):
            self.assertEqual(self.upload().status_code, 200)
        self.assertGreaterEqual(len(calls), 1)

    def test_14_upload_reuses_checkpoint(self):
        original = PostProductionPipeline.mark_component_completed
        with patch.object(
            PostProductionPipeline,
            "mark_component_completed",
            autospec=True,
            side_effect=original,
        ) as completed:
            self.assertEqual(self.upload().status_code, 200)
        completed.assert_called_once()

    def test_15_json_path_is_not_accepted(self):
        response = self.client.post(
            f"{self.base}/upload",
            json={"source_path": r"C:\\Users\\name\\music.wav"},
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "INVALID_REQUEST")

    def test_16_parent_traversal_filename_is_rejected(self):
        response = self.upload(filename="../music.wav")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "MUSIC_FILE_INVALID")

    def test_17_windows_path_filename_is_never_used_as_path(self):
        from web_backend.services.music import MusicFileInvalid, _safe_upload_suffix

        with self.assertRaises(MusicFileInvalid):
            _safe_upload_suffix(r"C:\Users\name\music.wav", ("wav",))

    def test_17a_encoded_traversal_filename_is_rejected(self):
        from web_backend.services.music import MusicFileInvalid, _safe_upload_suffix

        with self.assertRaises(MusicFileInvalid):
            _safe_upload_suffix("..%252fprivate%252fmusic.wav", ("wav",))

    def test_17b_unc_filename_is_rejected(self):
        from web_backend.services.music import MusicFileInvalid, _safe_upload_suffix

        with self.assertRaises(MusicFileInvalid):
            _safe_upload_suffix(r"\\server\share\music.wav", ("wav",))

    def test_18_oversized_upload_is_rejected(self):
        from web_backend.services.music import _MusicLimits

        with patch(
            "web_backend.services.music.MusicWebService._limits",
            return_value=_MusicLimits(("wav",), 32),
        ):
            response = self.upload()
        self.assertEqual(response.status_code, 413)

    def test_19_unsupported_extension_is_rejected(self):
        response = self.upload(filename="music.exe")
        self.assertEqual(response.status_code, 415)

    def test_20_invalid_signature_is_rejected(self):
        response = self.upload(content=b"not a wav", filename="music.wav")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "MUSIC_FILE_INVALID")

    def test_21_mime_spoof_does_not_bypass_core(self):
        response = self.upload(
            content=b"not an mp3", filename="music.mp3", content_type="audio/mpeg"
        )
        self.assertEqual(response.status_code, 422)

    def test_22_success_cleans_staging(self):
        self.upload()
        self.assertEqual(self.staging_children(), [])

    def test_23_core_failure_cleans_staging(self):
        with patch(
            "web_backend.services.music.add_local_music",
            side_effect=MusicProviderError("内容不匹配"),
        ):
            response = self.upload()
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.staging_children(), [])

    def test_24_invalid_upload_cleans_staging(self):
        def fail_after_partial(_upload, directory, **_kwargs):
            from web_backend.services.music import MusicUploadFailed

            (directory / "partial.upload").write_bytes(b"partial")
            raise MusicUploadFailed("client upload stream failed")

        with patch("web_backend.services.music._stage_upload", side_effect=fail_after_partial):
            response = self.upload()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.staging_children(), [])

    def test_25_busy_failure_cleans_staging(self):
        ready = threading.Event()
        release = threading.Event()

        def hold():
            with self.lock_manager.project_write(self.project_id):
                ready.set()
                release.wait(3)

        thread = threading.Thread(target=hold)
        thread.start()
        ready.wait(3)
        try:
            response = self.upload()
        finally:
            release.set()
            thread.join(3)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.staging_children(), [])

    def test_26_staging_never_enters_dto(self):
        payload = self.upload().json()
        self.assertNotIn("staging", json.dumps(payload).casefold())
        assert_public_payload(self, payload)

    def test_27_same_project_lock_rejects_overlap(self):
        ready = threading.Event()
        release = threading.Event()

        def hold():
            with self.lock_manager.project_write(self.project_id):
                ready.set()
                release.wait(3)

        thread = threading.Thread(target=hold)
        thread.start()
        ready.wait(3)
        response = self.upload()
        release.set()
        thread.join(3)
        self.assertEqual(response.json()["error"]["code"], "PROJECT_BUSY")

    def test_28_stale_expected_version_is_rejected(self):
        self.upload()
        response = self.upload(expected_active=None, expected_next=1)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "MUSIC_STATE_CHANGED")

    def test_29_concurrent_replace_does_not_duplicate_version(self):
        self.upload()

        def replace():
            return self.client.post(
                f"{self.base}/upload",
                data={"expected_active_version": "1", "expected_next_version": "2"},
                files={"file": ("replace.wav", silent_wav(0.1), "audio/wav")},
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(lambda _: replace(), range(2)))
        self.assertEqual(sum(item.status_code == 200 for item in responses), 1)
        self.assertEqual([item["version"] for item in self.music_manifest()["versions"]], [1, 2])

    def test_30_update_base_volume(self):
        self.upload()
        payload = self.client.patch(f"{self.base}/mix", json={"base_volume": 0.6}).json()
        self.assertEqual(payload["music_mix"]["base_volume"], 0.6)

    def test_31_update_ducking(self):
        self.upload()
        payload = self.client.patch(f"{self.base}/mix", json={"ducking_enabled": False}).json()
        self.assertFalse(payload["music_mix"]["ducking_enabled"])

    def test_32_update_attack_release(self):
        self.upload()
        payload = self.client.patch(
            f"{self.base}/mix",
            json={"duck_attack_seconds": 0.1, "duck_release_seconds": 0.2},
        ).json()["music_mix"]
        self.assertEqual((payload["duck_attack_seconds"], payload["duck_release_seconds"]), (0.1, 0.2))

    def test_33_update_fades(self):
        self.upload()
        payload = self.client.patch(
            f"{self.base}/mix", json={"fade_in_seconds": 0.3, "fade_out_seconds": 0.4}
        ).json()["music_mix"]
        self.assertEqual((payload["fade_in_seconds"], payload["fade_out_seconds"]), (0.3, 0.4))

    def test_34_partial_update_preserves_omitted_fields(self):
        self.upload()
        before = self.options()["mix"]
        after = self.client.patch(f"{self.base}/mix", json={"base_volume": 0.7}).json()["music_mix"]
        self.assertEqual(after["ducking_ratio"], before["ducking_ratio"])
        self.assertEqual(after["fade_out_seconds"], before["fade_out_seconds"])

    def test_35_invalid_mix_is_rejected(self):
        self.upload()
        response = self.client.patch(f"{self.base}/mix", json={"base_volume": 2})
        self.assertEqual(response.status_code, 422)

    def test_36_loop_true_is_rejected_by_core(self):
        self.upload()
        response = self.client.patch(f"{self.base}/mix", json={"loop_music": True})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "MUSIC_MIX_INVALID")

    def test_37_reset_succeeds(self):
        self.upload()
        self.client.patch(f"{self.base}/mix", json={"base_volume": 0.8})
        response = self.client.post(f"{self.base}/mix/reset")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["music_mix"]["base_volume"], 0.25)

    def test_38_mix_update_does_not_create_version(self):
        self.upload()
        self.client.patch(f"{self.base}/mix", json={"base_volume": 0.8})
        self.assertEqual(len(self.music_manifest()["versions"]), 1)
        self.assertEqual(self.music_manifest()["active_version"], 1)

    def test_39_reset_does_not_create_version(self):
        self.upload()
        self.client.post(f"{self.base}/mix/reset")
        self.assertEqual(len(self.music_manifest()["versions"]), 1)

    def test_40_history_lists_versions(self):
        self.upload_two()
        payload = self.client.get(f"{self.base}/history").json()
        self.assertEqual([item["version"] for item in payload["versions"]], [2, 1])

    def test_41_history_marks_active(self):
        self.upload_two()
        payload = self.client.get(f"{self.base}/history").json()
        self.assertTrue(payload["versions"][0]["is_active"])
        self.assertFalse(payload["versions"][1]["is_active"])

    def test_42_version_detail(self):
        self.upload_two()
        response = self.client.get(f"{self.base}/versions/1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["version"], 1)

    def test_43_version_audio_200(self):
        self.upload()
        response = self.client.get(f"{self.base}/versions/1/audio")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, silent_wav())

    def test_44_version_audio_range_206(self):
        self.upload()
        response = self.client.get(
            f"{self.base}/versions/1/audio", headers={"Range": "bytes=0-9"}
        )
        self.assertEqual(response.status_code, 206)
        self.assertEqual(len(response.content), 10)

    def test_45_version_route_traversal_is_rejected(self):
        response = self.client.get(f"{self.base}/versions/%2E%2E%2Fproject.json")
        self.assertIn(response.status_code, {404, 422})

    def test_46_upload_does_not_execute_final_export(self):
        with patch(
            "export_pipeline.ExportPipeline.export_current",
            side_effect=AssertionError("export"),
        ):
            self.assertEqual(self.upload().status_code, 200)

    def test_47_upload_does_not_run_ffmpeg(self):
        with patch.object(subprocess, "Popen", side_effect=AssertionError("ffmpeg")):
            self.assertEqual(self.upload().status_code, 200)

    def test_48_upload_does_not_call_voice_tts(self):
        with patch(
            "voice_generation.generate_confirmed_voice",
            side_effect=AssertionError("TTS"),
        ):
            self.assertEqual(self.upload().status_code, 200)

    def test_49_upload_does_not_generate_subtitle(self):
        with patch(
            "subtitle_generation.generate_subtitle_for_project",
            side_effect=AssertionError("subtitle"),
        ):
            self.assertEqual(self.upload().status_code, 200)

    def test_50_upload_does_not_use_deepseek(self):
        with patch(
            "web_backend.services.capabilities.CapabilityService.deepseek_api_key",
            side_effect=AssertionError("DeepSeek"),
        ):
            self.assertEqual(self.upload().status_code, 200)

    def test_51_upload_does_not_use_minimax(self):
        with (
            patch("providers.minimax_hailuo_provider.MiniMaxHailuoProvider.submit", side_effect=AssertionError("MiniMax")),
            patch("providers.minimax_h3_provider.MiniMaxH3Provider.submit", side_effect=AssertionError("MiniMax")),
        ):
            self.assertEqual(self.upload().status_code, 200)

    def test_52_upload_does_not_use_provider_network(self):
        with (
            patch.object(socket, "create_connection", side_effect=AssertionError("network")),
            patch.object(requests.sessions.Session, "request", side_effect=AssertionError("network")),
        ):
            self.assertEqual(self.upload().status_code, 200)

    def test_53_options_use_core_500mb_limit(self):
        self.assertEqual(self.options()["max_file_size_bytes"], 500 * 1024 * 1024)

    def test_54_upload_dto_has_no_paths_hashes_or_provider_internals(self):
        payload = self.upload().json()
        serialized = json.dumps(payload).casefold()
        for marker in ("sha256", "asset_path", "music_path", "config_path", "provider"):
            self.assertNotIn(marker, serialized)
        assert_public_payload(self, payload)

    def test_55_mix_is_durable_after_reload(self):
        self.upload()
        self.client.patch(f"{self.base}/mix", json={"base_volume": 0.63})
        self.assertEqual(self.options()["mix"]["base_volume"], 0.63)

    def test_56_gets_do_not_create_tasks(self):
        self.options()
        self.client.get(f"{self.base}/history")
        self.assertEqual(self.task_count(), 0)

    def test_57_missing_file_has_safe_error(self):
        response = self.client.post(
            f"{self.base}/upload", data={"expected_next_version": "1"}
        )
        self.assertEqual(response.status_code, 400)
        assert_public_payload(self, response.json())

    def test_58_empty_file_is_invalid(self):
        response = self.upload(content=b"")
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
