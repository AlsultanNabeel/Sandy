import json
from pathlib import Path
from typing import Any, Optional


def read_json_file(path: Optional[Path], default: Any) -> Any:
    """Read JSON file safely and return default on failure."""
    if not path:
        return default

    file_path = Path(path)
    if not file_path.exists():
        return default

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json_file(path: Optional[Path], data: Any) -> bool:
    """Write JSON file safely. Return True on success."""
    if not path:
        return False

    file_path = Path(path)
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False
