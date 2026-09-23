"""从树莓派 xlerobot_host 采集单臂 VLA 数据集。

本脚本只做官方 LeRobot 数据集写入：

- 通过 `RemoteXLerobotSingleArm` 从树莓派 host 读取右臂状态和摄像头；
- 使用官方 `LeRobotDataset.create()` 创建数据集；
- 使用官方 `build_dataset_frame()` 写入 observation/action；
- 使用官方 `dataset.save_episode()` 保存 episode。

第一版不实现逆运动学、轨迹规划或左右臂同步算法。默认 action 来源为右臂当前
关节位置，用来先验证数据集格式和远程 host 数据链路。
"""

from __future__ import annotations

import argparse
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")

import path_setup  # noqa: F401
from lerobot.datasets import LeRobotDataset, aggregate_pipeline_dataset_features, create_initial_features
from lerobot.processor import make_default_processors
from lerobot.utils.constants import ACTION, OBS_STR
from lerobot.utils.feature_utils import build_dataset_frame, combine_feature_dicts
from lerobot.utils.robot_utils import precise_sleep

from config import CAMERA_KEYS_BY_ROLE, ROBOT_ID, ROBOT_IP
from remote_xlerobot_single_arm import ARM_JOINT_NAMES, RemoteXLerobotSingleArm, RemoteXLerobotSingleArmConfig


@dataclass
class RemoteCameraSpec:
    """只提供 feature 需要的摄像头尺寸，不打开本地摄像头。"""

    width: int = 640
    height: int = 480
    fps: int = 10


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="从 xlerobot_host 读取右臂和摄像头，保存 LeRobot 格式的 pick-and-place 数据集。"
    )
    parser.add_argument("--arm", choices=["right", "left"], default="right", help="要记录哪只手臂。")
    parser.add_argument(
        "--repo-id",
        default="local/xlerobot_right_coke_can_to_shelf",
        help="LeRobot 数据集 repo_id。本地训练也需要一个 repo_id。",
    )
    parser.add_argument(
        "--root",
        default=None,
        help="数据集本地保存目录。默认使用带时间戳的新目录，避免覆盖旧数据。",
    )
    parser.add_argument(
        "--task",
        default="Pick up the red Coca-Cola can from the table and place it on the robot shelf.",
        help="写入数据集的单任务描述。",
    )
    parser.add_argument("--episodes", type=int, default=1, help="采集 episode 数量。")
    parser.add_argument("--seconds", type=float, default=8.0, help="每个 episode 时长。")
    parser.add_argument("--fps", type=int, default=10, help="采集帧率。")
    parser.add_argument(
        "--cameras",
        nargs="+",
        default=["main", "right_arm"],
        help="逻辑摄像头名，例如 main right_arm。实际 host key 由 config.py 映射。",
    )
    parser.set_defaults(use_videos=True)
    parser.add_argument(
        "--no-videos",
        dest="use_videos",
        action="store_false",
        help="调试用：不按视频格式保存。正式 VLA 数据不要使用。",
    )
    parser.add_argument(
        "--send-current-action",
        action="store_true",
        help="把当前关节位置作为目标 action 发回机器人。默认只记录，不发送动作。",
    )
    return parser.parse_args()


def resolve_dataset_root(root: str | None, arm: str) -> Path:
    if root:
        return Path(root)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path(r"E:\lerobot") / "datasets" / f"xlerobot_{arm}_coke_can_to_shelf_{stamp}"


def make_robot(args: argparse.Namespace) -> RemoteXLerobotSingleArm:
    camera_specs = {name: RemoteCameraSpec(fps=args.fps) for name in args.cameras}
    side = args.arm
    port = f"/dev/arm_{side}"
    config = RemoteXLerobotSingleArmConfig(
        id=f"remote_{side}_arm_dataset",
        port=port,
        arm_side=side,
        cameras=camera_specs,
        remote_ip=ROBOT_IP,
        robot_id=ROBOT_ID,
        camera_key_map=dict(CAMERA_KEYS_BY_ROLE),
        swap_red_blue=False,
    )
    return RemoteXLerobotSingleArm(config)


