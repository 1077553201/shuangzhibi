# XLeRobot 智能体改造作品说明书：技术路线与功能总结

本文档用于整理本次 XLeRobot 改造过程中的关键技术、系统架构、通信协议、功能实现和后续可扩展方向，方便后续撰写作品说明书、产品简介、答辩材料或教学文档。

## 1. 项目定位

本项目基于 XLeRobot / LeRobot / RoboCrew 官方生态，将原本偏本机直连硬件的机器人控制流程，改造成适合“树莓派负责硬件，Windows 高性能电脑负责智能控制”的 host-client 架构。

最终目标是让机器人具备以下能力：

- 树莓派稳定连接底盘、舵机、摄像头等硬件。
- Windows 端运行大模型、视觉模型、语音识别、数据采集、训练和策略推理。
- 大模型通过工具调用方式控制机器人移动、转头、观察、录制和播放手臂动作。
- 用户可以通过终端文字或语音自然语言与机器人连续交互。
- 复杂手臂动作尽量走官方 RoboCrew / VLA / ACT 路线，不手写运动学算法。

## 2. 总体架构

```text
用户
  ├── 终端文字输入
  └── 麦克风语音输入
          ↓
Windows 高性能电脑
  ├── voice_control_agent.py
  │     ├── 本地 Whisper 语音识别
  │     ├── Windows SAPI 语音播报
  │     └── 调用现有 LLM Agent
  ├── run_robocrew_client_agent.py
  │     ├── RoboCrew LLMAgent
  │     ├── MiMo/OpenAI-compatible 多模态大模型
  │     ├── RoboCrew 官方工具 create_*
  │     ├── 自定义 client 适配器
  │     └── VLA / ACT 工具入口
  ├── XLerobotClient
  │     ├── ZMQ 命令通道
  │     └── ZMQ observation 通道
  └── 数据集 / 模型训练 / policy server
          ↓ Ethernet / Wi-Fi
树莓派
  ├── xlerobot_host.py
  ├── XLerobot
  ├── Feetech 舵机总线
  ├── OpenCV 摄像头
  └── 轮式底盘
          ↓
XLeRobot 机器人本体
```

这种架构的核心思想是：

- 树莓派只做硬件 host，减少其计算压力。
- Windows 负责重计算任务，例如 LLM、视觉识别、语音识别、ACT 训练、policy 推理。
- 两端通过网络通信，机器人硬件不需要直接插到 Windows。

## 3. 关键技术路线

### 3.1 Host-Client 控制路线

官方 XLeRobot 原本支持 host-client 控制，本项目沿用并扩展了这条路线。

树莓派端运行：

```bash
python -m lerobot.robots.xlerobot.xlerobot_host
```

Windows 端通过：

```python
XLerobotClient
```

连接树莓派 host。

这条路线解决的问题：

- Windows 不直接连接 USB 舵机和摄像头。
- 树莓派作为硬件中转层，负责采集 observation 和执行 action。
- Windows 可以远程运行大模型、训练程序和控制程序。

### 3.2 通信协议：ZMQ

Windows 和树莓派之间使用 ZeroMQ 进行通信。

主要端口：

```text
5555：命令通道，Windows -> 树莓派
5556：观测通道，树莓派 -> Windows
```

命令通道：

```text
Windows XLerobotClient.send_action()
  -> JSON 字符串
  -> ZMQ PUSH
  -> 树莓派 host ZMQ PULL
  -> robot.send_action()
```

观测通道：

```text
树莓派 robot.get_observation()
  -> 电机状态 + 摄像头画面
  -> 摄像头帧 JPEG 编码
  -> base64 字符串
  -> JSON
  -> ZMQ PUSH
  -> Windows ZMQ PULL
  -> 解码为 observation
```

ZMQ 的优势：

- 低延迟。
- 实现简单。
- 对实时控制足够轻量。
- 能直接传输 JSON 控制包和图像数据。

### 3.3 图像传输与摄像头映射

树莓派端使用 OpenCV 读取摄像头，然后在 host 中将图像编码为 JPEG，再转成 base64 放入 JSON 发送。

