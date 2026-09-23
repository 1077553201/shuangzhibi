"""任务路由层。

这里不控制机器人，只让大模型判断用户这句话应该交给哪一类执行器。
这样可以保留官方 RoboCrew Agent 的视觉/导航路线，同时避免已录制动作
和导航工具混在一个工具池里互相干扰。
"""

from __future__ import annotations

import re
from typing import Any, Literal

from langchain.chat_models import init_chat_model
from pydantic import BaseModel, Field

from config import LLM_MODEL
from official_motion_tools import recorded_motion_catalog


ROUTER_MODES = {
    "base_control",
    "head_control",
    "recorded_motion",
    "record_motion",
    "list_motions",
    "navigation_agent",
    "keyboard_control",
    "chat",
}

# 大模型路由输出的最低自信度。低于此值视为“没听懂”，不执行，改为提示用户重说。
MIN_ROUTE_CONFIDENCE = 0.6

_ROUTE_CACHE: dict[tuple[str, str], dict[str, Any]] = {}


class RouteResult(BaseModel):
    """大模型路由的结构化输出，通过 function calling 让模型只填这张表。

    大模型只做两类判断：导航/观察/复合动作，还是闲聊。
    底盘、头部、动作播放、动作录制、动作列表等明确指令都由本地快路径处理，
    不交给大模型，避免模型对含糊输入乱猜动作名、误执行动作。
    """

    mode: Literal["navigation_agent", "chat"] = Field(
        description=(
            "执行路线。navigation_agent=需要移动、导航、观察或执行复合动作的任务；"
            "chat=闲聊、问能力、寒暄，或无法理解的话"
        )
    )
    confidence: float = Field(
        description="对用户意图的理解把握，必须是0到1之间的小数，0表示完全不确定，1表示完全确定；无法理解时填0.3以下"
    )
    reason: str = Field(description="简短理由")


_ASR_JUNK_TAILS = ("秘密", "字幕", "谢谢观看", "谢谢收看", "请订阅", "订阅", "点赞", "加油")


def _compact_task(task: str) -> str:
    return re.sub(r"[\s，。！？!?,；;：:、]+", "", task.strip())


def _spoken_task(task: str) -> str:
    """去掉语音识别常在句尾幻觉出来的词，再给快路径匹配。"""
    text = _compact_task(task)
    changed = True
    while changed:
        changed = False
        for tail in _ASR_JUNK_TAILS:
            if text.endswith(tail) and len(text) > len(tail):
                text = text[: -len(tail)]
                changed = True
    return text


def _catalog_signature(catalog: list[dict[str, Any]]) -> str:
    items = [
        f"{item.get('name', '')}:{item.get('arm_side', '')}:{item.get('frame_count', '')}"
        for item in catalog
    ]
    return "|".join(sorted(items))


def _motion_fast_route(task: str, catalog: list[dict[str, Any]]) -> dict[str, Any] | None:
    """动作目录匹配快路径。

    支持「11」「播放11」「播放动作11」「执行抓方块」等。
    """
    text = _compact_task(task)
    prefixes = ("播放", "执行", "做", "来一个", "给我来个")
    suffixes = ("动作", "这个动作", "一下")
    for item in catalog:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        compact_name = _compact_task(name)
        candidates = {compact_name, f"动作{compact_name}"}
        for prefix in prefixes:
            candidates.add(f"{prefix}{compact_name}")
            candidates.add(f"{prefix}动作{compact_name}")
        for suffix in suffixes:
            candidates.add(f"{compact_name}{suffix}")
        for prefix in prefixes:
            for suffix in suffixes:
                candidates.add(f"{prefix}{compact_name}{suffix}")
                candidates.add(f"{prefix}动作{compact_name}{suffix}")
        if text in candidates:
            return {
                "mode": "recorded_motion",
                "motion_name": name,
                "task": task,
                "reason": "用户明确说出已录制动作名，使用动作目录精确匹配快路径。",
            }

    play_match = re.match(r"^(?:播放(?:一下)?(?:动作)?|执行动作)(.+?)(?:动作|一下)?$", text)
    if play_match:
        needle = _compact_task(play_match.group(1))
        if needle:
            for item in catalog:
                compact_name = _compact_task(str(item.get("name") or ""))
                if needle == compact_name or needle == f"动作{compact_name}" or compact_name == f"动作{needle}":
                    return {
                        "mode": "recorded_motion",
                        "motion_name": str(item.get("name") or needle),
                        "task": task,
                        "reason": "播放指令匹配到已录制动作。",
                    }
            return {
                "mode": "recorded_motion",
                "motion_name": needle,
                "task": task,
                "reason": "播放指令未命中目录，交给播放逻辑返回未找到。",
            }
    return None


