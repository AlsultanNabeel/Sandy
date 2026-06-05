#!/usr/bin/env python3
"""Thin entrypoint for Sandy agent runtime."""

from app.agent.facade.agent import *  # noqa: F401,F403
from app.agent.facade.agent import _should_send_briefing  # noqa: F401  (test_think_pending_flows patches this)


if __name__ == "__main__":
    main()  # noqa: F405
