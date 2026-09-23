"""RoboCrew 主摄像头的 client 适配器。

RoboCrew 的 LLMAgent 主循环会调用：

    main_camera.capture_image(...)
    main_camera.reopen()

官方 `RobotCamera` 默认读取本机 `/dev/video*`。在我们的 client 模式里，
摄像头接在树莓派上，因此这里从 `XLerobotClient.get_observation()` 中取图像，
再编码成 RoboCrew 期望的 JPEG bytes。
"""

from __future__ import annotations

import cv2
import numpy as np
from pathlib import Path
from robocrew.core.utils import basic_augmentation

from client_servo_adapter import ClientServoControler
from config import CAMERA_SWAP_RED_BLUE


class ClientRobotCamera:
    """从树莓派 host 的 observation 中读取摄像头画面。"""

    def __init__(self, servo_controller: ClientServoControler, camera_key: str = "head") -> None:
        self.servo_controller = servo_controller
        self.camera_key = camera_key
        self.last_shape: tuple[int, ...] | None = None
        self.last_image_bytes: bytes | None = None

    def capture_image(
        self,
        camera_fov: int = 120,
        center_angle: int = 0,
        navigation_mode: str = "normal",
        **_: object,
    ) -> bytes:
        obs = self.servo_controller.get_observation()
        if self.camera_key not in obs:
            available = [name for name, value in obs.items() if hasattr(value, "shape")]
            raise RuntimeError(f"没有找到摄像头字段 {self.camera_key}，可用摄像头：{available}")

        frame = obs[self.camera_key]
        if not isinstance(frame, np.ndarray):
            raise RuntimeError(f"摄像头字段 {self.camera_key} 不是图像数组：{type(frame)}")
        if CAMERA_SWAP_RED_BLUE:
            frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        frame = basic_augmentation(
            frame.copy(),
            h_fov=camera_fov,
            center_angle=center_angle,
            navigation_mode=navigation_mode,
        )
        self.last_shape = tuple(frame.shape)
        ok, encoded = cv2.imencode(".jpg", frame)
        if not ok:
            raise RuntimeError(f"摄像头字段 {self.camera_key} 编码 JPEG 失败")
        self.last_image_bytes = encoded.tobytes()
        return self.last_image_bytes

    def save_last_image(self, path: str | Path) -> bool:
        if self.last_image_bytes is None:
            return False
        Path(path).write_bytes(self.last_image_bytes)
        return True

    def reopen(self) -> None:
        """兼容 RoboCrew 的 RobotCamera 接口；client 模式不需要重新打开本机相机。"""

    def release(self) -> None:
        """兼容 VLA 工具释放相机的接口；client 模式下暂不执行本机相机释放。"""