Windows 端解码后得到 numpy 图像。

当前实测摄像头映射：

```text
camera_0 = 头部主摄像头
camera_2 = 右臂摄像头
head     = 左臂摄像头
```

曾经遇到的问题：

- USB hub 顺序变化会导致 `/dev/video*` 顺序变化。
- 图像红蓝通道反了。

解决方案：

- 在 `config.py` 中固定当前 observation key 与物理摄像头对应关系。
- 在 client 侧增加 `CAMERA_SWAP_RED_BLUE` 开关修正颜色。

### 3.4 RoboCrew 官方工具适配路线

本项目坚持优先使用 RoboCrew 官方工具函数，不重写官方工具逻辑。

官方工具示例：

```python
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

官方工具原本期望本机有：

```text
ServoControler
RobotCamera
```

本项目实现了 client 适配器：

```text
client_servo_adapter.py
client_camera_adapter.py
```

它们的作用：

- 对外保持 RoboCrew 官方需要的方法名。
- 对内改成通过 `XLerobotClient` 远程访问树莓派 host。
- 这样官方 `create_*` 工具可以继续原样使用。

### 3.5 大模型工具调用路线

大模型 Agent 使用 RoboCrew 的 `LLMAgent` / `XLeRobotAgent`。

模型配置使用 OpenAI-compatible 接口：

```text
LLM_MODEL = openai:mimo-v2.5
OPENAI_COMPATIBLE_API_BASE = 小米 MiMo OpenAI-compatible API 地址
```

大模型具备能力：

- 读取摄像头图像。
- 理解用户自然语言。
- 根据任务选择工具。
- 调用工具控制机器人。
- 调用 `finish_task` 给出任务总结。

已经实现的自然语言能力：

```text
向前走一点
往后退
左转
右转
看到什么
环顾四周
抬头
低头
播放 both_show
录制双臂动作 both_show 5 秒
列出录制好的动作
```

### 3.6 语音控制路线

新增：

```text
voice_control_agent.py
```

语音控制采用“文字/语音混合入口”：

```text
直接输入文字 -> 执行文字任务
输入 v       -> 录音一次 -> ASR -> 执行任务
输入 v 10    -> 录音 10 秒 -> ASR -> 执行任务
输入 vv      -> 自动连续语音监听
```

语音识别路线：

```text
麦克风录音
  -> sounddevice
  -> wav 文件
  -> Whisper ASR
  -> 简体中文后处理
  -> 常见机器人命令纠错
```

当前推荐 ASR 后端：

```text
Transformers Whisper + PyTorch CUDA
```

原因：

- 当前 Windows 环境中 PyTorch CUDA 13 可用。
- RTX 4060 可以直接用于 Transformers Whisper。
- faster-whisper 的 ctranslate2 依赖 CUDA 12 的 `cublas64_12.dll`，与当前 Torch CUDA 13 环境不匹配，因此不作为默认 GPU 路线。

语音播报路线：

```text
Windows SAPI
```

优点：

- 本地可用。
- 不额外依赖网络。
- 不需要额外 Python TTS 模型。

语音安全设计：

- 静音检测，避免空录音进入长时间识别。
- 识别为空时不执行动作。
- 安全词：

```text
停止
别动
停下
不要动
```

- 自动监听退出词：

```text
停止监听
退出语音
结束语音
停止语音
```

- 自动监听中还可以按 `q` 或 `Esc` 回到终端输入。

### 3.7 手臂动作录制与播放

为了快速实现手臂展示和固定动作，本项目实现了“动作序列录制/播放”路线。

核心文件：

```text
official_motion_tools.py
录制官方动作序列.py
播放官方动作序列.py
```

实现方式：

```text
录制：
  1. Windows 给 host 发送释放手臂扭矩命令。
  2. 用户手动摆动手臂。
  3. Windows 读取 observation 中的手臂关节位置。
  4. 保存为动作序列 JSON。
  5. 自动恢复手臂扭矩。

