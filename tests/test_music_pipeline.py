from __future__ import annotations

import io
import json
import tempfile
import unittest
import wave
from copy import deepcopy
from pathlib import Path

from music_assets import MusicAssetManager
from music_generation import add_local_music, load_active_music_export_input
from music_provider import MusicAddRequest, MusicProviderError
from music_provider_registry import build_music_provider_registry
from post_production_menu import post_production_menu
from project_manager import create_project_paths
from project_state import AssemblyStatus, ProjectCheckpoint
from providers.local_music_provider import LocalMusicProvider
from task_logger import TaskLogger


def silent_wav(path: Path, duration: float, sample_rate: int = 8000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(sample_rate)
        stream.writeframes(b"\x00\x00" * max(1, int(duration * sample_rate)))
    payload = buffer.getvalue()
    path.write_bytes(payload)
    return payload


class Inputs:
    def __init__(self, values: list[str]) -> None:
        self.values = iter(values)

    def __call__(self, _prompt: str = "") -> str:
        return next(self.values)


class MusicPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.paths = create_project_paths(self.root / "project")
        self.music_one = self.root / "music-one.wav"
        self.music_two = self.root / "music-two.wav"
        self.music_one_bytes = silent_wav(self.music_one, 1.0)
        self.music_two_bytes = silent_wav(self.music_two, 1.5)
        self.checkpoint = ProjectCheckpoint.create(
            self.paths,
            "Music Test",
            {
                "product_name": "Product",
                "product_description": "Description",
                "user_notes": "",
            },
        )
        self.paths.final_video_path().write_bytes(b"mock-video")
        self.checkpoint.assembly_checkpoint().update(
            {
                "status": AssemblyStatus.COMPLETED.value,
                "needs_update": False,
                "final_video_path": "videos/final_video.mp4",
                "final_video_version": 1,
                "assembled_at": "2026-08-17T12:00:00+08:00",
                "total_duration": 6.0,
            }
        )
        self.checkpoint.save()
        self.logger = TaskLogger(self.paths, "music-test")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_A_local_provider_registration_and_preflight(self):
        registry = build_music_provider_registry()
        request = MusicAddRequest(self.music_one)
        provider = registry.resolve(request)
        self.assertIsInstance(provider, LocalMusicProvider)
        metadata = provider.get_metadata()
        self.assertEqual(metadata["provider"], "local_music")
        self.assertFalse(metadata["external_api"])

    def test_B_music_import_creates_asset_version_and_default_volume(self):
        entry = add_local_music(
            MusicAssetManager(self.paths),
            build_music_provider_registry(),
            self.music_one,
        )
        self.assertEqual(entry["version"], 1)
        self.assertEqual(entry["music_volume"], 0.25)
        self.assertTrue((self.paths.project_path / entry["asset_path"]).is_file())
        version_path = self.paths.project_path / entry["music_path"]
        self.assertEqual(version_path.read_bytes(), self.music_one_bytes)
        config = json.loads(
            self.paths.music_version_config_path(1).read_text(encoding="utf-8")
        )
        self.assertEqual(config["music_volume"], 0.25)
        self.assertEqual(config["provider"], "local_music")

    def test_C_replacement_creates_v2_and_never_overwrites_v1(self):
        manager = MusicAssetManager(self.paths)
        registry = build_music_provider_registry()
        add_local_music(manager, registry, self.music_one, music_volume=0.25)
        v1_path = self.paths.music_version_audio_path(1, "wav")
        v1_before = v1_path.read_bytes()
        second = add_local_music(
            manager,
            registry,
            self.music_two,
            music_volume=0.4,
        )
        self.assertEqual(second["version"], 2)
        self.assertEqual(second["music_volume"], 0.4)
        self.assertEqual(v1_path.read_bytes(), v1_before)
        self.assertEqual(
            self.paths.music_version_audio_path(2, "wav").read_bytes(),
            self.music_two_bytes,
        )

    def test_D_postproduction_upload_and_resume_do_not_duplicate_version(self):
        post_production_menu(
            self.paths,
            self.checkpoint,
            self.logger,
            input_fn=Inputs(["3", "1", str(self.music_one), "", "5"]),
            output_fn=lambda _value: None,
        )
        manager = MusicAssetManager(self.paths)
        self.assertEqual(len(manager.load_manifest()["versions"]), 1)
        self.assertEqual(
            self.checkpoint.data["post_production"]["components"]["music"][
                "status"
            ],
            "COMPLETED",
        )

        loaded = ProjectCheckpoint.load(self.paths)
        post_production_menu(
            self.paths,
            loaded,
            self.logger,
            input_fn=Inputs(["3", "6", "5"]),
            output_fn=lambda _value: None,
        )
        self.assertEqual(len(manager.load_manifest()["versions"]), 1)

    def test_E_music_pipeline_does_not_change_video_or_assembly(self):
        video_before = deepcopy(self.checkpoint.data["video_generation"])
        assembly_before = deepcopy(self.checkpoint.data["assembly"])
        add_local_music(
            MusicAssetManager(self.paths),
            build_music_provider_registry(),
            self.music_one,
        )
        loaded = ProjectCheckpoint.load(self.paths)
        self.assertEqual(loaded.data["video_generation"], video_before)
        self.assertEqual(loaded.data["assembly"], assembly_before)

    def test_F_export_input_snapshot_exposes_active_music_without_ffmpeg(self):
        add_local_music(
            MusicAssetManager(self.paths),
            build_music_provider_registry(),
            self.music_one,
            music_volume=0.3,
        )
        snapshot = load_active_music_export_input(self.paths)
        self.assertEqual(snapshot.version, 1)
        self.assertEqual(snapshot.music_volume, 0.3)
        self.assertEqual(snapshot.extension, "wav")
        self.assertTrue(snapshot.music_path.is_file())

    def test_G_invalid_volume_or_file_is_rejected_without_version(self):
        with self.assertRaises(ValueError):
            MusicAddRequest(self.music_one, music_volume=1.1)
        fake = self.root / "fake.mp3"
        fake.write_text("not an audio file", encoding="utf-8")
        with self.assertRaises(MusicProviderError):
            add_local_music(
                MusicAssetManager(self.paths),
                build_music_provider_registry(),
                fake,
            )
        self.assertFalse(self.paths.music_manifest_path().exists())


if __name__ == "__main__":
    unittest.main()
