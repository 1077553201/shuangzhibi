"""RoboCrew 官方保存姿态工具。

这些工具只调用 `ClientServoControler.set_saved_position()`，对应官方
`ServoControler.set_saved_position()` 的 client 适配实现。
不在这里写手臂轨迹、逆解或联动算法。
"""

from __future__ import annotations

from pathlib import Path

from langchain_core.tools import tool

from client_servo_adapter import ClientServoControler, DEFAULT_ARM_POSITION_DIR


def create_list_saved_arm_positions():
    @tool
    def list_saved_arm_positions() -> str:
        """列出 RoboCrew 官方保存的手臂姿态文件。"""
        position_dir = Path(DEFAULT_ARM_POSITION_DIR).expanduser()
        if not position_dir.exists():
            try:
                position_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                return f"官方姿态目录不存在，且自动创建失败：{position_dir}；错误：{exc}"
            return f"官方姿态目录已创建但当前为空：{position_dir}"
        files = sorted(path.stem for path in position_dir.glob("*.json"))
        if not files:
            return f"官方姿态目录为空：{position_dir}"
        return "可用官方手臂姿态：" + ", ".join(files)

    return list_saved_arm_positions


def create_go_to_saved_arm_position(servo_controller: ClientServoControler):
    @tool
    def go_to_saved_arm_position(position_name: str, arm_side: str = "both") -> str:
        """执行 RoboCrew 官方保存姿态。arm_side 可选 left、right、both。"""
        return servo_controller.set_saved_position(position_name, arm_side)

    return go_to_saved_arm_position


def create_save_current_arm_position(servo_controller: ClientServoControler):
    @tool
    def save_current_arm_position(position_name: str, arm_side: str = "both") -> str:
        """保存当前手臂姿态到 RoboCrew 官方姿态目录。arm_side 可选 left、right、both。"""
        return servo_controller.save_current_arm_position(position_name, arm_side)

    return save_current_arm_position


def create_saved_arm_pose_tools(servo_controller: ClientServoControler):
    return [
        create_list_saved_arm_positions(),
        create_save_current_arm_position(servo_controller),
        create_go_to_saved_arm_position(servo_controller),
    ]
