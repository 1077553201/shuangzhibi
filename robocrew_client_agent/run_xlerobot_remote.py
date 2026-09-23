"""树莓派 XLerobot 服务控制器。

既可以直接运行并通过菜单切换服务，也可以被同目录下的其他 Python 文件导入：

    from run_xlerobot_remote import ensure_host_running, start_vr, stop_service

    ensure_host_running()  # 必要时启动 host，并等待 ZMQ 真正就绪
    start_host("manual")  # 自动输入 c，进入手动校准
    start_vr()         # 自动停止 host，然后启动 VR 控制
    stop_service()     # 停止当前服务

依赖：pip install paramiko
"""

from __future__ import annotations

import atexit
import codecs
import http.client
import json
import re
import shlex
import socket
import ssl
import sys
import threading
import time
from collections.abc import Callable

import paramiko


HOST = "192.168.10.240"
PORT = 22
USERNAME = "mh"
PASSWORD = "1"

SERVICE_COMMANDS = {
    "host": "python -m lerobot.robots.xlerobot.xlerobot_host",
    "vr": (
        "telegrip --left-port=/dev/ttyACM1 --right-port=/dev/ttyACM0 "
        "--host=0.0.0.0 --autoconnect --log-level=info"
    ),
}

VR_REQUIRED_STATUS_FLAGS = (
    "running",
    "robot_connected",
    "left_arm_connected",
    "right_arm_connected",
    "robotEngaged",
    "visualizer_connected",
)

REMOTE_PROCESS_INSPECTOR = r"""
import glob
import os
import sys

service = sys.argv[1]
host_module = "lerobot.robots.xlerobot.xlerobot_host"
vr_common_args = {
    "--host=0.0.0.0",
    "--autoconnect",
}
vr_port_variants = (
    {"--left-port=/dev/ttyACM1", "--right-port=/dev/ttyACM0"},
    # 兼容并清理修复前从其他终端启动的反置参数进程。
    {"--left-port=/dev/ttyACM0", "--right-port=/dev/ttyACM1"},
)

for cmdline_path in glob.glob("/proc/[0-9]*/cmdline"):
    try:
        raw_args = open(cmdline_path, "rb").read().split(b"\0")
        args = [item.decode("utf-8", "replace") for item in raw_args if item]
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        continue
    if not args:
        continue

    executable = os.path.basename(args[0])
    is_python = executable == "python" or executable.startswith("python3")
    is_host = is_python and any(
        args[index] == "-m" and args[index + 1] == host_module
        for index in range(len(args) - 1)
    )
    is_vr = (
        any(os.path.basename(item) == "telegrip" for item in args)
        and vr_common_args.issubset(args)
        and any(port_args.issubset(args) for port_args in vr_port_variants)
    )
    if (service == "host" and is_host) or (service == "vr" and is_vr):
        print("__XLEROBOT_PID__" + os.path.basename(os.path.dirname(cmdline_path)))
""".strip()

REMOTE_PROCESS_SIGNALER = r"""
import os
import signal
import sys

service = sys.argv[1]
signal_name = sys.argv[2]
pids = [int(value) for value in sys.argv[3:]]
host_module = "lerobot.robots.xlerobot.xlerobot_host"
vr_common_args = {
    "--host=0.0.0.0",
    "--autoconnect",
}
vr_port_variants = (
    {"--left-port=/dev/ttyACM1", "--right-port=/dev/ttyACM0"},
    {"--left-port=/dev/ttyACM0", "--right-port=/dev/ttyACM1"},
)

def matches_service(pid):
    try:
        raw_args = open(f"/proc/{pid}/cmdline", "rb").read().split(b"\0")
        args = [item.decode("utf-8", "replace") for item in raw_args if item]
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return False
    if not args:
        return False
    executable = os.path.basename(args[0])
    is_python = executable == "python" or executable.startswith("python3")
    is_host = is_python and any(
        args[index] == "-m" and args[index + 1] == host_module
        for index in range(len(args) - 1)
    )
    is_vr = (
        any(os.path.basename(item) == "telegrip" for item in args)
        and vr_common_args.issubset(args)
        and any(port_args.issubset(args) for port_args in vr_port_variants)
    )
    return (service == "host" and is_host) or (service == "vr" and is_vr)

signum = getattr(signal, "SIG" + signal_name)
for pid in pids:
    if not matches_service(pid):
        continue
    try:
        os.kill(pid, signum)
    except ProcessLookupError:
        continue
    print("__XLEROBOT_SIGNALED__" + str(pid))
""".strip()

REMOTE_WRAPPER_PID_MARKER = "__XLEROBOT_WRAPPER_PID__"

REMOTE_WRAPPER_SIGNALER = r"""
import os
import signal
import sys

pid = int(sys.argv[1])
expected_start = sys.argv[2]
signal_name = sys.argv[3]
try:
    current_start = open(f"/proc/{pid}/stat").read().split()[21]
except (FileNotFoundError, PermissionError, ProcessLookupError):
    raise SystemExit(0)
if current_start != expected_start:
    raise SystemExit(0)
try:
    os.kill(pid, getattr(signal, "SIG" + signal_name))
except ProcessLookupError:
    raise SystemExit(0)
print("__XLEROBOT_WRAPPER_SIGNALED__" + str(pid))
""".strip()

REMOTE_WRAPPER_CHECKER = r"""
import sys

pid = int(sys.argv[1])
expected_start = sys.argv[2]
try:
    current_start = open(f"/proc/{pid}/stat").read().split()[21]
except (FileNotFoundError, PermissionError, ProcessLookupError):
    raise SystemExit(0)
if current_start == expected_start:
    print("__XLEROBOT_WRAPPER_ALIVE__" + str(pid))
""".strip()

REMOTE_SERIAL_OWNER_INSPECTOR = r"""
import glob
import os
import sys

requested_ports = {os.path.realpath(path): path for path in sys.argv[1:]}
for fd_path in glob.glob("/proc/[0-9]*/fd/[0-9]*"):
    try:
        target = os.path.realpath(fd_path)
    except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
        continue
    port = requested_ports.get(target)
    if port is None:
        continue
    pid = fd_path.split("/")[2]
    print(f"__XLEROBOT_SERIAL_OWNER__{port}:{pid}")
""".strip()

REMOTE_VR_CALIBRATION_CHECKER = r"""
import json
import os
import sys

required_motors = {
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
    "gripper",
}
for path in sys.argv[1:]:
    try:
        with open(path, encoding="utf-8") as calibration_file:
            calibration = json.load(calibration_file)
        valid = required_motors.issubset(calibration)
    except (OSError, ValueError, TypeError):
        valid = False
    if not valid:
        print("__XLEROBOT_BAD_CALIBRATION__" + path)
""".strip()

HOST_ZMQ_PORTS = (5555, 5556)
VR_SERVICE_PORTS = (8442, 8443)
VR_SERIAL_PORTS = ("/dev/ttyACM0", "/dev/ttyACM1")
VR_CALIBRATION_FILES = (
    "/home/mh/.cache/huggingface/lerobot/calibration/robots/so_follower/left_follower.json",
    "/home/mh/.cache/huggingface/lerobot/calibration/robots/so_follower/right_follower.json",
)
DEFAULT_HOST_START_TIMEOUT = 60.0
DEFAULT_VR_START_TIMEOUT = 45.0
DEFAULT_STOP_TIMEOUT = 8.0
DEFAULT_TERMINATE_TIMEOUT = 3.0
PORT_PROBE_TIMEOUT = 0.5
HOST_WAIT_LOG_INTERVAL = 5.0
VR_WAIT_LOG_INTERVAL = 5.0
SERIAL_RELEASE_TIMEOUT = 5.0
SERIAL_RELEASE_STABLE_TIME = 0.75

