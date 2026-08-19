"""Project-scoped reference image import, validation, deduplication, and CLI selection."""

from __future__ import annotations

import hashlib
import json
import shutil
import struct
import zlib
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from project_manager import ProjectPaths
from task_logger import TaskLogger
from visual_input import (
    first_frame_visual_input,
    none_visual_input,
    normalize_visual_input,
    reference_asset_visual_input,
    visual_input_label,
)


ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_IMAGE_BYTES = 20 * 1024 * 1024


class ReferenceAssetError(RuntimeError):
    """Raised when a reference image cannot be imported or verified."""


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _jpeg_dimensions(data: bytes) -> tuple[int, int]:
    offset = 2
    sof = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    while offset + 4 <= len(data):
        if data[offset] != 0xFF:
            offset += 1
            continue
        marker = data[offset + 1]
        offset += 2
        if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
            continue
        if offset + 2 > len(data):
            break
        length = int.from_bytes(data[offset : offset + 2], "big")
        if length < 2 or offset + length > len(data):
            break
        if marker in sof and length >= 7:
            height = int.from_bytes(data[offset + 3 : offset + 5], "big")
            width = int.from_bytes(data[offset + 5 : offset + 7], "big")
            return width, height
        offset += length
    raise ReferenceAssetError("JPEG image dimensions cannot be decoded.")


def inspect_image(path: Path) -> tuple[str, int, int]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ReferenceAssetError(f"Reference image cannot be read: {exc}") from exc
    if not data:
        raise ReferenceAssetError("Reference image is empty.")
    if len(data) > MAX_IMAGE_BYTES:
        raise ReferenceAssetError("Reference image exceeds the configured 20MB limit.")
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        offset = 8
        compressed = bytearray()
        saw_end = False
        while offset + 12 <= len(data):
            length = int.from_bytes(data[offset : offset + 4], "big")
            end = offset + 12 + length
            if end > len(data):
                raise ReferenceAssetError("PNG chunk is truncated.")
            kind = data[offset + 4 : offset + 8]
            payload = data[offset + 8 : offset + 8 + length]
            expected_crc = int.from_bytes(data[offset + 8 + length : end], "big")
            if zlib.crc32(kind + payload) & 0xFFFFFFFF != expected_crc:
                raise ReferenceAssetError("PNG CRC validation failed.")
            if kind == b"IDAT":
                compressed.extend(payload)
            if kind == b"IEND":
                saw_end = True
                break
            offset = end
        if not compressed or not saw_end:
            raise ReferenceAssetError("PNG image is incomplete.")
        try:
            zlib.decompress(bytes(compressed))
        except zlib.error as exc:
            raise ReferenceAssetError("PNG image data cannot be decoded.") from exc
        image_type = "png"
    elif data.startswith(b"\xff\xd8"):
        if not data.endswith(b"\xff\xd9"):
            raise ReferenceAssetError("JPEG image is incomplete.")
        width, height = _jpeg_dimensions(data)
        image_type = "jpeg"
    elif data.startswith(b"RIFF") and data[8:12] == b"WEBP" and len(data) >= 30:
        declared_size = int.from_bytes(data[4:8], "little") + 8
        if declared_size > len(data):
            raise ReferenceAssetError("WebP image is truncated.")
        chunk = data[12:16]
        if chunk == b"VP8X":
            width = 1 + int.from_bytes(data[24:27], "little")
            height = 1 + int.from_bytes(data[27:30], "little")
        elif chunk == b"VP8 " and len(data) >= 30:
            width = int.from_bytes(data[26:28], "little") & 0x3FFF
            height = int.from_bytes(data[28:30], "little") & 0x3FFF
        elif chunk == b"VP8L" and len(data) >= 25:
            bits = int.from_bytes(data[21:25], "little")
            width = (bits & 0x3FFF) + 1
            height = ((bits >> 14) & 0x3FFF) + 1
        else:
            raise ReferenceAssetError("WebP image dimensions cannot be decoded.")
        image_type = "webp"
    else:
        raise ReferenceAssetError("File content is not a readable JPG/JPEG/PNG/WebP image.")
    if width <= 0 or height <= 0:
        raise ReferenceAssetError("Reference image dimensions are invalid.")
    return image_type, width, height


