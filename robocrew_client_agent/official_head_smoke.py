"""用 RoboCrew 官方头部接口名测试 client 适配器。

这个脚本不实现新的头部运动算法，只直接调用官方 ServoControler 约定的方法：
`turn_head_yaw()` 和 `turn_head_pitch()`。它用于确认 client 映射是否和真实电机一致。
"""

from __future__ import annotations

import argparse
import os

os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")

from client_servo_adapter import ClientServoControler
from config import HEAD_PITCH_KEY, HEAD_YAW_KEY, ROBOT_ID, ROBOT_IP


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="测试 XLeRobot 头部 yaw/pitch 映射。")
    parser.add_argument("--yaw", type=float, default=None, help="调用 turn_head_yaw 的角度，例如 30 或 -30")
    parser.add_argument("--pitch", type=float, default=None, help="调用 turn_head_pitch 的角度，例如 20")
    parser.add_argument("--center", action="store_true", help="把 yaw 和 pitch 都回到 0")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    servo = ClientServoControler(remote_ip=ROBOT_IP, robot_id=ROBOT_ID)
    print(f"[配置] yaw -> {HEAD_YAW_KEY}, pitch -> {HEAD_PITCH_KEY}")
    servo.connect()
    try:
        if args.center:
            print("[动作] yaw=0, pitch=0")
            servo.turn_head_yaw(0)
            servo.turn_head_pitch(0)
        if args.yaw is not None:
            print(f"[动作] turn_head_yaw({args.yaw})")
            servo.turn_head_yaw(args.yaw)
        if args.pitch is not None:
            print(f"[动作] turn_head_pitch({args.pitch})")
            servo.turn_head_pitch(args.pitch)
        if not args.center and args.yaw is None and args.pitch is None:
            print("请传入 --yaw、--pitch 或 --center。")
    finally:
        servo.disconnect()


if __name__ == "__main__":
    main()
