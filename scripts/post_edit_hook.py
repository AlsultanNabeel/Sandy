#!/usr/bin/env python3
"""Post-edit hook used by the tests.

Reads a JSON payload from stdin and says whether CI should keep going.

For a "write_new_file" tool call with an expected_size, every listed file
has to exist and be at least that big. If one is missing or too small, exit 2
to block. Anything else (and the happy path) prints {"continue": True} and
exits 0.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    try:
        raw = sys.stdin.read()
        if not raw:
            payload = {}
        else:
            payload = json.loads(raw)
    except Exception as exc:  # pragma: no cover - defensive
        print(f"invalid input: {exc}", file=sys.stderr)
        return 1

    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input") or {}
    files = list(tool_input.get("files") or [])

    if tool_name == "write_new_file":
        expected = tool_input.get("expected_size")
        if expected is None:
            # no size to check
            print(json.dumps({"continue": True}))
            return 0
        try:
            expected = int(expected)
        except Exception:
            print("invalid expected_size", file=sys.stderr)
            return 2

        for p in files:
            try:
                size = Path(p).stat().st_size
            except FileNotFoundError:
                print(f"missing file: {p}", file=sys.stderr)
                return 2
            if size < expected:
                print(f"file too small: {p} ({size} < {expected})", file=sys.stderr)
                return 2

        print(json.dumps({"continue": True}))
        return 0

    # default: allow
    print(json.dumps({"continue": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
