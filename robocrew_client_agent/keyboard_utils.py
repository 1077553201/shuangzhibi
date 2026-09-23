"""跨平台键盘输入工具。

Windows 使用 msvcrt，Linux/macOS 使用 termios + select。
对外提供 kbhit() 和 getwch() 两个接口，行为与 Windows msvcrt 保持一致。
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager

if sys.platform == "win32":
    import msvcrt

    def kbhit() -> bool:
        """是否有按键等待读取。"""
        return msvcrt.kbhit()

    def getwch() -> str:
        """读取单个按键（不回显）。"""
        return msvcrt.getwch()

    @contextmanager
    def cbreak_stdin() -> Iterator[None]:
        """Windows 下无需切换终端模式。"""
        yield

else:
    import select
    import termios
    import tty

    def kbhit() -> bool:
        """是否有按键等待读取。"""
        return bool(select.select([sys.stdin], [], [], 0)[0])

    def getwch() -> str:
        """读取单个按键（不回显）。"""
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        return ch

    @contextmanager
    def cbreak_stdin() -> Iterator[None]:
        """进入非规范模式，让 q/Esc 不必等回车就能被 kbhit() 读到。"""
        if not sys.stdin.isatty():
            yield
            return
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            yield
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
