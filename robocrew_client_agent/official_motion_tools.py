"""RoboCrew/XLeRobot 示教动作序列工具。

这些工具不做 IK、不做轨迹规划、不训练模型。
录制时只读取官方 host-client observation；播放时只通过官方
XLerobotClient.send_action() 回放当时记录的关节目标。
"""

from __future__ import annotations

import json
import re
import hashlib
import time
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from client_servo_adapter import ARM_JOINT_NAMES, ClientServoControler


DEFAULT_MOTION_DIR = Path.home() / ".cache" / "robocrew" / "motions"


def _safe_name(name: str) -> str:
    safe = name.strip().replace("\\", "_").replace("/", "_")
    return safe or "motion"


def _motion_file(motion_name: str) -> Path:
    name = _safe_name(motion_name)
    file_name = name if name.endswith(".json") else f"{name}.json"
    return DEFAULT_MOTION_DIR / file_name


def _tool_safe_suffix(name: str) -> str:
    suffix = re.sub(r"[^a-zA-Z0-9_]+", "_", name).strip("_").lower()
    if not suffix:
        digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
        suffix = f"motion_{digest}"
    if suffix[0].isdigit():
        suffix = f"motion_{suffix}"
    return suffix[:48]


def _iter_motion_files() -> list[Path]:
    if not DEFAULT_MOTION_DIR.exists():
        return []
    return sorted(DEFAULT_MOTION_DIR.glob("*.json"))


def _motion_description(path: Path) -> str:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return f"播放录制好的手臂动作序列。动作名：{path.stem}。"

    if not isinstance(data, dict):
        return f"播放录制好的手臂动作序列。动作名：{path.stem}。"

    motion_name = str(data.get("name") or path.stem)
    arm_side = str(data.get("arm_side") or "unknown")
    duration_s = data.get("duration_s")
    frame_count = len(data.get("frames", [])) if isinstance(data.get("frames"), list) else 0
    extra_description = str(data.get("description") or "").strip()

    parts = [
        "播放一个已经录制好的手臂动作序列。",
        "当用户要求执行、播放、做出这个录制动作，或直接说出这个动作名时，使用此工具。",
        "这是回放示教动作，不需要先移动底盘、环顾四周或切换视觉抓取模式。",
        f"动作名：{motion_name}。",
        f"手臂：{arm_side}。",
    ]
    if duration_s is not None:
        parts.append(f"时长约 {float(duration_s):.1f} 秒。")
    if frame_count:
        parts.append(f"帧数：{frame_count}。")
    if extra_description:
        parts.append(f"动作说明：{extra_description}。")
    return "".join(parts)


def _arm_action_from_observation(obs: dict[str, Any], arm_side: str) -> dict[str, float]:
    sides = ("left", "right") if arm_side == "both" else (arm_side,)
    action: dict[str, float] = {}
    for side in sides:
        prefix = f"{side}_arm"
        for joint in ARM_JOINT_NAMES:
            key = f"{prefix}_{joint}.pos"
            if key in obs:
                action[key] = float(obs[key])
    return action


def _load_motion(motion_name: str) -> tuple[Path, dict[str, Any] | None, str | None]:
    path = _motion_file(motion_name)
    if not path.exists():
        return path, None, f"未找到动作序列：{path}"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return path, None, f"动作序列 JSON 解析失败：{path}；错误：{exc}"
    if not isinstance(data, dict) or not isinstance(data.get("frames"), list):
        return path, None, f"动作序列格式不正确：{path}"
    return path, data, None


def delete_motion(motion_name: str) -> str:
    """删除一条本地录制的动作序列文件。"""
    name = _safe_name(motion_name)
    if not name or name in {".", ".."}:
        return "动作名无效。"
    path = _motion_file(name)
    try:
        path.resolve().relative_to(DEFAULT_MOTION_DIR.resolve())
    except ValueError:
        return "动作名无效。"
    if not path.exists() or not path.is_file():
        return f"未找到动作序列：{name}"
    try:
        path.unlink()
    except OSError as exc:
        return f"删除失败：{name}；错误：{exc}"
    return f"已删除动作序列：{name}"


def list_recorded_motions() -> str:
    if not DEFAULT_MOTION_DIR.exists():
        try:
            DEFAULT_MOTION_DIR.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return f"动作序列目录不存在，且自动创建失败：{DEFAULT_MOTION_DIR}；错误：{exc}"
        return f"动作序列目录已创建但当前为空：{DEFAULT_MOTION_DIR}"

    files = [path.stem for path in _iter_motion_files()]
    if not files:
        return f"动作序列目录为空：{DEFAULT_MOTION_DIR}"
    return "可用动作序列：" + ", ".join(files)


def recorded_motion_catalog() -> list[dict[str, Any]]:
    """读取已录制动作目录，给任务路由器使用。"""
    catalog: list[dict[str, Any]] = []
    for path in _iter_motion_files():
        item: dict[str, Any] = {
            "name": path.stem,
            "file": str(path),
        }
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        if isinstance(data, dict):
            item["arm_side"] = data.get("arm_side")
            item["duration_s"] = data.get("duration_s")
            frames = data.get("frames")
            if isinstance(frames, list):
                item["frame_count"] = len(frames)
            description = data.get("description")
            if description:
                item["description"] = description
        catalog.append(item)
    return catalog


