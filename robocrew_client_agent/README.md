# XLeRobot RoboCrew Client Agent

这是 XLeRobot 的 **RoboCrew 官方 LLM Agent 路线 + Windows client 控制模式** 实验工程。

官方示例默认让运行 Agent 的机器直接访问硬件：

```text
RobotCamera("/dev/camera_center")
ServoControler("/dev/arm_right", "/dev/arm_left")
```

我们的目标是改成：

```text
Windows 高性能电脑
  跑 Agent / LLM / 视觉 / policy server
  通过 XLerobotClient 连接树莓派 host

树莓派
  只跑 xlerobot_host
  连接 USB 舵机、摄像头、底盘
```

## 当前原则

优先调用 RoboCrew 官方函数，不重写官方工具逻辑。

官方移动工具来自：

```python
from robocrew.robots.XLeRobot.tools import (
    create_move_forward,
    create_move_backward,
    create_strafe_left,
    create_strafe_right,
    create_turn_left,
    create_turn_right,
)
```

我们只提供一个很薄的适配器：

```python
from client_servo_adapter import ClientServoControler
```

这个适配器模拟官方 `ServoControler` 的方法名：

```text
go_forward(meters)
go_backward(meters)
strafe_left(meters)
strafe_right(meters)
turn_left(degrees)
turn_right(degrees)
turn_head_yaw(degrees)
turn_head_pitch(degrees)
turn_head_to_vla_position()
reset_head_position()
set_saved_position()
```

这样 RoboCrew 官方 `create_*` 工具可以原样使用，只是底层从“本机串口”换成“Windows 通过 XLerobotClient 访问树莓派 host”。

## 目录结构

```text
robocrew_client_agent\
├── run_robocrew_client_agent.py          # 正式 LLM Agent 入口，日常演示优先运行它
├── config.py                             # IP、模型、摄像头、提示词、动作安全参数
├── path_setup.py                         # 保证本地 lerobot/src 优先导入
├── client_*.py                           # Windows client 适配官方 RoboCrew 控制器/摄像头
├── official_*_tools.py                   # 注册给 LLM 的官方工具封装和动作序列工具
├── remote_*.py                           # host-client 数据集/单臂适配实验工具
├── 录制官方动作序列.py                  # 命令行录制手臂动作，录制时自动松手臂
├── 播放官方动作序列.py                  # 命令行播放已经录好的手臂动作
├── 检查LeRobot数据集.py                 # 检查本地 LeRobot 数据集和视频字段
├── docs\                                # 教学文档、路线分析、VLA/ACT 说明
├── scripts\powershell\                  # 录制、合并、训练、试跑等 PowerShell 脚本
├── templates\                           # RoboCrew/VLA 配置模板
└── outputs\                             # 摄像头预览图、调试输出图片
```

## 核心文件说明

| 文件 | 作用 |
| --- | --- |
| `run_robocrew_client_agent.py` | 连续对话 Agent。LLM 通过工具控制底盘、头部、录制/播放手臂动作、调用 VLA 工具。 |
| `voice_control_agent.py` | 语音连续对话入口。本地录音、本地 Whisper 识别，复用现有 Agent 执行任务，再用 Windows SAPI 播报结果。 |
| `config.py` | 统一配置树莓派 IP、机器人 ID、MiMo/OpenAI 兼容模型、摄像头 key、提示词、安全预算。 |
| `client_servo_adapter.py` | RoboCrew 官方 `ServoControler` 兼容适配器，内部使用 `XLerobotClient` 连接树莓派 host。 |
| `client_camera_adapter.py` | RoboCrew 官方 `RobotCamera` 兼容适配器，从 host observation 读取摄像头画面。 |
| `client_head_tools.py` | 头部工具，调用官方控制器预留的 `turn_head_yaw()` / `turn_head_pitch()` 方法。 |
| `official_arm_pose_tools.py` | 保存/执行手臂静态姿态。 |
| `official_motion_tools.py` | 录制/播放动作序列。只读 observation、只用 `send_action()` 回放，不做 IK/轨迹规划。 |
| `official_arm_tools.py` | 加载 RoboCrew 官方 VLA 工具配置。 |
| `remote_xlerobot_single_arm.py` | 远程单臂数据集适配层，服务于 ACT/VLA 数据采集。 |
| `录制官方动作序列.py` | 手动摆动手臂录动作的命令行入口。 |
| `播放官方动作序列.py` | 播放已录动作的命令行入口。 |

当前已接入的 RoboCrew 官方工具：

```text
create_move_forward
create_move_backward
create_strafe_left
create_strafe_right
create_turn_left
create_turn_right
create_go_to_precision_mode
create_go_to_normal_mode
create_look_around
finish_task
```

