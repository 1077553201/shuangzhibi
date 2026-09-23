"""XLeRobot Web 控制台。"""

from __future__ import annotations

import math
import wave
from pathlib import Path

import numpy as np
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from robot_session import RobotSession


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
UPLOAD_DIR = BASE_DIR / "outputs" / "uploads"

app = FastAPI(title="XLeRobot Web Control")
session = RobotSession()


class TaskRequest(BaseModel):
    text: str


class PlayMotionRequest(BaseModel):
    motion_name: str
    speed: float = 1.0


class RecordMotionRequest(BaseModel):
    motion_name: str
    arm_side: str = "right"
    seconds: float = 5.0
    fps: float = 20.0


@app.on_event("shutdown")
def shutdown() -> None:
    session.shutdown()


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/status")
def api_status():
    return session.status()


@app.get("/api/logs")
def api_logs(after: int = 0):
    return {"lines": session.log.read_since(after)}


@app.post("/api/task")
def api_task(req: TaskRequest):
    text = req.text.strip()
    if not text:
        return {"ok": False, "message": "任务不能为空。"}
    return session.submit_task(text)


@app.post("/api/abort")
def api_abort():
    session.abort()
    return {"ok": True, "message": "已请求终止本轮任务。"}


@app.get("/api/motions")
def api_motions():
    return session.motions()


@app.post("/api/motions/play")
def api_play_motion(req: PlayMotionRequest):
    return session.submit_play_motion(req.motion_name, req.speed)


@app.post("/api/motions/record")
def api_record_motion(req: RecordMotionRequest):
    return session.submit_record_motion(req.motion_name, req.arm_side, req.seconds, req.fps)


@app.get("/api/camera/preview")
def api_camera_preview():
    image_bytes = session.capture_preview()
    return Response(content=image_bytes, media_type="image/jpeg")


def wav_rms(path: Path) -> float:
    with wave.open(str(path), "rb") as wav:
        frames = wav.readframes(wav.getnframes())
        if wav.getsampwidth() != 2:
            return 0.0
    samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32767.0
    if samples.size == 0:
        return 0.0
    return float(math.sqrt(float(np.mean(np.square(samples)))))


@app.post("/api/asr")
async def api_asr(audio: UploadFile = File(...)):
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    suffix = Path(audio.filename or "voice.wav").suffix or ".wav"
    path = UPLOAD_DIR / f"voice_{len(list(UPLOAD_DIR.glob('voice_*'))):06d}{suffix}"
    path.write_bytes(await audio.read())
    rms = wav_rms(path)
    text = session.transcribe_wav(path, rms)
    return {"text": text, "rms": rms}


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
