from __future__ import annotations

import io
import json
import tempfile
import unittest
import wave
from copy import deepcopy
from pathlib import Path

from project_manager import create_project_paths
from project_state import ProjectCheckpoint
from providers.aliyun_tts_provider import AliyunTTSProvider
from voice_assets import VoiceAssetManager
from voice_generation import generate_confirmed_voice
from voice_provider import VoiceGenerationRequest, VoiceProviderError
from voice_provider_registry import build_voice_provider_registry


def silent_wav(duration_seconds: float = 0.1, sample_rate: int = 16000) -> bytes:
    buffer = io.BytesIO()
    frames = b"\x00\x00" * max(1, int(duration_seconds * sample_rate))
    with wave.open(buffer, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(sample_rate)
        stream.writeframes(frames)
    return buffer.getvalue()


class FakeResponse:
    def __init__(self, content: bytes, task_id: str = "aliyun-task-001") -> None:
        self.status_code = 200
        self.content = content
        self.headers = {"task_id": task_id, "Content-Type": "audio/mpeg"}

    def json(self):
        raise ValueError("audio response")


class NetworkDouble:
    def __init__(self) -> None:
        self.token_calls = 0
        self.http_calls = 0
        self.last_url = None
        self.last_payload = None

    def fetch_token(self, access_key_id: str, access_key_secret: str) -> str:
        self.token_calls += 1
        self.asserted_access_key_id = access_key_id
        self.asserted_access_key_secret = access_key_secret
        return "mock-nls-token"

    def post(self, url, *, json, timeout):
        self.http_calls += 1
        self.last_url = url
        self.last_payload = dict(json)
        self.last_timeout = timeout
        return FakeResponse(silent_wav())


class AliyunTTSProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.paths = create_project_paths(Path(self.temp.name) / "project")
        self.network = NetworkDouble()
        self.environ = {
            "ALIYUN_ACCESS_KEY_ID": "mock-access-id",
            "ALIYUN_ACCESS_KEY_SECRET": "mock-access-secret",
            "ALIYUN_TTS_APP_KEY": "mock-project-app-key",
            "ALIYUN_TTS_REGION": "cn-shanghai",
        }
        self.config = {
            "default_provider": "aliyun_tts",
            "providers": {
                "aliyun_tts": {
                    "enabled": True,
                    "model": "nls-stream-tts",
                    "language": "zh-CN",
                    "sample_rate": 16000,
                }
            },
        }
        self.request = VoiceGenerationRequest(
            script="欢迎了解本次品牌宣传片。",
            voice="xiaoyun",
            language="zh-CN",
            output_format="wav",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def registry(self, environ=None):
        return build_voice_provider_registry(
            self.config,
            environ=self.environ if environ is None else environ,
            token_fetcher=self.network.fetch_token,
            http_post=self.network.post,
        )

    def generate(self, inputs=None):
        answers = iter(inputs or ["1"])
        return generate_confirmed_voice(
            VoiceAssetManager(self.paths),
            self.registry(),
            self.request,
            input_fn=lambda _prompt="": next(answers),
            output_fn=lambda _message: None,
        )

    def test_A_provider_is_registered_with_official_nls_identity(self):
        registry = self.registry()
        adapter = registry.resolve(self.request)
        self.assertIsInstance(adapter, AliyunTTSProvider)
        self.assertEqual(adapter.provider_name, "aliyun_tts")
        self.assertEqual(adapter.model_name, "nls-stream-tts")
        self.assertTrue(adapter.supports(self.request))
        metadata = adapter.get_metadata()
        self.assertEqual(metadata["endpoint"], "https://nls-gateway-cn-shanghai.aliyuncs.com/stream/v1/tts")
        self.assertNotIn("mock-access-secret", json.dumps(metadata))

    def test_B_preflight_passes_and_missing_credentials_block_before_network(self):
        adapter = self.registry().preflight(self.request)
        self.assertIsInstance(adapter, AliyunTTSProvider)
        self.assertEqual((self.network.token_calls, self.network.http_calls), (0, 0))

        for name in (
            "ALIYUN_ACCESS_KEY_ID",
            "ALIYUN_ACCESS_KEY_SECRET",
            "ALIYUN_TTS_APP_KEY",
            "ALIYUN_TTS_REGION",
        ):
            with self.subTest(name=name):
                missing = dict(self.environ)
                missing[name] = ""
                with self.assertRaises(VoiceProviderError) as raised:
                    self.registry(missing).preflight(self.request)
                self.assertIn(name, str(raised.exception))
        self.assertEqual((self.network.token_calls, self.network.http_calls), (0, 0))

    def test_C_confirmed_generation_saves_wav_and_safe_version_metadata(self):
        entry = self.generate()
        self.assertEqual(entry["version"], 1)
        self.assertEqual(entry["provider"], "aliyun_tts")
        self.assertEqual(entry["provider_task_id"], "aliyun-task-001")
        self.assertAlmostEqual(entry["duration_seconds"], 0.1, places=2)
        self.assertEqual((self.network.token_calls, self.network.http_calls), (1, 1))
        self.assertEqual(self.network.last_payload["format"], "wav")
        self.assertEqual(self.network.last_payload["voice"], "xiaoyun")

        config_path = self.paths.voice_version_config_path(1)
        config = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(config["provider"], "aliyun_tts")
        self.assertEqual(config["model"], "nls-stream-tts")
        self.assertEqual(config["voice"], "xiaoyun")
        self.assertEqual(config["language"], "zh-CN")
        self.assertIn("created_at", config)
        self.assertAlmostEqual(config["duration"], 0.1, places=2)
        all_project_text = "\n".join(
            path.read_text(encoding="utf-8", errors="ignore")
            for path in self.paths.voice_dir.rglob("*")
            if path.is_file() and path.suffix != ".wav"
        )
        self.assertNotIn("mock-access-secret", all_project_text)
        self.assertNotIn("mock-nls-token", all_project_text)

    def test_D_resume_reads_saved_version_without_repeating_generation(self):
        self.generate()
        before = (self.network.token_calls, self.network.http_calls)
        resumed = VoiceAssetManager(create_project_paths(self.paths.project_path))
        active = resumed.active_version()
        self.assertEqual(active["version"], 1)
        self.assertEqual(active["provider_task_id"], "aliyun-task-001")
        self.assertEqual((self.network.token_calls, self.network.http_calls), before)

    def test_E_voice_generation_does_not_change_video_pipeline_state(self):
        checkpoint = ProjectCheckpoint.create(
            self.paths,
            "Aliyun Voice Test",
            {
                "product_name": "Product",
                "product_description": "Description",
                "user_notes": "",
            },
        )
        checkpoint.ensure_shots([1])
        video_before = deepcopy(checkpoint.data["video_generation"])
        assembly_before = deepcopy(checkpoint.data["assembly"])
        self.generate()
        loaded = ProjectCheckpoint.load(self.paths)
        self.assertEqual(loaded.data["video_generation"], video_before)
        self.assertEqual(loaded.data["assembly"], assembly_before)

    def test_F_cancel_confirmation_has_zero_network_and_zero_version(self):
        result = self.generate(["3"])
        self.assertIsNone(result)
        self.assertEqual((self.network.token_calls, self.network.http_calls), (0, 0))
        self.assertFalse(self.paths.voice_manifest_path().exists())

    def test_G_edit_script_is_confirmed_before_one_generation(self):
        entry = self.generate(["2", "新的第一行", "新的第二行", "END", "1"])
        script = self.paths.voice_version_script_path(entry["version"]).read_text(
            encoding="utf-8"
        )
        self.assertEqual(script, "新的第一行\n新的第二行")
        self.assertEqual((self.network.token_calls, self.network.http_calls), (1, 1))


if __name__ == "__main__":
    unittest.main()
