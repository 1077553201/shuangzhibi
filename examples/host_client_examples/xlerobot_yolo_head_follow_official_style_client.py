"""XLeRobot host/client YOLO 头部跟踪，按官方 SO100 YOLO 示例结构改写。

这个脚本刻意保持简单：

1. Windows client 从树莓派 host 获取摄像头画面。
2. YOLO 检测目标。
3. 计算目标中心相对画面中心的像素偏差 dx/dy。
4. 像官方示例一样用 K 系数把偏差映射成目标位置增量。
5. 只发送 head_motor_1/head_motor_2，不碰双臂和底盘。

它不做复杂积分、不做运行时热键调参，便于看清楚链路。
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass

import cv2
import numpy as np
from ultralytics import YOLO

from lerobot.robots.xlerobot import XLerobotClient, XLerobotClientConfig


@dataclass
class Detection:
    label: str
    confidence: float
    xyxy: tuple[int, int, int, int]

    @property
    def center(self) -> tuple[int, int]:
        x1, y1, x2, y2 = self.xyxy
        return (x1 + x2) // 2, (y1 + y2) // 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="官方风格 XLeRobot YOLO 头部跟踪 client。")
    parser.add_argument("--ip", default="192.168.10.239", help="树莓派 host IP")
    parser.add_argument("--id", default="my_xlerobot_pc", help="机器人 ID")
    parser.add_argument("--camera", default="head", help="摄像头名：head/camera_0/camera_2")
    parser.add_argument("--target", default="bottle", help="目标类别；多个用逗号分隔；all 表示任意类别")
    parser.add_argument("--model", default="yolo11n.pt", help="YOLO 模型权重")
    parser.add_argument("--conf", type=float, default=0.2, help="检测置信度阈值")
    parser.add_argument("--imgsz", type=int, default=640, help="YOLO 输入尺寸")
    parser.add_argument("--fps", type=float, default=8.0, help="控制循环频率")
    parser.add_argument("--frame-color", choices=["rgb", "bgr"], default="rgb", help="host 图像通道顺序")
    parser.add_argument("--kx", type=float, default=0.015, help="dx 像素偏差到 head_motor_1 的系数")
    parser.add_argument("--ky", type=float, default=0.015, help="dy 像素偏差到 head_motor_2 的系数")
    parser.add_argument("--deadzone-px", type=int, default=45, help="中心死区，单位像素")
    parser.add_argument("--max-step", type=float, default=2.0, help="每次最大目标变化")
    parser.add_argument("--soft-limit", type=float, default=90.0, help="头部软限位")
    parser.add_argument("--center-head", action="store_true", help="启动后先发送头部回中位")
    parser.add_argument("--dry-run", action="store_true", help="只显示和打印，不发送动作")
    return parser.parse_args()


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def prepare_frame(frame: np.ndarray, frame_color: str) -> np.ndarray:
    if frame_color == "rgb":
        return cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
    return frame


def pick_frame(obs: dict, camera_name: str) -> tuple[str | None, np.ndarray | None]:
    if camera_name in obs and hasattr(obs[camera_name], "shape"):
        return camera_name, obs[camera_name]
    for name, value in obs.items():
        if hasattr(value, "shape"):
            return name, value
    return None, None


def choose_detection(result, targets: set[str]) -> Detection | None:
    if result is None or result.boxes is None or len(result.boxes) == 0:
        return None

    candidates: list[Detection] = []
    for box in result.boxes:
        label = str(result.names[int(box.cls[0])])
        if targets and label not in targets:
            continue
        x1, y1, x2, y2 = map(int, box.xyxy[0])
        candidates.append(Detection(label=label, confidence=float(box.conf[0]), xyxy=(x1, y1, x2, y2)))

    if not candidates:
        return None
    return max(candidates, key=lambda item: item.confidence)


def compute_official_style_action(
    obs: dict,
    detection: Detection,
    frame_shape: tuple[int, int, int],
    kx: float,
    ky: float,
    deadzone_px: int,
    max_step: float,
    soft_limit: float,
) -> tuple[dict[str, float], dict[str, float]]:
    """模仿官方示例：用像素偏差乘 K，得到目标位置增量。"""
    height, width = frame_shape[:2]
    cx, cy = detection.center
    dx = cx - width // 2
    dy = cy - height // 2

    pan_current = float(obs.get("head_motor_1.pos", 0.0))
    tilt_current = float(obs.get("head_motor_2.pos", 0.0))

    pan_delta = 0.0 if abs(dx) < deadzone_px else clamp(kx * dx, -max_step, max_step)
    tilt_delta = 0.0 if abs(dy) < deadzone_px else clamp(ky * dy, -max_step, max_step)

    action = {
        "head_motor_1.pos": clamp(pan_current + pan_delta, -soft_limit, soft_limit),
        "head_motor_2.pos": clamp(tilt_current + tilt_delta, -soft_limit, soft_limit),
    }
    info = {
        "dx": float(dx),
        "dy": float(dy),
        "pan_current": pan_current,
        "tilt_current": tilt_current,
        "pan_delta": pan_delta,
        "tilt_delta": tilt_delta,
    }
    return action, info


def draw(frame: np.ndarray, detection: Detection | None, info: dict[str, float] | None, dry_run: bool) -> np.ndarray:
    display = frame.copy()
    height, width = display.shape[:2]
    center = (width // 2, height // 2)
    cv2.line(display, (center[0] - 20, center[1]), (center[0] + 20, center[1]), (80, 220, 80), 1)
    cv2.line(display, (center[0], center[1] - 20), (center[0], center[1] + 20), (80, 220, 80), 1)

    if detection is not None:
        x1, y1, x2, y2 = detection.xyxy
        cx, cy = detection.center
        cv2.rectangle(display, (x1, y1), (x2, y2), (60, 180, 255), 2)
        cv2.circle(display, (cx, cy), 5, (60, 180, 255), -1)
        cv2.line(display, center, (cx, cy), (60, 180, 255), 1)
        cv2.putText(
            display,
            f"{detection.label} {detection.confidence:.2f}",
            (x1, max(22, y1 - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (60, 180, 255),
            2,
        )

    mode = "DRY RUN" if dry_run else "SENDING ACTION"
    line = "no detection"
    if info is not None:
        line = f"dx={info['dx']:.0f} dy={info['dy']:.0f} step=({info['pan_delta']:.2f},{info['tilt_delta']:.2f})"
    cv2.putText(display, f"{mode} | q/ESC exit", (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)
    cv2.putText(display, line, (10, 52), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (255, 255, 255), 2)
    return display


def main() -> None:
    args = parse_args()
    targets = set() if args.target.strip().lower() in {"all", "*", ""} else {
        item.strip() for item in args.target.split(",") if item.strip()
    }

    robot = XLerobotClient(XLerobotClientConfig(remote_ip=args.ip, id=args.id))
    print(f"[MAIN] connecting to {args.ip} ...")
    robot.connect()
    print("[MAIN] connected.")

    model = YOLO(args.model)
    print(f"[YOLO] targets: {sorted(targets) if targets else 'all classes'}")
    print(f"[CTRL] kx={args.kx}, ky={args.ky}, deadzone_px={args.deadzone_px}, max_step={args.max_step}")

    if args.center_head and not args.dry_run:
        robot.send_action({"head_motor_1.pos": 0.0, "head_motor_2.pos": 0.0})
        print("[HEAD] center command sent.")
        time.sleep(1.0)

    period = 1.0 / max(args.fps, 1.0)

    try:
        while True:
            start = time.perf_counter()
            obs = robot.get_observation()
            camera_name, frame = pick_frame(obs, args.camera)
            if frame is None:
                print("[WARN] no camera frame in observation.")
                time.sleep(1.0)
                continue

            frame = prepare_frame(frame, args.frame_color)
            result = model.predict(frame, imgsz=args.imgsz, conf=args.conf, verbose=False)[0]
            detection = choose_detection(result, targets)

            action = None
            info = None
            if detection is not None:
                action, info = compute_official_style_action(
                    obs=obs,
                    detection=detection,
                    frame_shape=frame.shape,
                    kx=args.kx,
                    ky=args.ky,
                    deadzone_px=args.deadzone_px,
                    max_step=args.max_step,
                    soft_limit=args.soft_limit,
                )
                if not args.dry_run:
                    robot.send_action(action)
                print(
                    f"[TRACK] {detection.label}:{detection.confidence:.2f} "
                    f"dx={info['dx']:.0f} dy={info['dy']:.0f} "
                    f"head=({info['pan_current']:.2f},{info['tilt_current']:.2f}) "
                    f"step=({info['pan_delta']:.2f},{info['tilt_delta']:.2f}) "
                    f"target=({action['head_motor_1.pos']:.2f},{action['head_motor_2.pos']:.2f})"
                )
            else:
                print("[TRACK] no matching detection")

            cv2.imshow("XLeRobot YOLO Official Style Head Follow", draw(frame, detection, info, args.dry_run))
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break

            elapsed = time.perf_counter() - start
            if elapsed < period:
                time.sleep(period - elapsed)

    finally:
        cv2.destroyAllWindows()
        if robot.is_connected:
            robot.send_action({"x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0})
            robot.disconnect()
        print("[MAIN] disconnected.")


if __name__ == "__main__":
    main()