class ReferenceAssetManager:
    def __init__(
        self, project: ProjectPaths, task_logger: TaskLogger | None = None
    ) -> None:
        self.project = project
        self.task_logger = task_logger
        self.project.references_dir.mkdir(parents=True, exist_ok=True)
        self.project.project_references_dir.mkdir(parents=True, exist_ok=True)
        if not self.project.reference_manifest_path().is_file():
            self._save_manifest({"version": 1, "assets": []})

    def _load_manifest(self) -> dict[str, Any]:
        path = self.project.reference_manifest_path()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ReferenceAssetError(f"Reference manifest cannot be read: {exc}") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("assets"), list):
            raise ReferenceAssetError("Reference manifest format is invalid.")
        return payload

    def _save_manifest(self, payload: dict[str, Any]) -> None:
        self.project.save_json(self.project.reference_manifest_path(), payload)

    def list_assets(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._load_manifest().get("assets", [])]

    def asset(self, asset_id: str) -> dict[str, Any]:
        for item in self.list_assets():
            if item.get("asset_id") == asset_id:
                return item
        raise ReferenceAssetError(f"Unknown reference asset: {asset_id}")

    def asset_path(self, asset_id: str) -> Path:
        record = self.asset(asset_id)
        return self.project.ensure_within_project(
            self.project.project_path / str(record["project_path"])
        )

    def import_image(self, source_path: str | Path) -> dict[str, Any]:
        source = Path(str(source_path).strip().strip('"')).expanduser().resolve()
        if source.suffix.lower() not in ALLOWED_EXTENSIONS:
            raise ReferenceAssetError("Only JPG, JPEG, PNG, and WebP are supported.")
        if not source.is_file():
            raise ReferenceAssetError(f"Reference image does not exist: {source}")
        image_type, width, height = inspect_image(source)
        expected_image_type = {
            ".jpg": "jpeg",
            ".jpeg": "jpeg",
            ".png": "png",
            ".webp": "webp",
        }[source.suffix.lower()]
        if image_type != expected_image_type:
            raise ReferenceAssetError(
                "Reference image extension does not match its decoded format."
            )
        digest = _sha256(source)
        manifest = self._load_manifest()
        for existing in manifest["assets"]:
            if str(existing.get("sha256")) != digest:
                continue
            target = self.project.ensure_within_project(
                self.project.project_path / str(existing["project_path"])
            )
            if target.is_file() and target.stat().st_size == source.stat().st_size and _sha256(target) == digest:
                if self.task_logger:
                    self.task_logger.event(
                        "REFERENCE_ASSET_REUSED",
                        asset_id=existing.get("asset_id"),
                        project_path=existing.get("project_path"),
                    )
                return dict(existing)

        used_ids = {str(item.get("asset_id")) for item in manifest["assets"]}
        index = 1
        while f"ref_{index:03d}" in used_ids:
            index += 1
        asset_id = f"ref_{index:03d}"
        extension = "jpg" if image_type == "jpeg" else image_type
        target = self.project.reference_asset_path(asset_id, extension)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            if target.stat().st_size != source.stat().st_size or _sha256(target) != digest:
                target.unlink(missing_ok=True)
                raise ReferenceAssetError("Copied reference image failed size/SHA-256 validation.")
            inspect_image(target)
        except OSError as exc:
            target.unlink(missing_ok=True)
            raise ReferenceAssetError(f"Reference image import failed: {exc}") from exc
        record = {
            "asset_id": asset_id,
            "filename": target.name,
            "type": "reference_image",
            "source": "user_upload",
            "original_source_path": str(source),
            "project_path": target.relative_to(self.project.project_path).as_posix(),
            "sha256": digest,
            "file_size": target.stat().st_size,
            "width": width,
            "height": height,
            "added_at": _now_iso(),
        }
        manifest["assets"].append(record)
        try:
            self._save_manifest(manifest)
        except Exception:
            target.unlink(missing_ok=True)
            raise
        if self.task_logger:
            self.task_logger.event(
                "REFERENCE_ASSET_IMPORTED",
                asset_id=asset_id,
                project_path=record["project_path"],
                sha256=digest,
            )
        return dict(record)

    def validate_visual_input(self, value: Any) -> dict[str, Any]:
        visual = normalize_visual_input(value)
        if visual["mode"] == "none":
            return visual
        if visual["mode"] not in {"reference_asset", "first_frame"}:
            from visual_input import VisualInputNotImplementedError

            raise VisualInputNotImplementedError(visual["mode"])
        if not visual["assets"]:
            raise ReferenceAssetError(f"{visual['mode']} mode requires an image asset.")
        expected_role = (
            "reference_image" if visual["mode"] == "reference_asset" else "first_frame"
        )
        for asset in visual["assets"]:
            if asset.get("role") != expected_role:
                raise ReferenceAssetError(
                    f"{visual['mode']} requires asset role={expected_role}."
                )
            record = self.asset(asset["asset_id"])
            if str(record["project_path"]) != str(asset["path"]):
                raise ReferenceAssetError("Reference asset path does not match its manifest.")
            path = self.asset_path(asset["asset_id"])
            if not path.is_file() or path.stat().st_size <= 0:
                raise ReferenceAssetError(f"Reference asset is missing: {asset['asset_id']}")
            inspect_image(path)
            digest = _sha256(path)
            if digest != str(asset["sha256"]) or digest != str(record["sha256"]):
                raise ReferenceAssetError("Reference asset SHA-256 validation failed.")
        return visual


