"""XLeRobot LLM command client.

运行位置：Windows。

这是接入大模型前的安全入口层：

1. 读取一句自然语言命令。
2. 将命令转换成受限的结构化动作。
3. 默认 dry-run，只打印动作。
4. 显式加 `--execute` 才发送给树莓派 host。

安全原则：

LLM 不直接控制电机字段。它只能输出固定 command，
本脚本再把 command 映射成小幅、短时、安全动作。
"""

from __future__ import annotations

import argparse
import json
import math
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from lerobot.robots.xlerobot import XLerobotClient, XLerobotClientConfig


# =========================
# 用户常用配置：以后优先改这里
# =========================

DEFAULT_ROBOT_IP = "192.168.10.239"
DEFAULT_ROBOT_ID = "my_xlerobot_pc"

# rule：不用大模型，靠关键词解析，适合先确认机器人动作链路。
# openai-compatible：调用 Ollama / LM Studio / vLLM / OpenAI 兼容接口。
DEFAULT_PROVIDER = "rule"
DEFAULT_API_BASE = "http://localhost:11434/v1"
DEFAULT_API_KEY = "EMPTY"
DEFAULT_MODEL = "qwen2.5:7b"

# 安全动作默认值
DEFAULT_BASE_SPEED = 0.06
DEFAULT_BASE_SECONDS = 0.8
DEFAULT_TURN_SPEED_DEG_S = 18.0
DEFAULT_HEAD_STEP = 8.0
DEFAULT_HEAD_SETTLE_SECONDS = 0.35
DEFAULT_ARM_STEP = 5.0
DEFAULT_GRIPPER_STEP = 8.0
DEFAULT_ARM_SETTLE_SECONDS = 0.4
DEFAULT_ARM_SOFT_LIMIT = 120.0
DEFAULT_EE_STEP_M = 0.006
DEFAULT_EE_CONTROL_SECONDS = 0.9
DEFAULT_EE_CONTROL_HZ = 25.0
DEFAULT_EE_KP = 0.55

# 头部语义映射。
# 你现在观察到“抬头”会变成右转，说明 pitch/yaw 语义和 head_motor_1/2 对不上。
# 先在高层指令里修正语义，不急着动底层电机 ID。
HEAD_YAW_KEY = "head_motor_2.pos"
HEAD_PITCH_KEY = "head_motor_1.pos"

# 如果方向反了，只改这里的正负号。
HEAD_LEFT_SIGN = 1.0
HEAD_UP_SIGN = -1.0

# 手臂语义方向。
# 当前 shoulder_lift 初始值接近 -100，继续往负方向会顶到限位，所以默认“抬手”为正方向。
LEFT_ARM_UP_SIGN = 1.0
RIGHT_ARM_UP_SIGN = 1.0

# 末端控制语义方向。如果“举手”实际变成向下，优先改这里的正负号。
LEFT_EE_UP_SIGN = 1.0
RIGHT_EE_UP_SIGN = 1.0


SAFE_COMMANDS = {
    "status",
    "stop",
    "move_forward",
    "move_backward",
    "move_left",
    "move_right",
    "turn_left",
    "turn_right",
    "look_left",
    "look_right",
    "look_up",
    "look_down",
    "look_center",
    "left_arm_up",
    "left_arm_down",
    "right_arm_up",
    "right_arm_down",
    "left_gripper_open",
    "left_gripper_close",
    "right_gripper_open",
    "right_gripper_close",
}


@dataclass
class ParsedCommand:
    command: str
    seconds: float = DEFAULT_BASE_SECONDS
    speed: float = DEFAULT_BASE_SPEED
    head_step: float = DEFAULT_HEAD_STEP
    arm_step: float = DEFAULT_ARM_STEP
    gripper_step: float = DEFAULT_GRIPPER_STEP
    reason: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="XLeRobot LLM 高层指令 client。")
    parser.add_argument("--ip", default=DEFAULT_ROBOT_IP, help="树莓派 host IP")
    parser.add_argument("--id", default=DEFAULT_ROBOT_ID, help="机器人 ID")
    parser.add_argument("--execute", action="store_true", help="真正发送动作；默认只 dry-run")
    parser.add_argument("--provider", choices=["rule", "openai-compatible"], default=DEFAULT_PROVIDER, help="命令解析方式")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE, help="OpenAI 兼容接口地址，例如 http://localhost:11434/v1")
    parser.add_argument("--api-key", default=DEFAULT_API_KEY, help="OpenAI 兼容接口 key；本地服务可随便填")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="LLM 模型名")
    parser.add_argument("--command", default="", help="直接传入一句命令；不传则进入交互模式")
    return parser.parse_args()


