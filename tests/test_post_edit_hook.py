from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_post_edit_hook_emits_continue_json(tmp_path: Path) -> None:
    edited_file = tmp_path / "edited.py"
    edited_file.write_text("value = 1\n", encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, "scripts/post_edit_hook.py"],
        input=json.dumps({"tool_input": {"files": [str(edited_file)]}}),
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert json.loads(completed.stdout) == {"continue": True}


def test_write_new_file_size_too_small_blocks(tmp_path: Path) -> None:
    # create a small file but indicate a much larger expected size
    edited_file = tmp_path / "new.txt"
    edited_file.write_text("short", encoding="utf-8")

    payload = {
        "tool_name": "write_new_file",
        "tool_input": {"files": [str(edited_file)], "expected_size": 100},
    }

    completed = subprocess.run(
        [sys.executable, "scripts/post_edit_hook.py"],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 2


def test_write_new_file_size_ok_continues(tmp_path: Path) -> None:
    edited_file = tmp_path / "new2.txt"
    edited_file.write_text("long-enough-content\n", encoding="utf-8")

    payload = {
        "tool_name": "write_new_file",
        "tool_input": {"files": [str(edited_file)], "expected_size": 10},
    }

    completed = subprocess.run(
        [sys.executable, "scripts/post_edit_hook.py"],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert json.loads(completed.stdout) == {"continue": True}