HOST_CALIBRATION_PROMPT = "Press ENTER to restore calibration from file"
HOST_LOG_NOISE_RE = re.compile(
    r"^(?:WARNING:root:)?(?:"
    r"No command available|"
    r"Command not received for more than \d+ milliseconds\. Stopping the base\."
    r")\s*$"
)
VR_CALIBRATION_PROMPT_RE = re.compile(
    r"Press ENTER to use provided calibration file associated with the id "
    r"(left_follower|right_follower)"
)
VR_MANUAL_CALIBRATION_PROMPT = "to the middle of its range of motion and press ENTER"
VR_ARM_CONNECTED_MARKERS = {
    "left": "Left arm connected successfully",
    "right": "Right arm connected successfully",
}

# 交互式 Bash 通常会从 ~/.bashrc 载入 conda。若没有载入，则继续检测
# 树莓派上常见的 Conda 安装目录。
CONDA_SETUP = r"""
set -e
if ! type conda >/dev/null 2>&1; then
    for conda_sh in \
        "$HOME/miniforge3/etc/profile.d/conda.sh" \
        "$HOME/mambaforge/etc/profile.d/conda.sh" \
        "$HOME/miniconda3/etc/profile.d/conda.sh" \
        "$HOME/anaconda3/etc/profile.d/conda.sh" \
        "/opt/conda/etc/profile.d/conda.sh"; do
        if [ -f "$conda_sh" ]; then
            source "$conda_sh"
            break
        fi
    done
fi

if ! type conda >/dev/null 2>&1; then
    echo "错误：找不到 conda。请检查 ~/.bashrc 或 Conda 安装路径。" >&2
    exit 127
fi

conda activate lerobot
""".strip()


def _console_log(text: str) -> None:
    """默认日志输出函数。"""
    sys.stdout.write(text)
    sys.stdout.flush()


