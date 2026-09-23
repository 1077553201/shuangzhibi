"""命令行播放 XLeRobot 手臂动作序列。

底层只使用官方 host-client send_action，不做 IK、不做轨迹规划。
"""

from __future__ import annotations

import argparse
import os

os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")

import path_setup  # noqa: F401
from client_servo_adapter import ClientServoControler
from config import ROBOT_ID, ROBOT_IP
from official_motion_tools import play_motion


def main() -> None:
    parser = argparse.ArgumentParser(description="播放 XLeRobot 手臂动作序列。")
    parser.add_argument("motion_name", help="动作名，例如 wave_hello 或 try_pick_coke")
    parser.add_argument("--speed", type=float, default=1.0, help="播放速度，0.2 到 3.0")
    parser.add_argument("--ip", default=ROBOT_IP, help="树莓派 host IP")
    parser.add_argument("--id", default=ROBOT_ID, help="机器人 ID")
    args = parser.parse_args()

    servo = ClientServoControler(remote_ip=args.ip, robot_id=args.id)
    try:
        servo.connect()
        print(play_motion(servo, args.motion_name, args.speed))
    finally:
        servo.disconnect()


if __name__ == "__main__":
    main()
