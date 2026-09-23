# 官方 RoboCrew 实现梳理

本文记录当前 client 模式参考的 RoboCrew 官方实现位置。正式链路优先调用这些官方入口，只在硬件连接方式上做 Windows client 到树莓派 host 的适配。

## 官方 Agent 入口

官方 UI 组装入口：

```text
E:\lerobot\envs\lerobot-gpu\Lib\site-packages\robocrew\ui\agent_setup.py
```

这里创建：

```python
RobotCamera("/dev/camera_center")
ServoControler("/dev/arm_right", "/dev/arm_left")
XLeRobotAgent(...)
```

并注册官方工具：

```python
create_move_forward
create_move_backward
create_turn_left
create_turn_right
create_strafe_left
create_strafe_right
create_go_to_precision_mode
create_go_to_normal_mode
create_look_around
finish_task
```

如果 `~/.cache/robocrew/tools/vla_tools.json` 里有启用的工具，还会追加 `create_vla_single_arm_manipulation(...)`。

当前 client 入口已经复刻这个加载逻辑：读取同一个 VLA 配置文件，然后调用官方
`create_vla_single_arm_manipulation(...)`。没有配置时不会注册手臂工具。

## 官方提示词

控制器提示词：

```text
E:\lerobot\envs\lerobot-gpu\Lib\site-packages\robocrew\robots\XLeRobot\xlerobot.prompt
```

规划器提示词：

```text
E:\lerobot\envs\lerobot-gpu\Lib\site-packages\robocrew\robots\XLeRobot\planner.prompt
```

当前 `config.py` 已改成运行时读取官方 `xlerobot.prompt`，不再手写一大段中文限制规则。为了使用体验，只在官方 prompt 后追加：

```text
用中文回复用户
```

没有再追加“中文工具词典”，避免把模型过度限制成固定自动化流程。

补充：当前安装的 `agent_setup.py` 没有显式把这个 prompt 文件传给 `XLeRobotAgent`，因此纯 UI 路线会退回 `LLMAgent.py` 里的 `base_system_prompt`。本 client 实验入口选择使用随包发布的 `xlerobot.prompt`，因为它是官方放在 XLeRobot 目录下的机器人专用提示词，内容比通用 `base_system_prompt` 更贴合双臂移动机器人。

## 官方头部控制

官方没有单独提供 `create_look_up()` 或 `create_look_down()`。

官方上下视角藏在模式切换工具里：

```python
create_go_to_precision_mode(...)
    servo_controller.turn_head_to_vla_position(50)

create_go_to_normal_mode(...)
    servo_controller.reset_head_position()
```

官方左右扫视在：

```python
create_look_around(...)
    servo_controller.turn_head_yaw(-120)
    servo_controller.turn_head_yaw(-40)
    servo_controller.turn_head_yaw(40)
    servo_controller.turn_head_yaw(120)
    servo_controller.turn_head_yaw(0)
```

所以官方头部逻辑是：

```text
左右看：look_around
低头近距离观察：go_to_precision_mode
恢复普通视角：go_to_normal_mode
```

## 官方图像增强

官方相机：

```text
E:\lerobot\envs\lerobot-gpu\Lib\site-packages\robocrew\core\camera.py
```

会调用：

```text
E:\lerobot\envs\lerobot-gpu\Lib\site-packages\robocrew\core\utils.py
```

核心函数：

```python
basic_augmentation(image, h_fov, center_angle, navigation_mode)
```

它会画：

```text
normal 模式：水平角度标尺、LEFT/RIGHT 方向提示
precision 模式：额外画绿色 arm range 机械臂范围线
```

当前 `client_camera_adapter.py` 已补回这一步。树莓派 host 返回图像后，Windows client 会按官方方式添加角度标尺和 precision 绿色范围线，再发给 LLM。

## 官方 LLMAgent 循环

文件：

```text
E:\lerobot\envs\lerobot-gpu\Lib\site-packages\robocrew\core\LLMAgent.py
```

每轮流程：

```text
1. main_camera.capture_image(camera_fov=..., navigation_mode=...)
2. 把图像转成 base64 image_url
3. 追加当前任务文本
4. 调用大模型
5. 执行模型选择的 tool
6. 如果 tool 是 go_to_precision_mode，navigation_mode 设为 precision
7. 如果 tool 是 go_to_normal_mode，navigation_mode 设为 normal
8. 如果 tool 是 finish_task，结束当前任务
```

## 当前 client 模式改动边界

官方默认硬件在运行 Agent 的机器上：

```text
RobotCamera 直接读 /dev/camera_center
ServoControler 直接读 /dev/arm_right 和 /dev/arm_left
```

我们的硬件在树莓派 host 上，所以只替换硬件访问层：

```text
ClientServoControler
  使用 XLerobotClient.send_action/get_observation

ClientRobotCamera
  从 XLerobotClient.get_observation 取图像
  再调用官方 basic_augmentation
```

正式 Agent 注册官方 `create_*` 工具，并额外注册 `client_head_tools.py` 中的头部预留接口包装工具。它们不改官方包，不写运动学算法，只调用官方 ServoControler 约定的 `turn_head_yaw()` 和 `turn_head_pitch()`。

手臂动作不走这种方式。手臂只接官方 VLA/GR00T policy 工具，不在 client 侧拼关节目标。

为了让 Windows client Agent 也能用官方 VLA 工具，当前新增了
`RemoteXLerobotSingleArm`。它实现 LeRobot `Robot` 接口，但只做远程字段映射：

```text
get_observation(): 从树莓派 xlerobot_host 读 right_arm_*.pos / left_arm_*.pos 和摄像头图像
send_action(): 把 policy 输出的 shoulder_pan.pos 等字段映射回 right_arm_*.pos / left_arm_*.pos
```

`official_arm_tools.py` 在创建 VLA 工具前，把官方工具里的 `SOFollowerConfig`
替换为 `RemoteXLerobotSingleArmConfig`。后续执行流程仍然是官方
`create_vla_single_arm_manipulation(...)` 和官方 `RobotClient`。

另一个官方允许的手臂入口是保存姿态：

```python
ServoControler.save_arm_position(...)
ServoControler.set_saved_position(...)
```

当前 client 侧只适配 `set_saved_position`，用于执行已经保存在
`~/.cache/robocrew/positions/` 下的官方姿态文件。它不是轨迹算法，也不做 IK。

## 当前实测摄像头 key

当前 USB hub 顺序下，树莓派 host 的 observation key 与真实物理摄像头对应关系是：

```text
camera_0 = 头部主摄像头
camera_2 = 右臂摄像头
head     = 左臂摄像头
```

因此 client Agent 默认主摄像头使用 `camera_0`。如果以后重新插拔 USB 设备导致顺序变化，先运行 `--save-camera-previews` 重新确认，再更新 `config.py`。

