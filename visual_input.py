"""Versioned visual-input descriptors used by Shot generation bundles."""

from __future__ import annotations

import copy
from typing import Any


SUPPORTED_VISUAL_MODES = {"none", "reference_asset", "first_frame"}
RESERVED_VISUAL_MODES = {
    "none",
    "reference_asset",
    "first_frame",
    "generated_keyframe",
    "first_last_frame",
    "previous_shot_frame",
}
LEGACY_VISUAL_MODE_ALIASES = {"reference_image": "first_frame"}
RESERVED_VISUAL_SOURCES = {
    "user_upload",
    "multimodal_llm",
    "image_model",
    "previous_shot",
    "system_generated",
}


class VisualInputError(RuntimeError):
    """Raised when a visual-input descriptor is invalid."""


class VisualInputNotImplementedError(VisualInputError):
    code = "NOT_IMPLEMENTED"

    def __init__(self, mode: str) -> None:
        super().__init__(f"NOT_IMPLEMENTED: visual input mode '{mode}' is not implemented.")
        self.mode = mode


def none_visual_input() -> dict[str, Any]:
    return {"mode": "none", "source": None, "assets": []}


def normalize_visual_input(value: Any) -> dict[str, Any]:
    if value is None:
        return none_visual_input()
    if not isinstance(value, dict):
        raise VisualInputError("visual_input must be a JSON object.")
    mode = str(value.get("mode") or "none").strip()
    mode = LEGACY_VISUAL_MODE_ALIASES.get(mode, mode)
    source = value.get("source")
    source = str(source).strip() if source is not None else None
    raw_assets = value.get("assets") or []
    if not isinstance(raw_assets, list):
        raise VisualInputError("visual_input.assets must be a list.")
    assets: list[dict[str, Any]] = []
    for raw in raw_assets:
        if not isinstance(raw, dict):
            raise VisualInputError("Each visual input asset must be an object.")
        default_role = "reference_image" if mode == "reference_asset" else "first_frame"
        role = str(raw.get("role") or default_role).strip()
        if mode == "first_frame" and role == "start_frame":
            role = "first_frame"
        asset = {
            "asset_id": str(raw.get("asset_id") or "").strip(),
            "role": role,
            "path": str(raw.get("path") or "").strip(),
            "sha256": str(raw.get("sha256") or "").strip().lower(),
        }
        if not asset["asset_id"] or not asset["path"] or not asset["sha256"]:
            raise VisualInputError("Visual input asset is missing asset_id/path/sha256.")
        assets.append(asset)
    if mode == "none":
        return none_visual_input()
    if source is None:
        raise VisualInputError("A non-empty source is required for visual input.")
    return {"mode": mode, "source": source, "assets": assets}


def ensure_supported_visual_input(value: Any) -> dict[str, Any]:
    normalized = normalize_visual_input(value)
    mode = normalized["mode"]
    if mode not in SUPPORTED_VISUAL_MODES:
        raise VisualInputNotImplementedError(mode)
    if mode in {"reference_asset", "first_frame"} and not normalized["assets"]:
        raise VisualInputError(f"{mode} mode requires at least one asset.")
    return normalized


def _image_visual_input(
    asset: dict[str, Any], mode: str, role: str, *, source: str | None = None
) -> dict[str, Any]:
    visual = {
        "mode": mode,
        "source": source or str(asset.get("source") or "user_upload"),
        "assets": [
            {
                "asset_id": asset["asset_id"],
                "role": role,
                "path": asset["project_path"],
                "sha256": asset["sha256"],
            }
        ],
    }
    return ensure_supported_visual_input(visual)


def reference_asset_visual_input(
    asset: dict[str, Any], *, source: str | None = None
) -> dict[str, Any]:
    return _image_visual_input(
        asset, "reference_asset", "reference_image", source=source
    )


def first_frame_visual_input(
    asset: dict[str, Any], *, source: str | None = None
) -> dict[str, Any]:
    return _image_visual_input(asset, "first_frame", "first_frame", source=source)


def reference_visual_input(
    asset: dict[str, Any], *, source: str | None = None
) -> dict[str, Any]:
    """Backward-compatible constructor for the old first-frame behavior."""
    return first_frame_visual_input(asset, source=source)


def visual_input_label(value: Any) -> str:
    if isinstance(value, str):
        mode = LEGACY_VISUAL_MODE_ALIASES.get(value, value)
    else:
        mode = normalize_visual_input(value)["mode"]
    return {
        "none": "None",
        "reference_asset": "Product / Subject Reference",
        "first_frame": "First Frame",
        "generated_keyframe": "Generated Keyframe",
        "first_last_frame": "First / Last Frame",
        "previous_shot_frame": "Previous Shot Frame",
    }.get(mode, "Unknown")


def visual_input_snapshot(value: Any) -> dict[str, Any]:
    return copy.deepcopy(normalize_visual_input(value))


def visual_input_asset_ids(value: Any) -> list[str]:
    return [
        str(item["asset_id"])
        for item in normalize_visual_input(value).get("assets", [])
    ]
