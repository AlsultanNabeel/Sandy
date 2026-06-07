#!/usr/bin/env python3
"""Thin entrypoint for Sandy agent runtime."""

from app.agent.facade.agent import _should_send_briefing, main  # noqa: F401  (_should_send_briefing: test_think_pending_flows patches this)


if __name__ == "__main__":
    main()
