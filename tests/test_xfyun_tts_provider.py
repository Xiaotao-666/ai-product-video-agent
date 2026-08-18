from __future__ import annotations

import base64
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from project_manager import create_project_paths
from project_state import ProjectCheckpoint
from providers.xfyun_tts_provider import XFYUN_ENDPOINT, XfyunTTSProvider
from voice_assets import VoiceAssetManager
from voice_generation import generate_confirmed_voice
from voice_provider import VoiceGenerationRequest, VoiceProviderError
from voice_provider_registry import build_voice_provider_registry


class MockWebSocket:
    def __init__(self, responses: list[dict]) -> None:
        self._responses = iter(json.dumps(item) for item in responses)
        self.sent: list[dict] = []
        self.closed = False

    def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))

    def recv(self) -> str:
        return next(self._responses)

    def close(self) -> None:
        self.closed = True


class WebSocketDouble:
    def __init__(self, *, error_code: int = 0) -> None:
        self.calls = 0
        self.last_url: str | None = None
        self.last_timeout: float | None = None
        pcm = b"\x00\x00" * 1600
        if error_code:
            responses = [
                {
                    "code": error_code,
                    "message": "mock provider error",
                    "sid": "xfyun-error-sid",
                    "data": {"status": 2},
                }
            ]
        else:
            responses = [
                {
                    "code": 0,
                    "message": "success",
                    "sid": "xfyun-sid-001",
                    "data": {
                        "status": 1,
                        "audio": base64.b64encode(pcm[:1600]).decode("ascii"),
                    },
                },
                {
                    "code": 0,
                    "message": "success",
                    "sid": "xfyun-sid-001",
                    "data": {
                        "status": 2,
                        "audio": base64.b64encode(pcm[1600:]).decode("ascii"),
                    },
                },
            ]
        self.socket = MockWebSocket(responses)

    def __call__(self, url: str, *, timeout: float) -> MockWebSocket:
        self.calls += 1
        self.last_url = url
        self.last_timeout = timeout
        return self.socket


class XfyunTTSProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.paths = create_project_paths(Path(self.temp.name) / "project")
        self.websocket = WebSocketDouble()
        self.environ = {
            "XFYUN_APP_ID": "mock-app-id",
            "XFYUN_API_KEY": "mock-api-key",
            "XFYUN_API_SECRET": "mock-api-secret",
        }
        self.config = {
            "default_provider": "xfyun_tts",
            "providers": {
                "xfyun_tts": {
                    "enabled": True,
                    "model": "online-tts-v2",
                    "language": "zh-CN",
                    "sample_rate": 16000,
                    "default_voice": "xiaoyan",
                },
                "aliyun_tts": {"enabled": False},
            },
        }
        self.request = VoiceGenerationRequest(
            script="欢迎了解本次品牌宣传片。",
            voice="xiaoyan",
            language="zh-CN",
            output_format="wav",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def registry(self, environ=None, websocket=None):
        return build_voice_provider_registry(
            self.config,
            environ=self.environ if environ is None else environ,
            xfyun_websocket_factory=(
                self.websocket if websocket is None else websocket
            ),
            xfyun_date_factory=lambda: "Sun, 17 Aug 2026 01:00:00 GMT",
        )

    def generate(self, answers=None):
        prompts = iter(answers or ["1"])
        return generate_confirmed_voice(
            VoiceAssetManager(self.paths),
            self.registry(),
            self.request,
            input_fn=lambda _prompt="": next(prompts),
            output_fn=lambda _message: None,
        )

    def test_A_provider_registration_and_safe_metadata(self):
        registry = self.registry()
        adapter = registry.resolve(self.request)
        self.assertIsInstance(adapter, XfyunTTSProvider)
        self.assertEqual(adapter.provider_name, "xfyun_tts")
        self.assertEqual(adapter.model_name, "online-tts-v2")
        self.assertEqual(adapter.api_version, "websocket-v2")
        metadata = adapter.get_metadata()
        self.assertEqual(metadata["endpoint"], XFYUN_ENDPOINT)
        serialized = json.dumps(metadata)
        self.assertNotIn("mock-api-key", serialized)
        self.assertNotIn("mock-api-secret", serialized)

    def test_B_preflight_blocks_missing_credentials_and_invalid_voice(self):
        self.registry().preflight(self.request)
        self.assertEqual(self.websocket.calls, 0)
        for name in ("XFYUN_APP_ID", "XFYUN_API_KEY", "XFYUN_API_SECRET"):
            with self.subTest(name=name):
                missing = dict(self.environ)
                missing[name] = ""
                with self.assertRaises(VoiceProviderError) as raised:
                    self.registry(missing).preflight(self.request)
                self.assertIn(name, str(raised.exception))
        invalid = VoiceGenerationRequest(
            script="测试",
            voice="中文发音人名称",
            language="zh-CN",
        )
        with self.assertRaises(VoiceProviderError):
            self.registry().preflight(invalid)
        self.assertEqual(self.websocket.calls, 0)

    def test_C_mock_websocket_generation_saves_version_and_exact_payload(self):
        output: list[str] = []
        entry = generate_confirmed_voice(
            VoiceAssetManager(self.paths),
            self.registry(),
            self.request,
            input_fn=lambda _prompt="": "1",
            output_fn=output.append,
        )
        self.assertEqual(entry["version"], 1)
        self.assertEqual(entry["provider"], "xfyun_tts")
        self.assertEqual(entry["provider_task_id"], "xfyun-sid-001")
        self.assertAlmostEqual(entry["duration_seconds"], 0.1, places=3)
        self.assertEqual(self.websocket.calls, 1)
        self.assertTrue(self.websocket.socket.closed)
        self.assertIn("Xfyun TTS", "\n".join(output))

        payload = self.websocket.socket.sent[0]
        self.assertEqual(payload["common"], {"app_id": "mock-app-id"})
        self.assertEqual(payload["business"]["aue"], "raw")
        self.assertEqual(payload["business"]["auf"], "audio/L16;rate=16000")
        self.assertEqual(payload["business"]["vcn"], "xiaoyan")
        decoded = base64.b64decode(payload["data"]["text"]).decode("utf-8")
        self.assertEqual(decoded, self.request.script)
        self.assertEqual(payload["data"]["status"], 2)

        parsed_url = urlparse(self.websocket.last_url)
        self.assertEqual(
            f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}",
            XFYUN_ENDPOINT,
        )
        query = parse_qs(parsed_url.query)
        self.assertEqual(query["host"], ["tts-api.xfyun.cn"])
        self.assertIn("authorization", query)
        self.assertIn("date", query)

        audio = self.paths.voice_version_audio_path(1).read_bytes()
        self.assertEqual(audio[:4], b"RIFF")
        self.assertEqual(audio[8:12], b"WAVE")
        config = json.loads(
            self.paths.voice_version_config_path(1).read_text(encoding="utf-8")
        )
        self.assertEqual(config["provider"], "xfyun_tts")
        self.assertEqual(config["voice"], "xiaoyan")
        self.assertNotIn("mock-api-secret", json.dumps(config))

    def test_D_resume_reads_saved_audio_without_repeating_generation(self):
        self.generate()
        before = self.websocket.calls
        resumed = VoiceAssetManager(create_project_paths(self.paths.project_path))
        active = resumed.active_version()
        self.assertEqual(active["version"], 1)
        self.assertEqual(active["provider_task_id"], "xfyun-sid-001")
        self.assertEqual(self.websocket.calls, before)

    def test_E_voice_generation_does_not_change_video_pipeline(self):
        checkpoint = ProjectCheckpoint.create(
            self.paths,
            "Xfyun Voice Test",
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

    def test_F_provider_error_is_safe_and_does_not_create_version(self):
        failing = WebSocketDouble(error_code=11200)
        with self.assertRaises(VoiceProviderError) as raised:
            generate_confirmed_voice(
                VoiceAssetManager(self.paths),
                self.registry(websocket=failing),
                self.request,
                input_fn=lambda _prompt="": "1",
                output_fn=lambda _message: None,
            )
        message = str(raised.exception)
        self.assertIn("11200", message)
        self.assertNotIn("mock-api-key", message)
        self.assertNotIn("mock-api-secret", message)
        self.assertFalse(self.paths.voice_manifest_path().exists())


if __name__ == "__main__":
    unittest.main()
