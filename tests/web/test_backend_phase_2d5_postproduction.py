from __future__ import annotations

import json
import socket
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import requests
from fastapi.testclient import TestClient

from tests.web.test_backend_phase_2d4_shots import tree_snapshot, write_json
from tests.web.web_response_assertions import assert_public_payload


PROJECT_ID = "post-project"


def base_project(project_id: str | None = PROJECT_ID) -> dict:
    payload = {
        "project_schema_version": 2,
        "project_name": "LEE柠檬",
        "updated_at": "2026-08-18T18:00:00+08:00",
        "status": "COMPLETED",
        "request": {"product_name": "LEE柠檬"},
        "assembly": {
            "status": "COMPLETED",
            "needs_update": False,
            "changed_shot_id": None,
            "final_video_version": 2,
            "assembled_at": "2026-08-18T10:00:00+08:00",
            "total_duration": 18.5,
            "shot_versions": [
                {"shot_id": 1, "approved_video_version": 2, "video_path": r"D:\hidden\shot.mp4"},
                {"shot_id": 2, "approved_video_version": 1, "video_path": "shots/shot_02/v001/video.mp4"},
            ],
        },
        "post_production": {
            "status": "FINAL_COMPLETED",
            "components": {
                "voice": {"status": "COMPLETED", "active_version": 2, "path": r"D:\hidden\audio.wav"},
                "subtitle": {"status": "COMPLETED", "active_version": 1},
                "music": {"status": "COMPLETED", "active_version": 1},
                "final_export": {"status": "COMPLETED", "active_version": 1},
            },
            "music_mix": {
                "base_volume": 0.25,
                "ducking_enabled": True,
                "ducking_ratio": 0.4,
                "duck_attack_seconds": 0.25,
                "duck_release_seconds": 0.35,
                "fade_in_seconds": 0.8,
                "fade_out_seconds": 1.2,
                "loop_music": False,
            },
        },
    }
    if project_id is not None:
        payload["project_id"] = project_id
    return payload


