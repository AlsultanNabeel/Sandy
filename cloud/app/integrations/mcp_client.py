"""MCPHub — manages MCP server subprocesses (JSON-RPC 2.0 over stdio).

Each registered server starts lazily on first use and stays alive until
the process exits. Falls back gracefully if Node.js is not installed.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import pathlib
import select
import shutil
import subprocess
import threading
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_ROOT = pathlib.Path(__file__).resolve().parents[3]  # Sandy/ root


# One subprocess per MCP server

class _MCPProcess:
    """Single MCP server subprocess, kept alive between calls."""

    def __init__(self, server_id: str, cmd: List[str], env: Optional[Dict] = None) -> None:
        self.server_id = server_id
        self.cmd = cmd
        self.env = {**os.environ, **(env or {})}
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._next_id = 0
        self._ready = False

    def _start(self) -> bool:
        if shutil.which(self.cmd[0]) is None:
            logger.warning(f"[MCP] '{self.cmd[0]}' not found — {self.server_id} disabled")
            return False
        try:
            self._proc = subprocess.Popen(
                self.cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                env=self.env,
            )
            return self._handshake()
        except Exception as exc:
            logger.error(f"[MCP] failed to start {self.server_id}: {exc}")
            return False

    def _handshake(self) -> bool:
        resp = self._rpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "clientInfo": {"name": "sandy", "version": "1.0"},
        })
        if "error" in resp:
            logger.error(f"[MCP] handshake failed ({self.server_id}): {resp['error']}")
            return False
        self._write({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        logger.info(f"[MCP] {self.server_id} ready")
        return True

    def _write(self, obj: dict) -> None:
        if self._proc and self._proc.stdin:
            try:
                self._proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
                self._proc.stdin.flush()
            except BrokenPipeError:
                pass

    def _read_response(self, req_id: int, timeout: float = 5.0) -> dict:
        if not self._proc or not self._proc.stdout:
            return {"error": "no process"}
        fd = self._proc.stdout.fileno()
        for _ in range(200):  # skip notifications, max 200 lines
            ready, _, _ = select.select([fd], [], [], timeout)
            if not ready:
                return {"error": "response timeout"}
            try:
                line = self._proc.stdout.readline()
            except Exception:
                return {"error": "read error"}
            if not line:
                return {"error": "EOF"}
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("id") == req_id:
                return obj
        return {"error": "response timeout"}

    def _rpc(self, method: str, params: dict) -> dict:
        self._next_id += 1
        rid = self._next_id
        self._write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        return self._read_response(rid)

    def call_tool(self, tool_name: str, args: dict) -> dict:
        with self._lock:
            if not self._ready:
                self._ready = self._start()
            if not self._ready:
                return {"handled": False, "reply": f"خدمة {self.server_id} غير متاحة حالياً."}

            if self._proc and self._proc.poll() is not None:
                logger.warning(f"[MCP] {self.server_id} died — restarting")
                self._ready = self._start()
                if not self._ready:
                    return {"handled": False, "reply": f"خدمة {self.server_id} توقفت."}

            resp = self._rpc("tools/call", {"name": tool_name, "arguments": args})
            if "error" in resp:
                return {"handled": False, "reply": f"خطأ MCP ({self.server_id}): {resp['error']}"}

            result = resp.get("result", {})
            content = result.get("content") or []
            texts = [
                c["text"] for c in content
                if isinstance(c, dict) and c.get("type") == "text" and c.get("text")
            ]
            return {"handled": True, "reply": "\n".join(texts) or str(result)}

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()


class MCPHub:
    """Manages multiple MCP server subprocesses."""

    def __init__(self) -> None:
        self._servers: Dict[str, _MCPProcess] = {}

    def register(
        self,
        server_id: str,
        cmd: List[str],
        env: Optional[Dict] = None,
    ) -> None:
        self._servers[server_id] = _MCPProcess(server_id, cmd, env)
        logger.debug(f"[MCPHub] registered server: {server_id}")

    def call(self, server_id: str, tool_name: str, args: Dict[str, Any]) -> dict:
        server = self._servers.get(server_id)
        if not server:
            return {"handled": False, "reply": f"MCP server غير مسجل: {server_id}"}
        return server.call_tool(tool_name, args)

    def shutdown_all(self) -> None:
        for server in self._servers.values():
            server.stop()


_hub: Optional[MCPHub] = None


def get_mcp_hub() -> MCPHub:
    global _hub
    if _hub is None:
        _hub = _build_default_hub()
    return _hub


def _build_default_hub() -> MCPHub:
    hub = MCPHub()

    github_token = os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN", "")
    if github_token:
        hub.register(
            "github",
            _resolve_cmd("@modelcontextprotocol/server-github", "mcp-server-github"),
            env={"GITHUB_PERSONAL_ACCESS_TOKEN": github_token},
        )
    else:
        logger.info("[MCPHub] GITHUB_PERSONAL_ACCESS_TOKEN not set — github server disabled")

    return hub


def _resolve_cmd(pkg: str, bin_name: str) -> List[str]:
    """Use local node_modules if installed, otherwise fall back to npx."""
    local = _ROOT / "node_modules" / ".bin" / bin_name
    if local.is_file():
        return ["node", str(local)]
    return ["npx", "-y", pkg]


def _cleanup_mcp_zombies() -> None:
    global _hub
    if _hub is not None:
        _hub.shutdown_all()


atexit.register(_cleanup_mcp_zombies)


def _reset_for_testing() -> None:
    """للاختبارات فقط."""
    global _hub
    _hub = None