另外本地注册了头部预留接口工具：

```text
turn_head_left
turn_head_right
look_up
look_down
center_head
```

它们不改官方包，不写运动学算法，只调用官方 ServoControler 约定的：

```text
turn_head_yaw(degrees)
turn_head_pitch(degrees)
```

手臂高级操作不手写运动逻辑，后续只接官方预留工具：

```text
create_vla_single_arm_manipulation
create_groot_single_arm_manipulation
```

这两类工具需要先准备 policy server、官方 camera_config、arm_port、policy_name 等参数。

主目录只保留官方函数 client 适配路线。正式运行优先使用 `official_tools_smoke.py` 和 `run_robocrew_client_agent.py`。

早期自写工具层和临时研究缓存已经移出本目录，保存到：

```text
E:\lerobot\archive\robocrew_client_agent_pre_official_adapter
```

## Windows 环境状态

当前 Windows `lerobot-gpu` 环境已有：

```text
lerobot
torch cuda
ultralytics
```

当前还缺：

```text
robocrew
langchain_core
```

安装后才能运行 RoboCrew 官方工具和 Agent。

## 先测试官方工具 + client 适配器

确保树莓派 host 已经启动：

```bash
cd ~/xlerobot-dev/lerobot
source .venv/bin/activate
python -m lerobot.robots.xlerobot.xlerobot_host
```

Windows 先只连接并打印 observation keys：

```powershell
E:\lerobot\envs\lerobot-gpu\Scripts\python.exe E:\lerobot\robocrew_client_agent\official_tools_smoke.py
```

用官方 `create_move_forward()` 低速前进 0.05 米：

```powershell
E:\lerobot\envs\lerobot-gpu\Scripts\python.exe E:\lerobot\robocrew_client_agent\official_tools_smoke.py --move-forward 0.05
```

用官方 `create_turn_left()` 左转 10 度：

```powershell
E:\lerobot\envs\lerobot-gpu\Scripts\python.exe E:\lerobot\robocrew_client_agent\official_tools_smoke.py --turn-left 10
```

## RoboCrew Agent 入口

安装 RoboCrew 后运行：

```powershell
E:\lerobot\envs\lerobot-gpu\Scripts\python.exe E:\lerobot\robocrew_client_agent\run_robocrew_client_agent.py
```

也可以直接给明确任务。执行后会进入连续对话模式，不需要每次重启：

```powershell
E:\lerobot\envs\lerobot-gpu\Scripts\python.exe E:\lerobot\robocrew_client_agent\run_robocrew_client_agent.py --task "向前移动 0.05 米后停止"
```

如果只想执行一条任务后退出：

```powershell
E:\lerobot\envs\lerobot-gpu\Scripts\python.exe E:\lerobot\robocrew_client_agent\run_robocrew_client_agent.py --task "向前移动 0.05 米后停止" --once
```

如果 USB 顺序变化导致 `head`、`camera_0`、`camera_2` 对不上真实摄像头，先保存所有预览图：

```powershell
E:\lerobot\envs\lerobot-gpu\Scripts\python.exe E:\lerobot\robocrew_client_agent\run_robocrew_client_agent.py --save-camera-previews
```

预览图会保存到：

```text
E:\lerobot\robocrew_client_agent\outputs\camera_previews
```

确认真正的头部摄像头对应哪个 key 后，可以临时指定：

```powershell
E:\lerobot\envs\lerobot-gpu\Scripts\python.exe E:\lerobot\robocrew_client_agent\run_robocrew_client_agent.py --camera-key camera_0
```

当前已按实测固定为：

```text
camera_0 = 头部主摄像头
camera_2 = 右臂摄像头
head     = 左臂摄像头
```

如果以后 USB 顺序再次变化，再把 `config.py` 里的摄像头 key 更新为新的实测结果。

默认会隐藏终端里的超长图像/base64 调试输出。若确实要看 RoboCrew 原始输出：

```powershell
E:\lerobot\envs\lerobot-gpu\Scripts\python.exe E:\lerobot\robocrew_client_agent\run_robocrew_client_agent.py --show-raw-output
```

## 语音连续对话入口

第一阶段语音控制不改机器人控制链路，只是在现有 Agent 外面加一层文字/语音混合入口：

```text
终端文字 -> run_one_task() 执行
输入 v   -> 麦克风录音一次 -> 本地 faster-whisper 识别 -> run_one_task() 执行 -> Windows SAPI 语音播报
输入 vv  -> 自动连续录音/识别/执行，直到说“停止监听”
```

第一次运行前安装本地语音依赖：

```powershell
E:\lerobot\envs\lerobot-gpu\Scripts\python.exe -m pip install sounddevice faster-whisper
```

语音脚本默认设置：