def _record_fast_route(task: str) -> dict[str, Any] | None:
    """解析「录制动作抓方块双臂15秒」这类指令。"""
    text = _compact_task(task)
    if text not in {"录制动作", "录个动作", "录制动做", "示教", "开始录制"} and not text.startswith("录制"):
        return None
    rest = text[2:] if text.startswith("录制") else ""
    if rest.startswith("动作"):
        rest = rest[2:]
    arm_side = "both"
    if "双臂" in rest:
        arm_side = "both"
        rest = rest.replace("双臂", "")
    elif "左臂" in rest:
        arm_side = "left"
        rest = rest.replace("左臂", "")
    elif "右臂" in rest:
        arm_side = "right"
        rest = rest.replace("右臂", "")
    seconds = 10.0
    duration = re.search(r"(\d+(?:\.\d+)?)秒", rest)
    if duration:
        seconds = max(0.5, min(float(duration.group(1)), 30.0))
        rest = rest[: duration.start()] + rest[duration.end() :]
    name = rest.strip() or "未命名动作"
    return {
        "mode": "record_motion",
        "motion_name": name,
        "arm_side": arm_side,
        "seconds": seconds,
        "task": task,
        "reason": "明确录制动作指令，本地快路径处理。",
    }


def _number_from_task(task: str, default: float) -> float:
    match = re.search(r"(\d+(?:\.\d+)?)", task)
    if not match:
        return default
    try:
        return float(match.group(1))
    except ValueError:
        return default


def _parse_base_sequence(task: str) -> list[dict[str, Any]] | None:
    """解析复合底盘指令（如“前进1.5米,右转45度”），返回动作序列。

    只解析明确带数字的动作单元；解析不出任何单元返回 None。
    距离默认米，厘米除以 100；角度默认度。
    """
    text = _compact_task(task)
    specs = [
        (r"(?:向前走|往前走|向前做|往前做|前进|往前|向前)(\d+(?:\.\d+)?)(米|厘米)?", "forward", False),
        (r"(?:向后退|往后退|向后做|后退|往后)(\d+(?:\.\d+)?)(米|厘米)?", "backward", False),
        (r"(?:向左平移|左移)(\d+(?:\.\d+)?)(米|厘米)?", "strafe_left", False),
        (r"(?:向右平移|右移)(\d+(?:\.\d+)?)(米|厘米)?", "strafe_right", False),
        (r"(?:向左转|左转)(\d+(?:\.\d+)?)度?", "turn_left", True),
        (r"(?:向右转|右转)(\d+(?:\.\d+)?)度?", "turn_right", True),
    ]
    matches: list[tuple[int, str, float]] = []
    for pattern, action, is_angle in specs:
        for m in re.finditer(pattern, text):
            value = float(m.group(1))
            if not is_angle and m.group(2) == "厘米":
                value = value / 100.0
            matches.append((m.start(), action, value))
    if not matches:
        return None
    matches.sort(key=lambda x: x[0])
    return [{"action": action, "value": value} for _, action, value in matches]