class RemoteServiceController:
    """通过一个持久 SSH 连接管理树莓派上的互斥服务。"""

    def __init__(
        self,
        host: str = HOST,
        port: int = PORT,
        username: str = USERNAME,
        password: str = PASSWORD,
        log_callback: Callable[[str], None] | None = _console_log,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.log_callback = log_callback

        self._client: paramiko.SSHClient | None = None
        self._channel: paramiko.Channel | None = None
        self._reader_thread: threading.Thread | None = None
        self._current_service: str | None = None
        self._pending_host_response: str | None = None
        self._host_prompt_errors: dict[paramiko.Channel, str] = {}
        self._vr_calibration_prompts_answered: dict[
            paramiko.Channel, set[str]
        ] = {}
        self._vr_connected_arms: dict[paramiko.Channel, set[str]] = {}
        self._vr_start_errors: dict[paramiko.Channel, str] = {}
        self._wrapper_identities: dict[paramiko.Channel, tuple[int, str]] = {}
        self._confirmed_exit_codes: dict[paramiko.Channel, int] = {}
        self._service_output_line_tail = ""

        # operation_lock 保证两个线程不会同时切换服务；state_lock 保护状态读取。
        self._operation_lock = threading.RLock()
        self._state_lock = threading.Lock()

    def _log(self, text: str) -> None:
        if self.log_callback is not None:
            self.log_callback(text)

    def _filter_noisy_host_log(self, text: str) -> str:
        """丢掉 host 控制环的空转警告，避免把本机终端刷满。"""
        data = self._service_output_line_tail + text
        if not data:
            return ""
        if data.endswith("\n") or data.endswith("\r"):
            complete, self._service_output_line_tail = data, ""
        else:
            last_break = max(data.rfind("\n"), data.rfind("\r"))
            if last_break < 0:
                self._service_output_line_tail = data
                return ""
            complete, self._service_output_line_tail = (
                data[: last_break + 1],
                data[last_break + 1 :],
            )
        kept: list[str] = []
        for line in complete.splitlines(keepends=True):
            if HOST_LOG_NOISE_RE.match(line.strip()):
                continue
            kept.append(line)
        return "".join(kept)

    def connect(self) -> None:
        """建立 SSH 连接；已经连接时不会重复连接。"""
        with self._operation_lock:
            if self._client is not None:
                transport = self._client.get_transport()
                if transport is not None and transport.is_active():
                    return
                self._client.close()
                self._client = None

            self._log(f"正在连接 {self.username}@{self.host}:{self.port} ...\n")
            client = paramiko.SSHClient()
            client.load_system_host_keys()
            # 首次连接自动记录主机；正式部署可改成 RejectPolicy 并预存指纹。
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            try:
                client.connect(
                    hostname=self.host,
                    port=self.port,
                    username=self.username,
                    password=self.password,
                    timeout=10,
                    auth_timeout=10,
                    banner_timeout=10,
                    look_for_keys=False,
                    allow_agent=False,
                )
            except Exception:
                client.close()
                raise

            transport = client.get_transport()
            if transport is None:
                client.close()
                raise RuntimeError("SSH transport 创建失败")
            transport.set_keepalive(30)

            self._client = client
            self._log("SSH 连接成功。\n")

    def probe_host_ports(self) -> dict[int, bool]:
        """返回 host 两个 ZMQ 端口当前是否可连接。"""
        result: dict[int, bool] = {}
        for port in HOST_ZMQ_PORTS:
            try:
                with socket.create_connection(
                    (self.host, port),
                    timeout=PORT_PROBE_TIMEOUT,
                ):
                    result[port] = True
            except OSError:
                result[port] = False
        return result

    def host_ports_ready(self) -> bool:
        """只有 host 的 ZMQ 命令端口与观测端口都监听时才返回 True。"""
        return all(self.probe_host_ports().values())

    def probe_vr_ports(self) -> dict[int, bool]:
        """探测 telegrip 的 WSS(8442) 与 HTTPS(8443) 端口。"""
        result: dict[int, bool] = {}
        for port in VR_SERVICE_PORTS:
            try:
                with socket.create_connection(
                    (self.host, port),
                    timeout=PORT_PROBE_TIMEOUT,
                ):
                    result[port] = True
            except OSError:
                result[port] = False
        return result

    def probe_vr_status(self) -> dict[str, object] | None:
        """读取 telegrip 状态 API；尚未提供服务或响应无效时返回 None。"""
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        connection = http.client.HTTPSConnection(
            self.host,
            8443,
            timeout=PORT_PROBE_TIMEOUT,
            context=context,
        )
        try:
            connection.request("GET", "/api/status")
            response = connection.getresponse()
            if response.status != 200:
                response.read()
                return None
            payload = json.loads(response.read(65536).decode("utf-8"))
            return payload if isinstance(payload, dict) else None
        except (OSError, ValueError, http.client.HTTPException):
            return None
        finally:
            connection.close()

    def remote_serial_port_owners(self) -> dict[str, set[int]]:
        """返回树莓派两个机械臂串口当前被哪些 PID 打开。"""
        command = (
            "python3 -c "
            + shlex.quote(REMOTE_SERIAL_OWNER_INSPECTOR)
            + " "
            + " ".join(shlex.quote(port) for port in VR_SERIAL_PORTS)
        )
        exit_code, stdout, stderr = self._run_control_command(command, timeout=3.0)
        if exit_code != 0:
            detail = stderr.strip() or f"退出码 {exit_code}"
            raise RuntimeError(f"无法检查远程串口占用：{detail}")

        owners = {port: set() for port in VR_SERIAL_PORTS}
        marker = "__XLEROBOT_SERIAL_OWNER__"
        for output_line in stdout.splitlines():
            output_line = output_line.strip()
            if not output_line.startswith(marker):
                continue
            value = output_line.removeprefix(marker)
            try:
                port, raw_pid = value.rsplit(":", 1)
            except ValueError as exc:
                raise RuntimeError(f"检查远程串口占用时收到无效结果：{value!r}") from exc
            if port not in owners or not raw_pid.isdigit():
                raise RuntimeError(f"检查远程串口占用时收到无效结果：{value!r}")
            owners[port].add(int(raw_pid))
        return owners

    def wait_for_serial_ports_released(
        self,
        timeout: float = SERIAL_RELEASE_TIMEOUT,
    ) -> None:
        """等待两个机械臂串口连续一段时间无人占用。"""
        deadline = time.monotonic() + max(0.0, timeout)
        free_since: float | None = None
        last_owners: dict[str, set[int]] = {port: set() for port in VR_SERIAL_PORTS}
        while True:
            last_owners = self.remote_serial_port_owners()
            now = time.monotonic()
            if not any(last_owners.values()):
                if free_since is None:
                    free_since = now
                if now - free_since >= SERIAL_RELEASE_STABLE_TIME:
                    return
            else:
                free_since = None

            if now >= deadline:
                occupied = {
                    port: sorted(pids) for port, pids in last_owners.items() if pids
                }
                raise RuntimeError(
                    f"机械臂串口未在 {timeout:g} 秒内释放：{occupied}；"
                    "已取消后续服务切换。"
                )
            time.sleep(0.25)

    def invalid_vr_calibration_files(self) -> list[str]:
        """返回缺失、损坏或电机字段不完整的 telegrip 校准文件。"""
        command = (
            "python3 -c "
            + shlex.quote(REMOTE_VR_CALIBRATION_CHECKER)
            + " "
            + " ".join(shlex.quote(path) for path in VR_CALIBRATION_FILES)
        )
        exit_code, stdout, stderr = self._run_control_command(command, timeout=3.0)
        if exit_code != 0:
            detail = stderr.strip() or f"退出码 {exit_code}"
            raise RuntimeError(f"无法检查 VR 校准文件：{detail}")

        marker = "__XLEROBOT_BAD_CALIBRATION__"
        return [
            line.strip().removeprefix(marker)
            for line in stdout.splitlines()
            if line.strip().startswith(marker)
        ]

    def _run_control_command(
        self,
        command: str,
        *,
        timeout: float = 10.0,
    ) -> tuple[int, str, str]:
        """通过独立 SSH channel 运行短控制命令并收集结果。"""
        self.connect()
        assert self._client is not None
        _stdin, stdout, _stderr = self._client.exec_command(
            "bash -lc " + shlex.quote(command),
            timeout=timeout,
        )
        channel = stdout.channel
        deadline = time.monotonic() + max(0.0, timeout)
        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []
        try:
            while True:
                while channel.recv_ready():
                    stdout_chunks.append(channel.recv(4096))
                while channel.recv_stderr_ready():
                    stderr_chunks.append(channel.recv_stderr(4096))
                if (
                    channel.exit_status_ready()
                    and not channel.recv_ready()
                    and not channel.recv_stderr_ready()
                ):
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"远程控制命令超过 {timeout:g} 秒未完成：{command[:120]}"
                    )
                time.sleep(0.02)

            exit_code = channel.recv_exit_status()
            stdout_text = b"".join(stdout_chunks).decode("utf-8", errors="replace")
            stderr_text = b"".join(stderr_chunks).decode("utf-8", errors="replace")
            return exit_code, stdout_text, stderr_text
        finally:
            channel.close()

    def remote_service_pids(self, service_name: str) -> set[int]:
        """按远程进程 argv 精确查找服务 PID，不匹配包装 shell。"""
        if service_name not in SERVICE_COMMANDS:
            raise ValueError(f"未知服务 {service_name!r}")
        command = (
            "python3 -c "
            + shlex.quote(REMOTE_PROCESS_INSPECTOR)
            + " "
            + shlex.quote(service_name)
        )
        exit_code, stdout, stderr = self._run_control_command(command, timeout=3.0)
        if exit_code != 0:
            detail = stderr.strip() or f"退出码 {exit_code}"
            raise RuntimeError(f"无法检查远程 [{service_name}] 进程：{detail}")

        pids: set[int] = set()
        marker = "__XLEROBOT_PID__"
        for output_line in stdout.splitlines():
            output_line = output_line.strip()
            if not output_line.startswith(marker):
                continue
            raw_pid = output_line.removeprefix(marker)
            if not raw_pid.isdigit():
                raise RuntimeError(
                    f"检查远程 [{service_name}] 进程时收到无效 PID：{raw_pid!r}"
                )
            pids.add(int(raw_pid))
        return pids

    def remote_process_running(self, service_name: str) -> bool:
        """检查指定服务进程，包括不是本控制器启动的进程。"""
        return bool(self.remote_service_pids(service_name))

    def _signal_remote_process(
        self,
        service_name: str,
        signal_name: str,
        pids: set[int] | None = None,
    ) -> bool:
        target_pids = self.remote_service_pids(service_name) if pids is None else pids
        if not target_pids:
            return False
        command = (
            "python3 -c "
            + shlex.quote(REMOTE_PROCESS_SIGNALER)
            + " "
            + shlex.quote(service_name)
            + " "
            + shlex.quote(signal_name)
            + " "
            + " ".join(str(pid) for pid in sorted(target_pids))
        )
        exit_code, stdout, stderr = self._run_control_command(command, timeout=3.0)
        if exit_code != 0:
            detail = stderr.strip() or f"退出码 {exit_code}"
            raise RuntimeError(f"向远程 [{service_name}] 发送 {signal_name} 失败：{detail}")
        return "__XLEROBOT_SIGNALED__" in stdout

    def _signal_wrapper_pid(
        self,
        pid: int,
        start_ticks: str,
        signal_name: str,
    ) -> bool:
        # start_ticks 是 /proc/<pid>/stat 的进程启动时钟，可防止 PID 复用误杀。
        command = (
            "python3 -c "
            + shlex.quote(REMOTE_WRAPPER_SIGNALER)
            + f" {pid} {shlex.quote(start_ticks)} {shlex.quote(signal_name)}"
        )
        exit_code, stdout, stderr = self._run_control_command(command, timeout=3.0)
        if exit_code != 0:
            detail = stderr.strip() or f"退出码 {exit_code}"
            raise RuntimeError(f"向远程 wrapper PID={pid} 发送 {signal_name} 失败：{detail}")
        return "__XLEROBOT_WRAPPER_SIGNALED__" in stdout

    def _managed_channel(
        self,
        service_name: str,
    ) -> tuple[paramiko.Channel | None, threading.Thread | None]:
        with self._state_lock:
            if self._current_service != service_name:
                return None, None
            return self._channel, self._reader_thread

    def _managed_wrapper_identity(
        self,
        channel: paramiko.Channel | None,
    ) -> tuple[int, str] | None:
        if channel is None:
            return None
        with self._state_lock:
            return self._wrapper_identities.get(channel)

    def remote_wrapper_running(self, identity: tuple[int, str]) -> bool:
        pid, start_ticks = identity
        command = (
            "python3 -c "
            + shlex.quote(REMOTE_WRAPPER_CHECKER)
            + f" {pid} {shlex.quote(start_ticks)}"
        )
        exit_code, stdout, stderr = self._run_control_command(command, timeout=3.0)
        if exit_code != 0:
            detail = stderr.strip() or f"退出码 {exit_code}"
            raise RuntimeError(f"检查远程 wrapper PID={pid} 失败：{detail}")
        return "__XLEROBOT_WRAPPER_ALIVE__" in stdout

    @staticmethod
    def _channel_is_running(channel: paramiko.Channel | None) -> bool:
        return bool(
            channel is not None
            and not channel.closed
            and not channel.exit_status_ready()
        )

    def _record_confirmed_exit_status(
        self,
        channel: paramiko.Channel | None,
    ) -> int | None:
        if channel is None:
            return None
        with self._state_lock:
            known = self._confirmed_exit_codes.get(channel)
        if known is not None:
            return known
        try:
            if not channel.exit_status_ready():
                return None
            exit_code = channel.recv_exit_status()
        except (OSError, paramiko.SSHException):
            return None
        # Paramiko 在没有收到服务端 exit-status 时返回 -1；这不是远端退出证明。
        if exit_code < 0:
            return None
        with self._state_lock:
            self._confirmed_exit_codes[channel] = exit_code
        return exit_code

    def _channel_has_exited(self, channel: paramiko.Channel | None) -> bool:
        if channel is None:
            return True
        with self._state_lock:
            return channel in self._confirmed_exit_codes

    def _clear_managed_channel(
        self,
        service_name: str,
        channel: paramiko.Channel | None,
    ) -> None:
        with self._state_lock:
            if (
                self._current_service == service_name
                and (channel is None or self._channel is channel)
            ):
                self._channel = None
                self._current_service = None
                self._pending_host_response = None
                self._reader_thread = None
                if channel is not None:
                    self._host_prompt_errors.pop(channel, None)
                    self._vr_calibration_prompts_answered.pop(channel, None)
                    self._vr_connected_arms.pop(channel, None)
                    self._vr_start_errors.pop(channel, None)
                    self._wrapper_identities.pop(channel, None)

    def _wait_service_stopped(
        self,
        service_name: str,
        channel: paramiko.Channel | None,
        timeout: float,
    ) -> bool:
        """等待 PID 消失且 managed channel 退出，并要求连续两轮稳定。"""
        deadline = time.monotonic() + max(0.0, timeout)
        consecutive_stopped = 0
        while time.monotonic() < deadline:
            pids = self.remote_service_pids(service_name)
            wrapper_identity = self._managed_wrapper_identity(channel)
            if wrapper_identity is not None:
                wrapper_stopped = not self.remote_wrapper_running(wrapper_identity)
            else:
                wrapper_stopped = self._channel_has_exited(channel)
            if not pids and wrapper_stopped:
                consecutive_stopped += 1
                if consecutive_stopped >= 2:
                    return True
            else:
                consecutive_stopped = 0
            time.sleep(0.25)
        return False

    def _wait_host_ports_closed(self, timeout: float = 3.0) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        while time.monotonic() < deadline:
            if not any(self.probe_host_ports().values()):
                return True
            time.sleep(0.25)
        return not any(self.probe_host_ports().values())

    def _handle_service_output(
        self,
        channel: paramiko.Channel,
        service_name: str,
        text: str,
        recent_output: str,
    ) -> str:
        # 单次 PTY recv 最多可带回 4096 字节。必须先在完整批次上解析，再
        # 截取滚动尾巴；否则批次开头的校准/双臂连接 marker 会被丢掉。
        combined_output = recent_output + text
        wrapper_matches = re.findall(
            re.escape(REMOTE_WRAPPER_PID_MARKER) + r"(\d+):(\d+)",
            combined_output,
        )
        if wrapper_matches:
            with self._state_lock:
                if self._channel is channel:
                    wrapper_pid, start_ticks = wrapper_matches[-1]
                    self._wrapper_identities[channel] = (
                        int(wrapper_pid),
                        start_ticks,
                    )

        visible_text = re.sub(
            re.escape(REMOTE_WRAPPER_PID_MARKER) + r"\d+:\d+\r?\n?",
            "",
            text,
        )
        visible_text = self._filter_noisy_host_log(visible_text)
        if visible_text:
            self._log(visible_text)
        self._answer_host_prompt(channel, service_name, combined_output)
        self._answer_vr_calibration_prompt(channel, service_name, combined_output)
        return combined_output[-1000:]

    def _stream_output(self, channel: paramiko.Channel, service_name: str) -> None:
        """后台读取某个服务的合并输出。"""
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        exit_code: int | None = None
        recent_output = ""
        self._service_output_line_tail = ""

        try:
            while not channel.exit_status_ready():
                if channel.recv_ready():
                    data = channel.recv(4096)
                    if not data:
                        break
                    text = decoder.decode(data)
                    if text:
                        recent_output = self._handle_service_output(
                            channel,
                            service_name,
                            text,
                            recent_output,
                        )
                else:
                    time.sleep(0.05)

            while channel.recv_ready():
                data = channel.recv(4096)
                if not data:
                    break
                text = decoder.decode(data)
                if text:
                    recent_output = self._handle_service_output(
                        channel,
                        service_name,
                        text,
                        recent_output,
                    )

            tail = decoder.decode(b"", final=True)
            if tail:
                recent_output = self._handle_service_output(
                    channel,
                    service_name,
                    tail,
                    recent_output,
                )

            exit_code = self._record_confirmed_exit_status(channel)
        except (OSError, paramiko.SSHException) as exc:
            self._log(f"\n[{service_name}] 日志通道异常：{exc}\n")
        finally:
            # 只有拿到服务端真实 exit-status 才清状态。单纯 SSH/channel 异常时保留
            # wrapper PID，后续 stop 仍可通过新 SSH 连接验证并清理远端。
            if self._channel_has_exited(channel):
                self._clear_managed_channel(service_name, channel)
            if exit_code is not None:
                self._log(f"\n[{service_name}] 已退出，退出码：{exit_code}\n")

    def _answer_host_prompt(
        self,
        channel: paramiko.Channel,
        service_name: str,
        recent_output: str,
    ) -> None:
        """检测 host 校准提示，并自动写回预设答案。"""
        if service_name != "host" or HOST_CALIBRATION_PROMPT not in recent_output:
            return

        with self._state_lock:
            if self._channel is not channel:
                return
            response = self._pending_host_response
            # 只回答一次，避免后续日志片段重复命中同一个提示。
            self._pending_host_response = None

        if response is None:
            return

        try:
            channel.send(response)
            action = "回车（恢复已有校准）" if response == "\n" else "c（手动校准）"
            self._log(f"\n[host] 已自动发送{action}。\n")
        except (OSError, paramiko.SSHException) as exc:
            with self._state_lock:
                if self._channel is channel:
                    self._host_prompt_errors[channel] = str(exc)
            self._log(f"\n[host] 发送校准选项失败：{exc}\n")

    def _answer_vr_calibration_prompt(
        self,
        channel: paramiko.Channel,
        service_name: str,
        recent_output: str,
    ) -> None:
        """只恢复已存在的左右臂校准；绝不自动推进首次手动校准。"""
        if service_name != "vr":
            return

        connected_arms = {
            arm
            for arm, marker in VR_ARM_CONNECTED_MARKERS.items()
            if marker in recent_output
        }
        if connected_arms:
            with self._state_lock:
                if self._channel is channel:
                    self._vr_connected_arms.setdefault(channel, set()).update(
                        connected_arms
                    )

        failure_markers = (
            "Left arm connection failed:",
            "Right arm connection failed:",
            "Robot interface failed to connect",
            "Control loop setup failed",
            "PyBullet visualizer setup failed",
            "Failed to engage robot motors",
        )
        failure_line = next(
            (
                line.strip()
                for line in reversed(recent_output.splitlines())
                if any(marker in line for marker in failure_markers)
            ),
            None,
        )
        if failure_line:
            with self._state_lock:
                if self._channel is channel:
                    self._vr_start_errors[channel] = failure_line
            return

        if VR_MANUAL_CALIBRATION_PROMPT in recent_output:
            with self._state_lock:
                if self._channel is channel:
                    self._vr_start_errors[channel] = (
                        "telegrip 进入了首次手动校准流程；为避免误动作，"
                        "远程自动启动已中止。请先在树莓派终端完成左右臂校准。"
                    )
            return

        prompt_ids = VR_CALIBRATION_PROMPT_RE.findall(recent_output)
        for follower_id in prompt_ids:
            with self._state_lock:
                if self._channel is not channel:
                    return
                answered = self._vr_calibration_prompts_answered.setdefault(
                    channel,
                    set(),
                )
                if follower_id in answered:
                    continue
                # 在发送前标记，避免日志分片重复命中时连发多个回车。
                answered.add(follower_id)

            try:
                channel.send("\n")
                self._log(
                    f"\n[vr] 已自动发送回车，恢复 {follower_id} 已有校准。\n"
                )
            except (OSError, paramiko.SSHException) as exc:
                with self._state_lock:
                    if self._channel is channel:
                        self._vr_start_errors[channel] = (
                            f"恢复 {follower_id} 校准失败：{exc}"
                        )
                self._log(f"\n[vr] 恢复 {follower_id} 校准失败：{exc}\n")

    def _launch_service(
        self,
        service_name: str,
        *,
        host_response: str | None = "\n",
    ) -> paramiko.Channel:
        """启动一个已完成互斥检查的服务，并返回其日志 channel。"""
        self.connect()
        assert self._client is not None
        transport = self._client.get_transport()
        if transport is None or not transport.is_active():
            raise RuntimeError("SSH 连接不可用")

        # PTY 让日志及时刷新，同时允许发送 Ctrl+C(SIGINT)停止前台进程。
        new_channel = transport.open_session()
        new_channel.get_pty(term="xterm")
        remote_script = (
            "wrapper_start=$(awk '{print $22}' /proc/$$/stat)\n"
            f'printf "{REMOTE_WRAPPER_PID_MARKER}%s:%s\\n" "$$" "$wrapper_start"\n'
            + CONDA_SETUP
            + "\nexec "
            + SERVICE_COMMANDS[service_name]
        )
        new_channel.exec_command("bash -ic " + shlex.quote(remote_script))

        with self._state_lock:
            self._channel = new_channel
            self._current_service = service_name
            self._pending_host_response = (
                host_response if service_name == "host" else None
            )
            if service_name == "vr":
                self._vr_calibration_prompts_answered[new_channel] = set()
                self._vr_connected_arms[new_channel] = set()
                self._vr_start_errors.pop(new_channel, None)

        reader = threading.Thread(
            target=self._stream_output,
            args=(new_channel, service_name),
            name=f"ssh-{service_name}-log",
            daemon=True,
        )
        with self._state_lock:
            self._reader_thread = reader
        reader.start()
        self._log(f"[{service_name}] 启动命令已发送。\n")
        return new_channel

    def _start_service_with_channel(
        self,
        service_name: str,
        *,
        host_response: str | None = "\n",
    ) -> tuple[bool, paramiko.Channel | None]:
        """启动服务并保留本次 channel，避免 reader 清状态造成竞态。"""
        if service_name not in SERVICE_COMMANDS:
            choices = ", ".join(SERVICE_COMMANDS)
            raise ValueError(f"未知服务 {service_name!r}，可选值：{choices}")

        with self._operation_lock:
            conflicting_service = "vr" if service_name == "host" else "host"
            self.stop_remote_service(
                conflicting_service,
                wait_for_serial_release=False,
            )

            # 每次进入 VR 都创建一个全新的 telegrip 会话。旧进程可能只启动了
            # Web 页面，却卡在校准确认或仅占用了一只机械臂；复用这种 PID 会让
            # 8443 看似正常、实际控制循环始终没有运行。
            if service_name == "vr":
                self.stop_remote_service(
                    "vr",
                    wait_for_serial_release=False,
                )

            channel, _reader = self._managed_channel(service_name)
            if channel is not None and not self._channel_has_exited(channel):
                self._log(f"[{service_name}] 启动命令已经在运行，无需重复发送。\n")
                return False, channel

            existing_pids = self.remote_service_pids(service_name)
            port_state = self.probe_host_ports()
            if service_name == "vr" and any(port_state.values()):
                open_ports = [port for port, is_open in port_state.items() if is_open]
                raise RuntimeError(
                    f"host 端口仍在监听：{open_ports}；已取消启动 VR。"
                )
            if existing_pids:
                if service_name == "vr":
                    raise RuntimeError(
                        "清理后又检测到新的 telegrip 进程："
                        f"PID={sorted(existing_pids)}；为避免串口争用，已取消启动 VR。"
                    )
                self._log(
                    f"[{service_name}] 远程进程已经存在，PID={sorted(existing_pids)}，"
                    "无需重复启动。\n"
                )
                return False, None

            if any(port_state.values()):
                open_ports = [port for port, is_open in port_state.items() if is_open]
                raise RuntimeError(
                    "未检测到 xlerobot_host 进程，但 ZMQ 端口仍被占用："
                    f"{open_ports}。为避免服务冲突，已取消启动 [{service_name}]。"
                )

            if service_name == "vr":
                invalid_calibrations = self.invalid_vr_calibration_files()
                if invalid_calibrations:
                    raise RuntimeError(
                        "telegrip 缺少可用的左右臂校准文件："
                        f"{invalid_calibrations}。请先在树莓派终端完成 VR 校准。"
                    )

            # 先让所有已知冲突进程退出，再统一等待双串口释放。若逐个服务
            # 停止时就要求全局串口为空，host 与残留 VR 各占一个串口时会
            # 互相等待，导致第二个服务永远得不到停止机会。
            self.wait_for_serial_ports_released()

            channel = self._launch_service(
                service_name,
                host_response=host_response,
            )
            return True, channel

    def start_service(
        self,
        service_name: str,
        *,
        host_response: str | None = "\n",
    ) -> bool:
        """互斥地启动 host 或 VR；已存在目标进程时不重复启动。"""
        started, _channel = self._start_service_with_channel(
            service_name,
            host_response=host_response,
        )
        return started

    def start_host(self, calibration: str = "restore") -> bool:
        """启动 host 服务。

        calibration 可选值：restore 自动回车恢复校准；manual 自动输入 c
        进入手动校准；ask 不自动回答，等待 send_input() 输入。
        """
        responses = {"restore": "\n", "manual": "c\n", "ask": None}
        if calibration not in responses:
            raise ValueError("calibration 必须是 restore、manual 或 ask")
        return self.start_service("host", host_response=responses[calibration])

    def wait_for_host_ready(
        self,
        timeout: float = DEFAULT_HOST_START_TIMEOUT,
        *,
        launch_channel: paramiko.Channel | None = None,
        process_already_seen: bool = False,
    ) -> None:
        """等待 host 进程及 5555/5556 连续两轮都处于就绪状态。"""
        started_at = time.monotonic()
        deadline = started_at + max(0.0, timeout)
        next_progress_log = started_at + HOST_WAIT_LOG_INTERVAL
        consecutive_ready = 0
        saw_process = process_already_seen

        while True:
            pids = self.remote_service_pids("host")
            port_state = self.probe_host_ports()
            process_ready = bool(pids)
            ports_ready = all(port_state.values())
            saw_process = saw_process or process_ready

            if process_ready and ports_ready:
                consecutive_ready += 1
                if consecutive_ready >= 2:
                    elapsed = time.monotonic() - started_at
                    self._log(f"[host] 已就绪，ZMQ 双端口可用（等待 {elapsed:.1f} 秒）。\n")
                    return
            else:
                consecutive_ready = 0

            exit_code = self._record_confirmed_exit_status(launch_channel)
            if exit_code is not None:
                raise RuntimeError(f"xlerobot_host 在就绪前退出，退出码：{exit_code}")
            if launch_channel is not None and not process_ready:
                wrapper_identity = self._managed_wrapper_identity(launch_channel)
                if (
                    wrapper_identity is not None
                    and not self.remote_wrapper_running(wrapper_identity)
                ):
                    raise RuntimeError("xlerobot_host 的远程启动 wrapper 在就绪前退出。")
                if wrapper_identity is None and launch_channel.closed:
                    raise RuntimeError(
                        "xlerobot_host 启动 channel 在报告 wrapper 身份前异常关闭；"
                        "无法确认远程状态。"
                    )
            with self._state_lock:
                prompt_error = (
                    self._host_prompt_errors.get(launch_channel)
                    if launch_channel is not None
                    else None
                )
            if prompt_error:
                raise RuntimeError(f"自动响应 host 校准提示失败：{prompt_error}")
            if launch_channel is None and saw_process and not process_ready:
                raise RuntimeError("远程 xlerobot_host 在等待就绪期间意外退出。")

            now = time.monotonic()
            if now >= deadline:
                raise TimeoutError(
                    f"等待 xlerobot_host 就绪超过 {timeout:g} 秒；"
                    f"进程 PID={sorted(pids)}，端口状态={port_state}。"
                )
            if now >= next_progress_log:
                elapsed = now - started_at
                self._log(
                    f"[host] 仍在启动，已等待 {elapsed:.0f} 秒；"
                    f"进程 PID={sorted(pids)}，端口状态={port_state}。\n"
                )
                next_progress_log = now + HOST_WAIT_LOG_INTERVAL
            time.sleep(0.5)

    def ensure_host_running(
        self,
        timeout: float = DEFAULT_HOST_START_TIMEOUT,
        calibration: str = "restore",
    ) -> bool:
        """确保 host 完全就绪；返回本次是否发送了启动命令。"""
        responses = {"restore": "\n", "manual": "c\n", "ask": None}
        if calibration not in responses:
            raise ValueError("calibration 必须是 restore、manual 或 ask")
        with self._operation_lock:
            self.stop_remote_service("vr", wait_for_serial_release=False)
            existing_pids = self.remote_service_pids("host")
            port_state = self.probe_host_ports()
            if existing_pids and all(port_state.values()):
                self._log(
                    f"[连接检查] xlerobot_host 已运行，PID={sorted(existing_pids)}。\n"
                )
                self.wait_for_host_ready(timeout, process_already_seen=True)
                return False

            if not existing_pids and any(port_state.values()):
                open_ports = [port for port, is_open in port_state.items() if is_open]
                raise RuntimeError(
                    "未检测到 xlerobot_host 进程，但端口被其他进程占用："
                    f"{open_ports}。"
                )

            launch_channel: paramiko.Channel | None = None
            started = False
            if existing_pids:
                self._log(
                    f"[连接检查] 检测到正在启动的 xlerobot_host，"
                    f"PID={sorted(existing_pids)}，等待 ZMQ 就绪。\n"
                )
            else:
                self._log("[连接检查] host 未启动，正在通过 SSH 远程启动...\n")
                started, launch_channel = self._start_service_with_channel(
                    "host",
                    host_response=responses[calibration],
                )

            try:
                self.wait_for_host_ready(
                    timeout,
                    launch_channel=launch_channel,
                    process_already_seen=bool(existing_pids),
                )
            except Exception:
                if launch_channel is not None:
                    try:
                        self.stop_remote_service("host")
                    except Exception as cleanup_exc:
                        self._log(f"[host] 启动失败后的清理也失败：{cleanup_exc}\n")
                raise
            return started

    def wait_for_vr_running(
        self,
        timeout: float = DEFAULT_VR_START_TIMEOUT,
        *,
        launch_channel: paramiko.Channel | None = None,
        process_already_seen: bool = False,
    ) -> None:
        """等待 telegrip 的进程、网络、串口、控制循环和电机全部就绪。"""
        started_at = time.monotonic()
        deadline = started_at + max(0.0, timeout)
        next_progress_log = started_at + VR_WAIT_LOG_INTERVAL
        consecutive_ready = 0
        saw_process = process_already_seen
        last_pids: set[int] = set()
        last_port_state = {port: False for port in VR_SERVICE_PORTS}
        last_serial_owners = {port: set() for port in VR_SERIAL_PORTS}
        last_status: dict[str, object] | None = None
        while True:
            pids = self.remote_service_pids("vr")
            last_pids = pids
            if pids:
                saw_process = True
            else:
                consecutive_ready = 0
                if saw_process:
                    raise RuntimeError("远程 VR 服务在启动确认期间意外退出。")

            last_port_state = self.probe_vr_ports()
            last_serial_owners = self.remote_serial_port_owners()
            last_status = self.probe_vr_status()
            with self._state_lock:
                connected_arms = set(
                    self._vr_connected_arms.get(launch_channel, set())
                )
            # INFO 日志可能因为远程 logging 已被依赖预先配置而不输出，
            # 因此连接 marker 只作辅助诊断，不能成为就绪的硬门槛。双臂
            # 由两组独立信号确认：API 必须同时报告左右臂连接，且同一个
            # 实际 telegrip PID 必须同时持有两个串口。
            common_serial_owner_pids = set(pids)
            for port in VR_SERIAL_PORTS:
                common_serial_owner_pids.intersection_update(
                    last_serial_owners[port]
                )
            serial_ready = bool(common_serial_owner_pids)
            status_ready = bool(
                last_status is not None
                and all(
                    last_status.get(key) is True
                    for key in VR_REQUIRED_STATUS_FLAGS
                )
            )
            fully_ready = bool(
                pids
                and all(last_port_state.values())
                and serial_ready
                and status_ready
            )
            if fully_ready:
                consecutive_ready += 1
                if consecutive_ready >= 2:
                    elapsed = time.monotonic() - started_at
                    self._log(
                        "[vr] 控制链已就绪："
                        f"PID={sorted(pids)}，双串口同进程占用、8442/8443、"
                        "左右臂 API、控制循环及电机 Engage 正常"
                        f"（等待 {elapsed:.1f} 秒）。\n"
                    )
                    return
            else:
                consecutive_ready = 0

            exit_code = self._record_confirmed_exit_status(launch_channel)
            if exit_code is not None:
                raise RuntimeError(f"VR 服务在启动期间退出，退出码：{exit_code}")
            if launch_channel is not None:
                with self._state_lock:
                    start_error = self._vr_start_errors.get(launch_channel)
                if start_error:
                    raise RuntimeError(start_error)
            if launch_channel is not None and not pids:
                wrapper_identity = self._managed_wrapper_identity(launch_channel)
                if (
                    wrapper_identity is not None
                    and not self.remote_wrapper_running(wrapper_identity)
                ):
                    raise RuntimeError("VR 的远程启动 wrapper 在服务出现前退出。")
                if wrapper_identity is None and launch_channel.closed:
                    raise RuntimeError(
                        "VR 启动 channel 在报告 wrapper 身份前异常关闭；"
                        "无法确认远程状态。"
                    )
            now = time.monotonic()
            if now >= deadline:
                status_summary = (
                    {
                        key: last_status.get(key)
                        for key in (*VR_REQUIRED_STATUS_FLAGS, "vrConnected")
                    }
                    if last_status is not None
                    else None
                )
                serial_summary = {
                    port: sorted(owners)
                    for port, owners in last_serial_owners.items()
                }
                raise TimeoutError(
                    f"等待 VR 控制链就绪超过 {timeout:g} 秒；"
                    f"PID={sorted(last_pids)}，端口={last_port_state}，"
                    f"串口占用={serial_summary}，API={status_summary}。"
                    f"左右臂连接日志（仅辅助诊断）="
                    f"{sorted(connected_arms)}。"
                )
            if now >= next_progress_log:
                elapsed = now - started_at
                status_summary = (
                    {
                        key: last_status.get(key)
                        for key in VR_REQUIRED_STATUS_FLAGS
                    }
                    if last_status is not None
                    else None
                )
                self._log(
                    f"[vr] 仍在初始化，已等待 {elapsed:.0f} 秒；"
                    f"PID={sorted(pids)}，端口={last_port_state}，"
                    f"串口占用="
                    f"{ {port: sorted(owners) for port, owners in last_serial_owners.items()} }，"
                    f"API={status_summary}，左右臂连接日志（辅助）="
                    f"{sorted(connected_arms)}。\n"
                )
                next_progress_log = now + VR_WAIT_LOG_INTERVAL
            time.sleep(0.5)

    def start_vr(self, timeout: float = DEFAULT_VR_START_TIMEOUT) -> bool:
        """停止 host 后启动 VR，并等待完整控制链可用。"""
        with self._operation_lock:
            try:
                started, channel = self._start_service_with_channel("vr")
                self.wait_for_vr_running(
                    timeout,
                    launch_channel=channel,
                    process_already_seen=not started and channel is None,
                )
                return started
            except KeyboardInterrupt:
                raise
            except Exception as start_exc:
                try:
                    self.stop_remote_service(
                        "vr",
                        wait_for_serial_release=False,
                    )
                    self.ensure_host_running(timeout=DEFAULT_HOST_START_TIMEOUT)
                except Exception as recovery_exc:
                    raise RuntimeError(
                        f"VR 启动失败：{start_exc}；恢复 host 也失败：{recovery_exc}"
                    ) from start_exc
                raise

    def stop_remote_service(
        self,
        service_name: str,
        timeout: float = DEFAULT_STOP_TIMEOUT,
        terminate_timeout: float = DEFAULT_TERMINATE_TIMEOUT,
        *,
        wait_for_serial_release: bool = True,
    ) -> bool:
        """停止指定服务，包括由其他终端启动的远程进程。

        批量清理互相冲突的服务时可暂不等待全局串口释放；调用方必须在
        所有目标进程都确认退出后统一调用 wait_for_serial_ports_released()。
        """
        if service_name not in SERVICE_COMMANDS:
            raise ValueError(f"未知服务 {service_name!r}")
        with self._operation_lock:
            channel, reader = self._managed_channel(service_name)
            pids = self.remote_service_pids(service_name)
            channel_pending = not self._channel_has_exited(channel)
            wrapper_identity = self._managed_wrapper_identity(channel)
            wrapper_pid = wrapper_identity[0] if wrapper_identity is not None else None
            if not pids and not channel_pending:
                if service_name == "host":
                    port_state = self.probe_host_ports()
                    if any(port_state.values()):
                        open_ports = [
                            port for port, is_open in port_state.items() if is_open
                        ]
                        raise RuntimeError(
                            "未检测到 xlerobot_host PID，但端口仍在监听："
                            f"{open_ports}。"
                        )
                self._clear_managed_channel(service_name, channel)
                return False

            self._log(
                f"正在停止 [{service_name}]，远程 PID={sorted(pids)}，"
                f"wrapper PID={wrapper_pid} ...\n"
            )
            if pids:
                self._signal_remote_process(service_name, "INT", pids)
            elif wrapper_pid is not None and channel_pending:
                assert wrapper_identity is not None
                self._signal_wrapper_pid(
                    wrapper_pid,
                    wrapper_identity[1],
                    "INT",
                )
            elif channel_pending and channel is not None and not channel.closed:
                # 仅在目标 PID 和 wrapper 身份都尚不可见时，才以 PTY Ctrl+C
                # 作为兜底。对同一进程同时发送 Ctrl+C 与 os.kill(SIGINT) 会
                # 在它的 finally/disconnect 阶段造成第二次 KeyboardInterrupt。
                try:
                    channel.send("\x03")
                except (OSError, paramiko.SSHException):
                    pass

            if not self._wait_service_stopped(service_name, channel, timeout):
                remaining = self.remote_service_pids(service_name)
                wrapper_identity = self._managed_wrapper_identity(channel)
                wrapper_pid = (
                    wrapper_identity[0] if wrapper_identity is not None else None
                )
                if remaining:
                    self._log(
                        f"[{service_name}] 未在 {timeout:g} 秒内退出，"
                        f"向 PID={sorted(remaining)} 发送 SIGTERM。\n"
                    )
                    self._signal_remote_process(service_name, "TERM", remaining)
                if (
                    wrapper_pid is not None
                    and wrapper_pid not in remaining
                    and not self._channel_has_exited(channel)
                ):
                    self._log(
                        f"[{service_name}] 向 wrapper PID={wrapper_pid} 发送 SIGTERM。\n"
                    )
                    assert wrapper_identity is not None
                    self._signal_wrapper_pid(
                        wrapper_pid,
                        wrapper_identity[1],
                        "TERM",
                    )
                if (
                    not remaining
                    and wrapper_pid is None
                    and not self._channel_has_exited(channel)
                ):
                    if channel is not None and not channel.closed:
                        channel.close()
                    self._clear_managed_channel(service_name, channel)
                    raise RuntimeError(
                        f"远程 [{service_name}] 的启动 channel 未退出，且无法确认 wrapper PID；"
                        "已取消后续服务切换。"
                    )
                if not self._wait_service_stopped(
                    service_name,
                    channel,
                    terminate_timeout,
                ):
                    remaining = self.remote_service_pids(service_name)
                    if channel is not None and not channel.closed:
                        channel.close()
                    self._clear_managed_channel(service_name, channel)
                    raise RuntimeError(
                        f"远程 [{service_name}] 无法停止，仍存活 PID={sorted(remaining)}，"
                        f"wrapper PID={wrapper_pid}；"
                        "已取消后续服务切换。"
                    )

            if service_name == "host" and not self._wait_host_ports_closed():
                if channel is not None and not channel.closed:
                    channel.close()
                if reader is not None and reader is not threading.current_thread():
                    reader.join(timeout=1.0)
                self._clear_managed_channel(service_name, channel)
                raise RuntimeError(
                    f"xlerobot_host 进程已退出，但端口 {list(HOST_ZMQ_PORTS)} 仍在监听。"
                )

            if wait_for_serial_release:
                try:
                    self.wait_for_serial_ports_released()
                except Exception:
                    if channel is not None and not channel.closed:
                        channel.close()
                    if reader is not None and reader is not threading.current_thread():
                        reader.join(timeout=1.0)
                    self._clear_managed_channel(service_name, channel)
                    raise

            if channel is not None and not channel.closed:
                channel.close()
            if reader is not None and reader is not threading.current_thread():
                reader.join(timeout=1.0)
            self._clear_managed_channel(service_name, channel)
            self._log(f"[{service_name}] 已停止。\n")
            return True

    def stop_service(self, timeout: float = DEFAULT_STOP_TIMEOUT) -> bool:
        """停止当前由控制器跟踪的服务。"""
        with self._state_lock:
            service_name = self._current_service
        if service_name is None:
            return False
        return self.stop_remote_service(service_name, timeout=timeout)

    def stop_all_services(self) -> bool:
        """先停止 VR/host 两类进程，再统一确认双串口释放。"""
        with self._operation_lock:
            stopped_any = False
            errors: list[str] = []
            for service_name in ("vr", "host"):
                try:
                    stopped_any = (
                        self.stop_remote_service(
                            service_name,
                            wait_for_serial_release=False,
                        )
                        or stopped_any
                    )
                except Exception as exc:
                    errors.append(f"{service_name}: {exc}")
            if stopped_any:
                try:
                    self.wait_for_serial_ports_released()
                except Exception as exc:
                    errors.append(f"serial: {exc}")
            if errors:
                raise RuntimeError("停止远程服务失败：" + "；".join(errors))
            return stopped_any

    def stop_vr_and_restore_host(
        self,
        timeout: float = DEFAULT_HOST_START_TIMEOUT,
    ) -> None:
        """停止 VR，启动 host，并等到主程序可以安全重连。"""
        with self._operation_lock:
            self.stop_remote_service("vr", wait_for_serial_release=False)
            self.ensure_host_running(timeout=timeout)

    def switch_service(self, service_name: str) -> bool:
        """切换到 host 或 vr；是 start_service 的语义化别名。"""
        if service_name == "host":
            return self.start_host()
        if service_name == "vr":
            return self.start_vr()
        raise ValueError(f"未知服务 {service_name!r}")

    def send_input(self, text: str = "", press_enter: bool = True) -> None:
        """向当前服务发送输入；默认在内容后附加回车。"""
        with self._state_lock:
            channel = self._channel
            service_name = self._current_service

        if (
            channel is None
            or service_name is None
            or channel.closed
            or channel.exit_status_ready()
        ):
            raise RuntimeError("当前没有可接收输入的远程服务")

        channel.send(text + ("\n" if press_enter else ""))
        display = repr(text) if text else "回车"
        self._log(f"[{service_name}] 已发送输入：{display}\n")

    def status(self) -> str | None:
        """根据远程真实 PID 返回 host/vr；同时存在时明确报冲突。"""
        with self._operation_lock:
            host_pids = self.remote_service_pids("host")
            vr_pids = self.remote_service_pids("vr")
            if host_pids and vr_pids:
                raise RuntimeError(
                    "远程 host 与 VR 同时运行："
                    f"host PID={sorted(host_pids)}，VR PID={sorted(vr_pids)}"
                )
            if host_pids:
                return "host"
            if vr_pids:
                return "vr"

            # conda 初始化阶段尚无目标 PID，保留 managed 启动中状态。
            with self._state_lock:
                channel = self._channel
                current = self._current_service
            if current is not None and channel is not None:
                self._record_confirmed_exit_status(channel)
                if self._channel_has_exited(channel):
                    self._clear_managed_channel(current, channel)
                    return None
                wrapper_identity = self._managed_wrapper_identity(channel)
                if wrapper_identity is not None:
                    if self.remote_wrapper_running(wrapper_identity):
                        return current
                    self._clear_managed_channel(current, channel)
                    return None
                raise RuntimeError(
                    f"[{current}] 启动 channel 状态不确定，且尚未取得 wrapper 身份。"
                )
            return None

    def close(self) -> None:
        """停止所有远程服务并断开 SSH，即使清理失败也关闭连接。"""
        with self._operation_lock:
            cleanup_error: Exception | None = None
            try:
                self.stop_all_services()
            except Exception as exc:
                cleanup_error = exc
            finally:
                if self._client is not None:
                    self._client.close()
                    self._client = None
                    self._log("SSH 连接已关闭。\n")
            if cleanup_error is not None:
                raise cleanup_error