播放：
  1. 读取动作序列 JSON。
  2. 按原始时间顺序调用 XLerobotClient.send_action()。
  3. host 执行动作。
```

这条路线没有做：

- IK。
- 轨迹规划。
- 手写抓取算法。
- 强化学习。

它只使用：

```text
get_observation()
send_action()
```

因此适合短时间内做稳定演示。

支持：

```text
右臂动作录制
左臂动作录制
双臂动作录制
动作播放
LLM 调用录制/播放工具
```

### 3.8 运行时手臂扭矩控制

为了避免每次录制动作都重启 host，扩展了 host 控制包：

```text
__xlerobot_host_command = set_arm_torque
arm_side = left / right / both
enabled = true / false
```

这样 Windows 可以在运行时控制：

```text
释放右臂
释放左臂
释放双臂
恢复右臂上力
恢复左臂上力
恢复双臂上力
```

解决的问题：

- 不需要每次设置 `XLEROBOT_PASSIVE_LEADER_ARM` 后重启 host。
- 大模型可以直接触发“录制动作”，录完马上播放。
- 演示流程更流畅。

### 3.9 VLA / ACT 数据采集与训练路线

本项目也实验了官方 VLA_ACT 路线。

核心思路：

- 使用 LeRobot 官方 `record.py` 数据格式。
- 使用 host-client 方式替代本机硬件直连。
- 通过 leader/follower 或动作录制方式采集 episode。
- 使用 ACT policy 训练。
- 通过 policy server 或 record.py policy 参数真机试跑。

涉及脚本：

```text
scripts/powershell/运行官方VLA_ACT_host_client左臂leader右臂执行录制.ps1
scripts/powershell/运行官方ACT训练_可乐罐到篮子.ps1
scripts/powershell/运行官方ACT模型真机试跑_可乐罐到篮子.ps1
```

这一路线更接近官方 VLA / ACT 训练流程，但对数据量、动作质量、硬件校准要求更高。

当前结论：

- 演示优先使用 LLM + 官方工具 + 动作序列。
- 后续正式抓取任务再继续扩大 ACT 数据集。

## 4. 已实现功能清单

### 4.1 底盘控制

支持：

```text
前进
后退
左移
右移
左转
右转
停止
```

控制方式：

- 终端文字。
- 语音识别。
- 大模型工具调用。

### 4.2 头部控制

支持：

```text
左看
右看
抬头
低头
回中
环顾四周
普通观察模式
近距离观察模式
```

实现方式：

- 优先调用 RoboCrew 官方 `go_to_precision_mode`、`go_to_normal_mode`、`look_around`。
- 对任意抬头/低头/左右转，使用官方 ServoControler 预留接口 `turn_head_yaw()`、`turn_head_pitch()` 做 client 适配。

### 4.3 视觉观察

支持：

```text
看到什么
环顾四周
观察桌面
观察机器人前方
```

实现方式：

- Windows 端从 host observation 获取图像。
- 将图像传给支持视觉输入的大模型。
- 大模型生成自然语言描述。

### 4.4 手臂动作

支持：

```text
录制右臂动作
录制左臂动作
录制双臂动作
播放已录动作
列出已录动作
```

示例：

```text
录制双臂动作 both_show 5 秒
播放 both_show
录制右臂动作 毕业 10 秒
播放 毕业
```

### 4.5 语音交互

支持：

```text
终端文字输入
单次语音输入
指定时长语音输入
自动连续语音监听
语音播报执行结果
```

交互方式：

```text
任务或 v> 向前走一点
任务或 v> v
任务或 v> v 10
任务或 v> vv
```

## 5. 关键文件说明

```text
run_robocrew_client_agent.py
  正式 LLM Agent 入口。

voice_control_agent.py
  文字/语音混合控制入口。

config.py
  IP、模型、摄像头、提示词、安全参数。

client_servo_adapter.py
  RoboCrew ServoControler 的 host-client 适配器。

client_camera_adapter.py
  RoboCrew RobotCamera 的 host-client 适配器。

