"""User-confirmed orchestration for future real VoiceProvider calls."""

from __future__ import annotations

from collections.abc import Callable

from voice_assets import VoiceAssetManager
from voice_provider import VoiceGenerationRequest, VoiceProvider
from voice_provider_registry import VoiceProviderRegistry


def _read_script(
    input_fn: Callable[[str], str], output_fn: Callable[[str], None]
) -> str:
    output_fn("请输入新的配音文本，可以输入多行；单独输入 END 表示完成：")
    lines: list[str] = []
    while True:
        line = input_fn("")
        if line.strip() == "END":
            break
        lines.append(line)
    return "\n".join(lines).strip()


def confirm_voice_generation(
    request: VoiceGenerationRequest,
    provider: VoiceProvider,
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> VoiceGenerationRequest | None:
    """Return a confirmed request, or None without calling the provider."""
    current = request
    while True:
        provider.preflight(current)
        settings = dict(current.settings)
        script_source = str(settings.get("script_source") or "manual")
        storyboard_source = script_source in {
            "compiled_storyboard",
            "storyboard_edited",
        }
        source_label = {
            "compiled_storyboard": "Storyboard Planned",
            "storyboard_edited": "Storyboard Planned (Edited for this Voice Version)",
            "manual": "Manual Script",
        }.get(script_source, script_source)
        provider_label = {
            "aliyun_tts": "Aliyun TTS",
            "xfyun_tts": "Xfyun TTS",
        }.get(provider.provider_name, provider.provider_name)
        output_fn("\n========== Voice Generation Confirmation ==========")
        output_fn(f"\nSource:\n{source_label}")
        output_fn(f"\nProvider:\n{provider_label}")
        output_fn(f"\nLanguage:\n{current.language}")
        output_fn(f"\nVoice:\n{current.voice}")
        planned_duration = settings.get("planned_narration_duration")
        if planned_duration is not None:
            output_fn(f"\nPlanned Narration:\n约 {float(planned_duration):g} 秒")
        output_fn(f"\nScript:\n{current.script}")
        output_fn("\nOutput:\naudio.wav")
        if storyboard_source:
            output_fn(
                "\n1. 确认并生成\n2. 编辑本次配音文本"
                "\n3. 改用手动文本\n4. 取消"
            )
            choice = input_fn("请选择 1、2、3 或 4：").strip()
        else:
            output_fn("\n1. Confirm Generate\n2. Edit Script\n3. Cancel")
            choice = input_fn("请选择 1、2 或 3：").strip()
        if choice == "1":
            return current
        if choice == "2":
            edited = _read_script(input_fn, output_fn)
            if not edited:
                output_fn("配音文本不能为空，请重新编辑。")
                continue
            updated_settings = dict(current.settings)
            updated_settings["script_source"] = (
                "storyboard_edited" if storyboard_source else "manual"
            )
            current = VoiceGenerationRequest(
                script=edited,
                voice=current.voice,
                language=current.language,
                output_format=current.output_format,
                settings=updated_settings,
            )
            continue
        if choice == "3" and storyboard_source:
            manual = _read_script(input_fn, output_fn)
            if not manual:
                output_fn("配音文本不能为空，请重新编辑。")
                continue
            updated_settings = dict(current.settings)
            updated_settings["script_source"] = "manual"
            current = VoiceGenerationRequest(
                script=manual,
                voice=current.voice,
                language=current.language,
                output_format=current.output_format,
                settings=updated_settings,
            )
            continue
        if (choice == "4" and storyboard_source) or (
            choice == "3" and not storyboard_source
        ):
            output_fn("已取消 Voice Generation，未发送 API 请求。")
            return None
        output_fn("输入无效，请重新选择。")


def generate_confirmed_voice(
    manager: VoiceAssetManager,
    registry: VoiceProviderRegistry,
    request: VoiceGenerationRequest,
    *,
    provider_name: str | None = None,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> dict | None:
    """Preflight, ask for confirmation, then create exactly one Voice Bundle."""
    provider = registry.preflight(request, provider_name)
    confirmed = confirm_voice_generation(
        request,
        provider,
        input_fn=input_fn,
        output_fn=output_fn,
    )
    if confirmed is None:
        return None
    return manager.generate_and_save(confirmed, provider)
