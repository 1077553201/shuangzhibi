"""XLeRobot client 最小控制示例。

运行位置：Windows。

默认只做 smoke test：连接树莓派 host，读取一帧 observation，然后发送停止命令。
如果显式传入 --move-base，才会让底盘低速前进一小段时间，然后立刻停止。
"""

from __future__ import annotations

import argparse
import time
from typing import Any

from lerobot.robots.xlerobot import XLerobotClient, XLerobotClientConfig


def build_client(ip: str, robot_id: str) -> XLerobotClient:
    """创建官方 XLeRobot client 对象。"""

    config = XLerobotClientConfig(remote_ip=ip, id=robot_id)
    return XLerobotClient(config)


def print_observation_summary(observation: dict[str, Any]) -> None:
    """打印 observation 中最重要的状态字段，避免输出太长。"""

    important_keys = [
        "left_arm_shoulder_pan.pos",
        "left_arm_shoulder_lift.pos",
        "left_arm_elbow_flex.pos",
        "right_arm_shoulder_pan.pos",
        "right_arm_shoulder_lift.pos",
        "right_arm_elbow_flex.pos",
        "head_motor_1.pos",
        "head_motor_2.pos",
        "x.vel",
        "y.vel",
        "theta.vel",
    ]

    print("[OBS] observation summary:")
    for key in important_keys:
        if key in observation:
            print(f"  {key}: {observation[key]}")


def stop_base(robot: XLerobotClient) -> None:
    """发送零速度，停止底盘。"""

    robot.send_action({"x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0})


def move_base_forward_briefly(robot: XLerobotClient, duration_s: float, speed: float) -> None:
    """让底盘低速前进一小段时间，然后停止。"""

    print(f"[MOVE] moving base forward: speed={speed}, duration={duration_s}s")
    start = time.perf_counter()
    while time.perf_counter() - start < duration_s:
        robot.send_action({"x.vel": speed, "y.vel": 0.0, "theta.vel": 0.0})
        time.sleep(0.05)
    stop_base(robot)
    print("[MOVE] base stopped")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Minimal XLeRobot host/client control example.")
    parser.add_argument("--ip", default="192.168.10.239", help="树莓派 host 的 IP 地址")
    parser.add_argument("--id", default="my_xlerobot_pc", help="机器人 ID，要和 host 校准 ID 对应")
    parser.add_argument("--move-base", action="store_true", help="显式启用底盘低速前进测试")
    parser.add_argument("--duration", type=float, default=0.5, help="底盘动作持续时间，单位秒")
    parser.add_argument("--speed", type=float, default=0.05, help="底盘前进速度，单位 m/s")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    robot = build_client(ip=args.ip, robot_id=args.id)

    try:
        print(f"[MAIN] connecting to XLeRobot host at {args.ip} ...")
        robot.connect()
        print("[MAIN] connected")

        observation = robot.get_observation()
        print_observation_summary(observation)

        stop_base(robot)
        print("[MAIN] sent stop command")

        if args.move_base:
            move_base_forward_briefly(robot, duration_s=args.duration, speed=args.speed)
        else:
            print("[MAIN] smoke test only. Add --move-base to run a small base movement.")

    finally:
        if robot.is_connected:
            stop_base(robot)
            robot.disconnect()
            print("[MAIN] disconnected")


if __name__ == "__main__":
    main()