# 模块级单例：同目录的其他 Python 直接调用下面的函数即可。
_default_controller: RemoteServiceController | None = None
_default_controller_lock = threading.Lock()


def get_controller(host: str = HOST) -> RemoteServiceController:
    """取得共享控制器。"""
    global _default_controller
    with _default_controller_lock:
        if _default_controller is None:
            _default_controller = RemoteServiceController(host=host)
        elif _default_controller.host != host:
            raise RuntimeError(
                "共享远程控制器已经绑定到 "
                f"{_default_controller.host}，不能改为 {host}。"
            )
        return _default_controller


def start_host(calibration: str = "restore") -> bool:
    """非阻塞启动 host；默认自动回车恢复已有校准。"""
    return get_controller().start_host(calibration)


def ensure_host_running(
    timeout: float = DEFAULT_HOST_START_TIMEOUT,
    calibration: str = "restore",
) -> bool:
    """必要时启动 host，并等待 ZMQ 双端口完全就绪。"""
    return get_controller().ensure_host_running(timeout, calibration)


def start_vr(timeout: float = DEFAULT_VR_START_TIMEOUT) -> bool:
    """停止 host，启动 VR，并等待完整控制链可用。"""
    return get_controller().start_vr(timeout)


def switch_service(service_name: str) -> bool:
    """切换服务，例如 switch_service("host") 或 switch_service("vr")。"""
    return get_controller().switch_service(service_name)