def rule_parse(text: str) -> ParsedCommand:
    """不用模型的保底解析器，方便先测试控制链路。"""
    lower = text.lower().strip()

    if any(word in lower for word in ["状态", "观察", "看看", "status"]):
        return ParsedCommand("status", reason="用户想查看机器人状态")
    if any(word in lower for word in ["停", "停止", "stop", "别动"]):
        return ParsedCommand("stop", reason="用户要求停止")

    if any(word in lower for word in ["前进", "向前", "forward"]):
        return ParsedCommand("move_forward", reason="用户要求向前移动")
    if any(word in lower for word in ["后退", "向后", "往后", "倒退", "back"]):
        return ParsedCommand("move_backward", reason="用户要求向后移动")
    if any(word in lower for word in ["左移", "向左平移"]):
        return ParsedCommand("move_left", reason="用户要求向左平移")
    if any(word in lower for word in ["右移", "向右平移"]):
        return ParsedCommand("move_right", reason="用户要求向右平移")
    if any(word in lower for word in ["左转", "turn left"]):
        return ParsedCommand("turn_left", reason="用户要求左转")
    if any(word in lower for word in ["右转", "turn right"]):
        return ParsedCommand("turn_right", reason="用户要求右转")

    if any(word in lower for word in ["看左", "头左", "镜头左", "look left"]):
        return ParsedCommand("look_left", reason="用户要求头部向左看")
    if any(word in lower for word in ["看右", "头右", "镜头右", "look right"]):
        return ParsedCommand("look_right", reason="用户要求头部向右看")
    if any(word in lower for word in ["看上", "抬头", "look up"]):
        return ParsedCommand("look_up", reason="用户要求抬头")
    if any(word in lower for word in ["看下", "低头", "look down"]):
        return ParsedCommand("look_down", reason="用户要求低头")
    if any(word in lower for word in ["回中", "看中间", "镜头回中", "look center"]):
        return ParsedCommand("look_center", reason="用户要求头部回中")

    if any(word in lower for word in ["左手抬", "左臂抬", "左胳膊抬", "举左手"]):
        return ParsedCommand("left_arm_up", reason="用户要求左臂小幅抬起")
    if any(word in lower for word in ["左手放", "左臂放", "左胳膊放"]):
        return ParsedCommand("left_arm_down", reason="用户要求左臂小幅放下")
    if any(word in lower for word in ["右手抬", "右臂抬", "右胳膊抬", "举右手", "举手"]):
        return ParsedCommand("right_arm_up", reason="用户要求右臂小幅抬起")
    if any(word in lower for word in ["右手放", "右臂放", "右胳膊放"]):
        return ParsedCommand("right_arm_down", reason="用户要求右臂小幅放下")

    if any(word in lower for word in ["左夹爪打开", "左手张开", "左夹打开"]):
        return ParsedCommand("left_gripper_open", reason="用户要求左夹爪打开")
    if any(word in lower for word in ["左夹爪关闭", "左夹爪夹紧", "左手夹紧", "左夹紧"]):
        return ParsedCommand("left_gripper_close", reason="用户要求左夹爪夹紧")
    if any(word in lower for word in ["右夹爪打开", "右手张开", "右夹打开", "张开手"]):
        return ParsedCommand("right_gripper_open", reason="用户要求右夹爪打开")
    if any(word in lower for word in ["右夹爪关闭", "右夹爪夹紧", "右手夹紧", "右夹紧", "夹紧"]):
        return ParsedCommand("right_gripper_close", reason="用户要求右夹爪夹紧")

    return ParsedCommand("status", reason="没有识别到安全动作，改为查看状态")


