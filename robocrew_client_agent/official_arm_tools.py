"""加载 RoboCrew 官方 VLA 手臂工具。

这里不实现任何手臂运动学、轨迹规划或关节控制逻辑。
本文件只复用 RoboCrew 官方 UI 的配置格式，把启用的 VLA 工具交给
`robocrew.robots.XLeRobot.tools.create_vla_single_arm_manipulation` 创建。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from config import ENABLE_OFFICIAL_VLA_TOOLS, OFFICIAL_VLA_TOOLS_FILE
from remote_xlerobot_single_arm import RemoteXLerobotSingleArmConfig


REQUIRED_VLA_FIELDS = {
    "tool_name",
    "tool_description",
    "task_prompt",
    "server_address",
    "policy_name",
    "policy_type",
    "policy_device",
    "arm_port",
    "execution_time",
}


def official_vla_camera_config() -> dict[str, dict[str, Any]]:
    """保持 RoboCrew 官方 UI 的默认相机配置。

    官方 agent_setup.py 里写死的是 `/dev/camera_center` 和 `/dev/camera_right`。
    这里不把它改成自定义图像算法；如果要跑官方 VLA 工具，需要在运行该工具
    的机器上准备相同的设备别名，或者后续按官方接口扩展 camera_config。
    """
    return {
        "main": {"index_or_path": "/dev/camera_center"},
        "right_arm": {"index_or_path": "/dev/camera_right"},
    }


def load_official_vla_tools(servo_controller, main_camera_object) -> list[Any]:
    """读取官方 VLA 配置并返回 LangChain tools。"""
    if not ENABLE_OFFICIAL_VLA_TOOLS:
        return []

    config_path = Path(OFFICIAL_VLA_TOOLS_FILE)
    if not config_path.exists():
        print(f"[手臂] 未发现官方 VLA 工具配置：{config_path}")
        print("[手臂] 当前不会注册手臂操作工具，避免模型误触发未配置的手臂动作。")
        return []

    import robocrew.robots.XLeRobot.tools as xlerobot_tools

    # 官方 create_vla_single_arm_manipulation 内部默认构造 SOFollowerConfig，
    # 也就是从本机 /dev/arm_right 读取手臂和相机。这里只把数据源配置替换成
    # xlerobot_host 远程单臂 Robot，后续仍由官方 create_vla + RobotClient 执行。
    xlerobot_tools.SOFollowerConfig = RemoteXLerobotSingleArmConfig

    with config_path.open("r", encoding="utf-8") as f:
        raw_tools = json.load(f)

    if isinstance(raw_tools, dict):
        raw_tools = [raw_tools]
    elif not isinstance(raw_tools, list):
        print(f"[手臂] {config_path} 格式不正确：顶层应该是对象或列表。")
        return []

    tools = []
    for index, item in enumerate(raw_tools):
        if not isinstance(item, dict):
            print(f"[手臂] 跳过第 {index + 1} 个 VLA 工具：配置项不是对象。")
            continue

        if not item.get("active", True):
            continue

        missing = sorted(REQUIRED_VLA_FIELDS.difference(item))
        if missing:
            print(f"[手臂] 跳过第 {index + 1} 个 VLA 工具，缺少字段：{missing}")
            continue

        tools.append(
            xlerobot_tools.create_vla_single_arm_manipulation(
                tool_name=item["tool_name"],
                tool_description=item["tool_description"],
                task_prompt=item["task_prompt"],
                server_address=item["server_address"],
                policy_name=item["policy_name"],
                policy_type=item["policy_type"],
                arm_port=item["arm_port"],
                servo_controler=servo_controller,
                camera_config=official_vla_camera_config(),
                main_camera_object=main_camera_object,
                policy_device=item["policy_device"],
                execution_time=int(item["execution_time"]),
                load_on_startup=False,
            )
        )

    if tools:
        tool_names = ", ".join(getattr(tool, "name", "<未命名工具>") for tool in tools)
        print(f"[手臂] 已加载官方 VLA 工具：{tool_names}")
    else:
        print(f"[手臂] {config_path} 中没有启用的官方 VLA 工具。")

    return tools