def fast_route_task(task: str) -> dict[str, Any] | None:
    """无歧义原子命令的本地快路径。

    这里只处理已经稳定验证过的单步头部控制命令，避免“抬头”这类
    明确动作还要先等待一次远端大模型路由。复合任务仍交给路由模型。
    """
    text = _spoken_task(task)
    capability_keys = ("做什么", "干什么", "会什么", "能做啥", "有什么功能", "你会啥", "介绍一下", "你是谁", "你叫什么")
    greetings = {"你好", "您好", "嗨", "哈喽", "在吗", "hello", "hi", "你好呀", "您好呀"}
    command_hints = ("走", "进", "退", "转", "抬头", "低头", "播放", "录制", "左移", "右移", "急停")
    if any(key in text for key in capability_keys) or text in greetings or text.casefold() in greetings:
        return {
            "mode": "chat",
            "task": task,
            "reason": "常见寒暄或问能力，本地快路径处理。",
        }
    if (text.startswith("你好") or text.startswith("您好")) and not any(hint in text for hint in command_hints):
        return {
            "mode": "chat",
            "task": task,
            "reason": "打招呼，本地快路径处理。",
        }
    if text.casefold() in {"键盘", "键盘控制", "手动遥控", "手动控制"}:
        return {
            "mode": "keyboard_control",
            "task": task,
            "reason": "明确要求手动遥控，不再交给大模型。",
        }
    if text in {"列出动作", "有哪些动作", "动作列表", "动作库", "看看动作", "查看动作", "已录动作"}:
        return {
            "mode": "list_motions",
            "task": task,
            "reason": "明确询问动作列表，本地快路径处理。",
        }
    record_route = _record_fast_route(task)
    if record_route is not None:
        return record_route
    head_routes = {
        "抬头": ("look_up", 20.0),
        "抬头一点": ("look_up", 20.0),
        "头抬起来": ("look_up", 20.0),
        "低头": ("look_down", 20.0),
        "低头一点": ("look_down", 20.0),
        "头低下去": ("look_down", 20.0),
        "左看": ("turn_head_left", 20.0),
        "向左看": ("turn_head_left", 20.0),
        "往左看": ("turn_head_left", 20.0),
        "右看": ("turn_head_right", 20.0),
        "向右看": ("turn_head_right", 20.0),
        "往右看": ("turn_head_right", 20.0),
        "回正": ("center_head", 0.0),
        "头回正": ("center_head", 0.0),
        "头部回正": ("center_head", 0.0),
        "恢复普通视角": ("center_head", 0.0),
    }
    if text not in head_routes:
        base_exact = {
            "前进": ("forward", 0.05),
            "向前走": ("forward", 0.05),
            "往前走": ("forward", 0.05),
            "走一点": ("forward", 0.05),
            "前进一点": ("forward", 0.05),
            "向前走一点": ("forward", 0.05),
            "往前走一点": ("forward", 0.05),
            "往前一点": ("forward", 0.05),
            "向前做": ("forward", 0.05),
            "往前做": ("forward", 0.05),
            "后退": ("backward", 0.05),
            "向后退": ("backward", 0.05),
            "往后退": ("backward", 0.05),
            "后退一点": ("backward", 0.05),
            "左转": ("turn_left", 15.0),
            "向左转": ("turn_left", 15.0),
            "右转": ("turn_right", 15.0),
            "向右转": ("turn_right", 15.0),
            "左移": ("strafe_left", 0.05),
            "向左平移": ("strafe_left", 0.05),
            "右移": ("strafe_right", 0.05),
            "向右平移": ("strafe_right", 0.05),
        }
        base_patterns = [
            (r"^(?:前进|向前走|往前走|向前做|往前做|往前|向前)(\d+(?:\.\d+)?)米?$", "forward", 0.05),
            (r"^(?:后退|向后退|往后退|向后做|往后)(\d+(?:\.\d+)?)米?$", "backward", 0.05),
            (r"^(?:左移|向左平移)(\d+(?:\.\d+)?)米?$", "strafe_left", 0.05),
            (r"^(?:右移|向右平移)(\d+(?:\.\d+)?)米?$", "strafe_right", 0.05),
            (r"^(?:左转|向左转)(\d+(?:\.\d+)?)度?$", "turn_left", 15.0),
            (r"^(?:右转|向右转)(\d+(?:\.\d+)?)度?$", "turn_right", 15.0),
        ]
        if text in base_exact:
            action, value = base_exact[text]
            return {
                "mode": "base_control",
                "base_action": action,
                "value": value,
                "task": task,
                "reason": "无歧义单步底盘动作，使用本地快路径跳过大模型路由。",
            }
        for pattern, action, default in base_patterns:
            if re.match(pattern, text):
                return {
                    "mode": "base_control",
                    "base_action": action,
                    "value": _number_from_task(text, default),
                    "task": task,
                    "reason": "无歧义单步底盘动作，使用本地快路径跳过大模型路由。",
                }
        for key, (action, value) in sorted(base_exact.items(), key=lambda item: -len(item[0])):
            if text.startswith(key) and 0 < len(text) - len(key) <= 4:
                rest = text[len(key) :]
                if not re.search(r"\d", rest):
                    return {
                        "mode": "base_control",
                        "base_action": action,
                        "value": value,
                        "task": task,
                        "reason": "语音指令带识别尾巴，本地快路径仍按底盘动作执行。",
                    }
        sequence = _parse_base_sequence(task)
        if sequence is not None:
            return {
                "mode": "base_sequence",
                "actions": sequence,
                "task": task,
                "reason": "复合底盘动作，本地解析成动作序列执行。",
            }
        return None
    action, angle = head_routes[text]
    return {
        "mode": "head_control",
        "head_action": action,
        "angle_degrees": angle,
        "task": task,
        "reason": "无歧义单步头部动作，使用本地快路径跳过大模型路由。",
    }


