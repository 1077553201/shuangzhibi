"""XLeRobot Windows client YOLO head tracking demo.

运行位置：Windows。

这个脚本通过 XLerobotClient 从树莓派 host 获取远程摄像头画面，
用 YOLO 检测目标物体，并可选地控制 head_motor_1/head_motor_2
让目标尽量回到画面中心。

默认只显示识别结果，不发送头部动作。确认识别稳定后，再加
`--enable-follow` 打开头部跟踪。
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
    parser = argparse.ArgumentParser(description="XLeRobot host/client YOLO 头部跟踪示例。")
    parser.add_argument("--ip", default="192.168.10.239", help="树莓派 host IP")
    parser.add_argument("--id", default="my_xlerobot_pc", help="机器人 ID，需和 host 使用同一套校准")
    parser.add_argument("--camera", default="head", help="使用哪一路远程摄像头，如 head/camera_0/camera_2")
    parser.add_argument("--target", default="bottle", help="目标类别，多个目标用逗号分隔；用 all 跟踪任意检测目标")
    parser.add_argument("--model", default="yolo11n.pt", help="YOLO 模型权重，默认使用较快的 yolo11n.pt")
    parser.add_argument("--conf", type=float, default=0.35, help="检测置信度阈值")
    parser.add_argument("--imgsz", type=int, default=640, help="YOLO 输入尺寸")
    parser.add_argument("--fps", type=float, default=12.0, help="主循环目标刷新率")
    parser.add_argument("--deadzone", type=float, default=0.16, help="中心死区，占画面宽/高比例")
    parser.add_argument("--pan-gain", type=float, default=8.0, help="水平误差到 head_motor_1 的目标修正系数")
    parser.add_argument("--tilt-gain", type=float, default=6.0, help="垂直误差到 head_motor_2 的目标修正系数")
    parser.add_argument("--max-step", type=float, default=3.0, help="每次发送时每个头部电机最大目标变化")
    parser.add_argument("--min-step", type=float, default=0.8, help="超出死区时的最小目标变化，用于克服头部电机小动作不响应")
    parser.add_argument("--command-interval", type=float, default=0.18, help="两次头部动作发送的最小间隔，延迟大时适当增大")
    parser.add_argument(
        "--target-source",
        choices=["observation", "integral"],
        default="observation",
        help="observation 使用当前观测做 P 控制；integral 为旧的连续累加模式",
    )
    parser.add_argument("--soft-limit", type=float, default=95.0, help="头部位置软限位，避免继续命令到 +/-100 之外")
    parser.add_argument("--center-head", action="store_true", help="启动后先发送 head_motor_1/2 到 0 的中位命令")
    parser.add_argument("--frame-color", choices=["rgb", "bgr"], default="rgb", help="host 回传图像的通道顺序；颜色红蓝反了就用 rgb")
    parser.add_argument("--enable-follow", action="store_true", help="启用头部跟踪动作发送")
    parser.add_argument("--debug", action="store_true", help="打印检测结果、误差和发送动作，便于调参")
    return parser.parse_args()


def pick_camera_frame(obs: dict, preferred_name: str) -> tuple[str, np.ndarray] | tuple[None, None]:
    """从 observation 里取一帧图像。"""
    if preferred_name in obs and hasattr(obs[preferred_name], "shape"):
        return preferred_name, obs[preferred_name]

    for name, value in obs.items():
        if hasattr(value, "shape"):
            return name, value

    return None, None


def detections_from_result(result, target_names: set[str]) -> list[Detection]:
    """从 YOLO 结果中提取候选检测框。"""
    detections: list[Detection] = []
    if result is None or result.boxes is None or len(result.boxes) == 0:
        return detections

    names = result.names
    for box in result.boxes:
        class_id = int(box.cls[0])
        label = str(names[class_id])
        if target_names and label not in target_names:
            continue

        x1, y1, x2, y2 = map(int, box.xyxy[0])
        confidence = float(box.conf[0])
        detections.append(Detection(label=label, confidence=confidence, xyxy=(x1, y1, x2, y2)))

    return detections


def choose_detection(result, target_names: set[str]) -> Detection | None:
    """从 YOLO 结果中选择一个目标：优先类别匹配，再选置信度最高的框。"""
    candidates = detections_from_result(result, target_names)
    if not candidates:
        return None

    return max(candidates, key=lambda det: det.confidence)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def prepare_frame_for_cv(frame: np.ndarray, frame_color: str) -> np.ndarray:
    """把 host 回传图像转换成 OpenCV/YOLO 常用的 BGR 顺序。"""
    if frame_color == "rgb":
        return cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
    return frame


def compute_head_action(
    detection: Detection,
    frame_shape: tuple[int, int, int],
    current_head: dict[str, float],
    deadzone: float,
    pan_gain: float,
    tilt_gain: float,
    max_step: float,
) -> tuple[dict[str, float], dict[str, float]]:
    """把目标中心偏差转换成头部两个电机的位置目标。"""
    height, width = frame_shape[:2]
    cx, cy = detection.center

    error_x = (cx - width / 2) / (width / 2)
    error_y = (cy - height / 2) / (height / 2)

    pan_current = float(current_head["head_motor_1.pos"])
    tilt_current = float(current_head["head_motor_2.pos"])

    pan_step = 0.0 if abs(error_x) < deadzone else clamp(pan_gain * error_x, -max_step, max_step)
    tilt_step = 0.0 if abs(error_y) < deadzone else clamp(tilt_gain * error_y, -max_step, max_step)

    action = {
        "head_motor_1.pos": pan_current + pan_step,
        "head_motor_2.pos": tilt_current + tilt_step,
    }
    debug_info = {
        "error_x": error_x,
        "error_y": error_y,
        "pan_step": pan_step,
        "tilt_step": tilt_step,
        "pan_current": pan_current,
        "tilt_current": tilt_current,
    }
    return action, debug_info


def apply_min_step(step: float, min_step: float, max_step: float) -> float:
    """让非零步长至少达到最小有效动作幅度。"""
    if step == 0.0 or min_step <= 0.0:
        return step

    direction = 1.0 if step > 0 else -1.0
    return direction * min(max(abs(step), min_step), max_step)


def draw_overlay(
    frame: np.ndarray,
    detection: Detection | None,
    enabled: bool,
    camera_name: str,
    status_line: str,
) -> np.ndarray:
    """绘制中心线、检测框和状态文字。"""
    display = frame.copy()
    height, width = display.shape[:2]
    center = (width // 2, height // 2)

    cv2.line(display, (center[0] - 18, center[1]), (center[0] + 18, center[1]), (80, 220, 80), 1)
    cv2.line(display, (center[0], center[1] - 18), (center[0], center[1] + 18), (80, 220, 80), 1)

    if detection is not None:
        x1, y1, x2, y2 = detection.xyxy
        cx, cy = detection.center
        cv2.rectangle(display, (x1, y1), (x2, y2), (60, 180, 255), 2)
        cv2.circle(display, (cx, cy), 5, (60, 180, 255), -1)
        cv2.line(display, center, (cx, cy), (60, 180, 255), 1)
        text = f"{detection.label} {detection.confidence:.2f}"
        cv2.putText(display, text, (x1, max(20, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (60, 180, 255), 2)

    status = "FOLLOW ON" if enabled else "VISION ONLY"
    cv2.putText(display, f"{camera_name} | {status} | q/ESC exit, f toggle", (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)
    cv2.putText(display, status_line, (10, 52), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)
    return display


def main() -> None:
    args = parse_args()
    if args.target.strip().lower() in {"all", "*", ""}:
        target_names = set()
    else:
        target_names = {name.strip() for name in args.target.split(",") if name.strip()}

    print(f"[MAIN] connecting to XLeRobot host at {args.ip} ...")
    robot = XLerobotClient(XLerobotClientConfig(remote_ip=args.ip, id=args.id))
    robot.connect()
    print("[MAIN] connected.")
    print(f"[YOLO] loading model: {args.model}")
    model = YOLO(args.model)
    print(f"[YOLO] targets: {sorted(target_names) if target_names else 'all classes'}")
    print(f"[SAFE] follow enabled: {args.enable_follow}")
    print("[KEYS] f:开关跟踪  p:水平反向  t:垂直反向  +/-:整体加减速  q/ESC:退出")

    follow_enabled = bool(args.enable_follow)
    pan_gain = args.pan_gain
    tilt_gain = args.tilt_gain
    max_step = args.max_step
    interval = 1.0 / max(args.fps, 1.0)
    last_debug_print = 0.0
    last_command_time = 0.0
    head_targets: dict[str, float] | None = None

    try:
        while True:
            start = time.perf_counter()
            obs = robot.get_observation()
            camera_name, frame = pick_camera_frame(obs, args.camera)
            if frame is None:
                print("[WARN] observation 中没有摄像头画面，请检查 host 的相机配置。")
                time.sleep(1.0)
                continue
            frame = prepare_frame_for_cv(frame, args.frame_color)

            if head_targets is None:
                head_targets = {
                    "head_motor_1.pos": float(obs.get("head_motor_1.pos", 0.0)),
                    "head_motor_2.pos": float(obs.get("head_motor_2.pos", 0.0)),
                }
                if args.center_head:
                    head_targets = {"head_motor_1.pos": 0.0, "head_motor_2.pos": 0.0}
                    robot.send_action(head_targets)
                    last_command_time = time.perf_counter()
                    print("[HEAD] center command sent: head_motor_1=0, head_motor_2=0")

            result = model.predict(frame, imgsz=args.imgsz, conf=args.conf, verbose=False)[0]
            detections = detections_from_result(result, target_names)
            detection = choose_detection(result, target_names)
            status_line = "no matching detection"
            debug_info: dict[str, float] = {}

            current_head = {
                "head_motor_1.pos": float(obs.get("head_motor_1.pos", 0.0)),
                "head_motor_2.pos": float(obs.get("head_motor_2.pos", 0.0)),
            }

            now = time.perf_counter()
            can_send_command = now - last_command_time >= args.command_interval

            if detection is not None and follow_enabled and can_send_command:
                base_head = head_targets if args.target_source == "integral" and head_targets is not None else current_head
                action, debug_info = compute_head_action(
                    detection=detection,
                    frame_shape=frame.shape,
                    current_head=base_head,
                    deadzone=args.deadzone,
                    pan_gain=pan_gain,
                    tilt_gain=tilt_gain,
                    max_step=max_step,
                )
                pan_step = apply_min_step(debug_info["pan_step"], args.min_step, max_step)
                tilt_step = apply_min_step(debug_info["tilt_step"], args.min_step, max_step)
                action = {
                    "head_motor_1.pos": clamp(base_head["head_motor_1.pos"] + pan_step, -args.soft_limit, args.soft_limit),
                    "head_motor_2.pos": clamp(base_head["head_motor_2.pos"] + tilt_step, -args.soft_limit, args.soft_limit),
                }
                debug_info["pan_step"] = pan_step
                debug_info["tilt_step"] = tilt_step
                head_targets.update(action)
                robot.send_action(action)
                last_command_time = now
                status_line = (
                    f"{detection.label} conf={detection.confidence:.2f} "
                    f"ex={debug_info['error_x']:.2f} ey={debug_info['error_y']:.2f} "
                    f"step=({debug_info['pan_step']:.2f},{debug_info['tilt_step']:.2f})"
                )
            elif detection is not None:
                status_line = f"{detection.label} conf={detection.confidence:.2f} detected, follow off"

            if args.debug and now - last_debug_print > 1.0:
                last_debug_print = now
                if detections:
                    summary = ", ".join(f"{det.label}:{det.confidence:.2f}" for det in detections[:5])
                else:
                    summary = "none"
                head_state = (
                    f"head=({float(obs.get('head_motor_1.pos', 0.0)):.2f}, "
                    f"{float(obs.get('head_motor_2.pos', 0.0)):.2f})"
                )
                target_state = ""
                if head_targets is not None:
                    target_state = (
                        f" target=({head_targets['head_motor_1.pos']:.2f}, "
                        f"{head_targets['head_motor_2.pos']:.2f})"
                    )
                print(
                    f"[DEBUG] detections={summary} | {status_line} | {head_state}{target_state} "
                    f"| gains=({pan_gain:.2f},{tilt_gain:.2f}) max_step={max_step:.2f}"
                )

            display = draw_overlay(frame, detection, follow_enabled, camera_name, status_line)
            cv2.imshow("XLeRobot YOLO Head Follow", display)

            raw_key = cv2.waitKeyEx(1)
            key = raw_key & 0xFF
            if key in (ord("q"), 27):
                break
            if key in (ord("f"), ord("F")):
                follow_enabled = not follow_enabled
                print(f"[SAFE] follow enabled: {follow_enabled}")
            elif key in (ord("p"), ord("P")):
                pan_gain *= -1
                print(f"[TUNE] pan_gain={pan_gain:.2f}")
            elif key in (ord("t"), ord("T")):
                tilt_gain *= -1
                print(f"[TUNE] tilt_gain={tilt_gain:.2f}")
            elif key in (ord("+"), ord("=")):
                max_step = min(max_step * 1.25, 10.0)
                pan_gain *= 1.15
                tilt_gain *= 1.15
                print(f"[TUNE] faster: pan_gain={pan_gain:.2f}, tilt_gain={tilt_gain:.2f}, max_step={max_step:.2f}")
            elif key in (ord("-"), ord("_")):
                max_step = max(max_step * 0.8, 0.1)
                pan_gain *= 0.85
                tilt_gain *= 0.85
                print(f"[TUNE] slower: pan_gain={pan_gain:.2f}, tilt_gain={tilt_gain:.2f}, max_step={max_step:.2f}")

            elapsed = time.perf_counter() - start
            if elapsed < interval:
                time.sleep(interval - elapsed)

    finally:
        cv2.destroyAllWindows()
        if robot.is_connected:
            robot.send_action({"x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0})
            robot.disconnect()
        print("[MAIN] disconnected.")


if __name__ == "__main__":
    main()
