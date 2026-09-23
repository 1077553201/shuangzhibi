"""用官方键盘遥操作接口采集右臂抓取放置数据集。

这个脚本是 `remote_pick_place_dataset_recorder.py` 的下一步：

- 仍然通过 `RemoteXLerobotSingleArm` 从树莓派 xlerobot_host 读取右臂和摄像头；
- 仍然用官方 `LeRobotDataset.create()` / `build_dataset_frame()` / `save_episode()` 保存数据；
- 键盘输入使用 LeRobot 官方 `KeyboardTeleop`；
- 右臂末端控制沿用 XLeRobot 官方示例里的 `SO101Kinematics` 和 P 控制写法。

注意：这里不做自动抓取算法，也不让模型控制手臂。它只是让人用键盘演示动作，
把演示过程保存成后续 ACT/VLA 训练需要的数据。
"""

from __future__ import annotations

import argparse
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")

import path_setup  # noqa: F401
from lerobot.datasets import LeRobotDataset
from lerobot.model.SO101Robot import SO101Kinematics
from lerobot.teleoperators.keyboard.configuration_keyboard import KeyboardTeleopConfig
from lerobot.teleoperators.keyboard.teleop_keyboard import KeyboardTeleop
from lerobot.utils.constants import ACTION, OBS_STR
from lerobot.utils.feature_utils import build_dataset_frame
from lerobot.utils.robot_utils import precise_sleep

from remote_pick_place_dataset_recorder import make_dataset, make_robot, resolve_dataset_root
from remote_xlerobot_single_arm import ARM_JOINT_NAMES, RemoteXLerobotSingleArm


# XLeRobot 官方键盘示例的右臂键位。这里只录右臂，所以不启用左臂键位。
RIGHT_KEYMAP = {
    "shoulder_pan+": "7",
    "shoulder_pan-": "9",
    "wrist_roll+": "/",
    "wrist_roll-": "*",
    "gripper+": "+",
    "gripper-": "-",
    "x+": "8",
    "x-": "2",
    "y+": "4",
    "y-": "6",
    "pitch+": "1",
    "pitch-": "3",
    "reset": "0",
}


def _joint_key(joint: str) -> str:
    return f"{joint}.pos"


@dataclass
class OfficialRightArmKeyboardController:
    """把官方 XLeRobot 键盘示例适配到远程单臂字段。

    官方示例中右臂字段是 `right_arm_shoulder_lift.pos`。
    `RemoteXLerobotSingleArm` 已经把它映射成单臂字段 `shoulder_lift.pos`，
    因此这里的 action 也只产生单臂字段。
    """

    kinematics: SO101Kinematics = field(default_factory=SO101Kinematics)
    kp: float = 0.81
    degree_step: float = 3.0
    xy_step: float = 0.0081
    current_x: float = 0.1629
    current_y: float = 0.1131
    pitch: float = 0.0
    target_positions: dict[str, float] = field(default_factory=dict)

    def initialize_from_observation(self, observation: dict) -> None:
        """以机器人当前姿态作为目标，避免启动时突然归零。"""

        self.target_positions = {
            joint: float(observation[_joint_key(joint)]) for joint in ARM_JOINT_NAMES
        }
        self.current_x, self.current_y = self.kinematics.forward_kinematics(
            self.target_positions["shoulder_lift"],
            self.target_positions["elbow_flex"],
        )
        # 官方示例中 wrist_flex = -shoulder_lift - elbow_flex + pitch。
        # 反推 pitch，确保没按键时腕部保持当前角度。
        self.pitch = (
            self.target_positions["wrist_flex"]
            + self.target_positions["shoulder_lift"]
            + self.target_positions["elbow_flex"]
        )

    def handle_pressed_keys(self, pressed_keys: set[str], observation: dict) -> None:
        """根据当前按键更新右臂目标姿态。"""

        key_state = {action: (key in pressed_keys) for action, key in RIGHT_KEYMAP.items()}

        if key_state["reset"]:
            # 官方示例的 reset 会回到 0 位。采集真实演示时更安全的做法是把目标同步到当前姿态。
            self.initialize_from_observation(observation)
            print("[右臂] 0: 已把控制目标同步到当前姿态。")
            return

        if key_state["shoulder_pan+"]:
            self.target_positions["shoulder_pan"] += self.degree_step
        if key_state["shoulder_pan-"]:
            self.target_positions["shoulder_pan"] -= self.degree_step
        if key_state["wrist_roll+"]:
            self.target_positions["wrist_roll"] += self.degree_step
        if key_state["wrist_roll-"]:
            self.target_positions["wrist_roll"] -= self.degree_step
        if key_state["gripper+"]:
            self.target_positions["gripper"] += self.degree_step
        if key_state["gripper-"]:
            self.target_positions["gripper"] -= self.degree_step
        if key_state["pitch+"]:
            self.pitch += self.degree_step
        if key_state["pitch-"]:
            self.pitch -= self.degree_step

        moved_xy = False
        if key_state["x+"]:
            self.current_x += self.xy_step
            moved_xy = True
        if key_state["x-"]:
            self.current_x -= self.xy_step
            moved_xy = True
        if key_state["y+"]:
            self.current_y += self.xy_step
            moved_xy = True
        if key_state["y-"]:
            self.current_y -= self.xy_step
            moved_xy = True

        if moved_xy:
            shoulder_lift, elbow_flex = self.kinematics.inverse_kinematics(
                self.current_x,
                self.current_y,
            )
            self.target_positions["shoulder_lift"] = shoulder_lift
            self.target_positions["elbow_flex"] = elbow_flex

        self.target_positions["wrist_flex"] = (
            -self.target_positions["shoulder_lift"]
            - self.target_positions["elbow_flex"]
            + self.pitch
        )

    def action_from_observation(self, observation: dict) -> dict[str, float]:
        """生成官方示例同款 P 控制 action。"""

        action: dict[str, float] = {}
        for joint, target in self.target_positions.items():
            key = _joint_key(joint)
            current = float(observation[key])
            action[key] = current + self.kp * (target - current)
        return action


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="右臂键盘遥操作并录制 LeRobot/VLA 数据集。"
    )
    parser.add_argument("--arm", choices=["right"], default="right", help="当前脚本只采集右臂。")
    parser.add_argument(
        "--repo-id",
        default="local/xlerobot_right_coke_can_to_shelf",
        help="LeRobot 数据集 repo_id。本地训练也需要一个 repo_id。",
    )
    parser.add_argument("--root", default=None, help="数据集保存目录。默认使用时间戳新目录。")
    parser.add_argument(
        "--task",
        default="Pick up the red Coca-Cola can from the table and place it on the robot shelf.",
        help="写入数据集的任务描述。",
    )
    parser.add_argument("--episodes", type=int, default=1, help="采集 episode 数量。")
    parser.add_argument("--seconds", type=float, default=20.0, help="每个 episode 时长。")
    parser.add_argument("--fps", type=int, default=15, help="采集和控制帧率。")
    parser.add_argument(
        "--cameras",
        nargs="+",
        default=["main", "right_arm"],
        help="逻辑摄像头名，默认使用头部主摄像头和右臂摄像头。",
    )
    parser.add_argument("--kp", type=float, default=0.81, help="官方示例同款 P 控制比例。")
    parser.add_argument("--degree-step", type=float, default=3.0, help="每帧关节/姿态角度步长。")
    parser.add_argument("--xy-step", type=float, default=0.0081, help="每帧末端 x/y 步长，单位米。")
    parser.set_defaults(use_videos=True)
    parser.add_argument(
        "--no-videos",
        dest="use_videos",
        action="store_false",
        help="调试用：不按视频格式保存。正式 VLA 数据不要使用。",
    )
    return parser.parse_args()