def _route_from_structured(result: RouteResult, task: str) -> dict[str, Any]:
    """把结构化路由结果转成路由 dict，并校验 confidence 范围。"""
    try:
        confidence = float(result.confidence)
    except (TypeError, ValueError):
        confidence = 0.0
    # 模型可能不遵守 0-1 范围（例如填 30），越界一律视为没把握。
    if not (0.0 <= confidence <= 1.0):
        confidence = 0.0

    mode = str(result.mode or "").strip()
    if mode not in ROUTER_MODES:
        mode = "unknown"

    # 模型明确没把握，或置信度太低，都当作“没听懂”，交给上层提示重说。
    if mode != "unknown" and confidence < MIN_ROUTE_CONFIDENCE:
        mode = "unknown"

    return {
        "mode": mode,
        "confidence": confidence,
        "reason": str(result.reason or "").strip(),
        "task": task,
    }


def route_task(task: str) -> dict[str, Any]:
    """使用大模型选择执行路线。

    路由器只输出 JSON，不拥有任何机器人工具，也不会触发动作。
    """
    fast_route = fast_route_task(task)
    if fast_route is not None:
        return fast_route

    catalog = recorded_motion_catalog()
    motion_route = _motion_fast_route(task, catalog)
    if motion_route is not None:
        return motion_route

    signature = _catalog_signature(catalog)
    cache_key = (_compact_task(task), signature)
    if cache_key in _ROUTE_CACHE:
        cached = dict(_ROUTE_CACHE[cache_key])
        cached["reason"] = str(cached.get("reason") or "") + "（路由缓存）"
        return cached

    prompt = (
        "你是机器人任务路由器。判断用户任务是需要机器人移动、导航、观察、"
        "或执行复合动作（navigation_agent），还是普通闲聊、问能力、寒暄、"
        "或无法理解的话（chat）。"
        f"用户任务={task}。"
    )
    llm = init_chat_model(LLM_MODEL, temperature=0, max_tokens=200)
    structured = llm.with_structured_output(RouteResult)
    try:
        result = structured.invoke(prompt)
        route = _route_from_structured(result, task)
    except Exception as exc:
        route = {
            "mode": "unknown",
            "task": task,
            "confidence": 0.0,
            "reason": f"路由调用失败：{exc}",
        }
    _ROUTE_CACHE[cache_key] = dict(route)
    return route