def _print_assets(manager: ReferenceAssetManager, output: Callable[[str], None]) -> list[dict[str, Any]]:
    assets = manager.list_assets()
    if not assets:
        output("当前项目还没有参考图片。")
        return []
    for index, asset in enumerate(assets, 1):
        output(
            f"{index}. {asset['asset_id']} | {asset['filename']} | "
            f"{asset.get('width')}x{asset.get('height')}"
        )
    return assets


def choose_reference_asset(
    manager: ReferenceAssetManager,
    *,
    mode: str = "first_frame",
    input_fn: Callable[[str], str] = input,
    output: Callable[[str], None] = print,
) -> dict[str, Any] | None:
    assets = _print_assets(manager, output)
    if not assets:
        return None
    raw = input_fn("请选择参考图片编号（0 返回）：").strip()
    if raw == "0":
        return None
    if not raw.isdigit() or not 1 <= int(raw) <= len(assets):
        output("无效选择。")
        return None
    asset = assets[int(raw) - 1]
    if mode == "reference_asset":
        return reference_asset_visual_input(asset)
    if mode == "first_frame":
        return first_frame_visual_input(asset)
    raise ReferenceAssetError(f"Unsupported selectable visual input mode: {mode}")


def import_reference_interactive(
    manager: ReferenceAssetManager,
    *,
    mode: str = "first_frame",
    input_fn: Callable[[str], str] = input,
    output: Callable[[str], None] = print,
) -> dict[str, Any] | None:
    raw = input_fn("请输入参考图片完整路径（留空返回）：").strip()
    if not raw:
        return None
    try:
        asset = manager.import_image(raw)
    except ReferenceAssetError as exc:
        output(f"参考图片导入失败：{exc}")
        return None
    output(f"参考图片已导入：{manager.asset_path(asset['asset_id'])}")
    output(f"Reference ID：{asset['asset_id']}")
    output(f"文件名：{asset['filename']}")
    output(f"项目内路径：{asset['project_path']}")
    if mode == "reference_asset":
        return reference_asset_visual_input(asset)
    if mode == "first_frame":
        return first_frame_visual_input(asset)
    raise ReferenceAssetError(f"Unsupported selectable visual input mode: {mode}")


def setup_project_references(
    manager: ReferenceAssetManager,
    *,
    input_fn: Callable[[str], str] = input,
    output: Callable[[str], None] = print,
) -> None:
    output("\n是否为本项目添加参考图片？\n1. 添加\n2. 跳过")
    while input_fn("请输入 1 或 2：").strip() == "1":
        import_reference_interactive(manager, input_fn=input_fn, output=output)
        if input_fn("继续添加参考图片？(y/N)：").strip().lower() not in {"y", "yes"}:
            break


