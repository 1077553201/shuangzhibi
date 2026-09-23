# 官方手臂 VLA 接入说明

这一步只走官方接口，不自己写手臂运动学、轨迹规划或关节联动逻辑。

## 官方路线

XLeRobot 官方文档里，手臂高级操作走 RoboCrew VLA 工具：

```python
from robocrew.robots.XLeRobot.tools import create_vla_single_arm_manipulation
```

这个工具内部使用 LeRobot 的异步推理：

```text
RobotClient 采集机器人手臂和摄像头观测
PolicyServer 在 GPU 电脑上推理动作
RobotClient 把策略动作写入手臂
```

所以手臂控制不是“给某个关节加 5 度”，而是训练好的 policy 根据视觉和任务文本输出动作。

## 当前已接入的内容

当前 `run_robocrew_client_agent.py` 会读取 RoboCrew 官方 UI 同款配置文件：

```text
C:\Users\10775\.cache\robocrew\tools\vla_tools.json
```

如果里面有启用的工具，就调用官方：

```python
create_vla_single_arm_manipulation(...)
```

如果没有这个文件，或者没有启用工具，Agent 不会注册手臂工具，避免模型胡说自己能控制手臂。

当前已经把官方 VLA 工具的数据源切到了树莓派 host：

```text
官方 create_vla_single_arm_manipulation
  -> 官方 RobotClient
  -> RemoteXLerobotSingleArm
  -> XLerobotClient
  -> 树莓派 xlerobot_host
```

也就是说，VLA policy 看到的单臂数据仍然是官方 `RobotClient` 格式：

```text
shoulder_pan.pos
shoulder_lift.pos
elbow_flex.pos
wrist_flex.pos
wrist_roll.pos
gripper.pos
main / right_arm 等摄像头图像
```

但这些值实际来自树莓派 host 的：

```text
right_arm_shoulder_pan.pos
right_arm_shoulder_lift.pos
right_arm_elbow_flex.pos
right_arm_wrist_flex.pos
right_arm_wrist_roll.pos
right_arm_gripper.pos
camera_0 / camera_2 / head
```

policy 输出动作后，也会映射回 `right_arm_*.pos` 或 `left_arm_*.pos`，再通过 host 发送给真实机械臂。

另外，当前也接入了 RoboCrew 官方保存姿态接口：

```text
list_saved_arm_positions
go_to_saved_arm_position
```

它们对应官方 `ServoControler.set_saved_position()` 的语义，只读取
`~/.cache/robocrew/positions/` 里的官方姿态 JSON，然后发送目标姿态。
这里不计算轨迹、不写逆运动学，也不生成新的关节联动方案。

## 配置模板

本目录提供了一个模板：

```text
E:\lerobot\robocrew_client_agent\templates\vla_tools_官方模板.json
```

字段含义：

```text
tool_name        LLM 看到的工具名
tool_description LLM 看到的工具说明
task_prompt      传给 VLA policy 的任务文本
server_address   policy server 地址，GPU 电脑一般是 电脑IP:8080
policy_name      训练好的策略名称或本地路径
policy_type      act、smolvla、pi0、pi05、groot、xvla 等
policy_device    policy 运行设备，通常 cpu 或 cuda
arm_port         手臂串口，官方示例是 /dev/arm_right 或 /dev/arm_left
execution_time   policy 执行秒数
active           true 才会注册成 Agent 工具
```

## Policy Server

官方文档给出的服务器命令是：

```powershell
E:\lerobot\envs\lerobot-gpu\Scripts\python.exe -m lerobot.async_inference.policy_server --host=0.0.0.0 --port=8080
```

这台 Windows 高性能电脑适合跑 policy server。树莓派负责连接手臂和摄像头。

## 重要边界

官方 VLA 工具默认要求运行工具的机器能直接看到：

```text
/dev/arm_right
/dev/arm_left
/dev/camera_center
/dev/camera_right
```

因此真正开始跑手臂 policy 前，需要确认采用哪一种官方部署方式：

```text
方式 A：RoboCrew Agent 在树莓派运行，Windows 只跑 policy_server。
方式 B：继续 Windows client Agent，使用当前 RemoteXLerobotSingleArm 从树莓派 host 读取观测并发送动作。
```

在没有这个准备之前，不应该用 `send_action` 自己拼手臂关节动作来假装 VLA。

当前已经完成方式 B 的远程数据源适配，但仍然需要准备真实可用的 policy。

## 当前可先测什么

如果已经有官方保存姿态文件，可以直接在 Agent 里说：

```text
列出官方保存的手臂姿态
执行官方保存姿态 default，双臂
执行官方保存姿态 cobra，右臂
```

如果没有姿态文件，工具会明确返回“没有找到官方姿态目录”或“未找到官方保存姿态”，不会移动手臂。

