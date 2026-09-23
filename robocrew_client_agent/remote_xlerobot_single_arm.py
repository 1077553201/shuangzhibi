"""把 LeRobot RobotClient 的单臂数据源接到树莓派 xlerobot_host。

这个类只做字段映射：

- 从 `XLerobotClient.get_observation()` 读取树莓派 host 发来的手臂关节和相机图像；
- 把单臂 policy 输出的 `shoulder_pan.pos` 等动作字段映射回
  `right_arm_shoulder_pan.pos` 或 `left_arm_shoulder_pan.pos`；
- 通过 `XLerobotClient.send_action()` 发给树莓派 host。

这里不实现手臂运动学、轨迹规划、逆解或自定义联动算法。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any, Literal

os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")

import cv2
import numpy as np

import path_setup  # noqa: F401
from lerobot.cameras import CameraConfig
from lerobot.robots.config import RobotConfig
from lerobot.robots.robot import Robot
from lerobot.robots.xlerobot import XLerobotClient, XLerobotClientConfig
from lerobot.types import RobotAction, RobotObservation

from config import CAMERA_KEYS_BY_ROLE, ROBOT_ID, ROBOT_IP


ARM_JOINT_NAMES = (
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
    "gripper",
)


def _infer_arm_side(port: str) -> Literal["left", "right"]:
    text = str(port).lower()
    if "left" in text:
        return "left"
    return "right"


@RobotConfig.register_subclass("remote_xlerobot_single_arm")
@dataclass(kw_only=True)
class RemoteXLerobotSingleArmConfig(RobotConfig):
    """供官方 RobotClient 使用的远程单臂配置。"""

    port: str = "/dev/arm_right"
    cameras: dict[str, CameraConfig] = field(default_factory=dict)
    remote_ip: str = ROBOT_IP
    robot_id: str = ROBOT_ID
    arm_side: Literal["left", "right"] | None = None
    camera_key_map: dict[str, str] = field(default_factory=lambda: dict(CAMERA_KEYS_BY_ROLE))
    # VLA/LeRobot 数据集写图像时走 PIL/HF，期望输入是 RGB。
    # xlerobot_host 当前把 RGB 帧交给 OpenCV JPEG 编码，client 解码后的数组
    # 不再做额外交换，按 RGB 语义交给 LeRobot 写入后颜色才正确。
    swap_red_blue: bool = False
    polling_timeout_ms: int = 15
    connect_timeout_s: int = 5

    @property
    def type(self) -> str:
        return "remote_xlerobot_single_arm"

    @type.setter
    def type(self, _: str) -> None:
        # RoboCrew 官方 VLA 工具会给 robot_config.type 赋值为 so101_follower。
        # 这里保留远程 host 数据源类型，避免回到本机 /dev/arm_right。
        return


class RemoteXLerobotSingleArm(Robot):
    """LeRobot Robot 接口的 xlerobot_host 单臂适配器。"""

    config_class = RemoteXLerobotSingleArmConfig
    name = "remote_xlerobot_single_arm"

    def __init__(self, config: RemoteXLerobotSingleArmConfig):
        self.config = config
        self.robot_type = self.name
        self.id = config.id
        self.calibration = {}
        self.calibration_dir = Path(__file__).resolve().parent / ".cache" / "remote_xlerobot_single_arm"
        self.calibration_fpath = self.calibration_dir / f"{self.id or 'robot_arm'}.json"
        self.arm_side = config.arm_side or _infer_arm_side(config.port)
        self.remote_robot = XLerobotClient(
            XLerobotClientConfig(
                remote_ip=config.remote_ip,
                id=config.robot_id,
                polling_timeout_ms=config.polling_timeout_ms,
                connect_timeout_s=config.connect_timeout_s,
            )
        )

    @cached_property
    def observation_features(self) -> dict[str, type | tuple[int, int, int]]:
        motor_features = {f"{joint}.pos": float for joint in ARM_JOINT_NAMES}
        camera_features = {}
        for name, camera in self.config.cameras.items():
            height = getattr(camera, "height", 480) or 480
            width = getattr(camera, "width", 640) or 640
            camera_features[name] = (height, width, 3)
        return {**motor_features, **camera_features}

    @cached_property
    def action_features(self) -> dict[str, type]:
        return {f"{joint}.pos": float for joint in ARM_JOINT_NAMES}

    @property
    def is_connected(self) -> bool:
        return self.remote_robot.is_connected

    @property
    def is_calibrated(self) -> bool:
        return True

    def connect(self, calibrate: bool = True) -> None:
        if not self.remote_robot.is_connected:
            self.remote_robot.connect()

    def calibrate(self) -> None:
        return

    def configure(self) -> None:
        return

    def _remote_joint_key(self, joint: str) -> str:
        return f"{self.arm_side}_arm_{joint}.pos"

    def _host_camera_key(self, camera_name: str) -> str:
        return self.config.camera_key_map.get(camera_name, camera_name)

    def get_observation(self) -> RobotObservation:
        self.connect()
        remote_obs = self.remote_robot.get_observation()

        observation: dict[str, Any] = {}
        for joint in ARM_JOINT_NAMES:
            remote_key = self._remote_joint_key(joint)
            if remote_key in remote_obs:
                observation[f"{joint}.pos"] = float(remote_obs[remote_key])

        for camera_name in self.config.cameras:
            host_key = self._host_camera_key(camera_name)
            frame = remote_obs.get(host_key)
            if not isinstance(frame, np.ndarray):
                available = [key for key, value in remote_obs.items() if isinstance(value, np.ndarray)]
                raise RuntimeError(f"host observation 中没有摄像头 {host_key}，可用摄像头：{available}")
            if self.config.swap_red_blue:
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            observation[camera_name] = frame

        return observation

    def send_action(self, action: RobotAction) -> RobotAction:
        self.connect()
        remote_action = {}
        for joint in ARM_JOINT_NAMES:
            local_key = f"{joint}.pos"
            if local_key in action:
                remote_action[self._remote_joint_key(joint)] = float(action[local_key])

        if remote_action:
            self.remote_robot.send_action(remote_action)
        return action

    def disconnect(self) -> None:
        if self.remote_robot.is_connected:
            self.remote_robot.disconnect()