def select_shot_visual_input(
    manager: ReferenceAssetManager,
    shot_id: int,
    current: Any = None,
    *,
    input_fn: Callable[[str], str] = input,
    output: Callable[[str], None] = print,
) -> dict[str, Any]:
    selected = normalize_visual_input(current)
    while True:
        output(f"\n========== Shot {shot_id:02d} 视觉输入 ==========")
        output(f"当前模式：{visual_input_label(selected)} ({selected['mode']})")
        if selected["assets"]:
            output("参考图片：" + ", ".join(a["asset_id"] for a in selected["assets"]))
        output("1. 不使用视觉参考")
        output("\n2. 产品 / 主体参考")
        output("   保持产品、人物或角色形象一致")
        output("   不限制首帧构图")
        output("\n3. 首帧参考")
        output("   当前图片将作为视频起始画面")
        output("\n4. 查看 / 更换参考素材")
        output("\n5. 返回")
        output("\n提示：产品 / 主体参考和首帧参考不是同一个功能。")
        choice = input_fn("请输入 1-5：").strip()
        if choice == "1":
            return none_visual_input()
        if choice in {"2", "3"}:
            mode = "reference_asset" if choice == "2" else "first_frame"
            value = choose_reference_asset(
                manager, mode=mode, input_fn=input_fn, output=output
            )
            if value is None and not manager.list_assets():
                value = import_reference_interactive(
                    manager, mode=mode, input_fn=input_fn, output=output
                )
            if value is not None:
                return value
            continue
        if choice == "4":
            _print_assets(manager, output)
            output("\n1. 更换当前模式使用的参考素材")
            output("2. 导入新素材并用于当前模式")
            output("3. 返回")
            sub_choice = input_fn("请输入 1-3：").strip()
            if selected["mode"] == "none" and sub_choice in {"1", "2"}:
                output("请先在主菜单选择‘产品 / 主体参考’或‘首帧参考’模式。")
                continue
            if sub_choice == "1":
                value = choose_reference_asset(
                    manager,
                    mode=selected["mode"],
                    input_fn=input_fn,
                    output=output,
                )
                if value is not None:
                    selected = value
            elif sub_choice == "2":
                value = import_reference_interactive(
                    manager,
                    mode=selected["mode"],
                    input_fn=input_fn,
                    output=output,
                )
                if value is not None:
                    selected = value
            continue
        if choice == "5":
            return selected
        output("无效选择。")


def select_regeneration_visual_input(
    manager: ReferenceAssetManager,
    shot_id: int,
    current: Any,
    *,
    input_fn: Callable[[str], str] = input,
    output: Callable[[str], None] = print,
) -> dict[str, Any] | None:
    output("\n本次重新生成如何使用参考图片？")
    output("1. 保留当前 Visual Input")
    output("2. 更换 Visual Input")
    output("3. 不使用参考图片")
    output("4. 取消任务")
    choice = input_fn("请输入 1-4：").strip()
    if choice == "1":
        return normalize_visual_input(current)
    if choice == "2":
        return select_shot_visual_input(
            manager, shot_id, current, input_fn=input_fn, output=output
        )
    if choice == "3":
        return none_visual_input()
    if choice == "4":
        return None
    output("无效选择，保留当前 Visual Input。")
    return normalize_visual_input(current)


def select_candidate_visual_input(
    manager: ReferenceAssetManager,
    shot_id: int,
    approved_visual: Any,
    *,
    input_fn: Callable[[str], str] = input,
    output: Callable[[str], None] = print,
) -> dict[str, Any] | None:
    output("\nCandidate 使用哪种 Visual Input？")
    output("1. 继承当前 Approved Visual Input")
    output("2. 更换 Visual Input")
    output("3. 不使用参考图片")
    output("4. 取消")
    choice = input_fn("请输入 1-4：").strip()
    if choice == "1":
        return normalize_visual_input(approved_visual)
    if choice == "2":
        return select_shot_visual_input(
            manager, shot_id, approved_visual, input_fn=input_fn, output=output
        )
    if choice == "3":
        return none_visual_input()
    return None
