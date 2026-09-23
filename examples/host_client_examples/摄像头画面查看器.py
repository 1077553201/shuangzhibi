"""XLeRobot Windows 端摄像头画面查看器。

运行位置：Windows。

前提：
1. 树莓派端已经同步相同的 xlerobot 相机配置。
2. 树莓派正在运行 `python -m lerobot.robots.xlerobot.xlerobot_host`。
3. Windows 与树莓派在同一局域网。
"""

from __future__ import annotations

import argparse
import time

import cv2

from lerobot.robots.xlerobot import XLerobotClient, XLerobotClientConfig


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="查看树莓派 XLeRobot host 回传的摄像头画面。")
    parser.add_argument("--ip", default="192.168.1.240", help="树莓派 host 的 IP 地址")
    parser.add_argument("--id", default="my_xlerobot_pc", help="机器人 ID")
    parser.add_argument("--fps", type=float, default=15.0, help="显示刷新率")
    parser.add_argument("--save-prefix", default="", help="可选：保存当前帧的文件名前缀")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    robot = XLerobotClient(XLerobotClientConfig(remote_ip=args.ip, id=args.id))

    print(f"[MAIN] connecting to XLeRobot host at {args.ip} ...")
    robot.connect()
    print("[MAIN] connected. Press q or ESC in any image window to exit.")

    interval = 1.0 / max(args.fps, 1.0)
    frame_count = 0

    try:
        while True:
            start = time.perf_counter()
            obs = robot.get_observation()

            camera_names = [name for name, value in obs.items() if hasattr(value, "shape")]
            if not camera_names:
                print("[WARN] no camera frames in observation. Check host camera config and restart host.")

            for name in camera_names:
                frame = obs[name]
                cv2.imshow(name, frame)

                if args.save_prefix:
                    cv2.imwrite(f"{args.save_prefix}_{name}_{frame_count:06d}.jpg", frame)

            frame_count += 1
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break

            elapsed = time.perf_counter() - start
            if elapsed < interval:
                time.sleep(interval - elapsed)





    finally:
        cv2.destroyAllWindows()
        if robot.is_connected:
            robot.send_action({"x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0})
            robot.disconnect()
        print("[MAIN] disconnected")


if __name__ == "__main__":
    main()
