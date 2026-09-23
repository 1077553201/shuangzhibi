"""XLeRobot 网页控制台后端。

把原本只能终端运行的语音/文字/底盘/头部/动作控制能力，暴露成网页 API，
方便不熟悉终端的人用浏览器操作机器人。

启动方式（在 lerobot0516 环境、robocrew_client_agent 目录下）：
    python web_server.py
然后浏览器打开 http://<本机IP>:8765
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HOME", str(Path(__file__).resolve().parents[1] / "hf_cache"))
os.environ.setdefault("HF_HUB_CACHE", str(Path(__file__).resolve().parents[1] / "hf_cache" / "hub"))

import cv2
import numpy as np
import uvicorn
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from config import (
    CAMERA_STREAMS,
    CAMERA_SWAP_RED_BLUE,
    HEAD_LEFT_SIGN,
    HEAD_PITCH_KEY,
    HEAD_STEP,
    HEAD_UP_SIGN,
    HEAD_YAW_KEY,
    MAIN_CAMERA_KEY,
    OPENAI_COMPATIBLE_API_BASE,
    OPENAI_COMPATIBLE_API_KEY,
    ROBOT_ID,
    ROBOT_IP,
)

# 设置大模型 API 环境变量（langchain init_chat_model 依赖）
os.environ.setdefault("OPENAI_API_KEY", OPENAI_COMPATIBLE_API_KEY)
os.environ.setdefault("OPENAI_API_BASE", OPENAI_COMPATIBLE_API_BASE)
os.environ.setdefault("OPENAI_BASE_URL", OPENAI_COMPATIBLE_API_BASE)
from client_camera_adapter import ClientRobotCamera
from client_servo_adapter import ClientServoControler
from official_motion_tools import (
    delete_motion,
    list_recorded_motions,
    play_motion,
    record_motion,
    recorded_motion_catalog,
)
from run_robocrew_client_agent import build_agent, run_one_task
from task_router import _compact_task
from run_xlerobot_remote import (
    get_controller as get_remote_service_controller,
    shutdown as shutdown_remote_services,
)
from voice_control_agent import (
    TELEGRIP_DIR,
    create_asr,
    disconnect_servo_for_mode,
    reconnect_servo_after_host,
    stop_local_vr_process,
)

WEB_HOST = "0.0.0.0"
WEB_PORT = 8765
UI_DIR = Path(__file__).resolve().parent / "web_ui"
HOST_START_TIMEOUT = 60.0

# 本地已缓存的 medium；GPU int8 识别会快很多，显存不够再自动回 CPU。
ASR_MODEL = "medium"
ASR_DEVICE = "auto"
ASR_BACKEND = "auto"

app = FastAPI(title="XLeRobot Web Console")

# 全局组件 + 锁
_servo: ClientServoControler | None = None
_main_camera: ClientRobotCamera | None = None
_agent = None
_asr = None
_init_error: str | None = None

# 控制命令串行化，避免多个请求同时操作机器人。
_action_lock = threading.Lock()
# 任务中止标志
_stop_event = threading.Event()

# 组件初始化锁（懒加载用）
_init_lock = threading.Lock()
# Whisper 不能并行推理，网页连续监听会同时丢来多段录音。
_asr_lock = threading.Lock()

# 手动遥控（持续控制）状态：当前按下的按钮集合 + 头部目标角度
_pressed: set[str] = set()
_pressed_lock = threading.Lock()
_head_yaw = 0.0
_head_pitch = 0.0
_head_initialized = False

# 手臂关节控制：左右臂 6 个关节，按按钮 +/- 步进。
_ARM_JOINTS = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")
_ARM_STEP = 3.0
_arm_targets: dict[str, float] | None = None

# 三路摄像头共用一份 observation，避免 3 个线程同时打 ZMQ。
_latest_obs: dict | None = None
_latest_obs_lock = threading.Lock()
# 录制/播放动作时暂停摄像头拉取，把 observation 通道让给动作采样。
_obs_paused = threading.Event()

# VR 进程句柄，用于关闭 VR。
_vr_process: subprocess.Popen | None = None
# VR 是否运行中：运行时锁住网页控制，避免和 VR 抢控制权。
_vr_active = False
_vr_lock = threading.Lock()
_need_arm_reset = False
_vr_url = ""

# 上一句播报内容，用来丢掉麦克风录到的扬声器回音。
_last_spoken = ""
_VOICE_NOISE = {
    "没听清",
    "请再说一遍",
    "再说一遍",
    "没听清请再说一遍",
    "我没听清",
    "我没听清请再说一遍",
}


def _init_servo() -> ClientServoControler:
    global _servo, _main_camera
    if _servo is None:
        _servo = ClientServoControler(remote_ip=ROBOT_IP, robot_id=ROBOT_ID)
        _main_camera = ClientRobotCamera(_servo, camera_key=MAIN_CAMERA_KEY)
    return _servo


def _vr_busy_response() -> JSONResponse | None:
    """VR 运行时锁住控制类 API，返回拒绝响应；未运行返回 None。"""
    if _vr_active:
        return JSONResponse({"ok": False, "error": "VR 控制中，请先关闭 VR 再操作。"})
    return None


def _local_lan_ip() -> str:
    """取访问树莓派所用的本机网卡 IP，供头显打开 telegrip 页面。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect((ROBOT_IP, 1))
        return sock.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        sock.close()