def llm_parse_openai_compatible(text: str, api_base: str, api_key: str, model: str) -> ParsedCommand:
    """调用 OpenAI 兼容 chat/completions 接口，要求模型只返回 JSON。"""
    if not api_base:
        raise ValueError("--api-base is required for openai-compatible provider")

    schema_hint = {
        "command": sorted(SAFE_COMMANDS),
        "seconds": "0.1 到 2.0 之间，默认 0.5",
        "speed": "0.0 到 0.08 之间，默认 0.04",
        "head_step": "0.0 到 10.0 之间，默认 5.0",
        "arm_step": "0.0 到 8.0 之间，默认 5.0",
        "gripper_step": "0.0 到 12.0 之间，默认 8.0",
        "reason": "中文简短解释",
    }
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是机器人高层指令解析器。只返回 JSON，不要返回 Markdown。"
                    "只能选择允许的 command。不能发明电机字段，不能输出危险动作。"
                    f"字段约束：{json.dumps(schema_hint, ensure_ascii=False)}"
                ),
            },
            {"role": "user", "content": text},
        ],
        "temperature": 0,
    }

    url = api_base.rstrip("/") + "/chat/completions"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key or 'EMPTY'}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"LLM API request failed: {exc}") from exc

    content = data["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    return normalize_command(parsed)


def normalize_command(raw: dict[str, Any]) -> ParsedCommand:
    command = str(raw.get("command", "status"))
    if command not in SAFE_COMMANDS:
        command = "status"

    seconds = max(0.1, min(float(raw.get("seconds", DEFAULT_BASE_SECONDS)), 2.0))
    speed = max(0.0, min(float(raw.get("speed", DEFAULT_BASE_SPEED)), 0.08))
    head_step = max(0.0, min(float(raw.get("head_step", DEFAULT_HEAD_STEP)), 15.0))
    arm_step = max(0.0, min(float(raw.get("arm_step", DEFAULT_ARM_STEP)), 8.0))
    gripper_step = max(0.0, min(float(raw.get("gripper_step", DEFAULT_GRIPPER_STEP)), 12.0))
    reason = str(raw.get("reason", ""))
    return ParsedCommand(
        command=command,
        seconds=seconds,
        speed=speed,
        head_step=head_step,
        arm_step=arm_step,
        gripper_step=gripper_step,
        reason=reason,
    )


def parse_command(text: str, args: argparse.Namespace) -> ParsedCommand:
    if args.provider == "rule":
        return rule_parse(text)
    return llm_parse_openai_compatible(text, args.api_base, args.api_key, args.model)


def summarize_observation(obs: dict[str, Any]) -> None:
    keys = [
        "left_arm_shoulder_pan.pos",
        "left_arm_shoulder_lift.pos",
        "left_arm_elbow_flex.pos",
        "left_arm_wrist_flex.pos",
        "left_arm_gripper.pos",
        "right_arm_shoulder_pan.pos",
        "right_arm_shoulder_lift.pos",
        "right_arm_elbow_flex.pos",
        "right_arm_wrist_flex.pos",
        "right_arm_gripper.pos",
        "head_motor_1.pos",
        "head_motor_2.pos",
        "x.vel",
        "y.vel",
        "theta.vel",
    ]
    print("[OBS]")
    for key in keys:
        if key in obs:
            print(f"  {key}: {obs[key]}")
    cameras = [name for name, value in obs.items() if hasattr(value, "shape")]
    print(f"  cameras: {cameras}")


def stop(robot: XLerobotClient) -> None:
    robot.send_action({"x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0})


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def inverse_kinematics(x: float, y: float, l1: float = 0.1159, l2: float = 0.1350) -> tuple[float, float]:
    """官方 SO100 示例里的二维末端反解，返回 shoulder_lift / elbow_flex 角度。"""
    theta1_offset = math.atan2(0.028, 0.11257)
    theta2_offset = math.atan2(0.0052, 0.1349) + theta1_offset

    r = math.sqrt(x**2 + y**2)
    r_max = l1 + l2
    if r > r_max:
        scale_factor = r_max / r
        x *= scale_factor
        y *= scale_factor
        r = r_max

    r_min = abs(l1 - l2)
    if 0 < r < r_min:
        scale_factor = r_min / r
        x *= scale_factor
        y *= scale_factor
        r = r_min

    cos_theta2 = -(r**2 - l1**2 - l2**2) / (2 * l1 * l2)
    cos_theta2 = clamp(cos_theta2, -1.0, 1.0)
    theta2 = math.pi - math.acos(cos_theta2)

    beta = math.atan2(y, x)
    gamma = math.atan2(l2 * math.sin(theta2), l1 + l2 * math.cos(theta2))
    theta1 = beta + gamma

    joint2 = clamp(theta1 + theta1_offset, -0.1, 3.45)
    joint3 = clamp(theta2 + theta2_offset, -0.2, math.pi)

    shoulder_lift = 90.0 - math.degrees(joint2)
    elbow_flex = math.degrees(joint3) - 90.0
    return shoulder_lift, elbow_flex


def forward_kinematics(shoulder_lift: float, elbow_flex: float, l1: float = 0.1159, l2: float = 0.1350) -> tuple[float, float]:
    """和上面的官方 IK 对应的二维正解，用当前关节估计末端 x/y。"""
    theta1_offset = math.atan2(0.028, 0.11257)
    theta2_offset = math.atan2(0.0052, 0.1349) + theta1_offset

    theta1 = math.radians(90.0 - shoulder_lift) - theta1_offset
    theta2 = math.radians(elbow_flex + 90.0) - theta2_offset

    x = l1 * math.cos(theta1) + l2 * math.cos(theta1 - theta2)
    y = l1 * math.sin(theta1) + l2 * math.sin(theta1 - theta2)
    return x, y


def build_end_effector_action(obs: dict[str, Any], side: str, dx: float, dy: float) -> dict[str, float]:
    """将末端 x/y 小步移动转换成 shoulder_lift + elbow_flex + wrist_flex 联动动作。"""
    prefix = f"{side}_arm"
    shoulder_key = f"{prefix}_shoulder_lift.pos"
    elbow_key = f"{prefix}_elbow_flex.pos"
    wrist_key = f"{prefix}_wrist_flex.pos"

    shoulder = float(obs.get(shoulder_key, 0.0))
    elbow = float(obs.get(elbow_key, 0.0))
    wrist = float(obs.get(wrist_key, 0.0))

    current_x, current_y = forward_kinematics(shoulder, elbow)
    target_shoulder, target_elbow = inverse_kinematics(current_x + dx, current_y + dy)

    pitch = wrist + shoulder + elbow
    target_wrist = -target_shoulder - target_elbow + pitch

    return {
        shoulder_key: clamp(target_shoulder, -DEFAULT_ARM_SOFT_LIMIT, DEFAULT_ARM_SOFT_LIMIT),
        elbow_key: clamp(target_elbow, -DEFAULT_ARM_SOFT_LIMIT, DEFAULT_ARM_SOFT_LIMIT),
        wrist_key: clamp(target_wrist, -DEFAULT_ARM_SOFT_LIMIT, DEFAULT_ARM_SOFT_LIMIT),
    }


def print_target_delta(obs: dict[str, Any], target_action: dict[str, float]) -> None:
    for key, target in target_action.items():
        current = float(obs.get(key, 0.0))
        print(f"  {key}: current={current:.2f}, target={target:.2f}, delta={target - current:+.2f}")


def run_position_p_control(
    robot: XLerobotClient,
    target_action: dict[str, float],
    duration_s: float,
    control_hz: float,
    kp: float,
) -> None:
    """官方示例同款思路：在一小段时间内持续发送 P 控制位置命令。"""
    period_s = 1.0 / control_hz
    deadline = time.perf_counter() + duration_s

    while time.perf_counter() < deadline:
        obs = robot.get_observation()
        action = {}
        for key, target in target_action.items():
            current = float(obs.get(key, target))
            action[key] = current + kp * (target - current)
        robot.send_action(action)
        time.sleep(period_s)


def execute_command(robot: XLerobotClient, parsed: ParsedCommand) -> None:
    command = parsed.command

    if command == "status":
        summarize_observation(robot.get_observation())
        return

    if command == "stop":
        stop(robot)
        return

    if command.startswith("move_") or command.startswith("turn_"):
        action = {"x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0}
        if command == "move_forward":
            action["x.vel"] = parsed.speed
        elif command == "move_backward":
            action["x.vel"] = -parsed.speed
        elif command == "move_left":
            action["y.vel"] = parsed.speed
        elif command == "move_right":
            action["y.vel"] = -parsed.speed
        elif command == "turn_left":
            action["theta.vel"] = DEFAULT_TURN_SPEED_DEG_S
        elif command == "turn_right":
            action["theta.vel"] = -DEFAULT_TURN_SPEED_DEG_S

        print(f"[ACTION] {action} for {parsed.seconds:.2f}s")
        start = time.perf_counter()
        while time.perf_counter() - start < parsed.seconds:
            robot.send_action(action)
            time.sleep(0.05)
        stop(robot)
        return

    obs = robot.get_observation()

    ee_actions = {
        "left_arm_up": ("left", 0.0, LEFT_EE_UP_SIGN * DEFAULT_EE_STEP_M),
        "left_arm_down": ("left", 0.0, -LEFT_EE_UP_SIGN * DEFAULT_EE_STEP_M),
        "right_arm_up": ("right", 0.0, RIGHT_EE_UP_SIGN * DEFAULT_EE_STEP_M),
        "right_arm_down": ("right", 0.0, -RIGHT_EE_UP_SIGN * DEFAULT_EE_STEP_M),
    }

    if command in ee_actions:
        side, dx, dy = ee_actions[command]
        action = build_end_effector_action(obs, side, dx, dy)
        print(f"[ACTION][EE] {action}")
        print_target_delta(obs, action)
        run_position_p_control(
            robot,
            action,
            duration_s=DEFAULT_EE_CONTROL_SECONDS,
            control_hz=DEFAULT_EE_CONTROL_HZ,
            kp=DEFAULT_EE_KP,
        )
        summarize_observation(robot.get_observation())
        return

    arm_actions = {
        "left_gripper_open": ("left_arm_gripper.pos", parsed.gripper_step),
        "left_gripper_close": ("left_arm_gripper.pos", -parsed.gripper_step),
        "right_gripper_open": ("right_arm_gripper.pos", parsed.gripper_step),
        "right_gripper_close": ("right_arm_gripper.pos", -parsed.gripper_step),
    }

    if command in arm_actions:
        key, delta = arm_actions[command]
        current = float(obs.get(key, 0.0))
        if "shoulder_lift" in key:
            target = clamp(current + delta, -DEFAULT_ARM_SOFT_LIMIT, DEFAULT_ARM_SOFT_LIMIT)
        else:
            target = clamp(current + delta, 0.0, 100.0)
        action = {key: target}
        print(f"[ACTION] {action}")
        robot.send_action(action)
        time.sleep(DEFAULT_ARM_SETTLE_SECONDS)
        summarize_observation(robot.get_observation())
        return

    head_yaw = float(obs.get(HEAD_YAW_KEY, 0.0))
    head_pitch = float(obs.get(HEAD_PITCH_KEY, 0.0))
    step = parsed.head_step

    if command == "look_left":
        action = {HEAD_YAW_KEY: head_yaw + HEAD_LEFT_SIGN * step}
    elif command == "look_right":
        action = {HEAD_YAW_KEY: head_yaw - HEAD_LEFT_SIGN * step}
    elif command == "look_up":
        action = {HEAD_PITCH_KEY: head_pitch + HEAD_UP_SIGN * step}
    elif command == "look_down":
        action = {HEAD_PITCH_KEY: head_pitch - HEAD_UP_SIGN * step}
    elif command == "look_center":
        action = {"head_motor_1.pos": 0.0, "head_motor_2.pos": 0.0}
    else:
        action = {}

    if action:
        print(f"[ACTION] {action}")
        robot.send_action(action)
        time.sleep(DEFAULT_HEAD_SETTLE_SECONDS)
        summarize_observation(robot.get_observation())


def run_once(text: str, args: argparse.Namespace, robot: XLerobotClient | None = None) -> None:
    parsed = parse_command(text, args)
    print("[LLM-COMMAND]", parsed)

    if not args.execute:
        print("[DRY-RUN] 未加 --execute，不发送机器人动作。")
        return

    if robot is not None:
        execute_command(robot, parsed)
        return

    one_shot_robot = XLerobotClient(XLerobotClientConfig(remote_ip=args.ip, id=args.id))
    try:
        print(f"[MAIN] connecting to XLeRobot host at {args.ip} ...")
        one_shot_robot.connect()
        execute_command(one_shot_robot, parsed)
    finally:
        if one_shot_robot.is_connected:
            stop(one_shot_robot)
            one_shot_robot.disconnect()
            print("[MAIN] disconnected")


def main() -> None:
    args = parse_args()

    if args.command:
        run_once(args.command, args)
        return

    print("[MAIN] 进入交互模式。输入 quit 退出。默认 dry-run；加 --execute 才会动。")
    print(f"[CONFIG] provider={args.provider}, model={args.model}, api_base={args.api_base}, ip={args.ip}")

    if not args.execute:
        while True:
            text = input("你想让机器人做什么？> ").strip()
            if text.lower() in {"q", "quit", "exit"}:
                break
            if text:
                run_once(text, args)
        return

    robot = XLerobotClient(XLerobotClientConfig(remote_ip=args.ip, id=args.id))
    try:
        print(f"[MAIN] connecting to XLeRobot host at {args.ip} ...")
        robot.connect()
        summarize_observation(robot.get_observation())
        while True:
            text = input("你想让机器人做什么？> ").strip()
            if text.lower() in {"q", "quit", "exit"}:
                break
            if text:
                run_once(text, args, robot=robot)
    finally:
        if robot.is_connected:
            stop(robot)
            robot.disconnect()
            print("[MAIN] disconnected")


if __name__ == "__main__":
    main()
