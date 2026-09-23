#!/usr/bin/env python3
"""Windows keyboard teleop client for XLeRobot Raspberry Pi host.

Run the Pi host first:
    python -m lerobot.robots.xlerobot.xlerobot_host

Then run this script on Windows. It connects to the host's ZMQ sockets,
listens to the local Windows keyboard, and sends action dictionaries.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import time
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np
import zmq
from pynput import keyboard


LEFT_KEYMAP = {
    "shoulder_pan+": "e",
    "shoulder_pan-": "q",
    "wrist_roll+": "r",
    "wrist_roll-": "f",
    "gripper+": "t",
    "gripper-": "g",
    "x+": "w",
    "x-": "s",
    "y+": "a",
    "y-": "d",
    "pitch+": "z",
    "pitch-": "x",
    "reset": "c",
    "head_motor_1+": "<",
    "head_motor_1-": ">",
    "head_motor_2+": ",",
    "head_motor_2-": ".",
}

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

NUMPAD_VK_TO_CHAR = {
    96: "0",
    97: "1",
    98: "2",
    99: "3",
    100: "4",
    101: "5",
    102: "6",
    103: "9",
    104: "8",
    105: "7",
    106: "*",
    107: "+",
    109: "-",
    110: ".",
    111: "/",
}

LEFT_JOINT_MAP = {
    "shoulder_pan": "left_arm_shoulder_pan",
    "shoulder_lift": "left_arm_shoulder_lift",
    "elbow_flex": "left_arm_elbow_flex",
    "wrist_flex": "left_arm_wrist_flex",
    "wrist_roll": "left_arm_wrist_roll",
    "gripper": "left_arm_gripper",
}

RIGHT_JOINT_MAP = {
    "shoulder_pan": "right_arm_shoulder_pan",
    "shoulder_lift": "right_arm_shoulder_lift",
    "elbow_flex": "right_arm_elbow_flex",
    "wrist_flex": "right_arm_wrist_flex",
    "wrist_roll": "right_arm_wrist_roll",
    "gripper": "right_arm_gripper",
}

BASE_KEYS = {
    "forward": "k",
    "backward": "i",
    "left": "l",
    "right": "j",
    "rotate_left": "u",
    "rotate_right": "o",
    "speed_up": "n",
    "speed_down": "m",
}


class PressedKeys:
    def __init__(self) -> None:
        self.keys: set[str] = set()
        self.stop = False

    def _key_to_str(self, key: keyboard.Key | keyboard.KeyCode) -> str | None:
        if key == keyboard.Key.esc:
            return "esc"
        if isinstance(key, keyboard.KeyCode) and key.char:
            return key.char
        vk = getattr(key, "vk", None)
        if isinstance(vk, int) and vk in NUMPAD_VK_TO_CHAR:
            return NUMPAD_VK_TO_CHAR[vk]
        return None

    def on_press(self, key: keyboard.Key | keyboard.KeyCode) -> bool | None:
        value = self._key_to_str(key)
        if value is None:
            return None
        if value == "esc":
            self.stop = True
            return False
        self.keys.add(value)
        return None

    def on_release(self, key: keyboard.Key | keyboard.KeyCode) -> None:
        value = self._key_to_str(key)
        if value:
            self.keys.discard(value)


class SO101Kinematics:
    def __init__(self, l1: float = 0.1159, l2: float = 0.1350) -> None:
        self.l1 = l1
        self.l2 = l2

    def inverse_kinematics(self, x: float, y: float) -> tuple[float, float]:
        theta1_offset = math.atan2(0.028, 0.11257)
        theta2_offset = math.atan2(0.0052, 0.1349) + theta1_offset

        r = math.sqrt(x * x + y * y)
        r_max = self.l1 + self.l2
        if r > r_max:
            scale = r_max / r
            x *= scale
            y *= scale
            r = r_max

        r_min = abs(self.l1 - self.l2)
        if 0 < r < r_min:
            scale = r_min / r
            x *= scale
            y *= scale
            r = r_min

        cos_theta2 = -(r * r - self.l1 * self.l1 - self.l2 * self.l2) / (2 * self.l1 * self.l2)
        cos_theta2 = max(-1.0, min(1.0, cos_theta2))
        theta2 = math.pi - math.acos(cos_theta2)

        beta = math.atan2(y, x)
        gamma = math.atan2(self.l2 * math.sin(theta2), self.l1 + self.l2 * math.cos(theta2))
        theta1 = beta + gamma

        joint2 = max(-0.1, min(3.45, theta1 + theta1_offset))
        joint3 = max(-0.2, min(math.pi, theta2 + theta2_offset))
        return 90 - math.degrees(joint2), math.degrees(joint3) - 90


@dataclass
class TeleopArm:
    prefix: str
    joint_map: dict[str, str]
    keymap: dict[str, str]
    kp: float = 0.81
    degree_step: float = 3.0
    xy_step: float = 0.0081
    kinematics: SO101Kinematics = field(default_factory=SO101Kinematics)
    current_x: float = 0.1629
    current_y: float = 0.1131
    pitch: float = 0.0
    target_positions: dict[str, float] = field(default_factory=dict)

    def initialize(self, obs: dict[str, Any]) -> None:
        self.target_positions = {
            joint: float(obs.get(f"{self.prefix}_arm_{joint}.pos", 0.0))
            for joint in self.joint_map
        }

    def reset_targets(self, obs: dict[str, Any]) -> None:
        self.initialize(obs)
        self.current_x = 0.1629
        self.current_y = 0.1131
        self.pitch = 0.0

    def handle_keys(self, pressed: set[str], obs: dict[str, Any]) -> None:
        if self.keymap["reset"] in pressed:
            self.reset_targets(obs)
            print(f"[{self.prefix}] reset targets to current position")
            return

        moved_xy = False
        if self.keymap["shoulder_pan+"] in pressed:
            self.target_positions["shoulder_pan"] += self.degree_step
        if self.keymap["shoulder_pan-"] in pressed:
            self.target_positions["shoulder_pan"] -= self.degree_step
        if self.keymap["wrist_roll+"] in pressed:
            self.target_positions["wrist_roll"] += self.degree_step
        if self.keymap["wrist_roll-"] in pressed:
            self.target_positions["wrist_roll"] -= self.degree_step
        if self.keymap["gripper+"] in pressed:
            self.target_positions["gripper"] += self.degree_step
        if self.keymap["gripper-"] in pressed:
            self.target_positions["gripper"] -= self.degree_step
        if self.keymap["pitch+"] in pressed:
            self.pitch += self.degree_step
        if self.keymap["pitch-"] in pressed:
            self.pitch -= self.degree_step

        if self.keymap["x+"] in pressed:
            self.current_x += self.xy_step
            moved_xy = True
        if self.keymap["x-"] in pressed:
            self.current_x -= self.xy_step
            moved_xy = True
        if self.keymap["y+"] in pressed:
            self.current_y += self.xy_step
            moved_xy = True
        if self.keymap["y-"] in pressed:
            self.current_y -= self.xy_step
            moved_xy = True

        if moved_xy:
            shoulder_lift, elbow_flex = self.kinematics.inverse_kinematics(self.current_x, self.current_y)
            self.target_positions["shoulder_lift"] = shoulder_lift
            self.target_positions["elbow_flex"] = elbow_flex

        self.target_positions["wrist_flex"] = (
            -self.target_positions["shoulder_lift"] - self.target_positions["elbow_flex"] + self.pitch
        )

    def p_action(self, obs: dict[str, Any]) -> dict[str, float]:
        action: dict[str, float] = {}
        for joint, target in self.target_positions.items():
            remote_name = self.joint_map[joint]
            obs_key = f"{remote_name}.pos"
            current = float(obs.get(obs_key, 0.0))
            action[obs_key] = current + self.kp * (target - current)
        return action


@dataclass
class HeadControl:
    kp: float = 0.81
    degree_step: float = 2.0
    target_positions: dict[str, float] = field(default_factory=dict)

    def initialize(self, obs: dict[str, Any]) -> None:
        self.target_positions = {
            "head_motor_1": float(obs.get("head_motor_1.pos", 0.0)),
            "head_motor_2": float(obs.get("head_motor_2.pos", 0.0)),
        }

    def handle_keys(self, pressed: set[str]) -> None:
        if LEFT_KEYMAP["head_motor_1+"] in pressed:
            self.target_positions["head_motor_1"] += self.degree_step
        if LEFT_KEYMAP["head_motor_1-"] in pressed:
            self.target_positions["head_motor_1"] -= self.degree_step
        if LEFT_KEYMAP["head_motor_2+"] in pressed:
            self.target_positions["head_motor_2"] += self.degree_step
        if LEFT_KEYMAP["head_motor_2-"] in pressed:
            self.target_positions["head_motor_2"] -= self.degree_step

    def p_action(self, obs: dict[str, Any]) -> dict[str, float]:
        action: dict[str, float] = {}
        for motor, target in self.target_positions.items():
            key = f"{motor}.pos"
            current = float(obs.get(key, 0.0))
            action[key] = current + self.kp * (target - current)
        return action


class RemoteXLeRobot:
    def __init__(self, remote_ip: str, cmd_port: int, obs_port: int, timeout_s: float) -> None:
        self.ctx = zmq.Context()
        self.cmd = self.ctx.socket(zmq.PUSH)
        self.cmd.setsockopt(zmq.CONFLATE, 1)
        self.cmd.connect(f"tcp://{remote_ip}:{cmd_port}")

        self.obs = self.ctx.socket(zmq.PULL)
        self.obs.setsockopt(zmq.CONFLATE, 1)
        self.obs.connect(f"tcp://{remote_ip}:{obs_port}")
        self.timeout_s = timeout_s

    def get_observation(self) -> dict[str, Any]:
        poller = zmq.Poller()
        poller.register(self.obs, zmq.POLLIN)
        socks = dict(poller.poll(int(self.timeout_s * 1000)))
        if self.obs not in socks:
            raise TimeoutError("No observation from Raspberry Pi host. Is xlerobot_host running?")

        latest = self.obs.recv_string()
        while True:
            try:
                latest = self.obs.recv_string(zmq.NOBLOCK)
            except zmq.Again:
                break
        data = json.loads(latest)
        for key, value in list(data.items()):
            if isinstance(value, str) and value:
                try:
                    raw = base64.b64decode(value)
                    arr = np.frombuffer(raw, dtype=np.uint8)
                    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                    if frame is not None:
                        data[key] = frame
                except Exception:
                    pass
        return data

    def send_action(self, action: dict[str, float]) -> None:
        self.cmd.send_string(json.dumps(action))

    def close(self) -> None:
        self.obs.close()
        self.cmd.close()
        self.ctx.term()


def base_action(pressed: set[str], speed_index: int) -> tuple[dict[str, float], int]:
    if BASE_KEYS["speed_up"] in pressed:
        speed_index = min(speed_index + 1, 2)
    if BASE_KEYS["speed_down"] in pressed:
        speed_index = max(speed_index - 1, 0)

    levels = [{"xy": 0.1, "theta": 30}, {"xy": 0.2, "theta": 60}, {"xy": 0.3, "theta": 90}]
    speed = levels[speed_index]
    x_cmd = 0.0
    y_cmd = 0.0
    theta_cmd = 0.0

    if BASE_KEYS["forward"] in pressed:
        x_cmd += speed["xy"]
    if BASE_KEYS["backward"] in pressed:
        x_cmd -= speed["xy"]
    if BASE_KEYS["left"] in pressed:
        y_cmd += speed["xy"]
    if BASE_KEYS["right"] in pressed:
        y_cmd -= speed["xy"]
    if BASE_KEYS["rotate_left"] in pressed:
        theta_cmd += speed["theta"]
    if BASE_KEYS["rotate_right"] in pressed:
        theta_cmd -= speed["theta"]

    return {"x.vel": x_cmd, "y.vel": y_cmd, "theta.vel": theta_cmd}, speed_index


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ip", default="192.168.1.239")
    parser.add_argument("--cmd-port", type=int, default=5555)
    parser.add_argument("--obs-port", type=int, default=5556)
    parser.add_argument("--fps", type=float, default=30.0)
    parser.add_argument("--smoke", action="store_true", help="Only connect and print one observation summary.")
    args = parser.parse_args()

    robot = RemoteXLeRobot(args.ip, args.cmd_port, args.obs_port, timeout_s=5.0)
    try:
        obs = robot.get_observation()
        print("[client] connected to host")
        if args.smoke:
            for key in sorted(k for k in obs if k.endswith(".pos") or k.endswith(".vel")):
                print(f"{key}: {obs[key]}")
            return

        left = TeleopArm("left", LEFT_JOINT_MAP, LEFT_KEYMAP)
        right = TeleopArm("right", RIGHT_JOINT_MAP, RIGHT_KEYMAP)
        head = HeadControl()
        left.initialize(obs)
        right.initialize(obs)
        head.initialize(obs)

        pressed = PressedKeys()
        listener = keyboard.Listener(on_press=pressed.on_press, on_release=pressed.on_release)
        listener.start()
        print("[client] teleop running. Press ESC to stop.")
        print("[left] q/e pan, w/s x, a/d y, r/f roll, t/g gripper, z/x pitch, c reset")
        print("[right] 7/9 pan, 8/2 x, 4/6 y, //*/ roll, +/- gripper, 1/3 pitch, 0 reset")
        print("[right] top-row numbers and numpad numbers/operators are both supported")
        print("[base] i/k forward/back, j/l left/right, u/o rotate, n/m speed")

        speed_index = 0
        dt = 1.0 / args.fps
        while not pressed.stop:
            t0 = time.perf_counter()
            obs = robot.get_observation()
            keys = set(pressed.keys)

            left.handle_keys(keys, obs)
            right.handle_keys(keys, obs)
            head.handle_keys(keys)
            base, speed_index = base_action(keys, speed_index)

            action = {**left.p_action(obs), **right.p_action(obs), **head.p_action(obs), **base}
            robot.send_action(action)
            time.sleep(max(dt - (time.perf_counter() - t0), 0.0))
    finally:
        robot.send_action({"x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0})
        robot.close()
        print("[client] stopped")


if __name__ == "__main__":
    main()