def record_motion(
    servo_controller: ClientServoControler,
    motion_name: str,
    arm_side: str = "right",
    seconds: float = 5.0,
    fps: float = 20.0,
    should_stop=None,
) -> str:
    if arm_side not in {"left", "right", "both"}:
        return "arm_side 只能是 left、right 或 both。"

    duration_s = max(0.5, min(float(seconds), 30.0))
    sample_fps = max(1.0, min(float(fps), 30.0))
    period_s = 1.0 / sample_fps
    frames: list[dict[str, Any]] = []

    print(f"[动作录制] {motion_name}: arm_side={arm_side}, seconds={duration_s:.1f}, fps={sample_fps:.1f}")
    print("[动作录制] 正在释放手臂扭矩。")

    try:
        servo_controller.release_arm_for_recording(arm_side)
        print("[动作录制] 现在开始移动手臂。")

        start_t = time.perf_counter()
        next_t = start_t
        while True:
            if should_stop is not None and should_stop():
                break
            now = time.perf_counter()
            if now - start_t >= duration_s:
                break
            if now < next_t:
                time.sleep(min(next_t - now, 0.01))
                continue

            obs = servo_controller.get_observation()
            action = _arm_action_from_observation(obs, arm_side)
            if action:
                frames.append(
                    {
                        "time": now - start_t,
                        "action": action,
                    }
                )
            next_t += period_s
    finally:
        print("[动作录制] 正在恢复手臂扭矩。")
        servo_controller.restore_arm_after_recording(arm_side)

    if not frames:
        return f"没有录到 {arm_side} 手臂关节数据，请确认 host observation 正常。"

    path = _motion_file(motion_name)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return f"创建动作序列目录失败：{path.parent}；错误：{exc}"

    payload = {
        "name": _safe_name(motion_name),
        "format": "xlerobot_host_client_motion_v1",
        "arm_side": arm_side,
        "fps": sample_fps,
        "duration_s": duration_s,
        "frames": frames,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return (
        f"TASK_COMPLETE: 已保存动作序列：{path}，帧数={len(frames)}，arm_side={arm_side}。"
        "本次录制请求已经完成；下一步只能调用 finish_task 总结结果，不能再次录制或播放。"
    )


def play_motion(
    servo_controller: ClientServoControler,
    motion_name: str,
    speed: float = 1.0,
    should_stop=None,
) -> str:
    path, data, error = _load_motion(motion_name)
    if error:
        return error
    assert data is not None

    frames = data["frames"]
    if not frames:
        return f"动作序列为空：{path}"

    playback_speed = max(0.2, min(float(speed), 3.0))
    start_t = time.perf_counter()
    first_time = float(frames[0].get("time", 0.0))
    sent = 0

    servo_controller.connect()
    for frame in frames:
        if should_stop is not None and should_stop():
            try:
                servo_controller.robot.send_action({"x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0})
            except Exception:
                pass
            return (
                f"TASK_COMPLETE: 已终止动作序列播放：{path.name}，已发送帧数={sent}。"
                "本次播放已中止并回到待命。"
            )
        target_t = (float(frame.get("time", 0.0)) - first_time) / playback_speed
        while time.perf_counter() - start_t < target_t:
            if should_stop is not None and should_stop():
                try:
                    servo_controller.robot.send_action({"x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0})
                except Exception:
                    pass
                return (
                    f"TASK_COMPLETE: 已终止动作序列播放：{path.name}，已发送帧数={sent}。"
                    "本次播放已中止并回到待命。"
                )
            time.sleep(0.005)
        action = frame.get("action", {})
        if isinstance(action, dict) and action:
            servo_controller.robot.send_action({str(key): float(value) for key, value in action.items()})
            sent += 1

    return (
        f"TASK_COMPLETE: 已播放动作序列：{path.name}，发送帧数={sent}。"
        "本次播放请求已经完成；下一步只能调用 finish_task 总结结果，不能再次播放同一个动作。"
    )


def create_list_recorded_motions():
    @tool
    def list_recorded_motions_tool() -> str:
        """列出已经录制好的手臂动作序列。"""
        return list_recorded_motions()

    list_recorded_motions_tool.name = "list_recorded_motions"
    return list_recorded_motions_tool


def create_record_motion(servo_controller: ClientServoControler):
    @tool
    def record_motion_tool(motion_name: str, arm_side: str = "right", seconds: float = 5.0, fps: float = 20.0) -> str:
        """录制当前手臂动作序列。arm_side 可选 left、right、both。成功返回 TASK_COMPLETE 后必须调用 finish_task，不要重复录制。"""
        return record_motion(servo_controller, motion_name, arm_side, seconds, fps)

    record_motion_tool.name = "record_motion"
    return record_motion_tool


def create_play_recorded_motion(servo_controller: ClientServoControler):
    @tool
    def play_recorded_motion_tool(motion_name: str, speed: float = 1.0) -> str:
        """按动作名播放已经录制好的手臂动作序列。适合用户说“播放某动作”“执行某动作”或直接说出已录制动作名。一次用户请求只播放一次。"""
        return play_motion(servo_controller, motion_name, speed)

    play_recorded_motion_tool.name = "play_recorded_motion"
    return play_recorded_motion_tool


def create_specific_recorded_motion_tool(servo_controller: ClientServoControler, path: Path):
    motion_name = path.stem

    @tool
    def play_this_recorded_motion(speed: float = 1.0) -> str:
        """播放一个已经录制好的手臂动作序列。"""
        return play_motion(servo_controller, motion_name, speed)

    play_this_recorded_motion.name = f"play_motion_{_tool_safe_suffix(motion_name)}"
    play_this_recorded_motion.description = _motion_description(path)
    return play_this_recorded_motion


def create_recorded_motion_tools(servo_controller: ClientServoControler):
    tools = [
        create_list_recorded_motions(),
        create_record_motion(servo_controller),
        create_play_recorded_motion(servo_controller),
    ]
    tools.extend(create_specific_recorded_motion_tool(servo_controller, path) for path in _iter_motion_files())
    return tools