def _servo_connected() -> bool:
    if _servo is None:
        return False
    try:
        return bool(_servo.robot.is_connected)
    except Exception:
        return False


def _cached_obs() -> dict | None:
    with _latest_obs_lock:
        return _latest_obs


def _clear_teleop() -> None:
    global _arm_targets
    with _pressed_lock:
        _pressed.clear()
    _arm_targets = None


def _ensure_head_state(servo: ClientServoControler) -> None:
    """用当前 observation 初始化头部目标，避免快捷键和按住遥控互相抢旧角度。"""
    global _head_yaw, _head_pitch, _head_initialized
    if _head_initialized:
        return
    obs = _cached_obs()
    if obs is None:
        obs = servo.get_observation()
    _head_yaw = float(obs.get(HEAD_YAW_KEY, 0.0))
    _head_pitch = float(obs.get(HEAD_PITCH_KEY, 0.0))
    _head_initialized = True


def _nudge_head(servo: ClientServoControler, d_yaw: float = 0.0, d_pitch: float = 0.0) -> None:
    global _head_yaw, _head_pitch
    _ensure_head_state(servo)
    _head_yaw += d_yaw
    _head_pitch += d_pitch
    servo.robot.send_action({HEAD_YAW_KEY: _head_yaw, HEAD_PITCH_KEY: _head_pitch})


def _finish_vr_session() -> str:
    """停 telegrip、重连主 client、松开手臂扭矩。调用方需持有 _vr_lock。"""
    global _vr_process, _vr_active, _need_arm_reset, _head_initialized, _arm_targets
    process = _vr_process
    _vr_process = None
    stop_local_vr_process(process)

    servo = _init_servo()
    reconnect_servo_after_host(servo, "VR 模式")
    _head_initialized = False
    _arm_targets = None
    try:
        servo.release_arm_for_recording("both")
        _need_arm_reset = True
        result = "VR 已关闭，双臂扭矩已释放。请用手摆回安全位置，再点「恢复扭矩」。"
    except Exception as exc:
        _need_arm_reset = True
        result = f"VR 已关闭，但释放扭矩失败：{exc}。请点「释放扭矩」后再手动复位。"
    _vr_active = False
    return result


def _watch_vr_process(process: subprocess.Popen) -> None:
    """telegrip 自己退出时，按关闭 VR 的流程恢复网页控制。"""
    global _vr_active, _vr_process
    try:
        process.wait()
    except Exception:
        return
    with _vr_lock:
        if _vr_process is not process or not _vr_active:
            return
        print("[web] telegrip 已自行退出，正在恢复主 client...")
        try:
            _finish_vr_session()
        except Exception as exc:
            print(f"[web] VR 进程退出后恢复失败：{exc}")
            _vr_process = None
            _vr_active = False


def _ensure_agent():
    global _agent
    if _agent is None:
        servo = _init_servo()
        _agent = build_agent(servo, _main_camera)
    return _agent


def _ensure_asr():
    global _asr
    if _asr is None:
        _asr = create_asr(ASR_MODEL, ASR_DEVICE, ASR_BACKEND)
    return _asr


