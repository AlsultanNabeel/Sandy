"""Tests for router_node — route_after_router()."""

import unittest
from app.agent.graph.state import create_initial_state, merge_state


class TestRouteAfterRouter(unittest.TestCase):

    def setUp(self):
        from app.agent.nodes.router import route_after_router
        self.route = route_after_router

    def _state(self, **kwargs):
        s = create_initial_state("مرحبا", "u1", "c1")
        return merge_state(s, kwargs)

    def test_no_pending_goes_execute(self):
        self.assertEqual(self.route(self._state()), "execute_node")

    def test_pending_goes_pending_node(self):
        s = self._state(pending_state={"pending_type": "confirmation"})
        self.assertEqual(self.route(s), "pending_node")

    def test_pending_with_execute_direct_goes_execute(self):
        s = self._state(
            pending_state={"pending_type": "confirmation"},
            routing_hint="execute_direct",
        )
        self.assertEqual(self.route(s), "execute_node")

    def test_requires_clarification_goes_clarify(self):
        s = self._state(requires_clarification=True)
        self.assertEqual(self.route(s), "clarify_node")

    def test_calendar_pick_suggestion_goes_execute(self):
        s = self._state(
            intent="calendar.pick_suggestion",
            pending_state={"pending_type": "conflict_resolution"},
        )
        self.assertEqual(self.route(s), "execute_node")

    def test_pending_overrides_clarification(self):
        s = self._state(
            pending_state={"pending_type": "confirmation"},
            requires_clarification=True,
        )
        self.assertEqual(self.route(s), "pending_node")


class TestRouterNode(unittest.TestCase):

    def test_router_node_preserves_message(self):
        from app.agent.nodes.router import router_node
        state = create_initial_state("أنشئ مهمة", "u1", "c1")
        result = router_node(state)
        self.assertEqual(result["message"], "أنشئ مهمة")

    # ملاحظة: حساب الـ complexity انتقل من router_node إلى fc_router،
    # فما عاد router_node يضبطها — التستات القديمة لذلك انحذفت.


if __name__ == "__main__":
    unittest.main()
