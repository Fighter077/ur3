"""Motion-mode regression tests; no robot connection is created."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, call, patch

sys.path.insert(0, str(Path(__file__).parents[1]))
from robot.controller import RTDEController, RobotError
from robot import controller


class StartupConnectionTests(unittest.TestCase):
    def test_failed_startup_keeps_backend_in_mock_and_allows_manual_retry(self):
        ip = "192.0.2.10"
        connected_robot = Mock()
        connected_robot.is_connected.return_value = True
        with (
            patch.object(controller, "_singleton", None),
            patch.object(controller, "_current_ip", ip),
            patch.object(controller.config, "ROBOT_MOCK", False),
            patch.object(controller.config, "ROBOT_IP", ip),
            patch.object(controller, "RTDEController", side_effect=[
                RobotError("robot unreachable"), connected_robot,
            ]) as connect,
        ):
            fallback = controller.get_controller()
            self.assertIsInstance(fallback, controller.MockController)
            self.assertIs(controller.get_controller(), fallback)
            connect.assert_called_once_with(ip)

            self.assertTrue(controller.set_controller(mock=False)["ok"])
            self.assertIs(controller.get_controller(), connected_robot)


class MotionTests(unittest.TestCase):
    def setUp(self):
        self.robot = RTDEController.__new__(RTDEController)
        self.robot._motion_kind = None
        self.robot._ctrl = lambda fn: fn()
        self.control = self.robot._rtde_c = Mock()
        self.robot._rtde_r = Mock()
        self.robot._rtde_r.getActualQd.return_value = [0] * 6
        self.pose = [0.2, 0, 0.3, 0, 3.14, 0]

    def test_zero_jog_ends_speed_thread(self):
        self.robot.jog_tcp(dx=0.01)
        self.control.reset_mock()
        self.robot.jog_tcp()
        self.control.speedStop.assert_called_once()
        self.control.speedL.assert_not_called()
        self.control.stopL.assert_not_called()

    def test_move_ends_existing_speed_thread_first(self):
        self.robot.jog_tcp(dx=0.01)
        self.control.reset_mock()
        self.robot.move_tcp(self.pose)
        self.assertEqual(self.control.mock_calls[0], call.speedStop())
        self.assertEqual(self.control.mock_calls[1][0], "moveL")

    def test_stop_matches_linear_and_joint_motion(self):
        self.robot.move_tcp(self.pose)
        self.robot.stop()
        self.control.stopL.assert_called_once_with(2.0)
        self.control.speedStop.assert_not_called()
        self.robot.move_joints([0] * 6)
        self.robot.stop()
        self.control.stopJ.assert_called_once_with(2.0)

    def test_async_thread_keeps_stationary_robot_busy(self):
        self.control.getAsyncOperationProgress.return_value = 0
        self.robot._motion_kind = "linear"
        self.assertTrue(self.robot.is_busy())
        self.control.getAsyncOperationProgress.return_value = -1
        self.assertFalse(self.robot.is_busy())
        self.assertIsNone(self.robot._motion_kind)

    def test_failed_status_read_is_not_completion(self):
        self.control.getAsyncOperationProgress.side_effect = RuntimeError("lost connection")
        with self.assertRaises(RuntimeError):
            self.robot.is_busy()

    def test_rejected_move_is_reported(self):
        self.control.moveL.return_value = False
        with self.assertRaises(RobotError):
            self.robot.move_tcp(self.pose)


if __name__ == "__main__":
    unittest.main()
