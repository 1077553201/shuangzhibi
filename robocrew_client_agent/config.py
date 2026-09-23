"""XLeRobot 的 RoboCrew client 模式配置。"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path


ROBOT_IP = "192.168.10.240"
ROBOT_ID = "my_xlerobot_pc"

# 换臂后网页核对（2026-09-08 晚）：左臂正确；中间与右臂对调。
# camera_0 = 左臂摄像头
# head     = 中间（头部）摄像头
# camera_2 = 右臂摄像头
MAIN_CAMERA_KEY = "head"
RIGHT_ARM_CAMERA_KEY = "camera_2"
LEFT_ARM_CAMERA_KEY = "camera_0"
CAMERA_KEYS_BY_ROLE = {
    "main": MAIN_CAMERA_KEY,
    "head": MAIN_CAMERA_KEY,
    "right_arm": RIGHT_ARM_CAMERA_KEY,
    "left_arm": LEFT_ARM_CAMERA_KEY,
}
# 网页预览从左到右：左臂 | 中间 | 右臂
CAMERA_STREAMS = (
    (LEFT_ARM_CAMERA_KEY, "左臂摄像头"),
    (MAIN_CAMERA_KEY, "中间摄像头"),
    (RIGHT_ARM_CAMERA_KEY, "右臂摄像头"),
)

# xlerobot_host 通过 OpenCV 编码 JPEG；如果上游帧是 RGB，会出现红蓝互换。
# 当前树莓派 host 返回的图像需要在 client 侧交换 R/B 才能恢复真实颜色。
CAMERA_SWAP_RED_BLUE = True

# RoboCrew 官方 UI 使用同一个文件保存 VLA 手臂工具配置。
# 本 client Agent 只读取这个配置，并调用官方 create_vla_single_arm_manipulation。
OFFICIAL_VLA_TOOLS_FILE = Path.home() / ".cache" / "robocrew" / "tools" / "vla_tools.json"
ENABLE_OFFICIAL_VLA_TOOLS = True

# 大模型配置。
# RoboCrew 内部使用 LangChain 的 init_chat_model(model=LLM_MODEL)。
# 如果使用 OpenAI 兼容接口，模型名前面保留 "openai:"，并在下面填写接口地址和 key。
# 注意：如果希望 Agent 真正理解摄像头画面，这里必须使用支持图像输入的模型。
LLM_MODEL = "openai:mimo-v2.5"
OPENAI_COMPATIBLE_API_BASE = "https://token-plan-sgp.xiaomimimo.com/v1"
OPENAI_COMPATIBLE_API_KEY = os.environ.get("OPENAI_COMPATIBLE_API_KEY", "")


def load_official_xlerobot_prompt() -> str | None:
    """读取 RoboCrew 官方 XLeRobot controller prompt。"""
    spec = importlib.util.find_spec("robocrew")
    if spec is None or spec.submodule_search_locations is None:
        return None
    robocrew_dir = Path(next(iter(spec.submodule_search_locations)))
    prompt_path = robocrew_dir / "robots" / "XLeRobot" / "xlerobot.prompt"
    if not prompt_path.exists():
        return None
    return prompt_path.read_text(encoding="utf-8").strip()


OFFICIAL_XLEROBOT_PROMPT = load_official_xlerobot_prompt()
AGENT_SYSTEM_PROMPT = (
    OFFICIAL_XLEROBOT_PROMPT
    + """

## LANGUAGE
- Reply to the user in Chinese.

## LOCAL TOOL RULES
- Use tool names and tool descriptions to decide what to do. Do not rely on hard-coded user phrase mappings.
- Recorded motions and local controls are exposed as tools with descriptions, following the same pattern as official RoboCrew tools.
- Do not invent joint values or private movement algorithms.
"""
    if OFFICIAL_XLEROBOT_PROMPT
    else None
)

# 单次 Agent 任务默认最多允许执行几次底盘动作。
# 这是 client 侧安全预算，用来防止模型在同一个任务里重复调用移动工具。
MAX_BASE_ACTIONS_PER_TASK = 6

# 自动执行任务，直到模型调用 finish_task。
# 仍然保留最大轮数和最大时长，避免模型忘记结束导致无限循环。
AUTO_RUN_UNTIL_FINISH = True
MAX_AGENT_STEPS_PER_TASK = 20
MAX_TASK_SECONDS = 60.0

BASE_SPEED = 0.06
BASE_SECONDS = 0.8
BASE_COMMAND_INTERVAL = 0.05
TURN_SPEED_DEG_S = 18.0

HEAD_STEP = 8.0
HEAD_SETTLE_SECONDS = 0.35
# 换臂后网页核对：抬头走了头左、低头走了头右 → pitch/yaw 电机对调，不是方向取反。
# head_motor_1 = yaw，head_motor_2 = pitch。
HEAD_YAW_KEY = "head_motor_1.pos"
HEAD_PITCH_KEY = "head_motor_2.pos"
HEAD_LEFT_SIGN = 1.0
HEAD_UP_SIGN = 1.0
