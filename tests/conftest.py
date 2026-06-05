import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "cloud"))


@pytest.fixture(autouse=True)
def _reset_tool_registry():
    """يمسح الـ ToolRegistry وMCPHub singletons بين كل test."""
    from app.agent.tools.registry import _reset_for_testing as _reset_reg
    from app.agent.tools.setup import _reset_for_testing as _reset_setup
    from app.integrations.mcp_client import _reset_for_testing as _reset_mcp
    _reset_reg()
    _reset_setup()
    _reset_mcp()
    yield
    _reset_reg()
    _reset_setup()
    _reset_mcp()
