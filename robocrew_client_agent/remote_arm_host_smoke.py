"""只读测试：确认官方 VLA 单臂数据源来自树莓派 xlerobot_host。"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")

import cv2
from lerobot.cameras.opencv.configuration_opencv import OpenCVCameraConfig

from remote_xlerobot_single_arm import RemoteXLerobotSingleArm, RemoteXLerobotSingleArmConfig


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="读取 xlerobot_host 的单臂 VLA observation。")
    parser.add_argument("--arm", choices=["right", "left"], default="right", help="读取右臂或左臂")
    parser.add_argument("--camera", default="right_arm", help="VLA 逻辑摄像头名，例如 main、right_arm、left_arm；填 none 只读关节")
    parser.add_argument("--save-image", action="store_true", help="保存读取到的摄像头画面")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    camera_config = {}
    if args.camera.lower() != "none":
        camera_config = {
            args.camera: OpenCVCameraConfig(index_or_path=0, width=640, height=480, fps=30)
        }
    robot = RemoteXLerobotSingleArm(
        RemoteXLerobotSingleArmConfig(
            port=f"/dev/arm_{args.arm}",
            cameras=camera_config,
        )
    )

    robot.connect()
    try:
        obs = robot.get_observation()
    finally:
        robot.disconnect()

    print(f"[远程单臂] arm={args.arm}")
    for key, value in obs.items():
        if hasattr(value, "shape"):
            print(f"  {key}: image shape={tuple(value.shape)}")
            if args.save_image:
                out_path = Path(__file__).resolve().parent / f"remote_{args.arm}_{key}.jpg"
                cv2.imwrite(str(out_path), cv2.cvtColor(value, cv2.COLOR_RGB2BGR))
                print(f"    saved: {out_path}")
        else:
            print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
