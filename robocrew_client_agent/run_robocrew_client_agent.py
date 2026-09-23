"""使用 RoboCrew 官方工具函数和 client 适配器启动连续对话 Agent。"""

from __future__ import annotations

import argparse
import builtins
import importlib.util
import logging
import os
import re
import socket
import sys
import time
from contextlib import contextmanager
from pathlib import Path

os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")

import cv2
import numpy as np

from client_camera_adapter import ClientRobotCamera
from official_arm_pose_tools import create_saved_arm_pose_tools
from official_motion_tools import (
    create_recorded_motion_tools,
    list_recorded_motions,
    play_motion,
    record_motion,
)
from client_head_tools import create_head_tools
from client_servo_adapter import ClientServoControler
from config import (
    AGENT_SYSTEM_PROMPT,
    AUTO_RUN_UNTIL_FINISH,
    LLM_MODEL,
    MAIN_CAMERA_KEY,
    MAX_AGENT_STEPS_PER_TASK,
    MAX_TASK_SECONDS,
    OPENAI_COMPATIBLE_API_BASE,
    OPENAI_COMPATIBLE_API_KEY,
    ROBOT_ID,
    ROBOT_IP,
    CAMERA_SWAP_RED_BLUE,
)
from official_arm_tools import load_official_vla_tools
from task_router import _compact_task, route_task

ZMQ_CMD_PORT = 5555
ZMQ_OBSERVATION_PORT = 5556


def require_module(module_name: str) -> None:
    if importlib.util.find_spec(module_name) is None:
        raise RuntimeError(
            f"缺少依赖：{module_name}。"
            "请先在当前 Windows Python 环境中安装 RoboCrew。"
        )


def check_host_ports(remote_ip: str) -> bool:
    """启动前检查树莓派 host 的 ZMQ 端口是否已经监听。"""
    failed_ports: list[int] = []
    for port in (ZMQ_CMD_PORT, ZMQ_OBSERVATION_PORT):
        try:
            with socket.create_connection((remote_ip, port), timeout=1.5):
                pass
        except OSError:
            failed_ports.append(port)

    if not failed_ports:
        return True

    print(f"[连接检查] 能 ping 到 {remote_ip}，但端口 {failed_ports} 没有监听。")
    print("[连接检查] 请先在树莓派启动 host：")
    print("  cd ~/xlerobot-dev/lerobot")
    print("  source .venv/bin/activate")
    print("  python -m lerobot.robots.xlerobot.xlerobot_host")
    print("[连接检查] 如果 host 已经启动，请看树莓派终端是否卡在校准输入、摄像头报错或已经退出。")
    return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="启动 RoboCrew 官方工具的 XLeRobot client Agent。")
    parser.add_argument("--task", default="", help="启动后先执行的一条明确任务")
    parser.add_argument("--once", action="store_true", help="只执行一条任务后退出")
    parser.add_argument("--camera-key", default=MAIN_CAMERA_KEY, help="本次运行使用的 observation 摄像头字段，例如 head、camera_0、camera_2")
    parser.add_argument("--save-camera-previews", action="store_true", help="连接 host 后保存所有摄像头预览图，然后退出")
    parser.add_argument("--show-raw-output", action="store_true", help="显示 RoboCrew/模型原始输出，包括可能很长的图像 base64")
    return parser.parse_args()