```text
HF_ENDPOINT=https://hf-mirror.com
```

所以首次下载 faster-whisper 模型时会优先走 Hugging Face 镜像站。

启动语音控制：

```powershell
E:\lerobot\envs\lerobot-gpu\Scripts\python.exe E:\lerobot\robocrew_client_agent\voice_control_agent.py
```

运行后：

- 直接输入文字，会按文字任务执行。
- 输入 `v` 再按 Enter，会开始录音，默认录 5 秒，识别后交给同一个 LLM Agent 执行。
- 输入 `v 10` 会录音 10 秒，适合长一点的任务描述。
- 输入 `vv` 会进入自动连续语音监听，执行完一轮自动继续录下一句。
- 自动连续监听中，说“停止监听”会回到文字/语音混合输入。
- 自动连续监听每轮之间，也可以按 `q` 或 `Esc` 回到文字/语音混合输入。
- 执行完会继续等待下一句。

常用参数：

```powershell
E:\lerobot\envs\lerobot-gpu\Scripts\python.exe E:\lerobot\robocrew_client_agent\voice_control_agent.py --seconds 4 --model medium --device auto
```

默认 `--asr-backend auto` 会优先使用 PyTorch/Transformers 走显卡。你的环境里 PyTorch CUDA 可用，所以语音识别会优先跑 RTX 4060。

如果需要明确指定显卡：

```powershell
E:\lerobot\envs\lerobot-gpu\Scripts\python.exe E:\lerobot\robocrew_client_agent\voice_control_agent.py --asr-backend transformers --device cuda --model medium
```

默认使用 `medium` 模型，识别更稳但首次加载更慢。需要更快测试时可以改成：

```powershell
E:\lerobot\envs\lerobot-gpu\Scripts\python.exe E:\lerobot\robocrew_client_agent\voice_control_agent.py --seconds 4 --model base
```

`faster-whisper` 后端在当前机器上缺 CUDA 12 的 `cublas64_12.dll`，所以不作为默认显卡方案。需要强制使用它时：

```powershell
E:\lerobot\envs\lerobot-gpu\Scripts\python.exe E:\lerobot\robocrew_client_agent\voice_control_agent.py --asr-backend faster-whisper --device cpu
```

如果空录音被误判，可以调低静音阈值；如果环境噪声太多，可以调高：

```powershell
E:\lerobot\envs\lerobot-gpu\Scripts\python.exe E:\lerobot\robocrew_client_agent\voice_control_agent.py --silence-threshold 0.003
```

自动连续监听每轮之间默认停 1.5 秒等待键盘退出。可以调整：

```powershell
E:\lerobot\envs\lerobot-gpu\Scripts\python.exe E:\lerobot\robocrew_client_agent\voice_control_agent.py --auto-pause-seconds 2.5
```

安全词：

```text
停止
别动
停下
不要动
停止监听
```

听到“停止、别动、停下、不要动”时，只发送底盘停止，不继续调用大模型执行动作。听到“停止监听”时，退出自动连续监听。

这个入口使用：

```text
RoboCrew LLMAgent
RoboCrew 官方 create_move_forward 等工具
ClientServoControler 适配器
ClientRobotCamera 适配器
XLerobotClient
树莓派 xlerobot_host
```

## 后续手臂/VLA

手臂复杂动作仍然不建议手写关节逻辑。

当前已经接入官方 VLA 工具加载器：如果发现 RoboCrew 官方配置文件
`C:\Users\10775\.cache\robocrew\tools\vla_tools.json`，就会按官方 UI 的方式
调用 `create_vla_single_arm_manipulation(...)` 注册手臂工具。

VLA 工具内部的数据源已经换成：

```text
RemoteXLerobotSingleArm -> XLerobotClient -> 树莓派 xlerobot_host
```

所以 Windows 端不需要直接看到 `/dev/arm_right`。配置里的 `arm_port` 仍然保留
`/dev/arm_right` 或 `/dev/arm_left`，当前只用来判断控制右臂还是左臂。

没有这个配置时，Agent 不会暴露手臂工具，避免模型误判。

同时也接入了官方保存姿态接口：

```text
list_saved_arm_positions
go_to_saved_arm_position
```

这两个工具只执行 RoboCrew 官方 `~/.cache/robocrew/positions/` 中保存好的姿态，
不做自写运动算法。

详细说明见：

```text
E:\lerobot\robocrew_client_agent\docs\官方手臂VLA接入说明.md
```

后续路线：

```text
LLM Agent 选择工具
VLA policy server 负责机械臂动作推理
Client/Host 负责把动作送到硬件
```

这样能继续保持“官方函数优先”，同时符合我们的 Windows GPU + 树莓派 host 架构。

