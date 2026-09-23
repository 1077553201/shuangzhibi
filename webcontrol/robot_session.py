"""Web 控制台的机器人会话封装。

Web 层只做任务调度、日志收集和中止控制；机器人动作仍复用
`robocrew_client_agent` 中已经调通的 host-client 逻辑。
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
import threading
import time
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]
AGENT_DIR = ROOT / "robocrew_client_agent"
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")

from client_camera_adapter import ClientRobotCamera
from client_servo_adapter import ClientServoControler
from config import (
    MAIN_CAMERA_KEY,
    OPENAI_COMPATIBLE_API_BASE,
    OPENAI_COMPATIBLE_API_KEY,
    ROBOT_ID,
    ROBOT_IP,
)
from official_motion_tools import list_recorded_motions, play_motion, record_motion, recorded_motion_catalog
from run_robocrew_client_agent import (
    build_agent,
    check_host_ports,
    configure_runtime_output,
    require_module,
    run_one_task,
)
from voice_control_agent import create_asr, transcribe_recorded_voice


class LogBuffer:
    def __init__(self, max_lines: int = 800) -> None:
        self.max_lines = max_lines
        self._lines: list[dict] = []
        self._next_id = 1
        self._lock = threading.Lock()

    def write_line(self, text: str, level: str = "info") -> None:
        for raw_line in str(text).splitlines():
            line = raw_line.rstrip()
            if not line:
                continue
            with self._lock:
                self._lines.append(
                    {
                        "id": self._next_id,
                        "time": time.strftime("%H:%M:%S"),
                        "level": level,
                        "text": line,
                    }
                )
                self._next_id += 1
                if len(self._lines) > self.max_lines:
                    self._lines = self._lines[-self.max_lines :]

    def read_since(self, after_id: int = 0) -> list[dict]:
        with self._lock:
            return [line for line in self._lines if int(line["id"]) > after_id]


class LogWriter(io.TextIOBase):
    def __init__(self, log_buffer: LogBuffer, level: str = "info") -> None:
        self.log_buffer = log_buffer
        self.level = level
        self._pending = ""

    def write(self, text: str) -> int:
        self._pending += str(text)
        while "\n" in self._pending:
            line, self._pending = self._pending.split("\n", 1)
            self.log_buffer.write_line(line, self.level)
        return len(text)

    def flush(self) -> None:
        if self._pending:
            self.log_buffer.write_line(self._pending, self.level)
            self._pending = ""


class RobotSession:
    def __init__(self) -> None:
        self.log = LogBuffer()
        self._init_lock = threading.Lock()
        self._task_lock = threading.Lock()
        self._abort_event = threading.Event()
        self._busy = False
        self._last_result = ""
        self._last_error = ""
        self._task_thread: threading.Thread | None = None
        self._asr = None

        self.servo_controller: ClientServoControler | None = None
        self.main_camera: ClientRobotCamera | None = None
        self.agent = None

    @property
    def busy(self) -> bool:
        return self._busy

    def initialize(self) -> None:
        with self._init_lock:
            if self.agent is not None:
                return
            configure_runtime_output()
            require_module("robocrew")
            os.environ.setdefault("OPENAI_API_KEY", OPENAI_COMPATIBLE_API_KEY)
            os.environ.setdefault("OPENAI_API_BASE", OPENAI_COMPATIBLE_API_BASE)
            os.environ.setdefault("OPENAI_BASE_URL", OPENAI_COMPATIBLE_API_BASE)
            self.log.write_line("[初始化] 正在检查 XLeRobot host 连接。")
            if not check_host_ports(ROBOT_IP):
                raise RuntimeError("XLeRobot host 端口未就绪，请先启动树莓派 host。")
            self.servo_controller = ClientServoControler(remote_ip=ROBOT_IP, robot_id=ROBOT_ID)
            self.servo_controller.connect()
            self.main_camera = ClientRobotCamera(self.servo_controller, camera_key=MAIN_CAMERA_KEY)
            self.agent = build_agent(self.servo_controller, self.main_camera)
            self.log.write_line("[初始化] Web 控制台已连接机器人。")

    def shutdown(self) -> None:
        self.abort()
        if self.servo_controller is not None:
            self.servo_controller.disconnect()

    def status(self) -> dict:
        return {
            "busy": self._busy,
            "connected": self.agent is not None,
            "last_result": self._last_result,
            "last_error": self._last_error,
        }

    def abort(self) -> None:
        self._abort_event.set()
        if self.servo_controller is not None:
            self.servo_controller.request_task_abort()
        self.log.write_line("[终止] 已请求终止本轮任务。")

    def submit_task(self, task: str) -> dict:
        return self._start_background("task", lambda: self._run_task(task))

    def submit_play_motion(self, motion_name: str, speed: float = 1.0) -> dict:
        return self._start_background("play_motion", lambda: self._run_play_motion(motion_name, speed))

    def submit_record_motion(self, motion_name: str, arm_side: str, seconds: float, fps: float = 20.0) -> dict:
        return self._start_background(
            "record_motion",
            lambda: self._run_record_motion(motion_name, arm_side, seconds, fps),
        )

    def _start_background(self, name: str, target: Callable[[], str]) -> dict:
        if not self._task_lock.acquire(blocking=False):
            return {"ok": False, "message": "当前已有任务在执行，请先等待或点击停止本轮。"}
        self._abort_event.clear()
        self._busy = True
        self._last_error = ""
        self._last_result = ""
        self.log.write_line(f"[任务队列] 已开始：{name}")

        def runner() -> None:
            stdout = LogWriter(self.log, "info")
            stderr = LogWriter(self.log, "error")
            try:
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    result = target()
                self._last_result = str(result)
                self.log.write_line(f"[完成] {self._last_result}")
            except Exception as exc:
                self._last_error = str(exc)
                self.log.write_line(f"[错误] {exc}", "error")
            finally:
                stdout.flush()
                stderr.flush()
                self._busy = False
                self._abort_event.clear()
                self._task_lock.release()

        self._task_thread = threading.Thread(target=runner, daemon=True)
        self._task_thread.start()
        return {"ok": True, "message": "任务已提交。"}

    def _run_task(self, task: str) -> str:
        self.initialize()
        assert self.servo_controller is not None
        assert self.main_camera is not None
        return run_one_task(
            self.agent,
            self.servo_controller,
            self.main_camera,
            task,
            show_raw_output=False,
            should_stop=self._abort_event.is_set,
        )

    def _run_play_motion(self, motion_name: str, speed: float) -> str:
        self.initialize()
        assert self.servo_controller is not None
        return play_motion(
            self.servo_controller,
            motion_name,
            speed=speed,
            should_stop=self._abort_event.is_set,
        ).replace("TASK_COMPLETE:", "").strip()

    def _run_record_motion(self, motion_name: str, arm_side: str, seconds: float, fps: float) -> str:
        self.initialize()
        assert self.servo_controller is not None
        return record_motion(
            self.servo_controller,
            motion_name,
            arm_side,
            seconds,
            fps,
            should_stop=self._abort_event.is_set,
        ).replace("TASK_COMPLETE:", "").strip()

    def motions(self) -> dict:
        return {
            "summary": list_recorded_motions(),
            "motions": recorded_motion_catalog(),
        }

    def capture_preview(self) -> bytes:
        self.initialize()
        assert self.main_camera is not None
        return self.main_camera.capture_image()

    def transcribe_wav(self, wav_path: Path, rms: float, silence_threshold: float = 0.001) -> str:
        if self._asr is None:
            self.log.write_line("[ASR] 正在加载本地语音识别模型。")
            self._asr = create_asr("medium", "auto", "auto")
        return transcribe_recorded_voice(self._asr, wav_path, rms, silence_threshold)
