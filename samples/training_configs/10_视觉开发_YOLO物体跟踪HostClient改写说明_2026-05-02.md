# YOLO 物体跟踪 Host/Client 改写说明

日期：2026-05-02

## 目标

把官方 `3_so100_yolo_ee_follow.py` 的思路迁移到当前 XLeRobot 架构：

```text
树莓派：host，负责电机、摄像头、校准、串口。
Windows：client，负责 YOLO 检测、画面显示、控制策略。
```

本次先做安全版：

```text
只控制头部摄像头云台，让检测目标回到画面中心。
不控制双臂。
不控制底盘。
默认只识别显示，不发送动作。
```

## 新增脚本

```text
E:\lerobot\host_client_examples\xlerobot_yolo_head_follow_client.py
```

## 与官方 SO100 示例的区别

官方示例的结构是：

```python
cap = cv2.VideoCapture(selected)
frame = cap.read()
robot = SO100Follower(...)
robot.send_action(...)
```

也就是说，官方示例假设：

```text
摄像头在本机
机械臂也直连本机
控制目标是 SO100/SO101 单臂
```

当前 XLeRobot 模式改成：

```python
robot = XLerobotClient(XLerobotClientConfig(remote_ip="192.168.10.240", id="my_xlerobot_pc"))
obs = robot.get_observation()
frame = obs["head"]
robot.send_action({"head_motor_1.pos": ..., "head_motor_2.pos": ...})
```

也就是说：

```text
摄像头画面来自树莓派 host
Windows 不直接打开树莓派摄像头
Windows 只通过 ZMQ client 收 observation、发 action
```

## 运行方式

先在树莓派运行 host：

```bash
cd ~/xlerobot-dev/lerobot
source .venv/bin/activate
python -m lerobot.robots.xlerobot.xlerobot_host
```

然后在 Windows 运行只识别、不动头部的安全模式：

```powershell
E:\lerobot\envs\lerobot-gpu\Scripts\python.exe E:\lerobot\host_client_examples\xlerobot_yolo_head_follow_client.py --target bottle --camera head
```

确认识别框稳定以后，再启用头部跟踪：

```powershell
E:\lerobot\envs\lerobot-gpu\Scripts\python.exe E:\lerobot\host_client_examples\xlerobot_yolo_head_follow_client.py --target bottle --camera head --enable-follow
```

窗口快捷键：

```text
f：开关头部跟踪
q 或 ESC：退出
```

## 主要参数

```text
--target bottle
```

指定要跟踪的 YOLO 类别。多个类别用逗号分隔：

```text
--target bottle,cup,mouse
```

```text
--camera head
```

使用哪一路 host 摄像头。目前可尝试：

```text
head
camera_0
camera_2
```

```text
--model yolo11n.pt
```

默认使用较快的 YOLO11 nano。需要更准可以换：

```text
yolo11s.pt
yolo11m.pt
yolo11x.pt
```

但模型越大延迟越高，闭环跟踪越容易滞后。

```text
--pan-gain -1.2
--tilt-gain 1.0
--max-step 0.6
```

这三个控制头部跟踪动作：

```text
pan-gain：画面水平误差到 head_motor_1 的转换系数。
tilt-gain：画面垂直误差到 head_motor_2 的转换系数。
max-step：每帧最大电机目标变化，越小越安全。
```

如果发现方向反了，不改代码，直接把对应 gain 改成相反数：

```powershell
--pan-gain 1.2
--tilt-gain -1.0
```

## 代码导入说明

脚本主要导入：

```python
import cv2
import numpy as np
from ultralytics import YOLO
from lerobot.robots.xlerobot import XLerobotClient, XLerobotClientConfig
```

用途：

```text
cv2：显示图像、画框、读取键盘事件。
numpy：标注图像类型。
YOLO：加载 ultralytics 模型并推理。
XLerobotClientConfig：配置树莓派 host IP 和机器人 ID。
XLerobotClient：连接 host，获取 observation，发送 action。
```

## 控制链路

完整链路：

```text
树莓派摄像头
-> xlerobot_host 将图像编码后通过 ZMQ 发给 Windows
-> XLerobotClient.get_observation()
-> obs["head"] 得到 OpenCV 图像
-> YOLO.predict(frame)
-> 选出目标检测框
-> 计算目标中心与画面中心偏差
-> 转换成 head_motor_1/head_motor_2 目标位置
-> XLerobotClient.send_action()
-> 树莓派 host 执行动作
```

## 为什么先做头部跟踪

物体跟踪可以分三层：

```text
1. 头部跟踪：只让摄像头看向目标，风险最低。
2. 底盘跟踪：让机器人移动到目标附近，需要避障和速度限制。
3. 机械臂跟踪/抓取：需要相机标定、深度估计或手眼标定，风险最高。
```

所以先从头部跟踪开始。等画面、延迟、方向和增益都稳定后，再接底盘和双臂。