def make_dataset(robot: RemoteXLerobotSingleArm, args: argparse.Namespace) -> LeRobotDataset:
    teleop_action_processor, _, robot_observation_processor = make_default_processors()
    dataset_features = combine_feature_dicts(
        aggregate_pipeline_dataset_features(
            pipeline=teleop_action_processor,
            initial_features=create_initial_features(action=robot.action_features),
            use_videos=args.use_videos,
        ),
        aggregate_pipeline_dataset_features(
            pipeline=robot_observation_processor,
            initial_features=create_initial_features(observation=robot.observation_features),
            use_videos=args.use_videos,
        ),
    )

    root = Path(args.root)
    if root.exists():
        raise FileExistsError(f"数据集目录已存在：{root}。请换一个 --root，避免覆盖旧数据。")

    image_writer_threads = max(1, len(args.cameras) * 2)
    return LeRobotDataset.create(
        repo_id=args.repo_id,
        fps=args.fps,
        root=root,
        robot_type=robot.name,
        features=dataset_features,
        use_videos=args.use_videos,
        image_writer_processes=0,
        image_writer_threads=image_writer_threads,
        vcodec="auto",
    )


def current_joint_action(observation: dict) -> dict[str, float]:
    """把当前单臂关节位置作为 action。

    这是安全的记录源，不计算目标轨迹。后续如果接入官方 leader arm 或 VR，
    这里应替换为官方 teleop 输出。
    """

    return {f"{joint}.pos": float(observation[f"{joint}.pos"]) for joint in ARM_JOINT_NAMES}


def record_episode(
    robot: RemoteXLerobotSingleArm,
    dataset: LeRobotDataset,
    args: argparse.Namespace,
    episode_index: int,
) -> None:
    control_interval = 1.0 / args.fps
    deadline = time.perf_counter() + args.seconds
    frame_index = 0

    while time.perf_counter() < deadline:
        loop_start = time.perf_counter()
        observation = robot.get_observation()
        action = current_joint_action(observation)
        if args.send_current_action:
            robot.send_action(action)

        observation_frame = build_dataset_frame(dataset.features, observation, prefix=OBS_STR)
        action_frame = build_dataset_frame(dataset.features, action, prefix=ACTION)
        dataset.add_frame({**observation_frame, **action_frame, "task": args.task})
        frame_index += 1

        dt = time.perf_counter() - loop_start
        precise_sleep(max(0.0, control_interval - dt))

    # Windows 上多摄像头并行编码可能生成当前用户不可读的 mp4。
    # 这里仍然调用官方 save_episode，只关闭并行编码，让视频由主进程顺序写入。
    dataset.save_episode(parallel_encoding=False)
    print(f"[保存] episode {episode_index + 1}/{args.episodes}，帧数约 {frame_index}")


def main() -> None:
    args = parse_args()
    args.root = str(resolve_dataset_root(args.root, args.arm))
    robot = make_robot(args)
    dataset = None

    print(f"[连接] host={ROBOT_IP}, arm={args.arm}, cameras={args.cameras}")
    print(f"[数据集] repo_id={args.repo_id}")
    print(f"[数据集] root={args.root}")
    print(f"[任务] {args.task}")
    print("[提示] 每个 episode 开始前请摆好物体和托盘，然后按 Enter。")

    try:
        robot.connect()
        dataset = make_dataset(robot, args)
        for episode_index in range(args.episodes):
            input(f"\n准备录制 episode {episode_index + 1}/{args.episodes}，按 Enter 开始...")
            record_episode(robot, dataset, args, episode_index)
            if episode_index < args.episodes - 1:
                input("请复位场景，按 Enter 继续下一条...")
    finally:
        if dataset is not None:
            dataset.finalize()
            print("[完成] 数据集已 finalize。")
        if robot.is_connected:
            robot.disconnect()


if __name__ == "__main__":
    main()
