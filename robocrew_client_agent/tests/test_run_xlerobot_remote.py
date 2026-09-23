from __future__ import annotations

import unittest
from unittest.mock import MagicMock, call, patch

import support  # noqa: F401  # Adds robocrew_client_agent to sys.path.

import run_xlerobot_remote as remote


class RemoteServiceControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.logs: list[str] = []
        self.controller = remote.RemoteServiceController(
            host="192.0.2.10",
            log_callback=self.logs.append,
        )

    def test_remote_pid_parser_ignores_login_noise(self) -> None:
        compile(remote.REMOTE_PROCESS_INSPECTOR, "<remote-process-inspector>", "exec")
        compile(remote.REMOTE_PROCESS_SIGNALER, "<remote-process-signaler>", "exec")
        compile(remote.REMOTE_WRAPPER_SIGNALER, "<remote-wrapper-signaler>", "exec")
        self.controller._run_control_command = MagicMock(
            return_value=(
                0,
                "welcome\n__XLEROBOT_PID__123\n__XLEROBOT_PID__456\n",
                "",
            )
        )

        self.assertEqual(self.controller.remote_service_pids("host"), {123, 456})
        command = self.controller._run_control_command.call_args.args[0]
        self.assertIn("/proc/[0-9]*/cmdline", command)
        self.assertIn("lerobot.robots.xlerobot.xlerobot_host", command)

    def test_vr_command_uses_robot_arm_ports_and_info_logging(self) -> None:
        command_args = remote.SERVICE_COMMANDS["vr"].split()

        self.assertIn("--left-port=/dev/ttyACM1", command_args)
        self.assertIn("--right-port=/dev/ttyACM0", command_args)
        self.assertNotIn("--left-port=/dev/ttyACM0", command_args)
        self.assertNotIn("--right-port=/dev/ttyACM1", command_args)
        self.assertIn("--log-level=info", command_args)

        # External-process discovery and PID revalidation must describe the
        # same service command, otherwise a corrected VR process cannot be
        # found or stopped safely.
        for remote_script in (
            remote.REMOTE_PROCESS_INSPECTOR,
            remote.REMOTE_PROCESS_SIGNALER,
        ):
            self.assertIn('"--left-port=/dev/ttyACM1"', remote_script)
            self.assertIn('"--right-port=/dev/ttyACM0"', remote_script)

    def test_service_output_captures_wrapper_pid_and_start_time(self) -> None:
        channel = MagicMock()
        self.controller._channel = channel
        self.controller._current_service = "host"

        self.controller._handle_service_output(
            channel,
            "host",
            "__XLEROBOT_WRAPPER_PID__77:9988\r\n",
            "",
        )

        self.assertEqual(
            self.controller._managed_wrapper_identity(channel),
            (77, "9988"),
        )

    def test_service_output_hides_host_empty_command_spam(self) -> None:
        channel = MagicMock()
        self.controller._channel = channel
        self.controller._current_service = "host"

        self.controller._handle_service_output(
            channel,
            "host",
            "WARNING:root:No command available\n"
            "Waiting for commands...\n"
            "WARNING:root:Command not received for more than 500 milliseconds. "
            "Stopping the base.\n",
            "",
        )

        self.assertEqual(self.logs, ["Waiting for commands...\n"])

    def test_service_output_filters_split_noise_lines_across_chunks(self) -> None:
        channel = MagicMock()
        self.controller._channel = channel
        self.controller._current_service = "host"

        self.controller._handle_service_output(
            channel,
            "host",
            "WARNING:root:No command av",
            "",
        )
        self.controller._handle_service_output(
            channel,
            "host",
            "ailable\nHost ready\n",
            "",
        )

        self.assertEqual(self.logs, ["Host ready\n"])

    def test_remote_pid_parser_rejects_invalid_marked_pid(self) -> None:
        self.controller._run_control_command = MagicMock(
            return_value=(0, "__XLEROBOT_PID__not-a-pid\n", "")
        )
        with self.assertRaisesRegex(RuntimeError, "无效 PID"):
            self.controller.remote_service_pids("vr")

    def test_ensure_host_does_not_restart_ready_process(self) -> None:
        self.controller.stop_remote_service = MagicMock(return_value=False)
        self.controller.remote_service_pids = MagicMock(return_value={12})
        self.controller.probe_host_ports = MagicMock(
            return_value={5555: True, 5556: True}
        )
        self.controller.wait_for_host_ready = MagicMock()
        self.controller._start_service_with_channel = MagicMock()

        started = self.controller.ensure_host_running(timeout=60)

        self.assertFalse(started)
        self.controller.stop_remote_service.assert_called_once_with(
            "vr",
            wait_for_serial_release=False,
        )
        self.controller._start_service_with_channel.assert_not_called()
        self.controller.wait_for_host_ready.assert_called_once_with(
            60,
            process_already_seen=True,
        )

    def test_ensure_host_starts_when_process_and_ports_are_absent(self) -> None:
        channel = MagicMock()
        self.controller.stop_remote_service = MagicMock(return_value=False)
        self.controller.remote_service_pids = MagicMock(return_value=set())
        self.controller.probe_host_ports = MagicMock(
            return_value={5555: False, 5556: False}
        )
        self.controller._start_service_with_channel = MagicMock(
            return_value=(True, channel)
        )
        self.controller.wait_for_host_ready = MagicMock()

        started = self.controller.ensure_host_running(timeout=60)

        self.assertTrue(started)
        self.controller._start_service_with_channel.assert_called_once_with(
            "host",
            host_response="\n",
        )
        self.controller.wait_for_host_ready.assert_called_once_with(
            60,
            launch_channel=channel,
            process_already_seen=False,
        )

    def test_ensure_host_rejects_unowned_partial_port(self) -> None:
        self.controller.stop_remote_service = MagicMock(return_value=False)
        self.controller.remote_service_pids = MagicMock(return_value=set())
        self.controller.probe_host_ports = MagicMock(
            return_value={5555: True, 5556: False}
        )

        with self.assertRaisesRegex(RuntimeError, "其他进程占用"):
            self.controller.ensure_host_running(timeout=60)

    def test_wait_for_host_requires_process_and_two_stable_port_checks(self) -> None:
        self.controller.remote_service_pids = MagicMock(
            side_effect=[{21}, {21}, {21}]
        )
        self.controller.probe_host_ports = MagicMock(
            side_effect=[
                {5555: True, 5556: False},
                {5555: True, 5556: True},
                {5555: True, 5556: True},
            ]
        )

        with patch.object(remote.time, "sleep", return_value=None):
            self.controller.wait_for_host_ready(timeout=5)

        self.assertEqual(self.controller.probe_host_ports.call_count, 3)

    def test_wait_for_host_fails_immediately_when_launch_channel_exits(self) -> None:
        channel = MagicMock()
        channel.exit_status_ready.return_value = True
        channel.recv_exit_status.return_value = 7
        self.controller.remote_service_pids = MagicMock(return_value=set())
        self.controller.probe_host_ports = MagicMock(
            return_value={5555: False, 5556: False}
        )

        with self.assertRaisesRegex(RuntimeError, "退出码：7"):
            self.controller.wait_for_host_ready(timeout=60, launch_channel=channel)

    def test_wait_for_previously_seen_external_host_detects_immediate_exit(self) -> None:
        self.controller.remote_service_pids = MagicMock(return_value=set())
        self.controller.probe_host_ports = MagicMock(
            return_value={5555: False, 5556: False}
        )

        with self.assertRaisesRegex(RuntimeError, "意外退出"):
            self.controller.wait_for_host_ready(
                timeout=60,
                process_already_seen=True,
            )

    def test_probe_vr_ports_checks_websocket_and_https_ports(self) -> None:
        connections: list[tuple[str, int]] = []

        def connect(address: tuple[str, int], *, timeout: float):
            del timeout
            connections.append(address)
            connection = MagicMock()
            connection.__enter__.return_value = connection
            return connection

        with patch.object(remote.socket, "create_connection", side_effect=connect):
            result = self.controller.probe_vr_ports()

        self.assertEqual(result, {8442: True, 8443: True})
        self.assertEqual(
            connections,
            [("192.0.2.10", 8442), ("192.0.2.10", 8443)],
        )

    def test_probe_vr_status_reads_https_api(self) -> None:
        response = MagicMock()
        response.status = 200
        response.read.return_value = (
            b'{"running": true, "robot_connected": true, '
            b'"robotEngaged": true, "visualizer_connected": true}'
        )
        connection = MagicMock()
        connection.getresponse.return_value = response

        with patch.object(
            remote.http.client,
            "HTTPSConnection",
            return_value=connection,
        ) as connection_factory:
            result = self.controller.probe_vr_status()

        connection_factory.assert_called_once()
        connection.request.assert_called_once_with("GET", "/api/status")
        connection.close.assert_called_once_with()
        self.assertEqual(
            result,
            {
                "running": True,
                "robot_connected": True,
                "robotEngaged": True,
                "visualizer_connected": True,
            },
        )

    def test_remote_serial_port_owners_parses_both_arm_ports(self) -> None:
        self.controller._run_control_command = MagicMock(
            return_value=(
                0,
                "login noise\n"
                "__XLEROBOT_SERIAL_OWNER__/dev/ttyACM0:77\n"
                "__XLEROBOT_SERIAL_OWNER__/dev/ttyACM1:77\n",
                "",
            )
        )

        self.assertEqual(
            self.controller.remote_serial_port_owners(),
            {"/dev/ttyACM0": {77}, "/dev/ttyACM1": {77}},
        )
        command = self.controller._run_control_command.call_args.args[0]
        self.assertIn("/dev/ttyACM0", command)
        self.assertIn("/dev/ttyACM1", command)

    def test_wait_for_vr_requires_complete_control_chain(self) -> None:
        channel = MagicMock()
        channel.exit_status_ready.return_value = False
        # INFO markers are deliberately absent: readiness must be based on
        # stable process, network, same-PID serial ownership and API health.
        self.controller._vr_connected_arms[channel] = set()
        self.controller.remote_service_pids = MagicMock(return_value={77})
        self.controller.probe_vr_ports = MagicMock(
            return_value={8442: True, 8443: True}
        )
        self.controller.probe_vr_status = MagicMock(
            return_value={
                "running": True,
                "robot_connected": True,
                "left_arm_connected": True,
                "right_arm_connected": True,
                "robotEngaged": True,
                "visualizer_connected": True,
            }
        )
        self.controller.remote_serial_port_owners = MagicMock(
            return_value={
                "/dev/ttyACM0": {77},
                "/dev/ttyACM1": {77},
            }
        )

        with patch.object(remote.time, "sleep", return_value=None):
            self.controller.wait_for_vr_running(timeout=5, launch_channel=channel)

        self.assertGreaterEqual(self.controller.probe_vr_ports.call_count, 1)
        self.assertGreaterEqual(self.controller.probe_vr_status.call_count, 1)
        self.assertGreaterEqual(
            self.controller.remote_serial_port_owners.call_count,
            1,
        )

    def test_wait_for_vr_rejects_each_incomplete_readiness_signal(self) -> None:
        ready_status = {
            "running": True,
            "robot_connected": True,
            "left_arm_connected": True,
            "right_arm_connected": True,
            "robotEngaged": True,
            "visualizer_connected": True,
        }
        cases = {
            "websocket_port": (
                {8442: False, 8443: True},
                ready_status,
                {"/dev/ttyACM0": {77}, "/dev/ttyACM1": {77}},
            ),
            "https_port": (
                {8442: True, 8443: False},
                ready_status,
                {"/dev/ttyACM0": {77}, "/dev/ttyACM1": {77}},
            ),
            "control_loop": (
                {8442: True, 8443: True},
                {**ready_status, "running": False},
                {"/dev/ttyACM0": {77}, "/dev/ttyACM1": {77}},
            ),
            "robot_connection": (
                {8442: True, 8443: True},
                {**ready_status, "robot_connected": False},
                {"/dev/ttyACM0": {77}, "/dev/ttyACM1": {77}},
            ),
            "left_arm_connection": (
                {8442: True, 8443: True},
                {**ready_status, "left_arm_connected": False},
                {"/dev/ttyACM0": {77}, "/dev/ttyACM1": {77}},
            ),
            "right_arm_connection": (
                {8442: True, 8443: True},
                {**ready_status, "right_arm_connected": False},
                {"/dev/ttyACM0": {77}, "/dev/ttyACM1": {77}},
            ),
            "robot_engagement": (
                {8442: True, 8443: True},
                {**ready_status, "robotEngaged": False},
                {"/dev/ttyACM0": {77}, "/dev/ttyACM1": {77}},
            ),
            "visualizer": (
                {8442: True, 8443: True},
                {**ready_status, "visualizer_connected": False},
                {"/dev/ttyACM0": {77}, "/dev/ttyACM1": {77}},
            ),
            "serial_ownership": (
                {8442: True, 8443: True},
                ready_status,
                {"/dev/ttyACM0": {77}, "/dev/ttyACM1": {88}},
            ),
        }

        for case_name, (ports, status, owners) in cases.items():
            with self.subTest(case=case_name):
                channel = MagicMock()
                channel.exit_status_ready.return_value = False
                self.controller._vr_connected_arms[channel] = {"left", "right"}
                self.controller.remote_service_pids = MagicMock(return_value={77})
                self.controller.probe_vr_ports = MagicMock(return_value=ports)
                self.controller.probe_vr_status = MagicMock(return_value=status)
                self.controller.remote_serial_port_owners = MagicMock(
                    return_value=owners
                )
                clock_values = iter((0.0, 0.0))

                def monotonic() -> float:
                    return next(clock_values, 100.0)

                with (
                    patch.object(remote.time, "monotonic", side_effect=monotonic),
                    patch.object(remote.time, "sleep", return_value=None),
                    self.assertRaises(TimeoutError),
                ):
                    self.controller.wait_for_vr_running(
                        timeout=1,
                        launch_channel=channel,
                    )

    def test_wait_for_vr_does_not_require_info_connection_markers(self) -> None:
        channel = MagicMock()
        channel.exit_status_ready.return_value = False
        self.controller._vr_connected_arms[channel] = set()
        self.controller.remote_service_pids = MagicMock(return_value={77})
        self.controller.probe_vr_ports = MagicMock(
            return_value={8442: True, 8443: True}
        )
        self.controller.probe_vr_status = MagicMock(
            return_value={key: True for key in remote.VR_REQUIRED_STATUS_FLAGS}
        )
        self.controller.remote_serial_port_owners = MagicMock(
            return_value={
                "/dev/ttyACM0": {77},
                "/dev/ttyACM1": {77},
            }
        )
        with patch.object(remote.time, "sleep", return_value=None):
            self.controller.wait_for_vr_running(
                timeout=5,
                launch_channel=channel,
            )

        self.assertTrue(any("控制链已就绪" in line for line in self.logs))

    def test_wait_for_vr_requires_one_process_to_own_both_serial_ports(self) -> None:
        channel = MagicMock()
        channel.exit_status_ready.return_value = False
        self.controller.remote_service_pids = MagicMock(return_value={77, 88})
        self.controller.probe_vr_ports = MagicMock(
            return_value={8442: True, 8443: True}
        )
        self.controller.probe_vr_status = MagicMock(
            return_value={key: True for key in remote.VR_REQUIRED_STATUS_FLAGS}
        )
        self.controller.remote_serial_port_owners = MagicMock(
            return_value={
                "/dev/ttyACM0": {77},
                "/dev/ttyACM1": {88},
            }
        )
        clock_values = iter((0.0, 0.0))

        def monotonic() -> float:
            return next(clock_values, 100.0)

        with (
            patch.object(remote.time, "monotonic", side_effect=monotonic),
            patch.object(remote.time, "sleep", return_value=None),
            self.assertRaises(TimeoutError),
        ):
            self.controller.wait_for_vr_running(
                timeout=1,
                launch_channel=channel,
            )

    def test_vr_output_records_actual_arm_connection_logs(self) -> None:
        channel = MagicMock()
        self.controller._channel = channel
        self.controller._current_service = "vr"

        recent = self.controller._handle_service_output(
            channel,
            "vr",
            "INFO - ✅ Left arm connected successfully\n",
            "",
        )
        self.controller._handle_service_output(
            channel,
            "vr",
            "INFO - ✅ Right arm connected successfully\n",
            recent,
        )

        self.assertEqual(self.controller._vr_connected_arms[channel], {"left", "right"})

    def test_vr_output_does_not_drop_arm_marker_before_large_log_tail(self) -> None:
        channel = MagicMock()
        self.controller._channel = channel
        self.controller._current_service = "vr"

        recent = self.controller._handle_service_output(
            channel,
            "vr",
            "INFO - Left arm connected successfully\n" + ("x" * 1500),
            "",
        )

        self.assertEqual(self.controller._vr_connected_arms[channel], {"left"})
        self.assertEqual(len(recent), 1000)

    def test_vr_calibration_prompts_answer_each_arm_once(self) -> None:
        channel = MagicMock()
        self.controller._channel = channel
        self.controller._current_service = "vr"
        left_prompt = (
            "Press ENTER to use provided calibration file associated with the id "
            "left_follower, or type 'c' and press ENTER to run calibration: "
        )
        right_prompt = (
            "Press ENTER to use provided calibration file associated with the id "
            "right_follower, or type 'c' and press ENTER to run calibration: "
        )

        recent = self.controller._handle_service_output(
            channel,
            "vr",
            left_prompt,
            "",
        )
        # The rolling output buffer still contains the left prompt here. It
        # must not cause a second answer when unrelated output arrives.
        recent = self.controller._handle_service_output(
            channel,
            "vr",
            "\nleft arm ready\n",
            recent,
        )
        recent = self.controller._handle_service_output(
            channel,
            "vr",
            right_prompt,
            recent,
        )
        self.controller._handle_service_output(
            channel,
            "vr",
            "\nright arm ready\n",
            recent,
        )

        self.assertEqual(channel.send.call_args_list, [call("\n"), call("\n")])

    def test_vr_manual_calibration_is_never_advanced_automatically(self) -> None:
        channel = MagicMock()
        self.controller._channel = channel
        self.controller._current_service = "vr"

        self.controller._handle_service_output(
            channel,
            "vr",
            "Move left_follower SOFollower to the middle of its range of "
            "motion and press ENTER....",
            "",
        )

        channel.send.assert_not_called()
        self.assertIn("手动校准", self.controller._vr_start_errors[channel])

    def test_start_service_stops_conflict_before_launch(self) -> None:
        events: list[str] = []
        self.controller.stop_remote_service = MagicMock(
            side_effect=lambda name, **_kwargs: events.append(f"stop:{name}") or False
        )
        self.controller.invalid_vr_calibration_files = MagicMock(return_value=[])
        self.controller.wait_for_serial_ports_released = MagicMock(
            side_effect=lambda: events.append("release:serial")
        )
        self.controller._managed_channel = MagicMock(return_value=(None, None))
        self.controller.remote_service_pids = MagicMock(return_value=set())
        self.controller.probe_host_ports = MagicMock(
            return_value={5555: False, 5556: False}
        )
        self.controller._launch_service = MagicMock(
            side_effect=lambda name, **_kwargs: events.append(f"launch:{name}")
        )

        self.assertTrue(self.controller.start_service("vr"))
        self.assertEqual(
            events,
            ["stop:host", "stop:vr", "release:serial", "launch:vr"],
        )
        self.assertEqual(
            self.controller.stop_remote_service.call_args_list,
            [
                call("host", wait_for_serial_release=False),
                call("vr", wait_for_serial_release=False),
            ],
        )

    def test_start_vr_restarts_existing_target_instead_of_reusing_it(self) -> None:
        events: list[str] = []
        self.controller.stop_remote_service = MagicMock(
            side_effect=lambda name, **_kwargs: events.append(f"stop:{name}")
            or name == "vr"
        )
        self.controller.invalid_vr_calibration_files = MagicMock(return_value=[])
        self.controller.wait_for_serial_ports_released = MagicMock(return_value=None)
        self.controller._managed_channel = MagicMock(return_value=(None, None))
        self.controller.remote_service_pids = MagicMock(return_value=set())
        self.controller.probe_host_ports = MagicMock(
            return_value={5555: False, 5556: False}
        )
        self.controller._launch_service = MagicMock(
            side_effect=lambda name, **_kwargs: events.append(f"launch:{name}")
        )

        self.assertTrue(self.controller.start_service("vr"))
        self.assertEqual(events, ["stop:host", "stop:vr", "launch:vr"])
        self.controller.wait_for_serial_ports_released.assert_called_once_with()

    def test_existing_vr_is_not_accepted_while_host_port_is_open(self) -> None:
        self.controller.stop_remote_service = MagicMock(return_value=False)
        self.controller._managed_channel = MagicMock(return_value=(None, None))
        self.controller.remote_service_pids = MagicMock(return_value={29})
        self.controller.probe_host_ports = MagicMock(
            return_value={5555: True, 5556: False}
        )

        with self.assertRaisesRegex(RuntimeError, "已取消启动 VR"):
            self.controller.start_service("vr")

    def test_stop_host_rejects_live_ports_without_matching_pid(self) -> None:
        self.controller._managed_channel = MagicMock(return_value=(None, None))
        self.controller.remote_service_pids = MagicMock(return_value=set())
        self.controller.probe_host_ports = MagicMock(
            return_value={5555: False, 5556: True}
        )

        with self.assertRaisesRegex(RuntimeError, "端口仍在监听"):
            self.controller.stop_remote_service("host")

    def test_stop_escalates_from_int_to_term(self) -> None:
        self.controller._managed_channel = MagicMock(return_value=(None, None))
        self.controller.remote_service_pids = MagicMock(side_effect=[{31}, {31}])
        self.controller._signal_remote_process = MagicMock(return_value=True)
        self.controller._wait_service_stopped = MagicMock(side_effect=[False, True])
        self.controller.wait_for_serial_ports_released = MagicMock(return_value=None)
        self.controller._clear_managed_channel = MagicMock()

        self.assertTrue(self.controller.stop_remote_service("vr"))
        self.assertEqual(
            self.controller._signal_remote_process.call_args_list,
            [call("vr", "INT", {31}), call("vr", "TERM", {31})],
        )

    def test_stop_with_target_pid_does_not_also_send_terminal_ctrl_c(self) -> None:
        channel = MagicMock()
        channel.closed = False
        channel.exit_status_ready.return_value = False
        self.controller._managed_channel = MagicMock(return_value=(channel, None))
        self.controller.remote_service_pids = MagicMock(return_value={31})
        self.controller._signal_remote_process = MagicMock(return_value=True)
        self.controller._wait_service_stopped = MagicMock(return_value=True)
        self.controller.wait_for_serial_ports_released = MagicMock(return_value=None)
        self.controller._clear_managed_channel = MagicMock()

        self.assertTrue(self.controller.stop_remote_service("vr"))

        channel.send.assert_not_called()
        self.controller._signal_remote_process.assert_called_once_with(
            "vr",
            "INT",
            {31},
        )
        self.controller.wait_for_serial_ports_released.assert_called_once()

    def test_wait_for_serial_ports_released_waits_for_both_ports(self) -> None:
        self.controller.remote_serial_port_owners = MagicMock(
            side_effect=[
                {"/dev/ttyACM0": {31}, "/dev/ttyACM1": {31}},
                {"/dev/ttyACM0": set(), "/dev/ttyACM1": {31}},
                {"/dev/ttyACM0": set(), "/dev/ttyACM1": set()},
                {"/dev/ttyACM0": set(), "/dev/ttyACM1": set()},
            ]
        )

        clock_values = iter((0.0, 0.0, 0.25, 0.5, 1.3))

        def monotonic() -> float:
            return next(clock_values, 1.3)

        with (
            patch.object(remote.time, "monotonic", side_effect=monotonic),
            patch.object(remote.time, "sleep", return_value=None),
        ):
            result = self.controller.wait_for_serial_ports_released(timeout=5)

        self.assertIsNone(result)
        self.assertEqual(self.controller.remote_serial_port_owners.call_count, 4)

    def test_wait_for_serial_ports_released_times_out_with_owner_details(self) -> None:
        self.controller.remote_serial_port_owners = MagicMock(
            return_value={"/dev/ttyACM0": {31}, "/dev/ttyACM1": set()}
        )
        clock_values = iter((0.0, 0.0))

        def monotonic() -> float:
            return next(clock_values, 100.0)

        with (
            patch.object(remote.time, "monotonic", side_effect=monotonic),
            patch.object(remote.time, "sleep", return_value=None),
            self.assertRaisesRegex(RuntimeError, "ttyACM0|串口"),
        ):
            self.controller.wait_for_serial_ports_released(timeout=1)

    def test_closed_local_channel_without_exit_status_is_not_stopped(self) -> None:
        channel = MagicMock()
        channel.exit_status_ready.return_value = False
        channel.closed = True
        self.controller.remote_service_pids = MagicMock(return_value=set())

        self.assertFalse(
            self.controller._wait_service_stopped("vr", channel, timeout=0)
        )

    def test_paramiko_minus_one_exit_status_is_not_remote_exit_proof(self) -> None:
        channel = MagicMock()
        channel.closed = True
        channel.exit_status_ready.return_value = True
        channel.recv_exit_status.return_value = -1

        self.assertIsNone(self.controller._record_confirmed_exit_status(channel))
        self.assertFalse(self.controller._channel_has_exited(channel))

    def test_stop_signals_managed_wrapper_before_target_pid_exists(self) -> None:
        channel = MagicMock()
        channel.closed = False
        channel.exit_status_ready.return_value = False
        self.controller._managed_channel = MagicMock(return_value=(channel, None))
        self.controller._managed_wrapper_identity = MagicMock(
            return_value=(88, "12345")
        )
        self.controller.remote_service_pids = MagicMock(return_value=set())
        self.controller._signal_wrapper_pid = MagicMock(return_value=True)
        self.controller._wait_service_stopped = MagicMock(return_value=True)
        self.controller.wait_for_serial_ports_released = MagicMock(return_value=None)
        self.controller._clear_managed_channel = MagicMock()

        self.assertTrue(self.controller.stop_remote_service("vr"))
        self.controller._signal_wrapper_pid.assert_called_once_with(
            88,
            "12345",
            "INT",
        )

    def test_remote_wrapper_absence_can_confirm_stop_after_channel_loss(self) -> None:
        channel = MagicMock()
        channel.closed = True
        channel.exit_status_ready.return_value = True
        channel.recv_exit_status.return_value = -1
        self.controller._wrapper_identities[channel] = (88, "12345")
        self.controller.remote_service_pids = MagicMock(return_value=set())
        self.controller.remote_wrapper_running = MagicMock(return_value=False)

        with patch.object(remote.time, "sleep", return_value=None):
            self.assertTrue(
                self.controller._wait_service_stopped("vr", channel, timeout=1)
            )

    def test_signal_revalidates_pid_argv_in_remote_python(self) -> None:
        self.controller._run_control_command = MagicMock(return_value=(0, "", ""))

        self.assertFalse(self.controller._signal_remote_process("host", "INT", {99}))
        command = self.controller._run_control_command.call_args.args[0]
        self.assertIn("/proc/{pid}/cmdline", command)
        self.assertIn(" 99", command)

    def test_stop_failure_prevents_silent_success(self) -> None:
        self.controller._managed_channel = MagicMock(return_value=(None, None))
        self.controller.remote_service_pids = MagicMock(
            side_effect=[{41}, {41}, {41}]
        )
        self.controller._signal_remote_process = MagicMock(return_value=True)
        self.controller._wait_service_stopped = MagicMock(side_effect=[False, False])
        self.controller._clear_managed_channel = MagicMock()

        with self.assertRaisesRegex(RuntimeError, "仍存活 PID"):
            self.controller.stop_remote_service("vr")

    def test_restore_orders_vr_stop_before_host_ensure(self) -> None:
        events: list[str] = []
        self.controller.stop_remote_service = MagicMock(
            side_effect=lambda name, **_kwargs: events.append(f"stop:{name}")
        )
        self.controller.ensure_host_running = MagicMock(
            side_effect=lambda **_kwargs: events.append("ensure:host")
        )

        self.controller.stop_vr_and_restore_host(timeout=60)

        self.assertEqual(events, ["stop:vr", "ensure:host"])

    def test_start_vr_failure_restores_host_before_reraising(self) -> None:
        events: list[str] = []
        self.controller._start_service_with_channel = MagicMock(
            side_effect=RuntimeError("VR launch failed")
        )
        self.controller.stop_remote_service = MagicMock(
            side_effect=lambda name, **_kwargs: events.append(f"stop:{name}")
        )
        self.controller.ensure_host_running = MagicMock(
            side_effect=lambda **_kwargs: events.append("ensure:host")
        )

        with self.assertRaisesRegex(RuntimeError, "VR launch failed"):
            self.controller.start_vr()

        self.assertEqual(events, ["stop:vr", "ensure:host"])

    def test_stop_all_stops_both_services_before_waiting_for_serials(self) -> None:
        events: list[str] = []
        self.controller.stop_remote_service = MagicMock(
            side_effect=lambda name, **_kwargs: events.append(f"stop:{name}") or True
        )
        self.controller.wait_for_serial_ports_released = MagicMock(
            side_effect=lambda: events.append("release:serial")
        )

        self.assertTrue(self.controller.stop_all_services())

        self.assertEqual(events, ["stop:vr", "stop:host", "release:serial"])
        self.assertEqual(
            self.controller.stop_remote_service.call_args_list,
            [
                call("vr", wait_for_serial_release=False),
                call("host", wait_for_serial_release=False),
            ],
        )

    def test_ensure_validates_calibration_before_remote_calls(self) -> None:
        self.controller.stop_remote_service = MagicMock()
        with self.assertRaises(ValueError):
            self.controller.ensure_host_running(calibration="invalid")
        self.controller.stop_remote_service.assert_not_called()

    def test_close_closes_ssh_even_when_cleanup_fails(self) -> None:
        client = MagicMock()
        self.controller._client = client
        self.controller.stop_all_services = MagicMock(
            side_effect=RuntimeError("cleanup failed")
        )

        with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
            self.controller.close()

        client.close.assert_called_once_with()
        self.assertIsNone(self.controller._client)


if __name__ == "__main__":
    unittest.main()