official_motion_tools.py
  动作序列录制/播放工具。

official_arm_pose_tools.py
  手臂静态姿态保存/执行工具。

official_arm_tools.py
  官方 VLA 工具加载器。

xlerobot_host.py
  树莓派 host，负责硬件控制和 ZMQ 通信。
```

## 6. 当前演示建议

推荐演示路线：

1. 启动树莓派 host。
2. 启动 Windows 语音/文字混合 Agent。
3. 通过文字让机器人描述看到什么。
4. 输入 `v`，通过语音让机器人移动或转头。
5. 输入 `录制双臂动作 both_show 5 秒`，手动摆动作。
6. 输入或说 `播放 both_show`。
7. 输入 `vv`，展示连续语音对话。
8. 说 `停止监听` 回到终端。

推荐命令：

```powershell
E:\lerobot\envs\lerobot-gpu\Scripts\python.exe E:\lerobot\robocrew_client_agent\voice_control_agent.py --asr-backend transformers --device cuda --model medium --seconds 6
```

## 7. 项目亮点

### 7.1 分布式机器人智能体架构

树莓派负责硬件，Windows 负责智能推理。这让低功耗机器人也能使用高性能 GPU、大模型和本地语音识别。

### 7.2 官方生态优先

没有完全重写控制系统，而是通过适配器复用：

- LeRobot。
- XLeRobot。
- RoboCrew 官方工具。
- ACT / VLA 官方训练路线。

### 7.3 多模态交互

系统同时支持：

- 文本输入。
- 语音输入。
- 摄像头视觉输入。
- 语音输出。
- 机器人动作输出。

### 7.4 低门槛示教能力

用户可以直接用手摆动机械臂，录制成动作序列，再由大模型通过自然语言调用。

这让机器人在没有大量训练数据的情况下，也能快速获得“挥手”“展示”“抓取尝试”“放置”等演示动作。

### 7.5 可扩展到多机器人

由于 Windows 端通过 IP 和端口连接 host，理论上可以扩展到多台 XLeRobot：

```text
Windows Agent
  ├── XLeRobot A host
  └── XLeRobot B host
```

后续可以实现：

- 一个 Agent 控制两台机器人。
- 多机器人协同观察。
- 一个机器人执行，另一个机器人辅助观察或递送。

## 8. 当前限制与后续优化

当前限制：

- 语音识别仍依赖 Whisper 模型，嘈杂环境下可能误识别。
- 动作序列只是回放关节轨迹，不具备自适应抓取能力。
- ACT/VLA 需要更多高质量数据才能稳定完成抓取任务。
- 摄像头和 USB 设备顺序变化仍需要人工确认。

后续优化方向：

- 增加唤醒词。
- 增加语音端点检测，实现自然停止录音。
- 增加激光雷达或深度相机。
- 扩大 ACT 数据集。
- 将动作序列工具和 VLA 工具结合，形成“固定技能 + 学习技能”的混合系统。
- 增加多机器人管理器。
- 增加 Web UI 或移动端控制界面。

## 9. 一句话产品简介

本项目将 XLeRobot 改造成一个由 Windows 高性能电脑驱动的多模态智能机器人系统：树莓派负责硬件控制，Windows 端运行大模型、视觉、语音识别和策略推理，用户可以通过文字或语音与机器人连续对话，并让机器人完成观察、移动、转头、录制和播放手臂动作等任务。

## 10. 简短作品介绍

这是一个基于 XLeRobot、LeRobot 和 RoboCrew 官方生态构建的智能机器人作品。系统采用 host-client 架构，树莓派连接机器人硬件，Windows 高性能电脑负责大模型推理、语音识别、视觉理解和策略训练。用户可以通过文字或语音向机器人下达自然语言指令，例如“向前走一点”“看到什么”“播放挥手动作”等。机器人能够读取摄像头画面、调用官方工具控制底盘和头部，并支持通过手动示教录制机械臂动作，再由大模型调用执行。该作品展示了低成本机器人接入多模态大模型、语音交互和学习型机器人控制的完整技术路线。

