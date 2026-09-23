"""基于 RoboCrew 官方 ServoControler 预留接口的头部工具。

RoboCrew 官方 `tools.py` 目前只提供 `look_around`、`go_to_precision_mode`
和 `go_to_normal_mode`，没有任意 yaw/pitch 的 create_* 工具。

这里不写运动学算法，只把官方约定的 `turn_head_yaw()` 和 `turn_head_pitch()`
包装成 LangChain 工具，方便 LLM 调用头部 7/8 号电机。
"""

from __future__ import annotations

from langchain_core.tools import tool

from client_servo_adapter import ClientServoControler


def create_turn_head_left(servo_controller: ClientServoControler):
    @tool
    def turn_head_left(angle_degrees: float = 20.0) -> str:
        """头部向左转指定角度，只控制 yaw，不移动底盘。成功返回 TASK_COMPLETE 后必须调用 finish_task。"""
        angle = abs(float(angle_degrees))
        servo_controller.turn_head_yaw(-angle)
        return f"TASK_COMPLETE: 头部已向左转 {angle:.1f} 度。下一步只能调用 finish_task 总结结果。"

    return turn_head_left


def create_turn_head_right(servo_controller: ClientServoControler):
    @tool
    def turn_head_right(angle_degrees: float = 20.0) -> str:
        """头部向右转指定角度，只控制 yaw，不移动底盘。成功返回 TASK_COMPLETE 后必须调用 finish_task。"""
        angle = abs(float(angle_degrees))
        servo_controller.turn_head_yaw(angle)
        return f"TASK_COMPLETE: 头部已向右转 {angle:.1f} 度。下一步只能调用 finish_task 总结结果。"

    return turn_head_right


def create_look_up(servo_controller: ClientServoControler):
    @tool
    def look_up(angle_degrees: float = 20.0) -> str:
        """头部向上抬指定角度，只控制 pitch，不移动底盘。成功返回 TASK_COMPLETE 后必须调用 finish_task。"""
        angle = abs(float(angle_degrees))
        servo_controller.turn_head_pitch(-angle)
        return f"TASK_COMPLETE: 头部已向上抬 {angle:.1f} 度。下一步只能调用 finish_task 总结结果。"

    return look_up


def create_look_down(servo_controller: ClientServoControler):
    @tool
    def look_down(angle_degrees: float = 20.0) -> str:
        """头部向下低指定角度，只控制 pitch，不移动底盘。成功返回 TASK_COMPLETE 后必须调用 finish_task。"""
        angle = abs(float(angle_degrees))
        servo_controller.turn_head_pitch(angle)
        return f"TASK_COMPLETE: 头部已向下低 {angle:.1f} 度。下一步只能调用 finish_task 总结结果。"

    return look_down


def create_center_head(servo_controller: ClientServoControler):
    @tool
    def center_head() -> str:
        """头部回到官方普通观察位置。成功返回 TASK_COMPLETE 后必须调用 finish_task。"""
        servo_controller.reset_head_position()
        return "TASK_COMPLETE: 头部已回到官方普通观察位置。下一步只能调用 finish_task 总结结果。"

    return center_head


def create_head_tools(servo_controller: ClientServoControler):
    return [
        create_turn_head_left(servo_controller),
        create_turn_head_right(servo_controller),
        create_look_up(servo_controller),
        create_look_down(servo_controller),
        create_center_head(servo_controller),
    ]
