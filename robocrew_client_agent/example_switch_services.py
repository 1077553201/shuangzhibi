"""同目录调用 run_xlerobot_remote.py 的简单示例。"""

import time

from run_xlerobot_remote import (
    send_input,
    service_status,
    shutdown,
    start_host,
    start_vr,
    stop_service,
)


try:
    # 函数均为非阻塞调用，调用后当前程序可以继续完成其他工作。
    # 默认识别到校准提示后自动发送回车，恢复已有校准。
    start_host()
    print("当前服务：", service_status())
    time.sleep(10)

    # 自动停止 host，并立即启动 VR 控制。
    start_vr()
    print("当前服务：", service_status())
    time.sleep(10)

    stop_service()

    # 如需手动校准，可以改用：start_host("manual")
    # 如以 start_host("ask") 启动，也可稍后调用：send_input("c")
finally:
    # 程序退出前停止远程服务并断开 SSH。
    shutdown()