def create_fixture(project_dir: Path, project_id: str | None = PROJECT_ID) -> None:
    write_json(project_dir / "project.json", base_project(project_id))
    write_json(
        project_dir / "videos" / "assembly_manifest.json",
        {
            "manifest_version": 1,
            "assembly_version": 2,
            "latest_assembly_version": 2,
            "latest_final_video_path": "videos/final_video_v002.mp4",
            "assemblies": [
                {
                    "assembly_version": 1,
                    "created_at": "2026-08-18T09:00:00+08:00",
                    "final_video_path": "videos/final_video.mp4",
                    "total_duration": 18.0,
                    "shots": [{"shot_id": 1, "approved_video_version": 1}],
                },
                {
                    "assembly_version": 2,
                    "created_at": "2026-08-18T10:00:00+08:00",
                    "final_video_path": "videos/final_video_v002.mp4",
                    "total_duration": 18.5,
                    "shots": [
                        {"shot_id": 1, "approved_video_version": 2},
                        {"shot_id": 2, "approved_video_version": 1},
                    ],
                },
            ],
        },
    )
    (project_dir / "videos" / "final_video.mp4").write_bytes(b"old-assembly")
    (project_dir / "videos" / "final_video_v002.mp4").write_bytes(bytes(range(256)) * 8)

    voice_entry = {
        "version": 2,
        "created_at": "2026-08-18T10:10:00+08:00",
        "provider": "xfyun_tts",
        "provider_task_id": "must-not-escape",
        "model": "online-tts-v2",
        "voice": "xiaoyan",
        "language": "zh-CN",
        "script_source": "compiled_storyboard",
        "planned_narration_duration": 12.0,
        "planned_first_voice_start": 2.0,
        "planned_last_voice_end": 14.0,
        "planned_voice_span": 12.0,
        "actual_audio_duration": 10.5,
        "timing_calibration": {
            "timing_mode": "whole_track",
            "status": "OUT_OF_TOLERANCE",
            "actual_audio_duration": 10.5,
            "voice_track_start": 2.0,
            "actual_voice_end": 12.5,
            "cue_level_alignment": False,
            "script_matches_storyboard": True,
            "source_fingerprint": {"path": r"D:\hidden\storyboard.json"},
        },
        "audio_path": r"D:\hidden\audio.wav",
    }
    write_json(
        project_dir / "voice" / "voice_manifest.json",
        {"voice_schema_version": 1, "active_version": 2, "versions": [voice_entry]},
    )
    write_json(
        project_dir / "voice" / "versions" / "v002" / "voice_config.json",
        voice_entry,
    )
    (project_dir / "voice" / "versions" / "v002" / "script.txt").write_text(
        "新鲜看得见，LEE柠檬点亮每一天。", encoding="utf-8"
    )
    (project_dir / "voice" / "versions" / "v002" / "audio.wav").write_bytes(
        b"RIFF" + b"\x00" * 64
    )

    write_json(
        project_dir / "subtitles" / "subtitle_manifest.json",
        {
            "subtitle_schema_version": 1,
            "active_version": 1,
            "versions": [
                {
                    "version": 1,
                    "created_at": "2026-08-18T10:20:00+08:00",
                    "source": "compiled_storyboard",
                    "timing_source": "compiled_storyboard_global_timeline",
                    "cue_count": 2,
                    "subtitle_path": r"D:\hidden\subtitle.srt",
                }
            ],
        },
    )
    write_json(
        project_dir / "subtitles" / "versions" / "v001" / "subtitle_config.json",
        {"source": "compiled_storyboard", "cue_count": 2},
    )
    (project_dir / "subtitles" / "versions" / "v001" / "subtitle.srt").write_text(
        "1\n00:00:02,000 --> 00:00:04,500\n新鲜看得见\n\n"
        "2\n00:00:09,000 --> 00:00:12,000\nLEE柠檬，点亮每一天\n",
        encoding="utf-8",
    )

    write_json(
        project_dir / "music" / "music_manifest.json",
        {
            "music_schema_version": 1,
            "active_version": 1,
            "versions": [
                {
                    "version": 1,
                    "created_at": "2026-08-18T10:30:00+08:00",
                    "provider": "local_music",
                    "credential_env_name": "MUST_NOT_ESCAPE",
                    "extension": "mp3",
                    "duration_seconds": 30.0,
                    "music_path": r"D:\hidden\music.mp3",
                }
            ],
        },
    )
    write_json(
        project_dir / "music" / "versions" / "v001" / "music_config.json",
        {"extension": "mp3", "duration": 30.0},
    )
    (project_dir / "music" / "versions" / "v001" / "music.mp3").write_bytes(
        b"ID3" + bytes(range(128)) * 8
    )

    export_entry = {
        "version": 1,
        "created_at": "2026-08-18T10:40:00+08:00",
        "final_video_path": r"D:\hidden\final.mp4",
        "assembly_version": 2,
        "voice_version": 2,
        "subtitle_version": 1,
        "music_version": 1,
        "voice": {
            "timing_mode": "whole_track",
            "voice_track_start": 2.0,
            "actual_audio_duration": 10.5,
            "actual_voice_end": 12.5,
            "calibration_status": "OUT_OF_TOLERANCE",
            "cue_level_alignment": False,
        },
        "music_mix": {
            "settings": {
                "base_volume": 0.25,
                "ducking_enabled": True,
                "ducking_ratio": 0.4,
                "duck_attack_seconds": 0.25,
                "duck_release_seconds": 0.35,
                "fade_in_seconds": 0.8,
                "fade_out_seconds": 1.2,
                "loop_music": False,
            },
            "ducking_status": "ENABLED",
        },
        "input_fingerprint_sha256": "must-not-escape",
    }
    write_json(
        project_dir / "exports" / "export_manifest.json",
        {"export_schema_version": 1, "active_version": 1, "versions": [export_entry]},
    )
    write_json(
        project_dir / "exports" / "v001" / "export_manifest.json",
        {**export_entry, "tools": {"ffmpeg": r"D:\tools\ffmpeg.exe"}},
    )
    (project_dir / "exports" / "v001" / "final_video.mp4").write_bytes(
        bytes(range(256)) * 12
    )


