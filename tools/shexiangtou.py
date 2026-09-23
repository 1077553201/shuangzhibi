"""树莓派头部摄像头排查工具。

运行位置：树莓派。

用途：
1. 单独测试某个 /dev/video*，不启动机器人 host。
2. 尝试不同 FOURCC、FPS、分辨率组合。
3. 找到能稳定读帧的头部摄像头参数，再写入 XLeRobot 配置。
"""

from __future__ import annotations

import argparse
import time

import cv2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="单独测试树莓派 OpenCV 摄像头读取稳定性。")
    parser.add_argument("--device", default="/dev/video4", help="摄像头设备路径，例如 /dev/video4")
    parser.add_argument("--width", type=int, default=640, help="请求宽度")
    parser.add_argument("--height", type=int, default=480, help="请求高度")
    parser.add_argument("--fps", type=float, default=30.0, help="请求 FPS")
    parser.add_argument("--fourcc", default="MJPG", help="请求 FOURCC，例如 MJPG 或 YUYV")
    parser.add_argument("--frames", type=int, default=60, help="读取帧数")
    parser.add_argument("--save", default="", help="可选：保存第一帧到指定 jpg 文件")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    print(f"[TEST] opening {args.device}")
    cap = cv2.VideoCapture(args.device, cv2.CAP_V4L2)
    if not cap.isOpened():
        raise RuntimeError(f"无法打开 {args.device}")

    if args.fourcc:
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*args.fourcc))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    cap.set(cv2.CAP_PROP_FPS, args.fps)

    actual_fourcc_int = int(cap.get(cv2.CAP_PROP_FOURCC))
    actual_fourcc = "".join(chr((actual_fourcc_int >> 8 * i) & 0xFF) for i in range(4))
    print("[TEST] requested:", args.width, args.height, args.fps, args.fourcc)
    print(
        "[TEST] actual:",
        int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
        int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        cap.get(cv2.CAP_PROP_FPS),
        actual_fourcc,
    )

    ok_count = 0
    first_shape = None
    start = time.perf_counter()
    first_frame_saved = False

    for index in range(args.frames):
        ok, frame = cap.read()
        if ok and frame is not None:
            ok_count += 1
            first_shape = first_shape or frame.shape
            if args.save and not first_frame_saved:
                cv2.imwrite(args.save, frame)
                first_frame_saved = True
        else:
            print(f"[WARN] frame {index} failed")
        time.sleep(0.01)

    elapsed = time.perf_counter() - start
    cap.release()

    print(f"[RESULT] ok={ok_count}/{args.frames}, elapsed={elapsed:.2f}s, shape={first_shape}")


if __name__ == "__main__":
    main()
