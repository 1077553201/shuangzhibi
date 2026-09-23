"""pygame 手柄输入检测工具。

运行位置：Windows。

用途：
1. 查看小米手柄在 pygame 中暴露的 axes/buttons/hats 编号。
2. 按一个键或拨一个摇杆，就能看到对应数值变化。
3. 用这些编号再去改 XLeRobot 的手柄映射，避免按 Xbox 布局硬猜。
"""

from __future__ import annotations

import argparse
import time

import pygame


def format_values(values: list[float | int]) -> str:
    return " ".join(f"{index}:{value:>6.3f}" if isinstance(value, float) else f"{index}:{value}" for index, value in enumerate(values))


def main() -> None:
    parser = argparse.ArgumentParser(description="打印 pygame 识别到的手柄输入编号和值。")
    parser.add_argument("--hz", type=float, default=10.0, help="打印频率，默认 10Hz")
    parser.add_argument("--deadzone", type=float, default=0.08, help="小于该绝对值的轴显示为 0")
    args = parser.parse_args()

    pygame.init()
    pygame.joystick.init()

    count = pygame.joystick.get_count()
    if count == 0:
        print("没有检测到手柄。请确认手柄已连接，并能被 Windows 识别。")
        return

    joystick = pygame.joystick.Joystick(0)
    joystick.init()

    print(f"检测到手柄数量: {count}")
    print(f"当前使用手柄: {joystick.get_name()}")
    print(f"axes={joystick.get_numaxes()}, buttons={joystick.get_numbuttons()}, hats={joystick.get_numhats()}")
    print("拨动摇杆/扳机/方向键，观察哪个编号变化。按 Ctrl+C 退出。")

    interval = 1.0 / max(args.hz, 1.0)
    try:
        while True:
            pygame.event.pump()

            axes: list[float] = []
            for index in range(joystick.get_numaxes()):
                value = joystick.get_axis(index)
                if abs(value) < args.deadzone:
                    value = 0.0
                axes.append(value)

            buttons = [joystick.get_button(index) for index in range(joystick.get_numbuttons())]
            hats = [joystick.get_hat(index) for index in range(joystick.get_numhats())]

            print(f"axes: {format_values(axes)}")
            print(f"buttons: {format_values(buttons)}")
            print(f"hats: {hats}")
            print("-" * 80)

            time.sleep(interval)

    except KeyboardInterrupt:
        print("退出手柄检测。")


if __name__ == "__main__":
    main()