def print_key_help() -> None:
    print("[键位] 右臂末端：8/2 = x+/x-，4/6 = y+/y-，1/3 = pitch+/pitch-")
    print("[键位] 右臂关节：7/9 = shoulder_pan，/ 和 * = wrist_roll，+/- = 夹爪")
    print("[键位] 0 = 把控制目标同步到当前姿态，ESC = 结束当前 episode")


def record_episode(
    robot: RemoteXLerobotSingleArm,
    dataset: LeRobotDataset,
    keyboard: KeyboardTeleop,
    controller: OfficialRightArmKeyboardController,
    args: argparse.Namespace,
    episode_index: int,
) -> None:
    control_interval = 1.0 / args.fps
    deadline = time.perf_counter() + args.seconds
    frame_index = 0

    while time.perf_counter() < deadline and keyboard.is_connected:
        loop_start = time.perf_counter()

        observation = robot.get_observation()
        pressed_keys = set(keyboard.get_action().keys())
        controller.handle_pressed_keys(pressed_keys, observation)
        action = controller.action_from_observation(observation)
        robot.send_action(action)

        observation_frame = build_dataset_frame(dataset.features, observation, prefix=OBS_STR)
        action_frame = build_dataset_frame(dataset.features, action, prefix=ACTION)
        dataset.add_frame({**observation_frame, **action_frame, "task": args.task})
        frame_index += 1

        dt = time.perf_counter() - loop_start
        precise_sleep(max(0.0, control_interval - dt))

    dataset.save_episode(parallel_encoding=False)
    print(f"[保存] episode {episode_index + 1}/{args.episodes}，帧数约 {frame_index}")


def main() -> None:
    args = parse_args()
    args.root = str(resolve_dataset_root(args.root, args.arm))

    robot = make_robot(args)
    keyboard = KeyboardTeleop(KeyboardTeleopConfig())
    dataset = None

    print(f"[连接] arm={args.arm}, cameras={args.cameras}")
    print(f"[数据集] repo_id={args.repo_id}")
    print(f"[数据集] root={args.root}")
    print(f"[任务] {args.task}")
    print_key_help()

    try:
        robot.connect()
        initial_observation = robot.get_observation()
        controller = OfficialRightArmKeyboardController(
            kp=args.kp,
            degree_step=args.degree_step,
            xy_step=args.xy_step,
        )
        controller.initialize_from_observation(initial_observation)

        dataset = make_dataset(robot, args)

        for episode_index in range(args.episodes):
            input(f"\n摆好可乐罐和置物架，准备录制 episode {episode_index + 1}/{args.episodes}，按 Enter 开始...")
            if not keyboard.is_connected:
                keyboard.connect()
            keyboard.get_action()
            keyboard.current_pressed.clear()
            print("[录制] 开始。请用键盘控制右臂演示完整抓取放置过程。")
            record_episode(robot, dataset, keyboard, controller, args, episode_index)
            if episode_index < args.episodes - 1:
                input("请复位场景，按 Enter 继续下一条...")
                controller.initialize_from_observation(robot.get_observation())
    finally:
        if keyboard.is_connected:
            keyboard.disconnect()
        if dataset is not None:
            dataset.finalize()
            print("[完成] 数据集已 finalize。")
        if robot.is_connected:
            robot.disconnect()


if __name__ == "__main__":
    main()
