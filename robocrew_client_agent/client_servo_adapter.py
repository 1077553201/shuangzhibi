"""RoboCrew 官方 XLeRobot 工具的 client 适配器。

RoboCrew 官方工具函数入口是 `create_move_forward(servo_controller)` 等。
这些函数只要求传入对象具有 `go_forward()`、`turn_left()` 等方法。

本文件只提供一个兼容这些方法名的适配器。上层继续调用 RoboCrew 官方
`robocrew.robots.XLeRobot.tools.create_*` 函数，不重写官方工具逻辑。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")

import path_setup  # noqa: F401
from lerobot.robots.xlerobot import XLerobotClient, XLerobotClientConfig

from config import (
    BASE_COMMAND_INTERVAL,
    HEAD_LEFT_SIGN,
    HEAD_PITCH_KEY,
    HEAD_SETTLE_SECONDS,
    HEAD_UP_SIGN,
    HEAD_YAW_KEY,
    MAX_BASE_ACTIONS_PER_TASK,
    ROBOT_ID,
    ROBOT_IP,
)

try:
    from robocrew.robots.XLeRobot.servo_controls import ANGULAR_DPS, DEFAULT_ARM_POSITION_DIR, LINEAR_MPS
except ImportError:
    # RoboCrew 0.1.3 官方 servo_controls.py 中的默认常量。
    LINEAR_MPS = 0.25
    ANGULAR_DPS = 100.0
    DEFAULT_ARM_POSITION_DIR = "~/.cache/robocrew/positions/"


ARM_JOINT_NAMES = (
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
    "gripper",
)


class ClientServoControler:
    """用 XLerobotClient 实现的 ServoControler 兼容对象。"""

    def __init__(self, remote_ip: str = ROBOT_IP, robot_id: str = ROBOT_ID) -> None:
        self.remote_ip = remote_ip
        self.robot_id = robot_id
        self.left_arm_head_usb = None
        self.right_arm_wheel_usb = None
        self.base_action_count = 0
        self.max_base_actions_per_task = MAX_BASE_ACTIONS_PER_TASK
        self.abort_requested = False
        self.robot = XLerobotClient(XLerobotClientConfig(remote_ip=remote_ip, id=robot_id))

    def reset_task_budget(self) -> None:
        self.base_action_count = 0
        self.abort_requested = False

    def request_task_abort(self) -> None:
        self.abort_requested = True
        try:
            self._stop_base()
        except Exception:
            pass

    def _arm_position_file(self, position_name: str, base_dir: str = DEFAULT_ARM_POSITION_DIR) -> Path:
        safe_name = position_name.strip().replace("\\", "_").replace("/", "_")
        file_name = safe_name if safe_name.endswith(".json") else f"{safe_name}.json"
        return Path(base_dir).expanduser() / file_name

    def _arm_action_from_position(self, positions: dict[str, float], arm_side: str) -> dict[str, float]:
        prefix = f"{arm_side}_arm"
        return {
            f"{prefix}_{name}.pos": float(value)
            for name, value in positions.items()
            if name in ARM_JOINT_NAMES
        }

    def _read_current_arm_positions(self, obs: dict[str, Any], arm_side: str) -> dict[str, float]:
        prefix = f"{arm_side}_arm"
        positions: dict[str, float] = {}
        for name in ARM_JOINT_NAMES:
            key = f"{prefix}_{name}.pos"
            if key in obs:
                positions[name] = float(obs[key])
        return positions

    def save_current_arm_position(self, position_name: str, arm_side: str = "both") -> str:
        """按 RoboCrew 官方保存姿态格式保存当前 host 上报的手臂关节位置。"""
        if arm_side not in {"left", "right", "both"}:
            return "arm_side 只能是 left、right 或 both。"

        obs = self.get_observation()
        if arm_side == "both":
            positions: dict[str, Any] = {
                "left": self._read_current_arm_positions(obs, "left"),
                "right": self._read_current_arm_positions(obs, "right"),
            }
            if not positions["left"] and not positions["right"]:
                return "当前 observation 里没有左右手臂关节位置。"
        else:
            positions = self._read_current_arm_positions(obs, arm_side)
            if not positions:
                return f"当前 observation 里没有 {arm_side} 手臂关节位置。"

        file_path = self._arm_position_file(position_name)
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return f"创建官方姿态目录失败：{file_path.parent}；错误：{exc}"

        data = {
            "arm_side": arm_side,
            "positions": positions,
        }
        file_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return f"已保存当前手臂姿态：{file_path}，arm_side={arm_side}。"

    def set_saved_position(self, position_name: str, arm_side: str = "both") -> str:
        """执行 RoboCrew 官方保存姿态文件。

        官方 ServoControler 的同名方法从 `~/.cache/robocrew/positions/` 读取
        JSON 后写入手臂目标位置。这里仅做 client 传输适配，不计算轨迹、不做 IK。
        """
        file_path = self._arm_position_file(position_name)
        if not file_path.exists():
            return f"未找到官方保存姿态：{file_path}"

        raw_data = json.loads(file_path.read_text(encoding="utf-8"))
        if isinstance(raw_data, dict) and "arm_side" in raw_data and "positions" in raw_data:
            saved_side = raw_data["arm_side"]
            data = raw_data["positions"]
        elif isinstance(raw_data, dict) and "left" in raw_data and "right" in raw_data:
            saved_side = "both"
            data = raw_data
        else:
            return "姿态文件格式不符合 RoboCrew 官方保存格式。"

        if saved_side != arm_side:
            return f"姿态文件属于 {saved_side}，但本次请求的是 {arm_side}。请使用匹配的 arm_side。"

        action: dict[str, float] = {}
        if arm_side == "both":
            if isinstance(data, dict) and "left" in data and "right" in data:
                action.update(self._arm_action_from_position(data["left"], "left"))
                action.update(self._arm_action_from_position(data["right"], "right"))
            else:
                action.update(self._arm_action_from_position(data, "left"))
                action.update(self._arm_action_from_position(data, "right"))
        elif arm_side in {"left", "right"}:
            if isinstance(data, dict) and "left" in data and "right" in data:
                data = data[arm_side]
            action.update(self._arm_action_from_position(data, arm_side))
        else:
            return "arm_side 只能是 left、right 或 both。"

        if not action:
            return "姿态文件里没有可发送的手臂关节字段。"

        self.connect()
        self.robot.send_action(action)
        time.sleep(0.8)
        return f"已按 RoboCrew 官方保存姿态发送手臂目标：{position_name}, arm_side={arm_side}。"

    def connect(self) -> None:
        if not self.robot.is_connected:
            self.robot.connect()

    def disconnect(self) -> None:
        if self.robot.is_connected:
            self._stop_base()
            self.robot.disconnect()

    def get_observation(self) -> dict[str, Any]:
        self.connect()
        return self.robot.get_observation()

    def _stop_base(self) -> None:
        self.robot.send_action({"x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0})

    def set_arm_torque(self, arm_side: str = "both", enabled: bool = True) -> None:
        """请求 host 运行时切换手臂扭矩。

        这只控制 Feetech 扭矩开关，不计算动作轨迹。
        """
        if arm_side not in {"left", "right", "both"}:
            raise ValueError("arm_side 只能是 left、right 或 both。")
        self.connect()
        self.robot.send_action(
            {
                "__xlerobot_host_command": "set_arm_torque",
                "arm_side": arm_side,
                "enabled": bool(enabled),
            }
        )
        time.sleep(0.4)

    def release_arm_for_recording(self, arm_side: str = "both") -> None:
        self.set_arm_torque(arm_side, enabled=False)

    def restore_arm_after_recording(self, arm_side: str = "both") -> None:
        self.set_arm_torque(arm_side, enabled=True)

    def _run_base(self, x_vel: float, y_vel: float, theta_vel: float, duration_s: float) -> str:
        self.connect()
        if self.base_action_count >= self.max_base_actions_per_task:
            message = "动作未执行：本任务的底盘动作预算已用完。不要声称该移动已经完成。"
            print(message)
            self._stop_base()
            return message
        self.base_action_count += 1
        action = {"x.vel": x_vel, "y.vel": y_vel, "theta.vel": theta_vel}
        deadline = time.perf_counter() + max(0.0, duration_s)
        while time.perf_counter() < deadline:
            if self.abort_requested:
                self._stop_base()
                return "动作已终止：收到本次任务中止请求。"
            self.robot.send_action(action)
            time.sleep(BASE_COMMAND_INTERVAL)
        self._stop_base()
        return f"动作已执行：底盘速度 {action}，持续 {duration_s:.2f} 秒。"

    def go_forward(self, meters: float) -> str:
        return self._run_base(LINEAR_MPS, 0.0, 0.0, float(meters) / LINEAR_MPS)

    def go_backward(self, meters: float) -> str:
        return self._run_base(-LINEAR_MPS, 0.0, 0.0, float(meters) / LINEAR_MPS)

    def strafe_left(self, meters: float) -> str:
        return self._run_base(0.0, LINEAR_MPS, 0.0, float(meters) / LINEAR_MPS)

    def strafe_right(self, meters: float) -> str:
        return self._run_base(0.0, -LINEAR_MPS, 0.0, float(meters) / LINEAR_MPS)

    def turn_left(self, degrees: float) -> str:
        return self._run_base(0.0, 0.0, ANGULAR_DPS, float(degrees) / ANGULAR_DPS)

    def turn_right(self, degrees: float) -> str:
        return self._run_base(0.0, 0.0, -ANGULAR_DPS, float(degrees) / ANGULAR_DPS)

    def turn_head_yaw(self, degrees: float) -> None:
        self.connect()
        if self.abort_requested:
            return
        self.robot.send_action({HEAD_YAW_KEY: HEAD_LEFT_SIGN * float(degrees)})
        self._sleep_interruptible(HEAD_SETTLE_SECONDS)

    def turn_head_pitch(self, degrees: float) -> None:
        self.connect()
        if self.abort_requested:
            return
        self.robot.send_action({HEAD_PITCH_KEY: HEAD_UP_SIGN * float(degrees)})
        self._sleep_interruptible(HEAD_SETTLE_SECONDS)

    def _sleep_interruptible(self, seconds: float) -> None:
        deadline = time.perf_counter() + max(0.0, seconds)
        while time.perf_counter() < deadline and not self.abort_requested:
            time.sleep(min(0.05, deadline - time.perf_counter()))

    def turn_head_to_vla_position(self, pitch_deg: float = 45) -> str:
        self.turn_head_pitch(pitch_deg)
        self.turn_head_yaw(0)
        time.sleep(0.9)
        return "头部已移动到 VLA 观察位置。"

    def reset_head_position(self) -> str:
        self.turn_head_pitch(22)
        self.turn_head_yaw(0)
        time.sleep(0.9)
        return "头部已恢复到普通观察位置。"
