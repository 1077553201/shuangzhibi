from __future__ import annotations

import unittest
from unittest.mock import MagicMock

import support  # noqa: F401
import path_setup  # noqa: F401

from lerobot.robots.xlerobot.xlerobot_host import apply_host_command


class ApplyHostCommandTests(unittest.TestCase):
    def test_set_arm_torque_is_consumed_and_not_treated_as_motion(self) -> None:
        robot = MagicMock()
        handled = apply_host_command(
            robot,
            {
                "__xlerobot_host_command": "set_arm_torque",
                "arm_side": "both",
                "enabled": False,
            },
        )
        self.assertTrue(handled)
        robot.set_arm_torque.assert_called_once_with(arm_side="both", enabled=False)
        robot.send_action.assert_not_called()

    def test_ordinary_action_is_left_for_send_action(self) -> None:
        robot = MagicMock()
        handled = apply_host_command(
            robot,
            {"x.vel": 0.1, "y.vel": 0.0, "theta.vel": 0.0},
        )
        self.assertFalse(handled)
        robot.set_arm_torque.assert_not_called()