def stop_service() -> bool:
    """停止当前服务。"""
    return get_controller().stop_service()


def stop_remote_service(service_name: str) -> bool:
    """停止指定远程服务，包括外部终端启动的进程。"""
    return get_controller().stop_remote_service(service_name)


def stop_all_services() -> bool:
    """停止远程 host 与 VR。"""
    return get_controller().stop_all_services()


def stop_vr_and_restore_host(
    timeout: float = DEFAULT_HOST_START_TIMEOUT,
) -> None:
    """停止 VR 并等待 host 恢复就绪。"""
    get_controller().stop_vr_and_restore_host(timeout)


def service_status() -> str | None:
    """返回 host、vr 或 None。"""
    return get_controller().status()


def send_input(text: str = "", press_enter: bool = True) -> None:
    """向当前远程服务发送输入，默认在文本后附加回车。"""
    get_controller().send_input(text, press_enter)


def shutdown() -> None:
    """停止所有服务并关闭共享 SSH 连接。"""
    global _default_controller
    with _default_controller_lock:
        controller = _default_controller
        _default_controller = None
    if controller is not None:
        controller.close()


def _shutdown_at_exit() -> None:
    try:
        shutdown()
    except Exception as exc:
        print(f"远程服务退出清理失败：{exc}", file=sys.stderr)


