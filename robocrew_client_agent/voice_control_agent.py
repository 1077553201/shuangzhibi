"""XLeRobot 文字/语音混合控制入口。

第一阶段目标：
- 终端直接输入文字时，按文字任务执行。
- 输入 v 时，本地麦克风录音。
- 本地 Whisper/faster-whisper 语音识别。
- 复用已经调通的 RoboCrew LLM Agent 任务执行链路。
- 用 Windows SAPI 做本地语音播报。

本文件只做语音输入/输出外壳，不重新实现机器人控制逻辑。
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import signal
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HOME", str(Path(__file__).resolve().parents[1] / "hf_cache"))
os.environ.setdefault("HF_HUB_CACHE", str(Path(__file__).resolve().parents[1] / "hf_cache" / "hub"))

import numpy as np

from keyboard_utils import cbreak_stdin, getwch, kbhit

from client_camera_adapter import ClientRobotCamera
from client_servo_adapter import ClientServoControler
from config import (
    MAIN_CAMERA_KEY,
    OPENAI_COMPATIBLE_API_BASE,
    OPENAI_COMPATIBLE_API_KEY,
    ROBOT_ID,
    ROBOT_IP,
)
from run_robocrew_client_agent import (
    build_agent,
    configure_runtime_output,
    require_module,
    run_one_task,
)
from run_xlerobot_remote import (
    RemoteServiceController,
    VR_REQUIRED_STATUS_FLAGS,
    get_controller as get_remote_service_controller,
    shutdown as shutdown_remote_services,
)


VOICE_DIR = Path(__file__).resolve().parent / "outputs" / "voice"
KEYBOARD_CLIENT_PATH = Path(__file__).resolve().parents[1] / "xlerobot_remote_keyboard_client.py"
# 新版 VR（client 模式）的 telegrip 项目目录，本地用 python -m telegrip 启动
TELEGRIP_DIR = Path(__file__).resolve().parents[1] / "lwy" / "telegrip" / "telegrip"
STOP_WORDS = ("停止", "别动", "停下", "不要动")
STOP_LISTEN_WORDS = ("停止监听", "退出语音", "结束语音", "停止语音")
DEFAULT_SILENCE_THRESHOLD = 0.01
HOST_START_TIMEOUT = 60.0
TASK_ABORT_KEYS = {" "}
AUTO_LISTEN_EXIT_KEYS = {"q", "Q", "\x1b"}
RERECORD_KEYS = {"r", "R"}
VR_EXIT_KEYS = {"q", "Q", "\x1b"}
VR_STOP_WAIT_SECONDS = 8.0

TRADITIONAL_TO_SIMPLIFIED = str.maketrans(
    {
        "臺": "台",
        "台": "台",
        "後": "后",
        "機": "机",
        "語": "语",
        "虛": "虚",
        "擬": "拟",
        "動": "动",
        "錄": "录",
        "製": "制",
        "聽": "听",
        "頭": "头",
        "臂": "臂",
        "雙": "双",
        "關": "关",
        "開": "开",
        "轉": "转",
        "顯": "显",
        "個": "个",
        "麼": "么",
        "嗎": "吗",
        "這": "这",
        "裡": "里",
        "裏": "里",
        "點": "点",
        "隻": "只",
        "夾": "夹",
        "爪": "爪",
        "錢": "钱",
        "說": "说",
        "話": "话",
        "現": "现",
        "實": "实",
        "監": "监",
        "聆": "聆",
    }
)

COMMON_COMMAND_FIXES = {
    "向钱走": "向前走",
    "像前走": "向前走",
    "往钱走": "往前走",
    "网前走": "往前走",
    "王前走": "往前走",
    "往厚走": "往后走",
    "往後走": "往后走",
    "后退": "往后退",
    "後退": "往后退",
    "左转": "左转",
    "右转": "右转",
    "像左转": "向左转",
    "像右转": "向右转",
    "抬投": "抬头",
    "台头": "抬头",
    "低投": "低头",
    "底头": "低头",
    "挥挥首": "挥挥手",
    "挥挥收": "挥挥手",
    "保存动做": "保存动作",
    "录制动做": "录制动作",
    "前进一点秘密": "前进一点",
    "向前走一点秘密": "向前走一点",
}

ASR_JUNK_TAILS = (
    "秘密",
    "字幕",
    "谢谢观看",
    "谢谢收看",
    "请订阅",
    "订阅",
    "点赞",
    "加油",
    "以下字幕",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="XLeRobot 文字/语音混合控制 Agent。")
    parser.add_argument("--seconds", type=float, default=5.0, help="每次按 Enter 后录音秒数")
    parser.add_argument("--sample-rate", type=int, default=44100, help="麦克风采样率")
    parser.add_argument("--model", default="medium", help="faster-whisper 模型名或本地模型路径")
    parser.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"], help="ASR 推理设备")
    parser.add_argument(
        "--asr-backend",
        default="auto",
        choices=["auto", "transformers", "faster-whisper"],
        help="ASR 后端。auto 会优先使用 PyTorch/Transformers GPU。",
    )
    parser.add_argument("--camera-key", default=MAIN_CAMERA_KEY, help="传给现有 Agent 的主摄像头 key")
    parser.add_argument("--show-raw-output", action="store_true", help="显示模型原始输出")
    parser.add_argument("--no-tts", action="store_true", help="关闭语音播报，只打印文字")
    parser.add_argument("--silence-threshold", type=float, default=DEFAULT_SILENCE_THRESHOLD, help="低于该音量视为静音")
    parser.add_argument("--auto-pause-seconds", type=float, default=1.5, help="自动监听每轮之间等待键盘退出的秒数")
    parser.add_argument("--input-device", default=None, help="指定 sounddevice 输入设备编号或名称")
    parser.add_argument("--list-audio-devices", action="store_true", help="列出本机音频输入设备后退出")
    return parser.parse_args()


def ensure_voice_dependencies() -> None:
    missing = []
    for module_name in ("sounddevice",):
        if importlib.util.find_spec(module_name) is None:
            missing.append(module_name)
    if missing:
        modules = " ".join(missing)
        raise RuntimeError(
            "缺少本地语音依赖："
            + ", ".join(missing)
            + "\n请在 Windows 端当前环境安装：\n"
            + f"E:\\lerobot\\envs\\lerobot-gpu\\Scripts\\python.exe -m pip install {modules}"
        )


def resolve_asr_backend(requested_backend: str, requested_device: str) -> str:
    if requested_backend != "auto":
        return requested_backend
    # 默认用 faster-whisper：它自带 Silero VAD，能过滤纯噪声/静音，
    # 避免 transformers Whisper 把没说话的环境噪声幻觉成乱字（如“字幕由 Amara.org 社群提供”）。
    return "faster-whisper"


def resolve_local_faster_whisper(model_name: str) -> str:
    """优先用本仓库已经下载好的 faster-whisper 权重，避免识别时再访问 Hub。"""
    if "/" in model_name or Path(model_name).exists():
        return model_name
    hub = Path(__file__).resolve().parents[1] / "hf_cache" / "hub"
    folder = hub / f"models--Systran--faster-whisper-{model_name}"
    snapshots = folder / "snapshots"
    if snapshots.is_dir():
        for snap in sorted(snapshots.iterdir()):
            if (snap / "model.bin").is_file():
                return str(snap)
    return model_name


def create_asr(model_name: str, device: str, backend: str):
    selected_backend = resolve_asr_backend(backend, device)
    if selected_backend == "transformers":
        return TransformersWhisperAsr(model_name, device)
    if importlib.util.find_spec("faster_whisper") is None:
        raise RuntimeError(
            "缺少 faster-whisper。请安装：\n"
            "E:\\lerobot\\envs\\lerobot-gpu\\Scripts\\python.exe -m pip install faster-whisper"
        )
    return LocalWhisperAsr(model_name, device)


def hf_whisper_model_name(model_name: str) -> str:
    aliases = {
        "tiny": "openai/whisper-tiny",
        "base": "openai/whisper-base",
        "small": "openai/whisper-small",
        "medium": "openai/whisper-medium",
        "large": "openai/whisper-large-v3",
        "large-v3": "openai/whisper-large-v3",
    }
    return aliases.get(model_name, model_name)


def print_audio_devices() -> None:
    import sounddevice as sd

    print("[音频设备] 可用输入设备：")
    for index, device in enumerate(sd.query_devices()):
        if int(device.get("max_input_channels", 0)) <= 0:
            continue
        default_mark = " *默认" if index == sd.default.device[0] else ""
        print(f"  {index}: {device['name']}  inputs={device['max_input_channels']}{default_mark}")


def record_wav(path: Path, seconds: float, sample_rate: int, input_device: str | None = None) -> float:
    import sounddevice as sd

    duration_s = max(1.0, min(float(seconds), 20.0))
    sample_count = int(duration_s * sample_rate)
    print(f"[录音] 开始录音 {duration_s:.1f} 秒...")
    device = int(input_device) if isinstance(input_device, str) and input_device.isdigit() else input_device
    audio = sd.rec(sample_count, samplerate=sample_rate, channels=1, dtype="float32", device=device)
    sd.wait()

    pcm = np.clip(audio[:, 0], -1.0, 1.0)
    rms = float(np.sqrt(np.mean(np.square(pcm)))) if pcm.size else 0.0
    pcm_i16 = (pcm * 32767).astype(np.int16)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm_i16.tobytes())
    print(f"[录音] 已保存：{path}，音量={rms:.4f}")
    return rms


class LocalWhisperAsr:
    def __init__(self, model_name: str, device: str) -> None:
        from faster_whisper import WhisperModel

        self.model_name = model_name
        self.device = device
        selected_device = "cuda" if device == "auto" else device
        compute_type = "int8"
        model_path = resolve_local_faster_whisper(model_name)
        local_only = Path(model_path).is_dir()
        try:
            self.model = WhisperModel(
                model_path,
                device=selected_device,
                compute_type=compute_type,
                local_files_only=local_only,
            )
            self.device = selected_device
            print(
                f"[ASR] 已加载 faster-whisper model={model_name}, "
                f"path={model_path}, device={selected_device}, compute={compute_type}"
            )
        except Exception as exc:
            if device == "auto":
                print(f"[ASR] CUDA 加载失败，改用 CPU：{exc}")
                self.model = WhisperModel(
                    model_path,
                    device="cpu",
                    compute_type="int8",
                    local_files_only=local_only,
                )
                self.device = "cpu"
                print(f"[ASR] 已加载 faster-whisper model={model_name}, device=cpu")
            else:
                raise

    def _move_to_cpu(self) -> None:
        self.model = None
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
        from faster_whisper import WhisperModel

        model_path = resolve_local_faster_whisper(self.model_name)
        self.model = WhisperModel(
            model_path,
            device="cpu",
            compute_type="int8",
            local_files_only=Path(model_path).is_dir(),
        )
        self.device = "cpu"
        print("[ASR] 已改用 CPU 识别，避免再占满显存。")

    def transcribe(self, wav_path: Path, allow_no_vad_retry: bool = True) -> str:
        try:
            return self._transcribe_with_retry(wav_path, allow_no_vad_retry)
        except RuntimeError as exc:
            message = str(exc).lower()
            if "cublas" not in message and "cuda" not in message and "out of memory" not in message:
                raise
            print(f"[ASR] CUDA 推理失败，自动切换到 CPU：{exc}")
            self._move_to_cpu()
            return self._transcribe_with_retry(wav_path, allow_no_vad_retry)

    def _transcribe_with_retry(self, wav_path: Path, allow_no_vad_retry: bool) -> str:
        text = self._transcribe_once(wav_path, vad_filter=True)
        if text or not allow_no_vad_retry:
            return text
        rms = wav_rms(wav_path)
        if rms < 0.012:
            print(f"[ASR] 静音，跳过重试 rms={rms:.4f}")
            return ""
        print(f"[ASR] VAD 未检出语音，rms={rms:.4f}，关闭 VAD 再识别一次")
        if self.device == "cuda":
            self._move_to_cpu()
        return self._transcribe_once(wav_path, vad_filter=False)

    def _transcribe_once(self, wav_path: Path, vad_filter: bool) -> str:
        kwargs: dict = {
            "language": "zh",
            "vad_filter": vad_filter,
            "beam_size": 1,
            "condition_on_previous_text": False,
            "initial_prompt": (
                "以下是普通话简体中文机器人控制命令，例如：你好、向前走、往后退、"
                "左转、右转、抬头、低头、看到什么、播放动作、录制双臂动作。"
            ),
        }
        if vad_filter:
            kwargs["vad_parameters"] = {
                "threshold": 0.22,
                "min_silence_duration_ms": 200,
                "speech_pad_ms": 500,
            }
        segments, _info = self.model.transcribe(str(wav_path), **kwargs)
        return normalize_asr_text("".join(segment.text for segment in segments))


class TransformersWhisperAsr:
    def __init__(self, model_name: str, device: str) -> None:
        import torch
        from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline

        self.model_name = hf_whisper_model_name(model_name)
        if device == "cuda" or (device == "auto" and torch.cuda.is_available()):
            self.device = "cuda:0"
            torch_dtype = torch.float16
        else:
            self.device = "cpu"
            torch_dtype = torch.float32

        model = AutoModelForSpeechSeq2Seq.from_pretrained(
            self.model_name,
            torch_dtype=torch_dtype,
            low_cpu_mem_usage=True,
            use_safetensors=True,
        )
        model.to(self.device)
        processor = AutoProcessor.from_pretrained(self.model_name)
        self.pipe = pipeline(
            "automatic-speech-recognition",
            model=model,
            tokenizer=processor.tokenizer,
            feature_extractor=processor.feature_extractor,
            torch_dtype=torch_dtype,
            device=0 if self.device.startswith("cuda") else -1,
        )
        print(f"[ASR] 已加载 transformers Whisper model={self.model_name}, device={self.device}")

    def transcribe(self, wav_path: Path, allow_no_vad_retry: bool = True) -> str:
        from scipy.io import wavfile

        sample_rate, audio = wavfile.read(str(wav_path))
        if audio.ndim > 1:
            audio = audio[:, 0]
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)
            max_value = np.max(np.abs(audio)) if audio.size else 1.0
            if max_value > 1.0:
                audio = audio / max_value

        result = self.pipe(
            {"array": audio, "sampling_rate": int(sample_rate)},
            generate_kwargs={
                "language": "zh",
                "task": "transcribe",
                "condition_on_prev_tokens": False,
            },
        )
        text = result.get("text", "") if isinstance(result, dict) else str(result)
        return normalize_asr_text(text)


def wav_rms(wav_path: Path) -> float:
    """估算 wav 音量，用来判断是真静音还是 VAD 误杀。"""
    try:
        with wave.open(str(wav_path), "rb") as wav:
            frames = wav.readframes(wav.getnframes())
            width = wav.getsampwidth()
        if not frames:
            return 0.0
        if width == 2:
            pcm = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        else:
            pcm = (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
        if pcm.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(np.square(pcm))))
    except Exception:
        return 0.0


def normalize_asr_text(text: str) -> str:
    normalized = text.strip().translate(TRADITIONAL_TO_SIMPLIFIED)
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = normalized.replace("，", "").replace("。", "").replace("！", "").replace("？", "")
    for wrong, right in COMMON_COMMAND_FIXES.items():
        normalized = normalized.replace(wrong, right)
    changed = True
    while changed:
        changed = False
        for tail in ASR_JUNK_TAILS:
            if normalized.endswith(tail) and len(normalized) > len(tail):
                normalized = normalized[: -len(tail)]
                changed = True
    return normalized.strip()


def speak_windows_sapi(text: str) -> None:
    """跨平台语音播报：Windows 用 SAPI，Linux 用 espeak（不可用则打印）。"""
    text = clean_text_for_tts(text)
    if not text.strip():
        return
    if sys.platform == "win32":
        _speak_sapi(text)
    else:
        _speak_linux(text)


def _speak_sapi(text: str) -> None:
    # SAPI 是同步朗读。超时需要保护卡死，但不能短到把正常长回复截断。
    # 中文普通语速大约每秒 3-5 个字，这里留足余量，最多等待 90 秒。
    timeout_s = max(15.0, min(90.0, len(text) * 0.45))
    # Windows 自带 SAPI，本地播报；不需要额外 Python TTS 包。
    escaped = text.replace("'", "''")
    command = (
        "Add-Type -AssemblyName System.Speech; "
        "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$s.Rate=0; "
        f"$s.Speak('{escaped}')"
    )
    try:
        proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-Command", command],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.perf_counter() + timeout_s
        while proc.poll() is None:
            if time.perf_counter() >= deadline:
                proc.terminate()
                print(f"[TTS] 语音播报超过 {timeout_s:.0f} 秒，已跳过剩余播报。")
                return
            if kbhit():
                key = getwch()
                if key in TASK_ABORT_KEYS:
                    proc.terminate()
                    print("[TTS] 已终止本次语音播报。")
                    return
            time.sleep(0.05)
    except Exception as exc:
        print(f"[TTS] 语音播报失败：{exc}")


def _speak_linux(text: str) -> None:
    # 用 edge-tts（微软神经网络 TTS，中文发音自然）生成 mp3 再播放。
    # 没有 edge-tts 或播放器时退化为仅打印，不阻塞主流程。
    import shutil
    import tempfile

    edge_tts_bin = shutil.which("edge-tts")
    if edge_tts_bin is None:
        print(f"[TTS] 未找到 edge-tts，跳过播报：{text}")
        return
    # 播放器优先级：paplay（PulseAudio 原生，最稳）> ffplay > 系统 mpg123。
    # 注意：conda 里的 mpg123 1.32.9 的 pulse 模块加载失败，不能用它。
    if shutil.which("paplay"):
        player = "paplay"
    elif shutil.which("ffplay"):
        player = "ffplay"
    elif os.path.exists("/usr/bin/mpg123"):
        player = "mpg123"
    else:
        print(f"[TTS] 未找到播放器，跳过播报：{text}")
        return

    out_path = Path(tempfile.gettempdir()) / "xlerobot_tts.mp3"
    try:
        proc = subprocess.run(
            [
                edge_tts_bin,
                "--voice",
                "zh-CN-XiaoxiaoNeural",
                "--text",
                text,
                "--write-media",
                str(out_path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
        )
        if proc.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
            detail = (proc.stderr or proc.stdout).decode("utf-8", "replace").strip()
            print(f"[TTS] edge-tts 生成失败：{detail}")
            print(f"[TTS] {text}")
            return
        # mpg123 明确走 PulseAudio 默认输出设备；ffplay 自动退出且不弹窗口。
        if player == "paplay":
            play_cmd = ["paplay", str(out_path)]
        elif player == "ffplay":
            play_cmd = ["ffplay", "-autoexit", "-nodisp", "-loglevel", "quiet", str(out_path)]
        else:
            play_cmd = ["/usr/bin/mpg123", "-o", "pulse", str(out_path)]
        proc = subprocess.Popen(
            play_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # 同步等待播放完成，支持按空格中止，避免上一句还没念完就进入下一轮监听。
        deadline = time.perf_counter() + 120.0
        while proc.poll() is None:
            if time.perf_counter() >= deadline:
                proc.terminate()
                print("[TTS] 语音播报超时，已跳过剩余内容。")
                return
            if kbhit():
                key = getwch()
                if key in TASK_ABORT_KEYS:
                    proc.terminate()
                    print("[TTS] 已终止本次语音播报。")
                    return
            time.sleep(0.05)
    except Exception as exc:
        print(f"[TTS] 语音播报失败：{exc}")


def clean_text_for_tts(text: str) -> str:
    """把 Agent/工具返回值清洗成适合语音播报的中文。

    终端仍然打印原始结果；这里只避免 TTS 念出工具名、下划线、路径等技术细节。
    """
    cleaned = str(text)
    if "执行结果：" in cleaned:
        spoken_part, execution_part = cleaned.split("执行结果：", 1)
        spoken_part = spoken_part.strip()
        execution_part = execution_part.strip()
        if spoken_part:
            cleaned = spoken_part + "。动作已完成。"
        else:
            cleaned = execution_part
    cleaned = cleaned.replace("TASK_COMPLETE:", "")
    cleaned = re.sub(r"本次.*?(?:不能|不要).*?(?:。|$)", "", cleaned)
    cleaned = re.sub(r"下一步只能.*?(?:。|$)", "", cleaned)
    cleaned = re.sub(r"Calling .*", "", cleaned)
    cleaned = re.sub(r"[A-Za-z]:\\[^\s，。；;]+", "", cleaned)
    cleaned = re.sub(r"C:\\[^\s，。；;]+", "", cleaned)
    cleaned = re.sub(r"E:\\[^\s，。；;]+", "", cleaned)
    cleaned = re.sub(r"`[^`]+`", "", cleaned)
    cleaned = re.sub(r"\b[a-zA-Z]+_[a-zA-Z0-9_]+(?:\.json)?\b", "这个动作", cleaned)
    cleaned = re.sub(r"\b(play_recorded_motion|record_motion|list_recorded_motions|finish_task|look_up|look_down|turn_head_left|turn_head_right|center_head)\b", "", cleaned)
    cleaned = cleaned.replace("_", "")
    cleaned = cleaned.replace("motion_name", "动作名")
    cleaned = cleaned.replace("arm_side", "手臂")
    cleaned = cleaned.replace("fps", "帧率")
    cleaned = cleaned.replace(".json", "")
    cleaned = re.sub(r"发送帧数=\d+", "", cleaned)
    cleaned = re.sub(r"帧数=\d+", "", cleaned)
    cleaned = re.sub(r"这个动作=both", "双臂", cleaned)
    cleaned = cleaned.replace("动作序列", "动作")
    cleaned = re.sub(r"\s+", " ", cleaned)
    # 去掉 Markdown 标记，避免 TTS 把 **、#、-、` 等符号念出来。
    cleaned = cleaned.replace("**", "")
    cleaned = cleaned.replace("`", "")
    cleaned = cleaned.replace("#", "")
    cleaned = re.sub(r"[-*]\s+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"[，,；;：:]\s*[，,；;：:。]+", "，", cleaned)
    cleaned = re.sub(r"[，,；;：:]\s*。", "。", cleaned)
    cleaned = cleaned.replace("，。", "。")
    return cleaned.strip(" ，,。；;：:")


def is_stop_command(text: str) -> bool:
    return any(word in text for word in STOP_WORDS)


def parse_voice_seconds(raw: str, default_seconds: float) -> float:
    parts = raw.split()
    if len(parts) < 2:
        return default_seconds
    try:
        return max(1.0, min(float(parts[1]), 30.0))
    except ValueError:
        return default_seconds


def is_voice_command(raw: str) -> bool:
    head = raw.split(maxsplit=1)[0].lower()
    return head in {"v", "voice", "语音"}


def is_auto_voice_command(raw: str) -> bool:
    return raw.lower() in {"vv", "auto", "listen"} or raw in {"连续", "连续语音", "自动语音"}


def is_keyboard_control_command(text: str) -> bool:
    normalized = normalize_asr_text(text).replace(" ", "").lower()
    return normalized in {
        "键盘控制",
        "键盘模式",
        "键盘遥控",
        "进入键盘控制",
        "进入键盘模式",
        "切换到键盘控制",
        "切换到键盘模式",
        "开始键盘控制",
        "手动控制",
        "手动遥控",
        "进入手动控制",
        "切换到手动控制",
        "keyboard",
        "teleop",
        "keyboardcontrol",
    }


def is_vr_control_command(text: str) -> bool:
    normalized = normalize_asr_text(text).replace(" ", "").upper()
    return normalized in {
        "VR",
        "VR控制",
        "VR模式",
        "进入VR控制",
        "切换到VR控制",
        "虚拟现实控制",
        "虚拟现实模式",
        "进入虚拟现实控制",
        "切换到虚拟现实控制",
        "VRCONTROL",
        "VRMODE",
        "VIRTUALREALITYCONTROL",
    }


class ModeRecoveryError(RuntimeError):
    """控制模式结束后无法安全恢复主程序。"""


def drain_console_input() -> None:
    """清掉 pynput/模式退出键可能遗留在 Windows 控制台中的字符。"""
    while kbhit():
        getwch()


def disconnect_servo_for_mode(
    servo_controller: ClientServoControler,
    mode_name: str,
) -> None:
    """停止底盘并确保主 ZMQ client 已完全断开。"""
    try:
        servo_controller.disconnect()
    except Exception as exc:
        print(f"[{mode_name}] 正常断开主 client 失败，尝试强制关闭连接：{exc}")
        try:
            if servo_controller.robot.is_connected:
                servo_controller.robot.disconnect()
        except Exception as force_exc:
            raise ModeRecoveryError(
                f"进入{mode_name}前无法断开主 client：{force_exc}"
            ) from force_exc

    if servo_controller.robot.is_connected:
        raise ModeRecoveryError(
            f"进入{mode_name}前主 client 仍处于连接状态，已取消切换。"
        )


def reconnect_servo_after_host(
    servo_controller: ClientServoControler,
    mode_name: str,
) -> None:
    try:
        servo_controller.connect()
    except Exception as exc:
        try:
            if servo_controller.robot.is_connected:
                servo_controller.robot.disconnect()
        except Exception:
            pass
        raise ModeRecoveryError(f"{mode_name}结束后主 client 重连失败：{exc}") from exc


def wait_for_manual_arm_reset(servo_controller: ClientServoControler) -> None:
    """VR 结束后松开手臂扭矩，等用户手动摆回安全位置再按当前位置锁住。"""
    print("[手臂] 正在释放双臂扭矩，请用手把机械臂摆回安全位置。")
    try:
        servo_controller.release_arm_for_recording("both")
    except Exception as exc:
        print(f"[手臂] 释放扭矩失败：{exc}")
        print("[手臂] 若手臂仍然发力，请重启树莓派 host 后再试。")
        return

    print("[手臂] 扭矩已释放。摆好后按 Enter，按当前位置恢复扭矩。")
    print("[手臂] 直接 Ctrl+C 可保持松开，回到任务输入。")
    try:
        input()
    except EOFError:
        print("[手臂] 未等到确认，手臂保持可手动摆动。")
        return
    except KeyboardInterrupt:
        print("\n[手臂] 已跳过恢复扭矩，手臂保持可手动摆动。")
        return

    try:
        servo_controller.restore_arm_after_recording("both")
        print("[手臂] 已按当前手动位置恢复扭矩。")
    except Exception as exc:
        print(f"[手臂] 恢复扭矩失败：{exc}")


def run_keyboard_control_mode(
    servo_controller: ClientServoControler,
    remote_controller: RemoteServiceController,
) -> None:
    print("[键盘] 进入键盘控制模式。键盘窗口内按 ESC 返回当前语音/文字 Agent。")
    print(f"[键盘] 启动：{KEYBOARD_CLIENT_PATH}")
    disconnect_servo_for_mode(servo_controller, "键盘")

    restore_agent = True
    mode_error: Exception | None = None
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(KEYBOARD_CLIENT_PATH),
                "--ip",
                ROBOT_IP,
            ],
            check=False,
        )
        if completed.returncode:
            print(f"[键盘] 键盘客户端退出码：{completed.returncode}")
    except KeyboardInterrupt:
        # 用户要退出整个主程序时，不重启/重连；由 main 的最终清理统一收尾。
        restore_agent = False
        raise
    except Exception as exc:
        mode_error = exc
    finally:
        drain_console_input()
        if restore_agent:
            print("[键盘] 已退出键盘控制模式，正在确认 host 并恢复主 client。")
            try:
                remote_controller.ensure_host_running(timeout=HOST_START_TIMEOUT)
            except Exception as exc:
                raise ModeRecoveryError(f"键盘模式结束后 host 恢复失败：{exc}") from exc
            reconnect_servo_after_host(servo_controller, "键盘模式")

    if mode_error is not None:
        print(f"[键盘] 键盘客户端异常：{mode_error}")
        print("[键盘] host 与主 client 已恢复，可以继续使用语音/文字控制。")


def wait_for_vr_exit(remote_controller: RemoteServiceController) -> None:
    """等待 Esc/q，同时监测远程 VR 进程是否意外退出。"""
    print(
        "[VR] 机械臂控制链已就绪。请在头显打开 "
        f"https://{remote_controller.host}:8443，点击 Start Controller Tracking，"
        "进入 VR 后按住侧握键（Grip）移动对应机械臂。"
    )
    print("[VR] 本机按 Esc 或 q 返回语音/文字 Agent。")
    next_remote_check = 0.0
    last_vr_connected: bool | None = None
    headset_wait_started = time.monotonic()
    certificate_hint_shown = False
    while True:
        if kbhit():
            key = getwch()
            if key in VR_EXIT_KEYS:
                return
            if key in {"\x00", "\xe0"} and kbhit():
                getwch()

        now = time.monotonic()
        if now >= next_remote_check:
            if not remote_controller.remote_process_running("vr"):
                raise RuntimeError("远程 VR 服务意外退出。")
            status = remote_controller.probe_vr_status()
            if status is not None:
                runtime_ok = all(
                    status.get(key) is True
                    for key in VR_REQUIRED_STATUS_FLAGS
                )
                if not runtime_ok:
                    raise RuntimeError(f"VR 控制链运行状态异常：{status}")

                vr_connected = status.get("vrConnected") is True
                if vr_connected != last_vr_connected:
                    if vr_connected:
                        print("[VR] 已检测到头显控制器连接，可以按住 Grip 控制机械臂。")
                    else:
                        print("[VR] 正在等待头显进入 WebXR 并连接 8442 WSS...")
                    last_vr_connected = vr_connected

                if (
                    not vr_connected
                    and not certificate_hint_shown
                    and now - headset_wait_started >= 10.0
                ):
                    print(
                        "[VR] 仍未检测到头显：请确认已点击 Start Controller Tracking、"
                        "头显信任当前 HTTPS 证书，并且 8442 端口可达。"
                    )
                    certificate_hint_shown = True
            next_remote_check = now + 1.0
        time.sleep(0.05)


def wait_for_local_vr_exit(process: subprocess.Popen) -> None:
    """等待 Esc/q，或 telegrip 自己退出。Ctrl+C 由调用方当成退出 VR。"""
    print("[VR] 本机按 Esc、q 或 Ctrl+C 返回语音/文字 Agent。")
    with cbreak_stdin():
        while True:
            if process.poll() is not None:
                return
            if kbhit():
                key = getwch()
                if key in VR_EXIT_KEYS:
                    return
                if key in {"\x00", "\xe0"} and kbhit():
                    getwch()
            time.sleep(0.05)


def stop_local_vr_process(
    process: subprocess.Popen | None,
    timeout: float = VR_STOP_WAIT_SECONDS,
) -> None:
    """结束本地 telegrip 进程组，避免 VR 退出后还占着控制权。"""
    if process is None or process.poll() is not None:
        return

    def _signal(sig: int, fallback) -> None:
        if sys.platform != "win32":
            try:
                os.killpg(process.pid, sig)
                return
            except (ProcessLookupError, PermissionError, OSError):
                pass
        try:
            fallback()
        except OSError:
            pass

    _signal(signal.SIGINT, process.terminate)
    try:
        process.wait(timeout=timeout)
        return
    except subprocess.TimeoutExpired:
        print("[VR] telegrip 未在时限内退出，正在强制结束。")

    kill_signal = getattr(signal, "SIGKILL", signal.SIGTERM)
    _signal(kill_signal, process.kill)
    try:
        process.wait(timeout=3)
    except Exception:
        pass


def run_vr_control_mode(
    servo_controller: ClientServoControler,
    remote_controller: RemoteServiceController,
) -> None:
    print("[VR] 正在启动本地 VR 控制（client 模式，通过 ZMQ 连 host）...")
    disconnect_servo_for_mode(servo_controller, "VR")

    process: subprocess.Popen | None = None
    mode_error: Exception | None = None
    try:
        process = subprocess.Popen(
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
        wait_for_local_vr_exit(process)
    except KeyboardInterrupt:
        print("\n[VR] 收到 Ctrl+C，正在退出 VR 并返回语音/文字 Agent。")
    except Exception as exc:
        mode_error = exc
    finally:
        stop_local_vr_process(process)
        drain_console_input()
        print("[VR] 已退出 VR 控制，正在恢复主 client...")
        try:
            reconnect_servo_after_host(servo_controller, "VR 模式")
        except Exception as exc:
            raise ModeRecoveryError(f"退出 VR 后恢复主 client 失败：{exc}") from exc
        wait_for_manual_arm_reset(servo_controller)

    if process is not None and process.returncode:
        print(f"[VR] telegrip 退出码：{process.returncode}")
    if mode_error is not None:
        print(f"[VR] VR 控制异常：{mode_error}")
        print("[VR] 主 client 已恢复，可以继续使用语音/文字控制。")
    else:
        print("[VR] 已退出 VR 控制并恢复主程序。")


def wait_for_auto_listen_exit(timeout_s: float) -> bool:
    """自动监听间隔中检查键盘退出。

    返回 True 表示退出自动监听，回到终端输入。
    """
    deadline = time.perf_counter() + max(0.0, timeout_s)
    print("[语音] 自动监听中：按 q 或 Esc 回到终端输入；空格丢弃本轮并回待命。")
    while time.perf_counter() < deadline:
        if kbhit():
            key = getwch()
            if key in AUTO_LISTEN_EXIT_KEYS:
                print("[语音] 已退出自动监听，回到终端输入。")
                return True
            if key in TASK_ABORT_KEYS:
                print("[语音] 已丢弃本轮并回到终端输入。")
                return True
        time.sleep(0.05)
    return False


def wait_after_transcription(timeout_s: float = 2.0) -> str:
    """识别后给用户一个短窗口决定是否执行。

    返回：
    - execute：继续执行识别结果。
    - rerecord：重新录音。
    - standby：丢弃本轮，回到待命/终端输入。
    """
    deadline = time.perf_counter() + max(0.0, timeout_s)
    print("[语音] 按 r 重新录音；按空格丢弃本轮回待命；不按键则继续执行。")
    while time.perf_counter() < deadline:
        if not kbhit():
            time.sleep(0.05)
            continue
        key = getwch()
        if key in RERECORD_KEYS:
            print("[语音] 重新录音。")
            return "rerecord"
        if key in TASK_ABORT_KEYS:
            print("[语音] 已丢弃本轮，回到待命。")
            return "standby"
        if key in AUTO_LISTEN_EXIT_KEYS:
            print("[语音] 已退出语音监听，回到终端输入。")
            return "standby"
    return "execute"


class TaskAbortMonitor:
    """任务执行期间监听一键终止。

    只在机器人执行任务时启动；按空格会设置中止标记，
    并尽量立刻发送底盘停止，随后主流程回到待命。
    """

    def __init__(self, servo_controller: ClientServoControler) -> None:
        self.servo_controller = servo_controller
        self._stop = threading.Event()
        self._aborted = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self):
        print("[终止] 任务执行中按空格终止本次任务并回到待命。")
        self._thread = threading.Thread(target=self._watch_keys, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.2)

    def should_stop(self) -> bool:
        return self._aborted.is_set()

    def _watch_keys(self) -> None:
        while not self._stop.is_set():
            if not kbhit():
                time.sleep(0.05)
                continue
            key = getwch()
            if key not in TASK_ABORT_KEYS:
                continue
            self._aborted.set()
            print("\n[终止] 已收到终止键，正在停止本次任务并回到待命。")
            try:
                self.servo_controller.request_task_abort()
            except Exception as exc:
                print(f"[终止] 发送停止指令时出现警告：{exc}")
            return


def transcribe_recorded_voice(asr: LocalWhisperAsr, wav_path: Path, rms: float, silence_threshold: float) -> str:
    if rms < silence_threshold:
        print(f"[语音] 音量过低，判定为空录音：{rms:.4f} < {silence_threshold:.4f}")
        return ""
    # 只有音量明显高于静音阈值时才允许关 VAD 重试。
    # 没说话时环境噪声虽然可能略高于阈值，但 VAD 会整段过滤；
    # 若再关 VAD 强制识别，Whisper 会把纯噪声幻觉成乱字，所以这里收紧。
    allow_no_vad_retry = rms >= silence_threshold * 3.0
    return asr.transcribe(wav_path, allow_no_vad_retry=allow_no_vad_retry)


def execute_task(
    task: str,
    agent,
    servo_controller: ClientServoControler,
    main_camera: ClientRobotCamera,
    remote_controller: RemoteServiceController,
    show_raw_output: bool,
    enable_tts: bool,
    speak_empty: bool = True,
) -> bool:
    if not task:
        print("[语音] 没听清，本轮不执行。")
        if enable_tts and speak_empty:
            speak_windows_sapi("我没听清，请再说一遍。")
        return True

    if task.lower() in {"q", "quit", "exit"} or task in {"退出", "结束"}:
        return False

    if any(word in task for word in STOP_LISTEN_WORDS):
        print("[语音] 已退出连续监听。")
        if enable_tts:
            speak_windows_sapi("已退出连续监听。")
        return False

    if is_keyboard_control_command(task):
        if enable_tts:
            speak_windows_sapi("进入键盘控制。按 ESC 返回。")
        run_keyboard_control_mode(servo_controller, remote_controller)
        if enable_tts:
            speak_windows_sapi("已返回智能体控制。")
        return True

    if is_vr_control_command(task):
        if enable_tts:
            speak_windows_sapi("进入 VR 控制。按 ESC、q 或 Ctrl+C 返回。")
        run_vr_control_mode(servo_controller, remote_controller)
        if enable_tts:
            speak_windows_sapi("已返回智能体控制。")
        return True

    if is_stop_command(task):
        servo_controller.robot.send_action({"x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0})
        print("[安全] 已发送底盘停止。")
        if enable_tts:
            speak_windows_sapi("已停止。")
        return True

    task_t0 = time.perf_counter()
    with TaskAbortMonitor(servo_controller) as abort_monitor:
        result = run_one_task(
            agent,
            servo_controller,
            main_camera,
            task,
            show_raw_output,
            should_stop=abort_monitor.should_stop,
        )
    print(f"[耗时] 执行链路 {time.perf_counter() - task_t0:.2f}s")
    if enable_tts and "已终止" not in result:
        tts_t0 = time.perf_counter()
        speak_windows_sapi(result)
        print(f"[耗时] 语音播报 {time.perf_counter() - tts_t0:.2f}s")
    return True


def main() -> None:
    configure_runtime_output()
    args = parse_args()
    require_module("robocrew")
    ensure_voice_dependencies()
    if args.list_audio_devices:
        print_audio_devices()
        return

    os.environ.setdefault("OPENAI_API_KEY", OPENAI_COMPATIBLE_API_KEY)
    os.environ.setdefault("OPENAI_API_BASE", OPENAI_COMPATIBLE_API_BASE)
    os.environ.setdefault("OPENAI_BASE_URL", OPENAI_COMPATIBLE_API_BASE)

    remote_controller = get_remote_service_controller(host=ROBOT_IP)
    servo_controller: ClientServoControler | None = None
    try:
        try:
            remote_controller.ensure_host_running(timeout=HOST_START_TIMEOUT)
        except Exception as exc:
            print(f"[连接检查] 无法自动启动或连接 xlerobot_host：{exc}")
            return

        asr = create_asr(args.model, args.device, args.asr_backend)
        print(
            f"[语音] 静音阈值={args.silence_threshold:.4f}；"
            f"输入设备={args.input_device or '系统默认'}"
        )
        servo_controller = ClientServoControler(remote_ip=ROBOT_IP, robot_id=ROBOT_ID)
        servo_controller.connect()
        main_camera = ClientRobotCamera(
            servo_controller=servo_controller,
            camera_key=args.camera_key,
        )
        agent = build_agent(servo_controller, main_camera)

        print("进入文字/语音混合连续对话模式。")
        print("直接输入文字会立刻执行；输入 v 再按 Enter 开始语音录制；输入 v 10 可录 10 秒。")
        print("输入 vv 进入自动连续语音监听；输入 quit/退出 结束。")
        print("输入或语音说“键盘控制/键盘模式/手动控制”进入键盘遥控，按 ESC 返回。")
        print("输入或语音说“VR控制/VR模式/虚拟现实控制”进入 VR，按 Esc/q 或 Ctrl+C 返回。")
        print("最高优先级：执行任务或语音播报过程中按空格，终止本轮并回到待命。")
        print("语音识别后按 r 可重新录音；连续监听间隔按 q/Esc 返回终端输入。")
        print("安全词：停止、别动、停下、不要动。")

        while True:
            raw = input("任务或 v> ").strip()
            if raw.lower() in {"q", "quit", "exit"} or raw in {"退出", "结束"}:
                break
            if not raw:
                continue

            if is_auto_voice_command(raw):
                print("[语音] 进入自动连续监听。说“停止监听”退出自动监听。")
                print("[语音] 每轮识别后可按 r 重录，按空格丢弃本轮回待命，按 q/Esc 退出连续监听。")
                if not args.no_tts:
                    speak_windows_sapi("已进入连续监听。")
                while True:
                    if wait_for_auto_listen_exit(args.auto_pause_seconds):
                        break
                    while True:
                        wav_path = VOICE_DIR / f"voice_{int(time.time())}.wav"
                        rms = record_wav(wav_path, args.seconds, args.sample_rate, args.input_device)
                        task = transcribe_recorded_voice(asr, wav_path, rms, args.silence_threshold)
                        print(f"[识别] {task}")
                        decision = wait_after_transcription()
                        if decision == "rerecord":
                            continue
                        if decision == "standby":
                            task = ""
                        break
                    if not task:
                        break
                    should_continue = execute_task(
                        task,
                        agent,
                        servo_controller,
                        main_camera,
                        remote_controller,
                        args.show_raw_output,
                        enable_tts=not args.no_tts,
                        speak_empty=False,
                    )
                    if not should_continue:
                        break
                continue

            if is_voice_command(raw):
                seconds = parse_voice_seconds(raw, args.seconds)
                while True:
                    wav_path = VOICE_DIR / f"voice_{int(time.time())}.wav"
                    rms = record_wav(wav_path, seconds, args.sample_rate, args.input_device)
                    task = transcribe_recorded_voice(asr, wav_path, rms, args.silence_threshold)
                    print(f"[识别] {task}")
                    decision = wait_after_transcription()
                    if decision == "rerecord":
                        continue
                    if decision == "standby":
                        task = ""
                    break
                if not task:
                    continue
            else:
                task = raw
                print(f"[文字] {task}")

            should_continue = execute_task(
                task,
                agent,
                servo_controller,
                main_camera,
                remote_controller,
                args.show_raw_output,
                enable_tts=not args.no_tts,
            )
            if not should_continue:
                break
    except ModeRecoveryError as exc:
        print(f"[致命] {exc}")
        print("[致命] 主 client 未安全恢复，正在退出并清理远程服务。")
    except KeyboardInterrupt:
        print("\n收到 Ctrl+C，正在退出并清理远程服务。")
    finally:
        if servo_controller is not None:
            try:
                servo_controller.disconnect()
            except Exception as exc:
                print(f"[退出清理] 主 client 断开失败：{exc}")
                try:
                    if servo_controller.robot.is_connected:
                        servo_controller.robot.disconnect()
                except Exception as force_exc:
                    print(f"[退出清理] 主 client 强制关闭也失败：{force_exc}")
        try:
            shutdown_remote_services()
        except Exception as exc:
            print(f"[退出清理] 远程 host/VR 清理失败：{exc}")


if __name__ == "__main__":
    main()