class WebBackendPhase2D5PostProductionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.projects_root = Path(self.temp.name) / "projects"
        self.project_dir = self.projects_root / "柠檬"
        create_fixture(self.project_dir)
        from web_backend.app import create_app
        from web_backend.settings import BackendSettings

        self.client = TestClient(
            create_app(settings=BackendSettings(projects_root=self.projects_root)),
            raise_server_exceptions=False,
        )
        self.addCleanup(self.client.close)

    def get(self, route: str, **kwargs):
        return self.client.get(f"/api/projects/{PROJECT_ID}/{route}", **kwargs)

    def update_project(self, update) -> None:
        path = self.project_dir / "project.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        update(payload)
        write_json(path, payload)

    def test_01_assembly_uses_canonical_project_state(self):
        payload = self.get("assembly").json()
        self.assertEqual(payload["status"], "COMPLETED")
        self.assertEqual(payload["current_version"], 2)
        self.assertEqual(payload["total_duration"], 18.5)

    def test_02_assembly_exposes_safe_shot_versions(self):
        payload = self.get("assembly").json()
        self.assertEqual(
            payload["shots"],
            [{"shot_id": 1, "video_version": 2}, {"shot_id": 2, "video_version": 1}],
        )
        assert_public_payload(self, payload)

    def test_03_assembly_needs_update_and_changed_shot_are_preserved(self):
        def change(payload):
            payload["assembly"]["needs_update"] = True
            payload["assembly"]["changed_shot_id"] = 2

        self.update_project(change)
        payload = self.get("assembly").json()
        self.assertTrue(payload["needs_update"])
        self.assertEqual(payload["changed_shot_id"], 2)

    def test_04_assembly_media_get_is_200(self):
        response = self.get("assembly/video")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "video/mp4")

    def test_05_assembly_media_range_is_206(self):
        response = self.get("assembly/video", headers={"Range": "bytes=0-31"})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(len(response.content), 32)
        self.assertEqual(response.headers["accept-ranges"], "bytes")

    def test_06_voice_metadata_is_projected(self):
        payload = self.get("post-production/voice").json()
        self.assertEqual(payload["version"], 2)
        self.assertEqual(payload["model"], "online-tts-v2")
        self.assertEqual(payload["voice"], "xiaoyan")
        self.assertEqual(payload["language"], "zh-CN")

    def test_07_voice_script_and_source_are_read_from_fixed_bundle(self):
        payload = self.get("post-production/voice").json()
        self.assertIn("LEE柠檬", payload["script"])
        self.assertEqual(payload["script_source"], "compiled_storyboard")

    def test_08_voice_timing_calibration_is_persisted_result(self):
        payload = self.get("post-production/voice").json()
        self.assertEqual(payload["calibration_status"], "OUT_OF_TOLERANCE")
        self.assertEqual(payload["planned_narration_duration"], 12.0)
        self.assertEqual(payload["voice_track_start"], 2.0)
        self.assertEqual(payload["actual_voice_end"], 12.5)

    def test_09_voice_audio_is_served_without_provider_metadata(self):
        response = self.get("post-production/voice/audio")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "audio/wav")
        self.assertNotIn("content-disposition", response.headers)

    def test_10_subtitle_srt_is_parsed_to_cues(self):
        payload = self.get("post-production/subtitle").json()
        self.assertEqual(payload["cue_count"], 2)
        self.assertEqual([cue["index"] for cue in payload["cues"]], [1, 2])
        self.assertEqual(payload["cues"][0]["text"], "新鲜看得见")

    def test_11_subtitle_times_and_order_are_preserved(self):
        cues = self.get("post-production/subtitle").json()["cues"]
        self.assertEqual(cues[0]["start"], "00:00:02,000")
        self.assertEqual(cues[0]["end"], "00:00:04,500")
        self.assertEqual(cues[1]["start"], "00:00:09,000")

    def test_12_subtitle_source_is_safe(self):
        payload = self.get("post-production/subtitle").json()
        self.assertEqual(payload["source"], "compiled_storyboard")
        self.assertEqual(payload["timing_source"], "compiled_storyboard_global_timeline")

    def test_13_music_asset_and_format_are_projected(self):
        payload = self.get("post-production/music").json()
        self.assertEqual(payload["version"], 1)
        self.assertEqual(payload["format"], "mp3")
        self.assertTrue(payload["audio_available"])

    def test_14_music_mix_config_is_read_from_project(self):
        mix = self.get("post-production/music").json()["music_mix"]
        self.assertEqual(mix["base_volume"], 0.25)
        self.assertTrue(mix["ducking_enabled"])
        self.assertEqual(mix["ducking_ratio"], 0.4)
        self.assertEqual(mix["fade_out_seconds"], 1.2)

    def test_15_music_audio_is_served_in_real_format(self):
        response = self.get("post-production/music/audio")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "audio/mpeg")

    def test_16_export_manifest_relationships_are_projected(self):
        payload = self.get("export").json()
        self.assertEqual(payload["version"], 1)
        self.assertEqual(payload["assembly_version"], 2)
        self.assertEqual(payload["voice_version"], 2)
        self.assertEqual(payload["subtitle_version"], 1)
        self.assertEqual(payload["music_version"], 1)

    def test_17_export_voice_timing_and_music_mix_are_safe(self):
        payload = self.get("export").json()
        self.assertEqual(payload["voice_timing"]["actual_voice_end"], 12.5)
        self.assertEqual(payload["voice_timing"]["calibration_status"], "OUT_OF_TOLERANCE")
        self.assertEqual(payload["music_mix"]["ducking_status"], "ENABLED")
        assert_public_payload(self, payload)

    def test_18_export_is_stale_when_assembly_needs_update(self):
        self.update_project(lambda payload: payload["assembly"].update(needs_update=True))
        payload = self.get("export").json()
        self.assertTrue(payload["stale"])
        self.assertEqual(payload["status"], "STALE")

    def test_19_export_is_stale_when_assembly_version_changed(self):
        self.update_project(lambda payload: payload["assembly"].update(final_video_version=3))
        payload = self.get("export").json()
        self.assertTrue(payload["stale"])

    def test_20_export_media_get_is_200(self):
        response = self.get("export/video")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "video/mp4")

    def test_21_export_media_range_is_206(self):
        response = self.get("export/video", headers={"Range": "bytes=10-41"})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(len(response.content), 32)
        self.assertIn("bytes 10-41/", response.headers["content-range"])

    def test_22_new_project_returns_not_started_without_creating_data(self):
        fresh = self.projects_root / "新项目"
        payload = base_project("fresh-project")
        payload["assembly"] = {"status": "NOT_STARTED", "needs_update": False}
        payload["post_production"] = {"status": "NOT_STARTED", "components": {}}
        write_json(fresh / "project.json", payload)
        before = tree_snapshot(fresh)
        routes = (
            "assembly",
            "post-production/voice",
            "post-production/subtitle",
            "post-production/music",
            "export",
        )
        for route in routes:
            response = self.client.get(f"/api/projects/fresh-project/{route}")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "NOT_STARTED")
        self.assertEqual(tree_snapshot(fresh), before)

    def test_23_missing_media_keeps_metadata_readable(self):
        (self.project_dir / "voice" / "versions" / "v002" / "audio.wav").unlink()
        payload = self.get("post-production/voice").json()
        self.assertEqual(payload["version"], 2)
        self.assertFalse(payload["audio_available"])
        self.assertIn("LEE柠檬", payload["script"])

    def test_24_missing_media_endpoint_returns_safe_404(self):
        (self.project_dir / "exports" / "v001" / "final_video.mp4").unlink()
        response = self.get("export/video")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "EXPORT_MEDIA_NOT_FOUND")
        assert_public_payload(self, response.json())

    def test_25_project_not_found_is_safe(self):
        response = self.client.get("/api/projects/missing/export")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "PROJECT_NOT_FOUND")

    def test_26_corrupt_component_json_is_mapped(self):
        (self.project_dir / "voice" / "voice_manifest.json").write_text("{broken", encoding="utf-8")
        response = self.get("post-production/voice")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "VOICE_DATA_CORRUPT")
        self.assertNotIn("JSONDecodeError", response.text)

    def test_27_corrupt_srt_is_mapped(self):
        (self.project_dir / "subtitles" / "versions" / "v001" / "subtitle.srt").write_text(
            "1\ninvalid timeline\ntext", encoding="utf-8"
        )
        response = self.get("post-production/subtitle")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "SUBTITLE_DATA_CORRUPT")

    def test_28_legacy_chinese_project_id_is_supported(self):
        write_json(self.project_dir / "project.json", base_project(None))
        response = self.client.get("/api/projects/柠檬/assembly")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["project_id"], "柠檬")

    def test_29_encoded_project_traversal_is_rejected(self):
        response = self.client.get("/api/projects/%252e%252e/assembly")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "INVALID_PROJECT_ID")

    def test_30_symlink_escape_is_rejected(self):
        media = self.project_dir / "exports" / "v001" / "final_video.mp4"
        outside = Path(self.temp.name) / "outside.mp4"
        outside.write_bytes(b"outside")
        media.unlink()
        try:
            media.symlink_to(outside)
        except OSError as exc:
            self.skipTest(f"symlink unavailable: {exc}")
        response = self.get("export/video")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "EXPORT_DATA_CORRUPT")

    def test_31_all_json_payloads_hide_paths_credentials_and_raw_metadata(self):
        for route in (
            "assembly",
            "post-production/voice",
            "post-production/subtitle",
            "post-production/music",
            "export",
        ):
            response = self.get(route)
            self.assertEqual(response.status_code, 200)
            assert_public_payload(self, response.json())
            serialized = json.dumps(response.json(), ensure_ascii=False).lower()
            for forbidden in ("provider_task_id", "credential", "fingerprint", "ffmpeg", "d:\\"):
                self.assertNotIn(forbidden, serialized)

    def test_32_detail_gets_are_zero_write(self):
        before = tree_snapshot(self.project_dir)
        for route in (
            "assembly",
            "post-production/voice",
            "post-production/subtitle",
            "post-production/music",
            "export",
        ):
            self.assertEqual(self.get(route).status_code, 200)
        self.assertEqual(tree_snapshot(self.project_dir), before)

    def test_33_media_gets_are_zero_write(self):
        before = tree_snapshot(self.project_dir)
        for route in (
            "assembly/video",
            "post-production/voice/audio",
            "post-production/music/audio",
            "export/video",
        ):
            self.assertEqual(self.get(route).status_code, 200)
        self.assertEqual(tree_snapshot(self.project_dir), before)

    def test_34_gets_do_not_call_provider_or_network(self):
        with (
            patch.object(socket, "create_connection", side_effect=AssertionError("network used")),
            patch.object(requests.sessions.Session, "request", side_effect=AssertionError("provider used")),
        ):
            for route in ("assembly", "post-production/voice", "post-production/subtitle", "post-production/music", "export"):
                self.assertEqual(self.get(route).status_code, 200)

    def test_35_gets_do_not_run_ffmpeg_or_subprocess(self):
        with (
            patch.object(subprocess, "run", side_effect=AssertionError("process used")),
            patch.object(subprocess, "Popen", side_effect=AssertionError("process used")),
        ):
            for route in ("assembly/video", "post-production/voice/audio", "post-production/music/audio", "export/video"):
                self.assertEqual(self.get(route).status_code, 200)

    def test_36_shot_media_range_regression(self):
        shot_dir = self.project_dir / "shots" / "shot_01"
        write_json(
            shot_dir / "shot.json",
            {"shot_schema_version": 2, "shot_id": 1, "status": "APPROVED", "active_version": 1, "approved_version": 1, "versions": [1]},
        )
        (shot_dir / "v001").mkdir(parents=True, exist_ok=True)
        (shot_dir / "v001" / "video.mp4").write_bytes(bytes(range(128)) * 4)
        response = self.client.get(
            f"/api/projects/{PROJECT_ID}/shots/shot_01/versions/1/video",
            headers={"Range": "bytes=0-15"},
        )
        self.assertEqual(response.status_code, 206)
        self.assertEqual(len(response.content), 16)

    def test_37_new_surface_publishes_get_only(self):
        schema = self.client.get("/openapi.json").json()
        suffixes = (
            "/assembly",
            "/assembly/video",
            "/post-production/voice",
            "/post-production/voice/audio",
            "/post-production/subtitle",
            "/post-production/music",
            "/post-production/music/audio",
            "/export",
            "/export/video",
        )
        for suffix in suffixes:
            methods = set(schema["paths"][f"/api/projects/{{project_id}}{suffix}"])
            self.assertEqual(methods, {"get"})


if __name__ == "__main__":
    unittest.main()