atexit.register(_shutdown_at_exit)


def main() -> int:
    """交互式服务切换菜单。"""
    controller = get_controller()
    print(
        "\nXLerobot 远程服务控制\n"
        "  1 / host  启动 host 服务\n"
        "  1c        启动 host 并进入手动校准\n"
        "  2 / vr    启动 VR 控制\n"
        "  0 / stop  停止当前服务\n"
        "  s         查看状态\n"
        "  enter     向当前服务发送回车\n"
        "  i 内容    向当前服务发送内容和回车\n"
        "  q         停止服务并退出\n"
    )

    try:
        while True:
            raw_choice = input("请选择> ").strip()
            choice = raw_choice.lower()
            try:
                if choice in {"1", "host"}:
                    controller.start_host()
                elif choice in {"1c", "host-c", "host-manual"}:
                    controller.start_host("manual")
                elif choice in {"2", "vr"}:
                    controller.start_vr()
                elif choice in {"0", "stop"}:
                    if not controller.stop_service():
                        print("当前没有运行中的服务。")
                elif choice in {"s", "status"}:
                    print(f"当前服务：{controller.status() or '未运行'}")
                elif choice == "enter":
                    controller.send_input()
                elif choice.startswith("i "):
                    controller.send_input(raw_choice[2:])
                elif choice in {"q", "quit", "exit"}:
                    break
                elif choice:
                    print("无效选项，请输入 1、1c、2、0、s、enter、i 内容或 q。")
            except (paramiko.SSHException, socket.error, RuntimeError) as exc:
                print(f"操作失败：{exc}", file=sys.stderr)
    except (KeyboardInterrupt, EOFError):
        print("\n收到退出信号。")
    finally:
        shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