def configure_runtime_output() -> None:
    """让 Windows 终端稳定显示中文，并压低第三方库的超长调试日志。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    for logger_name in ("openai", "httpx", "httpcore", "langchain_openai"):
        logging.getLogger(logger_name).setLevel(logging.WARNING)


def is_noisy_output(text: str) -> bool:
    if len(text) > 900:
        return True
    if "data:image" in text or "base64" in text:
        return True
    compact = text.replace("/", "").replace("+", "").replace("=", "").replace("\n", "")
    return len(compact) > 300 and compact.isalnum()


class FilteredTextStream:
    """过滤第三方库直接写到 stdout/stderr 的超长图像内容。"""

    def __init__(self, wrapped):
        self.wrapped = wrapped
        self._buffer = ""

    def write(self, text: str) -> int:
        self._buffer += str(text)
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._write_line(line + "\n")
        if len(self._buffer) > 900:
            self._write_line(self._buffer)
            self._buffer = ""
        return len(text)

    def flush(self) -> None:
        if self._buffer:
            self._write_line(self._buffer)
            self._buffer = ""
        self.wrapped.flush()

    def _write_line(self, text: str) -> None:
        if is_noisy_output(text):
            self.wrapped.write("[已隐藏一段很长的模型/图像调试输出，避免污染终端]\n")
            return
        try:
            self.wrapped.write(text)
        except UnicodeEncodeError:
            encoding = getattr(self.wrapped, "encoding", None) or "utf-8"
            safe_text = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
            self.wrapped.write(safe_text)

    def __getattr__(self, name: str):
        return getattr(self.wrapped, name)


@contextmanager
def filtered_print(enabled: bool):
    if not enabled:
        yield
        return

    original_print = builtins.print
    original_stdout = sys.stdout
    original_stderr = sys.stderr

    def clean_print(*args, **kwargs):
        text = " ".join(str(arg) for arg in args)
        if is_noisy_output(text):
            original_print("[已隐藏一段很长的模型/图像调试输出，避免污染终端]")
            return
        original_print(*args, **kwargs)

    builtins.print = clean_print
    sys.stdout = FilteredTextStream(original_stdout)
    sys.stderr = FilteredTextStream(original_stderr)
    try:
        yield
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        builtins.print = original_print
        sys.stdout = original_stdout
        sys.stderr = original_stderr


def build_agent(servo_controller: ClientServoControler, main_camera: ClientRobotCamera):
    from robocrew.core.tools import finish_task
    from robocrew.robots.XLeRobot.tools import (
        create_go_to_normal_mode,
        create_go_to_precision_mode,
        create_look_around,
        create_move_backward,
        create_move_forward,
        create_strafe_left,
        create_strafe_right,
        create_turn_left,
        create_turn_right,
    )
    from robocrew.robots.XLeRobot.xlerobot_LLM_agent import XLeRobotAgent

    recorded_motion_tools = create_recorded_motion_tools(servo_controller)
    vla_tools = load_official_vla_tools(servo_controller, main_camera)

    tools = [
        *recorded_motion_tools,
        create_move_forward(servo_controller),
        create_move_backward(servo_controller),
        create_strafe_left(servo_controller),
        create_strafe_right(servo_controller),
        create_turn_left(servo_controller),
        create_turn_right(servo_controller),
        create_go_to_precision_mode(servo_controller),
        create_go_to_normal_mode(servo_controller),
        create_look_around(servo_controller, main_camera),
        *create_head_tools(servo_controller),
        *create_saved_arm_pose_tools(servo_controller),
        *vla_tools,
        finish_task,
    ]

    agent = XLeRobotAgent(
        model=LLM_MODEL,
        tools=tools,
        main_camera=main_camera,
        servo_controler=servo_controller,
        system_prompt=AGENT_SYSTEM_PROMPT,
        history_len=8,
    )
    agent._recorded_motion_tool_names = {tool.name for tool in recorded_motion_tools}
    install_task_complete_guard(agent)
    return agent


def latest_ai_message(agent):
    """取最近一条模型回复，用于判断是否还需要自动进入下一轮。"""
    for message in reversed(agent.message_history):
        if getattr(message, "type", None) == "ai":
            return message
    return None


def latest_tool_names(agent) -> list[str]:
    """读取最近一条模型回复里的工具名，用于防止同一工具空转。"""
    message = latest_ai_message(agent)
    tool_calls = getattr(message, "tool_calls", None) if message is not None else None
    if not tool_calls:
        return []
    names = []
    for call in tool_calls:
        if isinstance(call, dict):
            name = call.get("name") or call.get("function", {}).get("name")
        else:
            name = getattr(call, "name", None)
        if name:
            names.append(str(name))
    return names


def latest_ai_text(agent, start_index: int = 0) -> str:
    """读取本轮最近一条模型自然语言内容。"""
    for message in reversed(agent.message_history[start_index:]):
        if getattr(message, "type", None) != "ai":
            continue
        content = getattr(message, "content", "")
        if isinstance(content, str) and content.strip():
            return content.strip()
    return ""


def latest_task_complete_message(agent, start_index: int = 0) -> str | None:
    """读取最近的工具完成信号。

    本地动作/头部工具会返回 TASK_COMPLETE，表示工具本身已经完成用户请求。
    RoboCrew 官方 LLMAgent 只在 finish_task 时停止；这里把完成信号收束成结果，
    避免模型在动作已经完成后继续调用移动、观察等无关工具。
    """
    for message in reversed(agent.message_history[start_index:]):
        if getattr(message, "type", None) != "tool":
            continue
        content = getattr(message, "content", None)
        if not isinstance(content, str):
            continue
        if not content.startswith("TASK_COMPLETE:"):
            continue
        return content.split("TASK_COMPLETE:", 1)[1].strip()
    return None


def install_task_complete_guard(agent) -> None:
    """安装工具执行保护，防止同一轮完成后继续执行后续工具。"""
    if getattr(agent, "_task_complete_guard_installed", False):
        return

    from langchain_core.messages import ToolMessage

    original_invoke_tool = agent.invoke_tool

    def guarded_invoke_tool(tool_call):
        if getattr(agent, "_task_complete_seen", False):
            return (
                ToolMessage(
                    content="Skipped: task already completed by a previous tool.",
                    tool_call_id=tool_call["id"],
                ),
                None,
            )

        tool_response, additional_response = original_invoke_tool(tool_call)
        content = getattr(tool_response, "content", None)
        if isinstance(content, str) and content.startswith("TASK_COMPLETE:"):
            agent._task_complete_seen = True
            agent._task_complete_message = content.split("TASK_COMPLETE:", 1)[1].strip()
        return tool_response, additional_response

    agent.invoke_tool = guarded_invoke_tool
    agent._task_complete_guard_installed = True


def format_task_complete_result(agent, complete_message: str, start_index: int = 0) -> str:
    """组合模型当轮说明和工具执行结果，给终端更完整的反馈。"""
    ai_text = latest_ai_text(agent, start_index)
    if ai_text:
        return f"{ai_text}\n\n执行结果：{complete_message}"
    return complete_message


def strip_task_complete_prefix(text: str) -> str:
    if text.startswith("TASK_COMPLETE:"):
        return text.split("TASK_COMPLETE:", 1)[1].strip()
    return text


def natural_motion_reply(motion_name: str, result: str) -> str:
    """把动作播放结果转成适合终端和语音的自然反馈。"""
    if "已终止" in result or "中止" in result:
        return f"已停止“{motion_name}”动作，回到待命。"
    if "未找到" in result or "失败" in result or "为空" in result or "不正确" in result:
        return result
    sent = re.search(r"发送帧数=(\d+)", result)
    if "已播放" in result or "TASK_COMPLETE" in result:
        extra = f"，共 {sent.group(1)} 帧" if sent else ""
        return f"好的，已执行“{motion_name}”{extra}。"
    return result or f"没有播放“{motion_name}”。"


def natural_record_reply(motion_name: str, result: str) -> str:
    if "没有录到" in result or "失败" in result or "只能是" in result:
        return result
    frames = re.search(r"帧数=(\d+)", result)
    arm = re.search(r"arm_side=(\w+)", result)
    if "已保存" in result or "TASK_COMPLETE" in result:
        parts = [f"好的，已录好“{motion_name}”"]
        if arm:
            arm_label = {"both": "双臂", "left": "左臂", "right": "右臂"}.get(arm.group(1), arm.group(1))
            parts.append(arm_label)
        if frames:
            parts.append(f"共 {frames.group(1)} 帧")
        return "，".join(parts) + "。之后你可以直接让我执行这个动作。"
    return result or f"没有录下“{motion_name}”。"


CAPABILITY_REPLY = (
    "我是双智臂。我能走、能转头、能做双臂动作，也能看摄像头、录动作和播放动作。"
    "你直接说指令，比如向前走一点、抬头、列出动作。"
)
GREET_REPLY = "我在，请说指令。"
CHAT_FALLBACK_REPLY = "我在。请说具体指令，比如向前走一点、抬头，或问我会做什么。"
_CAPABILITY_KEYS = ("做什么", "干什么", "会什么", "能做啥", "有什么功能", "你会啥", "介绍一下", "你是谁", "你叫什么")
_GREETINGS = {"你好", "您好", "嗨", "哈喽", "在吗", "hello", "hi", "你好呀", "您好呀"}
_COMMAND_HINTS = ("走", "进", "退", "转", "抬头", "低头", "播放", "录制", "左移", "右移", "急停")


def _chat_reply(task: str) -> str:
    """常见寒暄和问能力本地秒回，不再等大模型。"""
    text = _compact_task(task)
    if any(key in text for key in _CAPABILITY_KEYS):
        return CAPABILITY_REPLY
    if text.casefold() in _GREETINGS or text in _GREETINGS:
        return GREET_REPLY
    if (text.startswith("你好") or text.startswith("您好")) and not any(hint in text for hint in _COMMAND_HINTS):
        return GREET_REPLY
    return CHAT_FALLBACK_REPLY


def _clarify_reply(task: str) -> str:
    """没听懂时，让大模型生成一句澄清，生成不稳定时退回固定话术。"""
    from langchain.chat_models import init_chat_model

    llm = init_chat_model(LLM_MODEL, temperature=0.3, max_tokens=100)
    prompt = (
        f"用户对机器人说：「{task}」。"
        "请用一句完整、自然、友好的中文，告诉用户这句话没听懂，请TA再清楚地说一遍。"
    )
    try:
        response = llm.invoke([{"role": "user", "content": prompt}])
        text = str(getattr(response, "content", "")).strip()
    except Exception:
        text = ""
    # 模型生成可能返回空，或被截断成半句话（以“但/可是/不过”结尾），此时退回固定澄清。
    if len(text) < 6 or text.endswith(("但", "可是", "不过", "只是")):
        return f"我没听懂「{task}」，请再清楚地说一遍。"
    return text


def _base_was_stopped(raw: str | None) -> bool:
    text = str(raw or "")
    return "已终止" in text or "未执行" in text or "中止" in text


def execute_routed_task(
    route: dict,
    servo_controller: ClientServoControler,
    task: str,
    should_stop=None,
) -> str | None:
    """执行路由器已经明确选定的非官方导航任务。

    返回 None 表示继续交给 RoboCrew 官方 Agent。
    """
    mode = route.get("mode")
    reason = str(route.get("reason") or "").strip()

    print(f"[路由] mode={mode}" + (f"，理由：{reason}" if reason else ""))

    if mode == "unknown":
        return _clarify_reply(task)

    if mode == "navigation_agent":
        return None

    if should_stop is not None and should_stop():
        return "已停止，没有继续执行。"
    if getattr(servo_controller, "abort_requested", False):
        return "已停止，没有继续执行。"

    if mode == "base_sequence":
        actions = route.get("actions") or []
        replies = []
        for item in actions:
            if should_stop is not None and should_stop():
                return "已停止，没有走完。"
            if getattr(servo_controller, "abort_requested", False):
                return "已停止，没有走完。"
            action = str(item.get("action") or "").strip()
            value = float(item.get("value") or 0.0)
            raw = ""
            if action == "forward":
                raw = servo_controller.go_forward(value)
                replies.append(f"前进 {value:.2f} 米")
            elif action == "backward":
                raw = servo_controller.go_backward(value)
                replies.append(f"后退 {value:.2f} 米")
            elif action == "strafe_left":
                raw = servo_controller.strafe_left(value)
                replies.append(f"左移 {value:.2f} 米")
            elif action == "strafe_right":
                raw = servo_controller.strafe_right(value)
                replies.append(f"右移 {value:.2f} 米")
            elif action == "turn_left":
                raw = servo_controller.turn_left(value)
                replies.append(f"左转 {value:.1f} 度")
            elif action == "turn_right":
                raw = servo_controller.turn_right(value)
                replies.append(f"右转 {value:.1f} 度")
            if _base_was_stopped(raw):
                return "已停止，没有走完。"
        if replies:
            return "好的，已执行：" + "，".join(replies) + "。"
        return "没有解析到可执行的底盘动作。"

    if mode == "base_control":
        action = str(route.get("base_action") or "").strip()
        value = float(route.get("value") or 0.05)
        if action == "forward":
            raw = servo_controller.go_forward(value)
            return "已停止，没有走完。" if _base_was_stopped(raw) else f"好的，已向前走 {value:.2f} 米。"
        if action == "backward":
            raw = servo_controller.go_backward(value)
            return "已停止，没有走完。" if _base_was_stopped(raw) else f"好的，已向后退 {value:.2f} 米。"
        if action == "strafe_left":
            raw = servo_controller.strafe_left(value)
            return "已停止，没有走完。" if _base_was_stopped(raw) else f"好的，已向左平移 {value:.2f} 米。"
        if action == "strafe_right":
            raw = servo_controller.strafe_right(value)
            return "已停止，没有走完。" if _base_was_stopped(raw) else f"好的，已向右平移 {value:.2f} 米。"
        if action == "turn_left":
            raw = servo_controller.turn_left(value)
            return "已停止，没有转完。" if _base_was_stopped(raw) else f"好的，已向左转 {value:.1f} 度。"
        if action == "turn_right":
            raw = servo_controller.turn_right(value)
            return "已停止，没有转完。" if _base_was_stopped(raw) else f"好的，已向右转 {value:.1f} 度。"
        return f"未知底盘动作：{action}"

    if mode == "head_control":
        action = str(route.get("head_action") or "").strip()
        angle = float(route.get("angle_degrees") or 20.0)
        if action == "look_up":
            servo_controller.turn_head_pitch(-abs(angle))
            return f"头部已向上抬 {abs(angle):.1f} 度。"
        if action == "look_down":
            servo_controller.turn_head_pitch(abs(angle))
            return f"头部已向下低 {abs(angle):.1f} 度。"
        if action == "turn_head_left":
            servo_controller.turn_head_yaw(-abs(angle))
            return f"头部已向左转 {abs(angle):.1f} 度。"
        if action == "turn_head_right":
            servo_controller.turn_head_yaw(abs(angle))
            return f"头部已向右转 {abs(angle):.1f} 度。"
        if action == "center_head":
            servo_controller.reset_head_position()
            return "头部已回到普通观察位置。"
        return f"未知头部动作：{action}"

    if mode == "recorded_motion":
        motion_name = str(route.get("motion_name") or "").strip()
        if not motion_name:
            return "我还没确定要执行哪个动作。你可以先让我列出已经录好的动作。"
        print(f"[路由] 播放动作：{motion_name}")
        result = strip_task_complete_prefix(play_motion(servo_controller, motion_name, should_stop=should_stop))
        return natural_motion_reply(motion_name, result)

    if mode == "record_motion":
        motion_name = str(route.get("motion_name") or "").strip() or "未命名动作"
        arm_side = str(route.get("arm_side") or "right").strip()
        try:
            seconds = float(route.get("seconds") or 5.0)
        except (TypeError, ValueError):
            seconds = 5.0
        print(f"[路由] 录制动作：{motion_name}，arm_side={arm_side}，seconds={seconds:.1f}")
        result = strip_task_complete_prefix(
            record_motion(servo_controller, motion_name, arm_side, seconds, should_stop=should_stop)
        )
        return natural_record_reply(motion_name, result)

    if mode == "list_motions":
        return list_recorded_motions()

    if mode == "keyboard_control":
        return "请用网页键盘按住操作：方向键走底盘，左臂 QWERTY，右臂 UIOP。按钮仍可微调。输入框打字不会抢键。"

    if mode == "chat":
        return _chat_reply(task)

    return None


def refresh_recorded_motion_tools(agent, servo_controller: ClientServoControler) -> None:
    """刷新录制动作工具，让新录制动作不重启也能被模型看到。"""
    old_names = set(getattr(agent, "_recorded_motion_tool_names", set()))
    if old_names:
        agent.tools = [tool for tool in agent.tools if getattr(tool, "name", None) not in old_names]

    new_tools = create_recorded_motion_tools(servo_controller)
    agent.tools = [*new_tools, *agent.tools]
    agent.tool_name_to_tool = {tool.name: tool for tool in agent.tools}
    agent._recorded_motion_tool_names = {tool.name for tool in new_tools}

    base_llm = getattr(agent.llm, "bound", agent.llm)
    if hasattr(base_llm, "bind_tools"):
        agent.llm = base_llm.bind_tools(agent.tools)


def reset_agent_task_context(agent) -> None:
    """每条用户输入使用干净任务上下文，避免上一轮工具结果污染下一轮。"""
    system_message = getattr(agent, "system_message", None)
    if system_message is not None:
        agent.message_history = [system_message]


def save_camera_previews(servo_controller: ClientServoControler) -> None:
    """保存所有 observation 图像，方便确认真实物理摄像头和 key 的对应关系。"""
    preview_dir = Path(__file__).resolve().parent / "camera_previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    obs = servo_controller.get_observation()
    saved = []
    for key, value in obs.items():
        if not isinstance(value, np.ndarray) or len(value.shape) != 3:
            continue
        frame = value
        if CAMERA_SWAP_RED_BLUE:
            frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        out_path = preview_dir / f"{key}.jpg"
        cv2.imwrite(str(out_path), frame)
        saved.append((key, tuple(value.shape), out_path))

    if not saved:
        print("[摄像头诊断] observation 里没有找到图像字段。")
        print(f"[摄像头诊断] 当前 keys: {list(obs.keys())}")
        return

    print("[摄像头诊断] 已保存以下预览图：")
    for key, shape, path in saved:
        print(f"  {key}: shape={shape} -> {path}")
    print("[摄像头诊断] 打开这些图片，确认真正头部摄像头对应哪个 key。")


def run_one_task(
    agent,
    servo_controller: ClientServoControler,
    main_camera: ClientRobotCamera,
    task: str,
    show_raw_output: bool,
    should_stop=None,
) -> str:
    task_t0 = time.perf_counter()
    servo_controller.reset_task_budget()
    refresh_recorded_motion_tools(agent, servo_controller)
    reset_agent_task_context(agent)
    agent._task_complete_seen = False
    agent._task_complete_message = None
    agent.task = task

    print(f"[任务] {agent.task}")
    print(f"[模型] {LLM_MODEL}")

    route_t0 = time.perf_counter()
    try:
        route = route_task(task)
    except Exception as exc:
        route = {"mode": "navigation_agent", "reason": f"路由失败，交给官方 Agent：{exc}"}
    print(f"[耗时] 路由 {time.perf_counter() - route_t0:.2f}s")

    if should_stop is not None and should_stop():
        agent.task = None
        return "本次任务已终止，回到待命。"

    routed_t0 = time.perf_counter()
    routed_result = execute_routed_task(route, servo_controller, task, should_stop=should_stop)
    if routed_result is not None:
        refresh_recorded_motion_tools(agent, servo_controller)
        print(f"[耗时] 路由执行 {time.perf_counter() - routed_t0:.2f}s，总计 {time.perf_counter() - task_t0:.2f}s")
        print(f"[结果] {routed_result}")
        agent.task = None
        return routed_result

    print(f"[摄像头] observation['{main_camera.camera_key}']")
    camera_t0 = time.perf_counter()
    try:
        main_camera.capture_image()
        print(f"[摄像头] 已在调用模型前获取图像，shape={main_camera.last_shape}")
        preview_path = Path(__file__).resolve().parent / "last_camera_preview.jpg"
        if main_camera.save_last_image(preview_path):
            print(f"[摄像头] 预览图已保存：{preview_path}")
    except RuntimeError as exc:
        print(f"[摄像头] 获取失败：{exc}")
    print(f"[耗时] 摄像头 {time.perf_counter() - camera_t0:.2f}s")

    result = None
    deadline = time.perf_counter() + MAX_TASK_SECONDS
    repeated_tool_name = None
    repeated_tool_count = 0
    with filtered_print(enabled=not show_raw_output):
        for step in range(1, MAX_AGENT_STEPS_PER_TASK + 1):
            if should_stop is not None and should_stop():
                result = "本次任务已终止，回到待命。"
                agent.task = None
                break
            if time.perf_counter() >= deadline:
                print("[Agent] 达到本任务最大执行时长，停止继续执行。")
                break
            history_start = len(agent.message_history)
            step_t0 = time.perf_counter()
            result = agent.main_loop_content()
            print(f"[耗时] Agent 第 {step} 轮 {time.perf_counter() - step_t0:.2f}s")
            if should_stop is not None and should_stop():
                result = "本次任务已终止，回到待命。"
                agent.task = None
                break
            if result:
                break
            complete_message = latest_task_complete_message(agent, history_start)
            if complete_message:
                refresh_recorded_motion_tools(agent, servo_controller)
                result = format_task_complete_result(agent, complete_message, history_start)
                agent.task = None
                break
            if agent.task is None:
                break
            last_ai = latest_ai_message(agent)
            if last_ai is not None and not getattr(last_ai, "tool_calls", None):
                result = latest_ai_text(agent, history_start) or latest_ai_text(agent) or "我已经看完了。"
                agent.task = None
                break
            tool_names = latest_tool_names(agent)
            if len(tool_names) == 1:
                if tool_names[0] == repeated_tool_name:
                    repeated_tool_count += 1
                else:
                    repeated_tool_name = tool_names[0]
                    repeated_tool_count = 1
                if repeated_tool_count >= 3:
                    result = f"模型连续调用 `{repeated_tool_name}`，已停止本轮以避免重复空转。"
                    agent.task = None
                    break
            if not AUTO_RUN_UNTIL_FINISH:
                print("[Agent] 当前配置为单轮执行，已停止继续执行。")
                break
            print(f"[Agent] 第 {step} 轮未结束，继续下一轮观察/执行。")

    if result:
        print(f"[结果] {result}")
        result_text = str(result)
    else:
        print("[结果] 达到本任务最大轮数，已停止继续执行。")
        result_text = "达到本任务最大轮数，已停止继续执行。"

    agent.task = None
    print(f"[耗时] 任务总计 {time.perf_counter() - task_t0:.2f}s")
    return result_text


def main() -> None:
    configure_runtime_output()
    args = parse_args()
    require_module("robocrew")

    os.environ.setdefault("OPENAI_API_KEY", OPENAI_COMPATIBLE_API_KEY)
    os.environ.setdefault("OPENAI_API_BASE", OPENAI_COMPATIBLE_API_BASE)
    os.environ.setdefault("OPENAI_BASE_URL", OPENAI_COMPATIBLE_API_BASE)

    if not check_host_ports(ROBOT_IP):
        return

    servo_controller = ClientServoControler(remote_ip=ROBOT_IP, robot_id=ROBOT_ID)
    servo_controller.connect()
    if args.save_camera_previews:
        try:
            save_camera_previews(servo_controller)
        finally:
            servo_controller.disconnect()
        return

    main_camera = ClientRobotCamera(servo_controller=servo_controller, camera_key=args.camera_key)
    agent = build_agent(servo_controller, main_camera)

    try:
        if args.task:
            run_one_task(agent, servo_controller, main_camera, args.task.strip(), args.show_raw_output)
            if args.once:
                return

        print("进入连续对话模式。输入 quit 退出。")
        while True:
            task = input("任务> ").strip()
            if task.lower() in {"q", "quit", "exit"}:
                break
            if not task:
                continue
            run_one_task(agent, servo_controller, main_camera, task, args.show_raw_output)
    finally:
        servo_controller.disconnect()


if __name__ == "__main__":
    main()
