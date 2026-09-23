"""XLeRobot host 最小启动脚本。

运行位置：树莓派。

这个脚本只是把官方 host 入口包一层，便于学习 host/client 架构。
实际生产运行时，也可以直接使用：

    python -m lerobot.robots.xlerobot.xlerobot_host
"""

from lerobot.robots.xlerobot.xlerobot_host import main


if __name__ == "__main__":
    main()
