"""用 XLerobotClient 适配器测试 RoboCrew 官方工具函数。"""

from __future__ import annotations

import argparse
import os

os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")

from client_servo_adapter import ClientServoControler
from config import ROBOT_ID, ROBOT_IP


def call_tool(tool_obj, **kwargs):
    """兼容不同版本 LangChain/RoboCrew 工具对象的调用方式。"""
    if hasattr(tool_obj, "invoke"):
        return tool_obj.invoke(kwargs)
    if hasattr(tool_obj, "run"):
        return tool_obj.run(kwargs)
    return tool_obj(**kwargs)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="通过 client 适配器测试 RoboCrew 官方 XLeRobot 工具。")
    parser.add_argument("--ip", default=ROBOT_IP)
    parser.add_argument("--id", default=ROBOT_ID)
    parser.add_argument("--move-forward", type=float, default=None, metavar="METERS")
    parser.add_argument("--move-backward", type=float, default=None, metavar="METERS")
    parser.add_argument("--turn-left", type=float, default=None, metavar="DEGREES")
    parser.add_argument("--turn-right", type=float, default=None, metavar="DEGREES")
    parser.add_argument("--strafe-left", type=float, default=None, metavar="METERS")
    parser.add_argument("--strafe-right", type=float, default=None, metavar="METERS")
    parser.add_argument("--precision", action="store_true", help="调用官方 create_go_to_precision_mode，测试头部 pitch 到近距离观察位")
    parser.add_argument("--normal", action="store_true", help="调用官方 create_go_to_normal_mode，测试头部 pitch 恢复普通观察位")
    return parser.parse_args()


def main() -> None:
    from robocrew.robots.XLeRobot.tools import (
        create_move_backward,
        create_move_forward,
        create_go_to_normal_mode,
        create_go_to_precision_mode,
        create_strafe_left,
        create_strafe_right,
        create_turn_left,
        create_turn_right,
    )

    args = parse_args()
    servo_controller = ClientServoControler(remote_ip=args.ip, robot_id=args.id)

    try:
        servo_controller.connect()

        if args.move_forward is not None:
            tool = create_move_forward(servo_controller)
            print(call_tool(tool, distance_meters=args.move_forward))
        elif args.move_backward is not None:
            tool = create_move_backward(servo_controller)
            print(call_tool(tool, distance_meters=args.move_backward))
        elif args.turn_left is not None:
            tool = create_turn_left(servo_controller)
            print(call_tool(tool, angle_degrees=args.turn_left))
        elif args.turn_right is not None:
            tool = create_turn_right(servo_controller)
            print(call_tool(tool, angle_degrees=args.turn_right))
        elif args.strafe_left is not None:
            tool = create_strafe_left(servo_controller)
            print(call_tool(tool, distance_meters=args.strafe_left))
        elif args.strafe_right is not None:
            tool = create_strafe_right(servo_controller)
            print(call_tool(tool, distance_meters=args.strafe_right))
        elif args.precision:
            tool = create_go_to_precision_mode(servo_controller)
            print(call_tool(tool))
        elif args.normal:
            tool = create_go_to_normal_mode(servo_controller)
            print(call_tool(tool))
        else:
            obs = servo_controller.get_observation()
            print("已连接。当前 observation 字段：")
            print(sorted(obs.keys()))
    finally:
        servo_controller.disconnect()


if __name__ == "__main__":
    main()
