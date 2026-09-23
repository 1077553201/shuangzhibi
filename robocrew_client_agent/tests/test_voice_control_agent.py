from __future__ import annotations

import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import support  # noqa: F401  # Adds robocrew_client_agent to sys.path.

import voice_control_agent as voice


class FakeRobot:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.is_connected = True

    def disconnect(self) -> None:
        self.events.append("robot.disconnect")
        self.is_connected = False


class FakeServo:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.robot = FakeRobot(events)

    def disconnect(self) -> None:
        self.events.append("servo.disconnect")
        self.robot.is_connected = False

    def connect(self) -> None:
        self.events.append("servo.connect")
        self.robot.is_connected = True

    def release_arm_for_recording(self, arm_side: str = "both") -> None:
        self.events.append(f"release.{arm_side}")

    def restore_arm_after_recording(self, arm_side: str = "both") -> None:
        self.events.append(f"restore.{arm_side}")


class VoiceControlModeTests(unittest.TestCase):
    def test_keyboard_command_uses_explicit_whitelist(self) -> None:
        for text in ("键盘控制", "进入键盘模式", "切换到手动控制", "teleop"):
            with self.subTest(text=text):
                self.assertTrue(voice.is_keyboard_control_command(text))
        for text in ("不要键盘控制", "退出键盘控制", "键盘坏了吗"):
            with self.subTest(text=text):
                self.assertFalse(voice.is_keyboard_control_command(text))

    def test_vr_command_uses_explicit_whitelist(self) -> None:
        for text in (
            "VR",
            "VR控制",
            "进入 VR 控制",
            "虚拟现实控制",
            "切换到虚拟现实控制",
            "vrcontrol",
        ):
            with self.subTest(text=text):
                self.assertTrue(voice.is_vr_control_command(text))

        for text in ("退出VR控制", "不要VR控制", "VR设备在哪里", ""):
            with self.subTest(text=text):
                self.assertFalse(voice.is_vr_control_command(text))

    def test_keyboard_keeps_host_and_reconnects_after_ensure(self) -> None:
        events: list[str] = []
        servo = FakeServo(events)
        remote_controller = MagicMock()
        remote_controller.ensure_host_running.side_effect = (
            lambda **_kwargs: events.append("ensure.host")
        )
        completed = subprocess.CompletedProcess([], returncode=0)

        with (
            patch.object(
                voice.subprocess,
                "run",
                side_effect=lambda *_args, **_kwargs: events.append("keyboard.run")
                or completed,
            ),
            patch.object(voice, "drain_console_input", return_value=None),
        ):
            voice.run_keyboard_control_mode(servo, remote_controller)

        self.assertEqual(
            events,
            ["servo.disconnect", "keyboard.run", "ensure.host", "servo.connect"],
        )
        remote_controller.stop_remote_service.assert_not_called()
        remote_controller.stop_all_services.assert_not_called()

    def test_keyboard_ctrl_c_skips_reconnect(self) -> None:
        events: list[str] = []
        servo = FakeServo(events)
        remote_controller = MagicMock()

        with (
            patch.object(voice.subprocess, "run", side_effect=KeyboardInterrupt),
            patch.object(voice, "drain_console_input", return_value=None),
            self.assertRaises(KeyboardInterrupt),
        ):
            voice.run_keyboard_control_mode(servo, remote_controller)

        self.assertEqual(events, ["servo.disconnect"])
        remote_controller.ensure_host_running.assert_not_called()

    def test_keyboard_host_recovery_failure_does_not_reconnect(self) -> None:
        events: list[str] = []
        servo = FakeServo(events)
        remote_controller = MagicMock()
        remote_controller.ensure_host_running.side_effect = RuntimeError("host down")

        with (
            patch.object(
                voice.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], returncode=0),
            ),
            patch.object(voice, "drain_console_input", return_value=None),
            self.assertRaises(voice.ModeRecoveryError),
        ):
            voice.run_keyboard_control_mode(servo, remote_controller)

        self.assertEqual(events, ["servo.disconnect"])

    def test_keyboard_process_error_recovers_and_returns(self) -> None:
        events: list[str] = []
        servo = FakeServo(events)
        remote_controller = MagicMock()
        remote_controller.ensure_host_running.side_effect = (
            lambda **_kwargs: events.append("ensure.host")
        )

        with (
            patch.object(voice.subprocess, "run", side_effect=OSError("cannot run")),
            patch.object(voice, "drain_console_input", return_value=None),
        ):
            voice.run_keyboard_control_mode(servo, remote_controller)

        self.assertEqual(
            events,
            ["servo.disconnect", "ensure.host", "servo.connect"],
        )

    def test_vr_normal_round_trip_reconnects_client(self) -> None:
        events: list[str] = []
        servo = FakeServo(events)
        remote_controller = MagicMock()
        fake_proc = MagicMock()
        fake_proc.poll.return_value = 0
        fake_proc.returncode = 0

        with (
            patch.object(voice.subprocess, "Popen", return_value=fake_proc) as popen,
            patch.object(
                voice,
                "wait_for_local_vr_exit",
                side_effect=lambda _process: events.append("wait.vr"),
            ),
            patch.object(
                voice,
                "stop_local_vr_process",
                side_effect=lambda _process: events.append("stop.vr"),
            ),
            patch.object(voice, "drain_console_input", return_value=None),
            patch.object(
                voice,
                "wait_for_manual_arm_reset",
                side_effect=lambda _servo: events.append("manual.reset"),
            ),
        ):
            voice.run_vr_control_mode(servo, remote_controller)

        self.assertEqual(
            events,
            ["servo.disconnect", "wait.vr", "stop.vr", "servo.connect", "manual.reset"],
        )
        popen.assert_called_once()
        self.assertTrue(popen.call_args.kwargs.get("start_new_session"))
        self.assertIs(popen.call_args.kwargs.get("stdin"), subprocess.DEVNULL)
        remote_controller.start_vr.assert_not_called()
        remote_controller.stop_vr_and_restore_host.assert_not_called()

    def test_vr_start_failure_still_restores_and_returns(self) -> None:
        events: list[str] = []
        servo = FakeServo(events)
        remote_controller = MagicMock()

        with (
            patch.object(voice.subprocess, "Popen", side_effect=OSError("cannot run")),
            patch.object(
                voice,
                "stop_local_vr_process",
                side_effect=lambda _process: events.append("stop.vr"),
            ),
            patch.object(voice, "drain_console_input", return_value=None),
            patch.object(
                voice,
                "wait_for_manual_arm_reset",
                side_effect=lambda _servo: events.append("manual.reset"),
            ),
        ):
            voice.run_vr_control_mode(servo, remote_controller)

        self.assertEqual(
            events,
            ["servo.disconnect", "stop.vr", "servo.connect", "manual.reset"],
        )

    def test_vr_restore_failure_is_fatal_and_does_not_reconnect(self) -> None:
        events: list[str] = []
        servo = FakeServo(events)
        remote_controller = MagicMock()
        fake_proc = MagicMock()
        fake_proc.returncode = 0
        servo.connect = MagicMock(side_effect=RuntimeError("host failed"))

        with (
            patch.object(voice.subprocess, "Popen", return_value=fake_proc),
            patch.object(voice, "wait_for_local_vr_exit", return_value=None),
            patch.object(
                voice,
                "stop_local_vr_process",
                side_effect=lambda _process: events.append("stop.vr"),
            ),
            patch.object(voice, "drain_console_input", return_value=None),
            self.assertRaises(voice.ModeRecoveryError),
        ):
            voice.run_vr_control_mode(servo, remote_controller)

        self.assertEqual(events, ["servo.disconnect", "stop.vr"])

    def test_vr_ctrl_c_returns_to_agent(self) -> None:
        events: list[str] = []
        servo = FakeServo(events)
        remote_controller = MagicMock()
        fake_proc = MagicMock()
        fake_proc.returncode = 0

        with (
            patch.object(voice.subprocess, "Popen", return_value=fake_proc),
            patch.object(voice, "wait_for_local_vr_exit", side_effect=KeyboardInterrupt),
            patch.object(
                voice,
                "stop_local_vr_process",
                side_effect=lambda _process: events.append("stop.vr"),
            ),
            patch.object(voice, "drain_console_input", return_value=None),
            patch.object(
                voice,
                "wait_for_manual_arm_reset",
                side_effect=lambda _servo: events.append("manual.reset"),
            ),
        ):
            voice.run_vr_control_mode(servo, remote_controller)

        self.assertEqual(
            events,
            ["servo.disconnect", "stop.vr", "servo.connect", "manual.reset"],
        )

    def test_wait_for_vr_exit_accepts_q_without_remote_poll(self) -> None:
        remote_controller = MagicMock()
        with (
            patch.object(voice, "kbhit", return_value=True),
            patch.object(voice, "getwch", return_value="q"),
        ):
            voice.wait_for_vr_exit(remote_controller)
        remote_controller.remote_process_running.assert_not_called()

    def test_wait_for_vr_exit_detects_remote_failure(self) -> None:
        remote_controller = MagicMock()
        remote_controller.remote_process_running.return_value = False
        with (
            patch.object(voice, "kbhit", return_value=False),
            self.assertRaisesRegex(RuntimeError, "意外退出"),
        ):
            voice.wait_for_vr_exit(remote_controller)

    def test_wait_for_local_vr_exit_accepts_q(self) -> None:
        process = MagicMock()
        process.poll.return_value = None
        with (
            patch.object(voice, "kbhit", return_value=True),
            patch.object(voice, "getwch", return_value="q"),
        ):
            voice.wait_for_local_vr_exit(process)

    def test_wait_for_local_vr_exit_returns_when_process_exits(self) -> None:
        process = MagicMock()
        process.poll.return_value = 0
        voice.wait_for_local_vr_exit(process)

    def test_wait_for_manual_arm_reset_releases_then_restores_after_enter(self) -> None:
        events: list[str] = []
        servo = FakeServo(events)
        with patch("builtins.input", return_value=""):
            voice.wait_for_manual_arm_reset(servo)
        self.assertEqual(events, ["release.both", "restore.both"])

    def test_wait_for_manual_arm_reset_keeps_torque_off_on_ctrl_c(self) -> None:
        events: list[str] = []
        servo = FakeServo(events)
        with patch("builtins.input", side_effect=KeyboardInterrupt):
            voice.wait_for_manual_arm_reset(servo)
        self.assertEqual(events, ["release.both"])

    def test_execute_task_routes_vr_without_calling_agent(self) -> None:
        servo = FakeServo([])
        remote_controller = MagicMock()
        with (
            patch.object(voice, "run_vr_control_mode") as run_vr,
            patch.object(voice, "run_one_task") as run_agent,
        ):
            result = voice.execute_task(
                "VR控制",
                object(),
                servo,
                object(),
                remote_controller,
                show_raw_output=False,
                enable_tts=False,
            )

        self.assertTrue(result)
        run_vr.assert_called_once_with(servo, remote_controller)
        run_agent.assert_not_called()

    def test_main_ensures_host_before_client_and_cleans_up(self) -> None:
        events: list[str] = []
        remote_controller = MagicMock()
        remote_controller.ensure_host_running.side_effect = (
            lambda **_kwargs: events.append("ensure.host")
        )
        servo = FakeServo(events)
        args = SimpleNamespace(
            seconds=5.0,
            sample_rate=16000,
            model="medium",
            device="auto",
            asr_backend="auto",
            camera_key="camera_0",
            show_raw_output=False,
            no_tts=True,
            silence_threshold=0.001,
            auto_pause_seconds=1.5,
            input_device=None,
            list_audio_devices=False,
        )

        with (
            patch.object(voice, "parse_args", return_value=args),
            patch.object(voice, "configure_runtime_output", return_value=None),
            patch.object(voice, "require_module", return_value=None),
            patch.object(voice, "ensure_voice_dependencies", return_value=None),
            patch.object(
                voice,
                "get_remote_service_controller",
                return_value=remote_controller,
            ),
            patch.object(voice, "create_asr", return_value=object()),
            patch.object(voice, "ClientServoControler", return_value=servo),
            patch.object(voice, "ClientRobotCamera", return_value=object()),
            patch.object(voice, "build_agent", return_value=object()),
            patch("builtins.input", return_value="quit"),
            patch.object(
                voice,
                "shutdown_remote_services",
                side_effect=lambda: events.append("shutdown.remote"),
            ),
        ):
            voice.main()

        self.assertEqual(
            events,
            ["ensure.host", "servo.connect", "servo.disconnect", "shutdown.remote"],
        )


if __name__ == "__main__":
    unittest.main()
