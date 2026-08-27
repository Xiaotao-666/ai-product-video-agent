"""Read-only bundle completeness; a version allocation is not a video asset."""
from pathlib import Path

from project_manager import create_project_paths
from shot_storage import BUNDLE_FILES, ShotStorageError, read_bundle_json, validate_bundle
from visual_input import VisualInputError


def video_bundle_complete(project_dir: Path, shot_number: int, version: int) -> bool:
    try:
        root = project_dir.resolve(strict=True)
        directory = root / "shots" / f"shot_{shot_number:02d}" / f"v{version:03d}"
        for node in (root / "shots", directory.parent, directory):
            if node.is_symlink() or not node.is_dir() or node.resolve() != node:
                return False
        for name in BUNDLE_FILES:
            item = directory / name
            if item.is_symlink() or not item.is_file() or item.stat().st_size <= 0:
                return False
        paths = create_project_paths(root, ensure_directories=False)
        validate_bundle(paths, shot_number, version, require_video=True)
        read_bundle_json(paths, shot_number, version, "safety.json")
        return True
    except (OSError, ValueError, TypeError, KeyError, ShotStorageError, VisualInputError):
        return False