def _generate_tts(text: str) -> str | None:
    """用 edge-tts 生成 mp3，返回 base64。失败返回 None。"""
    clean = str(text or "").strip()
    if not clean:
        return None
    edge_tts_bin = shutil.which("edge-tts")
    if edge_tts_bin is None:
        return None
    out_path = Path(tempfile.gettempdir()) / "xlerobot_web_tts.mp3"
    try:
        proc = subprocess.run(
            [
                edge_tts_bin,
                "--voice",
                "zh-CN-XiaoxiaoNeural",
                "--text",
                clean,
                "--write-media",
                str(out_path),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=30,
        )
        if proc.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
            return None
        return base64.b64encode(out_path.read_bytes()).decode("ascii")
    except Exception:
        return None


def _json_body(response: JSONResponse) -> dict:
    return json.loads(response.body.decode("utf-8"))


def _is_voice_noise(task: str) -> bool:
    """空识别、回音、把播报再听成指令时，不应执行。"""
    text = _compact_task(task)
    if not text:
        return True
    if text in _VOICE_NOISE:
        return True
    if "语音助手" in text or "桌面机器人" in text:
        return True
    spoken = _compact_task(_last_spoken)
    if len(text) >= 8 and spoken:
        if text in spoken or (len(spoken) >= 8 and spoken[:16] in text):
            return True
    return False


def _run_task(task: str) -> dict:
    """执行一条文字任务，返回 {task, result, audio}。"""
    global _last_spoken
    compact = _compact_task(task).casefold()
    if compact in {"vr", "启动vr", "打开vr", "进入vr", "开始vr", "vr控制", "vr遥控"}:
        data = _json_body(api_vr_start())
        spoken = data.get("result") or data.get("error") or ""
        _last_spoken = spoken
        return {"task": task, "result": spoken, "audio": data.get("audio")}
    if compact in {"关闭vr", "退出vr", "停止vr"}:
        data = _json_body(api_vr_stop())
        spoken = data.get("result") or data.get("error") or ""
        _last_spoken = spoken
        return {"task": task, "result": spoken, "audio": data.get("audio")}

    agent = _ensure_agent()
    servo = _init_servo()
    _stop_event.clear()
    try:
        with _action_lock:
            result = run_one_task(
                agent,
                servo,
                _main_camera,
                task,
                show_raw_output=False,
                should_stop=_stop_event.is_set,
            )
    except Exception as exc:
        result = f"执行出错：{exc}"
    spoken = str(result)
    _last_spoken = spoken
    return {"task": task, "result": spoken, "audio": _generate_tts(spoken)}


class TextRequest(BaseModel):
    text: str


class ActionRequest(BaseModel):
    action: str
    value: float | None = None


class PlayMotionRequest(BaseModel):
    name: str


class RecordMotionRequest(BaseModel):
    name: str
    arm_side: str = "right"
    seconds: float = 5.0


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    index_file = UI_DIR / "index.html"
    if index_file.exists():
        return HTMLResponse(index_file.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>web_ui/index.html 不存在</h1>")


@app.get("/api/status")
def api_status() -> JSONResponse:
    connected = False
    if _servo is not None:
        try:
            connected = _servo.robot.is_connected
        except Exception:
            connected = False
    return JSONResponse(
        {
            "ok": True,
            "robot_ip": ROBOT_IP,
            "connected": connected,
            "vr_active": _vr_active,
            "vr_url": _vr_url or f"https://{_local_lan_ip()}:8443",
            "need_arm_reset": _need_arm_reset,
            "init_error": _init_error,
            "cameras": [{"key": key, "label": label} for key, label in CAMERA_STREAMS],
        }
    )


@app.post("/api/text")
def api_text(req: TextRequest) -> JSONResponse:
    busy = _vr_busy_response()
    if busy is not None:
        return busy
    task = str(req.text or "").strip()
    if not task:
        return JSONResponse({"ok": False, "error": "命令为空"})
    return JSONResponse({"ok": True, **_run_task(task)})


@app.post("/api/voice")
async def api_voice(file: UploadFile = File(...)) -> JSONResponse:
    """接收浏览器录制的音频，识别后执行，并返回结果和播报音频。"""
    busy = _vr_busy_response()
    if busy is not None:
        return busy
    raw = await file.read()
    if not raw:
        return JSONResponse({"ok": False, "error": "录音为空"})

    tmp_dir = Path(tempfile.gettempdir())
    stem = tmp_dir / f"xlerobot_web_voice_{uuid.uuid4().hex}"
    src_path = Path(str(stem) + ".bin")
    wav_path = Path(str(stem) + ".wav")
    src_path.write_bytes(raw)

    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin:
        proc = subprocess.run(
            [
                ffmpeg_bin,
                "-y",
                "-i",
                str(src_path),
                "-ar",
                "16000",
                "-ac",
                "1",
                str(wav_path),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=60,
        )
        if proc.returncode != 0 or not wav_path.exists() or wav_path.stat().st_size == 0:
            src_path.unlink(missing_ok=True)
            wav_path.unlink(missing_ok=True)
            return JSONResponse({"ok": False, "error": "录音转码失败，请确认本机已安装 ffmpeg。"})
    else:
        wav_path = src_path

    try:
        asr = _ensure_asr()
        with _asr_lock:
            task = str(asr.transcribe(wav_path, allow_no_vad_retry=True)).strip()
        print(f"[语音] 识别结果={task!r}，原始字节={len(raw)}")
    except Exception as exc:
        return JSONResponse({"ok": False, "error": f"识别失败：{exc}"})
    finally:
        src_path.unlink(missing_ok=True)
        if wav_path != src_path:
            wav_path.unlink(missing_ok=True)

    if _is_voice_noise(task):
        return JSONResponse({
            "ok": True,
            "task": task,
            "result": "",
            "audio": None,
            "ignored": True,
            "reason": "empty" if not task else "echo",
        })

    return JSONResponse({"ok": True, **_run_task(task)})


@app.post("/api/action")
def api_action(req: ActionRequest) -> JSONResponse:
    global _need_arm_reset, _head_yaw, _head_pitch, _head_initialized, _arm_targets
    action = str(req.action or "").strip()
    # 急停必须立刻生效：不能等正在跑的 AI/播放把 _action_lock 让出来。
    if action == "stop":
        _stop_event.set()
        _clear_teleop()
        try:
            _init_servo().request_task_abort()
        except Exception:
            pass
        return JSONResponse({"ok": True, "result": "已停止。"})
    busy = _vr_busy_response()
    if busy is not None:
        return busy
    servo = _init_servo()
    value = float(req.value) if req.value is not None else None
    _stop_event.clear()
    try:
        with _action_lock:
            if action == "forward":
                servo.go_forward(value if value is not None else 0.1)
                result = "已前进。"
            elif action == "backward":
                servo.go_backward(value if value is not None else 0.1)
                result = "已后退。"
            elif action == "left":
                servo.strafe_left(value if value is not None else 0.1)
                result = "已左移。"
            elif action == "right":
                servo.strafe_right(value if value is not None else 0.1)
                result = "已右移。"
            elif action == "turn_left":
                servo.turn_left(value if value is not None else 30.0)
                result = "已左转。"
            elif action == "turn_right":
                servo.turn_right(value if value is not None else 30.0)
                result = "已右转。"
            elif action == "look_up":
                _nudge_head(servo, d_pitch=HEAD_UP_SIGN * (-HEAD_STEP))
                result = "已抬头。"
            elif action == "look_down":
                _nudge_head(servo, d_pitch=HEAD_UP_SIGN * HEAD_STEP)
                result = "已低头。"
            elif action == "head_left":
                _nudge_head(servo, d_yaw=HEAD_LEFT_SIGN * (-HEAD_STEP))
                result = "头部已向左转。"
            elif action == "head_right":
                _nudge_head(servo, d_yaw=HEAD_LEFT_SIGN * HEAD_STEP)
                result = "头部已向右转。"
            elif action == "head_center":
                servo.reset_head_position()
                _head_yaw = 0.0
                _head_pitch = 22.0
                _head_initialized = True
                result = "头部已回正。"
            elif action == "release_torque":
                servo.set_arm_torque("both", enabled=False)
                _arm_targets = None
                _need_arm_reset = True
                result = "已释放双臂扭矩，可以手动掰动机械臂。"
            elif action == "restore_torque":
                servo.set_arm_torque("both", enabled=True)
                _arm_targets = None
                _need_arm_reset = False
                result = "已按当前手动位置恢复双臂扭矩。"
            else:
                return JSONResponse({"ok": False, "error": f"未知动作：{action}"})
    except Exception as exc:
        result = f"动作执行出错：{exc}"
    # 快捷按钮不走 TTS：edge-tts 每次要 1–2 秒，连点「低头」会明显卡住。
    return JSONResponse({"ok": True, "result": result})


def _teleop_loop():
    """手动遥控后台循环：读取按下的按钮，持续发送底盘速度/头部/手臂关节目标。"""
    global _head_yaw, _head_pitch, _head_initialized, _arm_targets
    servo = _init_servo()
    _heartbeat_logged = False
    while True:
        # VR 运行期间，网页侧持续控制暂停。
        if _vr_active:
            time.sleep(0.1)
            continue
        with _pressed_lock:
            pressed = set(_pressed)
        # 心跳不能一直占着锁，否则快捷按钮要点一下等很久。
        if not _action_lock.acquire(blocking=False):
            time.sleep(0.05)
            continue
        try:
            obs = _cached_obs()
            if servo.robot.is_connected and not _head_initialized and obs is not None:
                _head_yaw = float(obs.get(HEAD_YAW_KEY, 0.0))
                _head_pitch = float(obs.get(HEAD_PITCH_KEY, 0.0))
                _head_initialized = True

            if not pressed:
                _arm_targets = None
                if servo.robot.is_connected:
                    servo.robot.send_action({"x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0})
                    if not _heartbeat_logged:
                        print("[web] 已开始持续向 host 发送心跳命令")
                        _heartbeat_logged = True
            else:
                action: dict[str, float] = {}

                # 底盘速度：+x 前进，+y 左移，+theta 左转（与 client_servo_adapter 一致）
                x = y = theta = 0.0
                if "forward" in pressed:
                    x += 0.15
                if "backward" in pressed:
                    x -= 0.15
                if "left" in pressed:
                    y += 0.15
                if "right" in pressed:
                    y -= 0.15
                if "turn_left" in pressed:
                    theta += 40.0
                if "turn_right" in pressed:
                    theta -= 40.0
                action["x.vel"] = x
                action["y.vel"] = y
                action["theta.vel"] = theta

                # 头部：与 config 里 HEAD_*_SIGN / 语音工具同一套（左转、抬头传负角度再乘符号）
                if _head_initialized:
                    head_moved = False
                    if "look_up" in pressed:
                        _head_pitch += HEAD_UP_SIGN * (-0.5)
                        head_moved = True
                    if "look_down" in pressed:
                        _head_pitch += HEAD_UP_SIGN * 0.5
                        head_moved = True
                    if "head_left" in pressed:
                        _head_yaw += HEAD_LEFT_SIGN * (-0.5)
                        head_moved = True
                    if "head_right" in pressed:
                        _head_yaw += HEAD_LEFT_SIGN * 0.5
                        head_moved = True
                    if head_moved:
                        action[HEAD_YAW_KEY] = _head_yaw
                        action[HEAD_PITCH_KEY] = _head_pitch

                # 手臂：按住时在本地累加目标，不再每帧用 observation 当起点（否则会几乎不动或弹回）。
                arm_pressed = any(
                    f"{side}_{joint}_{delta}" in pressed
                    for joint in _ARM_JOINTS
                    for side in ("left", "right")
                    for delta in ("plus", "minus")
                )
                if arm_pressed:
                    if _arm_targets is None:
                        if obs is None:
                            try:
                                obs = servo.get_observation()
                            except Exception:
                                obs = None
                        if obs is not None:
                            targets: dict[str, float] = {}
                            for joint in _ARM_JOINTS:
                                for side in ("left", "right"):
                                    key = f"{side}_arm_{joint}.pos"
                                    if key in obs:
                                        targets[key] = float(obs[key])
                            _arm_targets = targets or None
                    if _arm_targets:
                        for joint in _ARM_JOINTS:
                            for side in ("left", "right"):
                                key = f"{side}_arm_{joint}.pos"
                                if key not in _arm_targets:
                                    continue
                                if f"{side}_{joint}_plus" in pressed:
                                    _arm_targets[key] += _ARM_STEP
                                    action[key] = _arm_targets[key]
                                elif f"{side}_{joint}_minus" in pressed:
                                    _arm_targets[key] -= _ARM_STEP
                                    action[key] = _arm_targets[key]
                else:
                    _arm_targets = None

                if servo.robot.is_connected:
                    servo.robot.send_action(action)
        except Exception as exc:
            print(f"[web] 遥控循环出错：{exc}")
        finally:
            _action_lock.release()
        time.sleep(0.05)


@app.post("/api/teleop/press")
def api_teleop_press(req: ActionRequest) -> JSONResponse:
    busy = _vr_busy_response()
    if busy is not None:
        return busy
    with _pressed_lock:
        _pressed.add(str(req.action or "").strip())
    return JSONResponse({"ok": True})


@app.post("/api/teleop/release")
def api_teleop_release(req: ActionRequest) -> JSONResponse:
    busy = _vr_busy_response()
    if busy is not None:
        return busy
    with _pressed_lock:
        _pressed.discard(str(req.action or "").strip())
    return JSONResponse({"ok": True})


@app.post("/api/vr/start")
def api_vr_start() -> JSONResponse:
    """断开主 client 后启动本地 telegrip，避免和网页抢 ZMQ。"""
    global _vr_process, _vr_active, _vr_url, _need_arm_reset
    if not (TELEGRIP_DIR / "telegrip").exists():
        return JSONResponse({"ok": False, "error": "找不到 telegrip 目录。"})
    with _vr_lock:
        if _vr_active:
            return JSONResponse({"ok": False, "error": "VR 已在运行。"})
        servo = _init_servo()
        with _pressed_lock:
            _pressed.clear()
        # 先立标志，让摄像头循环停止 get_observation，避免 disconnect 后又被重连。
        _vr_active = True
        time.sleep(0.15)
        try:
            disconnect_servo_for_mode(servo, "VR")
        except Exception as exc:
            _vr_active = False
            return JSONResponse({"ok": False, "error": f"进入 VR 前无法断开主 client：{exc}"})
        try:
            _vr_process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "telegrip",
                    "--remote-ip",
                    ROBOT_IP,
                    "--autoconnect",
                    "--log-level",
                    "error",
                ],
                cwd=str(TELEGRIP_DIR),
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception as exc:
            _vr_active = False
            try:
                reconnect_servo_after_host(servo, "VR 模式")
            except Exception:
                pass
            return JSONResponse({"ok": False, "error": f"启动 VR 失败：{exc}"})
        _need_arm_reset = False
        _vr_url = f"https://{_local_lan_ip()}:8443"
        threading.Thread(
            target=_watch_vr_process,
            args=(_vr_process,),
            daemon=True,
        ).start()
        result = f"VR 已启动，网页控制已锁定。请在头显浏览器打开 {_vr_url}，点 Start Controller Tracking 后按住 Grip。"
        return JSONResponse({"ok": True, "result": result, "vr_url": _vr_url, "audio": _generate_tts(result)})


@app.post("/api/vr/stop")
def api_vr_stop() -> JSONResponse:
    """停止本地 telegrip，重连主 client，并松开手臂扭矩。"""
    with _vr_lock:
        if not _vr_active and _vr_process is None:
            result = "VR 未在运行。"
            return JSONResponse({"ok": True, "result": result, "need_arm_reset": _need_arm_reset, "audio": _generate_tts(result)})
        try:
            result = _finish_vr_session()
        except Exception as exc:
            return JSONResponse({"ok": False, "error": f"关闭 VR 后恢复失败：{exc}"})
        return JSONResponse(
            {
                "ok": True,
                "result": result,
                "need_arm_reset": _need_arm_reset,
                "audio": _generate_tts(result),
            }
        )


@app.get("/api/motions")
def api_motions() -> JSONResponse:
    catalog = recorded_motion_catalog()
    return JSONResponse({"ok": True, "motions": [c.get("name") for c in catalog], "text": list_recorded_motions()})


@app.post("/api/motions/play")
def api_play_motion(req: PlayMotionRequest) -> JSONResponse:
    busy = _vr_busy_response()
    if busy is not None:
        return busy
    servo = _init_servo()
    name = str(req.name or "").strip()
    if not name:
        return JSONResponse({"ok": False, "error": "动作名为空"})
    if not servo.robot.is_connected:
        msg = "未连接到树莓派 host，请先在树莓派启动 host 再播放动作。"
        return JSONResponse({"ok": True, "result": msg, "audio": _generate_tts(msg)})
    _stop_event.clear()
    try:
        with _action_lock:
            raw = play_motion(servo, name, should_stop=_stop_event.is_set)
    except Exception as exc:
        raw = f"播放出错：{exc}"
    if "已播放" in raw or "TASK_COMPLETE" in raw:
        result = f"动作「{name}」已播放完成。"
    else:
        result = raw
    return JSONResponse({"ok": True, "result": result, "audio": _generate_tts(result)})


@app.post("/api/motions/delete")
def api_delete_motion(req: PlayMotionRequest) -> JSONResponse:
    name = str(req.name or "").strip()
    if not name:
        return JSONResponse({"ok": False, "error": "动作名为空"})
    try:
        raw = delete_motion(name)
    except Exception as exc:
        raw = f"删除出错：{exc}"
    ok = raw.startswith("已删除")
    return JSONResponse({"ok": ok, "result": raw, "error": None if ok else raw})


@app.post("/api/motions/record")
def api_record_motion(req: RecordMotionRequest) -> JSONResponse:
    busy = _vr_busy_response()
    if busy is not None:
        return busy
    servo = _init_servo()
    name = str(req.name or "").strip() or "未命名动作"
    arm_side = str(req.arm_side or "right").strip()
    seconds = float(req.seconds if req.seconds is not None else 5.0)
    _obs_paused.set()
    try:
        with _action_lock:
            raw = record_motion(servo, name, arm_side, seconds, should_stop=_stop_event.is_set)
    except Exception as exc:
        raw = f"录制出错：{exc}"
    finally:
        _obs_paused.clear()
    if "已保存" in raw or "TASK_COMPLETE" in raw:
        result = f"动作「{name}」已录制完成。"
    else:
        result = raw
    return JSONResponse({"ok": True, "result": result, "audio": _generate_tts(result)})


def _shared_observation_loop() -> None:
    """单线程拉取 observation，三路摄像头只读缓存。"""
    global _latest_obs
    servo = _init_servo()
    while True:
        if _vr_active or _obs_paused.is_set() or not _servo_connected():
            time.sleep(0.2)
            continue
        try:
            obs = servo.get_observation()
            with _latest_obs_lock:
                _latest_obs = obs
        except Exception:
            pass
        time.sleep(0.08)


def _camera_frames(camera_key: str):
    """持续读取指定摄像头并编码 JPEG，供 MJPEG 流使用。"""
    while True:
        try:
            if _vr_active or not _servo_connected():
                time.sleep(0.2)
                continue
            with _latest_obs_lock:
                obs = _latest_obs
            frame = None if obs is None else obs.get(camera_key)
            if isinstance(frame, np.ndarray) and frame.size:
                if CAMERA_SWAP_RED_BLUE:
                    frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                ok, encoded = cv2.imencode(".jpg", frame)
                if ok:
                    data = encoded.tobytes()
                    yield (
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n\r\n" + data + b"\r\n"
                    )
        except Exception:
            pass
        time.sleep(0.08)


@app.get("/api/camera/{camera_key}")
def api_camera(camera_key: str) -> StreamingResponse:
    allowed = {key for key, _label in CAMERA_STREAMS}
    if camera_key not in allowed:
        return JSONResponse({"ok": False, "error": f"未知摄像头：{camera_key}"}, status_code=404)
    return StreamingResponse(
        _camera_frames(camera_key),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


def main() -> None:
    global _init_error

    # 自动 SSH 启动 host（复用 voice_control_agent 的远程服务控制逻辑）。
    remote_controller = get_remote_service_controller(host=ROBOT_IP)
    try:
        remote_controller.ensure_host_running(timeout=HOST_START_TIMEOUT)
    except Exception as exc:
        _init_error = str(exc)
        print(f"[web] 自动启动 host 失败：{exc}")

    # 连接 client。
    try:
        servo = _init_servo()
        servo.connect()
        print(f"[web] 已连接 host（is_connected={servo.robot.is_connected}）")
    except Exception as exc:
        _init_error = str(exc)
        print(f"[web] 连接 host 失败：{exc}")

    print(f"[web] XLeRobot 控制台已启动：http://127.0.0.1:{WEB_PORT}")
    threading.Thread(target=_teleop_loop, daemon=True).start()
    threading.Thread(target=_shared_observation_loop, daemon=True).start()
    try:
        uvicorn.run(app, host=WEB_HOST, port=WEB_PORT, log_level="warning")
    finally:
        with _vr_lock:
            if _vr_process is not None:
                stop_local_vr_process(_vr_process)
        try:
            shutdown_remote_services()
        except Exception:
            pass


if __name__ == "__main__":
    main()
