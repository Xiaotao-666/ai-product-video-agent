"""Human-visible model routing and pre-submit confirmation for one Shot."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable, Mapping

from video_generation_request import ProviderSelection, VideoGenerationRequest
from video_provider import VideoProvider, VideoProviderError
from video_provider_registry import ProviderRoute, VideoProviderRegistry
from visual_input import visual_input_asset_ids, visual_input_label


InputFunction = Callable[[str], str]
OutputFunction = Callable[[str], None]


@dataclass(frozen=True)
class GenerationDecision:
    action: str
    provider_selection: ProviderSelection | None = None
    metadata: Mapping[str, str | None] | None = None


def _provider_label(value: str) -> str:
    return "MiniMax" if value.lower() == "minimax" else value


def _mode_lines(adapter: VideoProvider) -> list[str]:
    labels = {
        "none": "Text-to-Video",
        "first_frame": "First Frame",
        "reference_asset": "Reference Asset",
        "first_last_frame": "First / Last Frame",
        "previous_shot_frame": "Previous Shot Frame",
        "generated_keyframe": "Generated Keyframe",
    }
    return [
        labels.get(mode, mode)
        for mode in sorted(adapter.capabilities.supported_visual_modes)
    ]


def _manual_selection(
    registry: VideoProviderRegistry,
    visual_mode: str,
    *,
    input_func: InputFunction,
    output: OutputFunction,
) -> ProviderSelection | str:
    adapters = registry.registered_adapters()
    while True:
        output("\n========== 可用视频模型 ==========")
        for index, adapter in enumerate(adapters, 1):
            output(f"\n{index}. {adapter.model_name}")
            output("\n支持：")
            for label in _mode_lines(adapter):
                output(f"- {label}")
        output("\n0. 返回")
        raw = input_func("请选择视频模型：").strip()
        if raw == "0":
            return "back"
        if not raw.isdigit() or not 1 <= int(raw) <= len(adapters):
            output("无效选择。")
            continue
        adapter = adapters[int(raw) - 1]
        if adapter.supports(visual_mode):
            return ProviderSelection(
                adapter.provider_name, adapter.model_name, "manual"
            )
        supported = "\n".join(
            sorted(adapter.capabilities.supported_visual_modes)
        )
        output(
            f"\n{adapter.model_name} 不支持当前：\n"
            f"{visual_input_label(visual_mode)}\n\n支持的模式：\n{supported}"
        )
        output("\n请选择：\n1. 更换模型\n2. 更换 Visual Input\n3. 取消")
        choice = input_func("请输入 1、2 或 3: ").strip()
        if choice == "2":
            return "change_visual"
        if choice == "3":
            return "cancel"


def _initial_selection(
    registry: VideoProviderRegistry,
    request: VideoGenerationRequest,
    *,
    regeneration: bool,
    previous_metadata: Mapping[str, Any] | None,
    input_func: InputFunction,
    output: OutputFunction,
) -> ProviderSelection | str | None:
    mode = request.required_capability
    recommended = registry.default_selection(mode)
    if regeneration:
        previous = dict(previous_metadata or {})
        output(f"\n========== 本次重新生成 ==========")
        output(f"\nVisual Input：\n{visual_input_label(mode)}")
        output(
            f"\n上次模型：\n{previous.get('provider_model') or '无历史模型'}"
        )
        output(
            "\n请选择：\n"
            "1. 保持上次模型\n"
            "2. 自动选择模型\n"
            "3. 手动更换模型\n"
            "4. 更换 Visual Input\n"
            "5. 取消"
        )
        choice = input_func("请输入 1-5: ").strip()
        if choice == "1":
            if previous.get("provider") and previous.get("provider_model"):
                return ProviderSelection(
                    str(previous["provider"]),
                    str(previous["provider_model"]),
                    "manual",
                )
            output("没有可复用的上次模型，请重新选择。")
            return "retry"
        if choice == "2":
            return None
        if choice == "3":
            return _manual_selection(
                registry, mode, input_func=input_func, output=output
            )
        if choice == "4":
            return "change_visual"
        if choice == "5":
            return "cancel"
        output("无效选择。")
        return "retry"

    output(f"\n========== Shot {int(request.shot_id or 0):02d} 视频模型 ==========")
    output(f"\n当前视觉输入：\n{visual_input_label(mode)}")
    output(
        f"\n推荐模型：\n{recommended.model if recommended else '未配置'}"
    )
    output("\n请选择：\n1. 自动选择推荐模型\n2. 手动选择视频模型\n3. 返回")
    choice = input_func("请输入 1、2 或 3: ").strip()
    if choice == "1":
        return None
    if choice == "2":
        return _manual_selection(
            registry, mode, input_func=input_func, output=output
        )
    if choice == "3":
        return "change_visual"
    output("无效选择。")
    return "retry"


def _show_confirmation(
    request: VideoGenerationRequest,
    route: ProviderRoute,
    prompt_version: int | None,
    output: OutputFunction,
) -> None:
    metadata = route.metadata(request.required_capability)
    references = visual_input_asset_ids(request.visual_input)
    output("\n========== 视频生成确认 ==========")
    output(f"\nShot：\n{int(request.shot_id or 0):02d}")
    output(
        f"\nPrompt Version：\n"
        f"v{prompt_version if prompt_version is not None else '?'}"
    )
    output(f"\nVisual Input：\n{visual_input_label(request.required_capability)}")
    output(f"\nReference：\n{', '.join(references) if references else '无'}")
    output(f"\nProvider：\n{_provider_label(str(metadata['provider']))}")
    output(f"\nModel：\n{metadata['provider_model']}")
    output(f"\nAPI Version：\n{metadata['provider_api_version']}")
    output(f"\nGeneration Mode：\n{metadata['generation_mode']}")
    output(f"\nResolution：\n{request.resolution}")
    output(f"\nDuration：\n{request.duration} 秒")
    output(f"\nModel Selection：\n{route.selection_mode.upper()}")
    output(f"\nCredential：\n{route.credential_env_name or '未声明'}")
    output("\n注意：这里只显示环境变量名称，不显示 API Key 内容。")


def choose_and_confirm_video_generation(
    registry: VideoProviderRegistry,
    request: VideoGenerationRequest,
    *,
    prompt_version: int | None,
    regeneration: bool = False,
    previous_metadata: Mapping[str, Any] | None = None,
    input_func: InputFunction = input,
    output: OutputFunction = print,
) -> GenerationDecision:
    """Select, locally preflight, and confirm a new billable submission."""
    force_model_menu = False
    while True:
        if force_model_menu:
            selected = _manual_selection(
                registry,
                request.required_capability,
                input_func=input_func,
                output=output,
            )
            force_model_menu = False
        else:
            selected = _initial_selection(
                registry,
                request,
                regeneration=regeneration,
                previous_metadata=previous_metadata,
                input_func=input_func,
                output=output,
            )
        if selected == "retry" or selected == "back":
            continue
        if selected in {"change_visual", "cancel"}:
            return GenerationDecision(str(selected))
        provider_selection = (
            selected if isinstance(selected, ProviderSelection) else None
        )
        routed_request = replace(
            request, provider_selection=provider_selection
        )
        try:
            route = registry.preflight(routed_request)
        except VideoProviderError as exc:
            output(f"\n{exc}")
            output("\n请选择：\n1. 更换模型\n2. 更换 Visual Input\n3. 取消")
            action = input_func("请输入 1、2 或 3: ").strip()
            if action == "1":
                force_model_menu = True
                continue
            if action == "2":
                return GenerationDecision("change_visual")
            if action == "3":
                return GenerationDecision("cancel")
            output("无效选择。")
            continue

        _show_confirmation(routed_request, route, prompt_version, output)
        output(
            "\n请选择：\n"
            "1. 确认并生成\n"
            "2. 更换模型\n"
            "3. 更换 Visual Input\n"
            "4. 取消"
        )
        action = input_func("请输入 1-4: ").strip()
        if action == "1":
            return GenerationDecision(
                "generate",
                provider_selection,
                route.metadata(routed_request.required_capability),
            )
        if action == "2":
            force_model_menu = True
            continue
        if action == "3":
            return GenerationDecision("change_visual")
        if action == "4":
            return GenerationDecision("cancel")
        output("无效选择。")
