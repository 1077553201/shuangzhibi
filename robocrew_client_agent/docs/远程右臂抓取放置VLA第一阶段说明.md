# 远程右臂抓取放置 VLA 第一阶段说明

本文档记录我们接下来从“看到物体、抓起物体、放到指定位置”开始训练 XLeRobot 手臂策略的第一阶段方案。

## 目标任务

先做单臂、单物体、固定场景的 pick-and-place：

- 机器人底盘固定不动。
- 使用右臂执行抓取和放置。
- 物体固定为一个红色可口可乐空罐。
- 放置区域固定为 XLeRobot 自带置物架。
- 每次采集前把头部摄像头切到同一 VLA 视角。

推荐第一条任务描述：

```text
Pick up the red Coca-Cola can from the table and place it on the robot shelf.
```

中文理解就是：从桌面抓起红色可口可乐空罐，放到机器人置物架上。

## 现场摆放要求

当前任务使用可口可乐空罐和机器人自带置物架。采集时建议固定以下条件：

- 可乐罐竖直摆放，不要横放，避免滚动。
- 可乐罐底部位置用胶带或桌面标记固定。
- 置物架位置不要变，最好在头部主摄像头里完整可见。
- 头部主摄像头 `camera_0` 要同时看到桌面上的罐子和置物架入口。
- 右臂摄像头 `camera_2` 要能看到夹爪靠近罐子的过程。
- 桌面背景尽量简单，避免红色杂物干扰模型。

## 为什么先做这个

这是官方 VLA 路线里最容易闭环的任务形态：

- 单臂动作维度只有 6 个关节，训练比双臂容易。
- 头部主摄像头和右臂腕部摄像头能同时看到目标与夹爪。
- 数据采集质量可控，失败 episode 容易重录。
- 后续可以直接接入 RoboCrew 官方 VLA 工具，作为 LLM Agent 的一个工具。

“比耶”“挥手”这类固定姿态更适合 RoboCrew 保存姿态工具；“看见物体并抓取放置”才适合 VLA。

## 官方路线修正

根据 XLeRobot 官方 VLA_ACT 文档，ACT/VLA 数据采集的正式控制设备只有两条：

1. 单臂任务：`so101_follower` 右臂 + `so101_leader` leader arm。
2. 双臂/底盘任务：`xlerobot` + `xlerobot_vr`。

官方文档明确说当前教程使用 VR 和 leader arm 作为控制设备，键盘和 xbox 还不是
VLA_ACT 教程里的正式采集控制器。因此，键盘遥操作脚本只能作为调试或临时验证，
不能作为我们后续 ACT/VLA 训练的主入口。

正式训练链路应当是：

1. 使用官方 `LeRobotDataset.create()` 创建数据集。
2. 使用官方 `record.py` 采集 episode。
3. 单臂采集使用官方 `--teleop.type=so101_leader`。
4. 双臂/底盘采集使用官方 `--teleop.type=xlerobot_vr`。
5. 训练使用官方 `lerobot-train` 或 `python -m lerobot.scripts.train`。
6. 推理使用官方 `lerobot.async_inference.policy_server`。
7. Agent 工具使用 RoboCrew 官方 `create_vla_single_arm_manipulation()`。

本阶段不写自定义逆运动学、不写手臂轨迹规划、不自己定义抓取算法。之前新增的
`remote_keyboard_pick_place_recorder.py` 保留为调试脚本，不再视为正式 VLA 采集方案。

## 当前硬件映射

树莓派 host IP：

```text
192.168.10.239
```

摄像头映射：

```text
camera_0 = 头部主摄像头
camera_2 = 右臂摄像头
head     = 左臂摄像头
```

单臂 VLA 建议使用：

```text
main      -> camera_0
right_arm -> camera_2
```

## 关于左臂当 leader

官方文档中的 leader arm 指的是一套单独的 leader 手臂，例如 `so101_leader`，它给 follower arm 提供遥操作动作。

我们现在的左臂属于同一台 XLeRobot 的 follower 结构，不等同于官方 leader arm。直接把左臂姿态映射到右臂会遇到几个问题：

- 左臂可能已经上 torque，手动摆动会被电机保持力抵抗。
- 左右臂结构镜像，关节正负方向不一定可以直接复制。
- 如果直接把左臂关节值发给右臂，可能造成异常姿态。

所以第一阶段先做安全的“远程数据集写入管线”和“右臂当前状态记录”。后续如果要尝试左臂带右臂，先做 dry-run 打印映射，再逐关节小范围验证。

## 第一阶段链路自检脚本

以下脚本只用于确认远程 host、图像、颜色、LeRobot 数据格式是否能写通：

```text
E:\lerobot\robocrew_client_agent\remote_pick_place_dataset_recorder.py
```

它做三件事：

1. 连接树莓派 `xlerobot_host`。
2. 读取右臂 6 个关节、头部摄像头、右臂摄像头。
3. 按 LeRobot 官方数据集格式保存 episode。

第一版 action 来源为右臂当前关节位置，适合先验证数据集格式、图像写入、episode 保存和后续训练命令。

以下脚本不是官方 VLA_ACT 采集入口，只作为键盘调试工具保留：

```text
E:\lerobot\robocrew_client_agent\remote_keyboard_pick_place_recorder.py
```

它的限制：

- 键盘监听使用 LeRobot 官方 `KeyboardTeleop`。
- 右臂末端控制参考 XLeRobot 键盘示例。
- 数据集写入继续使用官方 `LeRobotDataset.create()`、`build_dataset_frame()`、`dataset.save_episode()`。
- 但它不是官方 VLA_ACT 文档推荐的 leader arm 或 VR 采集方案。

右臂键位：

```text
8 / 2 = 末端 x+ / x-
4 / 6 = 末端 y+ / y-
1 / 3 = pitch+ / pitch-
7 / 9 = shoulder_pan+ / shoulder_pan-
/ / * = wrist_roll+ / wrist_roll-
+ / - = 夹爪打开 / 夹爪夹紧
0     = 把控制目标同步到当前姿态
ESC   = 提前结束当前 episode
```

注意：官方示例里的 reset 会回到 0 位。为避免真实机械臂突然归零，本脚本把 `0` 改成“目标同步到当前姿态”，只用于消除遥操作目标漂移。

## 正式采集入口

### 单臂 ACT 采集

如果我们要做“右臂抓可乐罐放到置物架”，官方路径应优先使用 leader arm：

```powershell
python E:\lerobot\lerobot\src\lerobot\record.py `
  --robot.type=so101_follower `
  --robot.port=/dev/right_arm `
  --robot.id=robot_right_arm `
  --robot.cameras="{ head: {type: opencv, index_or_path: '/dev/video0', width: 640, height: 480, fps: 20}, right: {type: opencv, index_or_path: '/dev/video2', width: 640, height: 480, fps: 20} }" `
  --teleop.type=so101_leader `
  --teleop.port=/dev/ttyACM_LEADER `
  --teleop.id=my_leader_arm `
  --display_data=true `
  --dataset.repo_id=local/xlerobot_right_coke_can_to_shelf `
  --dataset.root=E:\lerobot\datasets\xlerobot_right_coke_can_to_shelf_official `
  --dataset.num_episodes=50 `
  --dataset.single_task="Pick up the red Coca-Cola can from the table and place it on the robot shelf."
```

上面的 `/dev/right_arm`、`/dev/video0`、`/dev/video2`、`/dev/ttyACM_LEADER`
是官方文档风格的占位符。实际使用前必须替换成当前设备真实端口。

### VR 全身采集

如果要按官方 XLeRobot 全身路线走，则使用官方 `xlerobot_vr`：

```powershell
python E:\lerobot\lerobot\src\lerobot\record.py `
  --robot.type=xlerobot `
  --robot.cameras="{ head: {type: opencv, index_or_path: '/dev/video0', width: 640, height: 480, fps: 20}, right: {type: opencv, index_or_path: '/dev/video2', width: 640, height: 480, fps: 20}, left: {type: opencv, index_or_path: '/dev/video4', width: 640, height: 480, fps: 20} }" `
  --dataset.repo_id=local/xlerobot_coke_can_to_shelf `
  --dataset.root=E:\lerobot\datasets\xlerobot_coke_can_to_shelf_vr `
  --dataset.single_task="Pick up the red Coca-Cola can from the table and place it on the robot shelf." `
  --display_data=true `
  --teleop.type=xlerobot_vr
```

VR 路线需要先把 `XLeRobot/software/src/record.py` 和
`XLeRobot/software/src/teleporators/xlerobot_vr` 按官方文档复制到 LeRobot 源码对应位置。

我们当前是 Windows + 树莓派 host-client 模式，不把串口和摄像头原生接到 Windows。
因此不能使用 `--robot.type=xlerobot` 和 `/dev/video*`，而应使用已经在 LeRobot 中提供的
`--robot.type=xlerobot_client`：

```powershell
E:\lerobot\robocrew_client_agent\scripts\powershell\运行官方VLA_ACT_host_client录制.ps1
```

这个脚本仍然运行官方 `record.py`，只是把硬件层改成：

```text
Windows record.py -> xlerobot_client -> 192.168.10.239 xlerobot_host -> 真实 XLeRobot
```

不要直接运行 `E:\lerobot\lerobot\src\lerobot\record.py` 文件路径。Windows 直接跑包内文件时，
`lerobot\types.py` 会和 Python 标准库 `types` 冲突。应使用：

```powershell
E:\lerobot\envs\lerobot-gpu\Scripts\python.exe -m lerobot.record ...
```

## 建议采集数量

先跑通：

```text
3 个 episode，每个 8-12 秒
```

正式训练：

```text
50 个以上成功 episode
```

更稳：

```text
100 个以上成功 episode
```

坏数据不要保留。抓空、碰歪、拖拽失败、光照变化过大、背景有人移动，都应该重录。

## 推荐操作顺序

1. 先确认我们采用哪种官方控制设备：`so101_leader` 或 VR。
2. 用 RoboCrew 官方头部工具把头部切到 VLA 视角，并保持每次一致。
3. 用官方 `lerobot-find-cameras opencv` 确认三个摄像头真实设备号。
4. 按官方命令运行 `record.py`，先保存 1 个短 episode。
5. 用检查脚本确认数据集任务、视频字段、颜色、帧数、action 维度。
6. 采 20 个小样本做首次 ACT 训练验证。
7. 质量通过后采 50-100 个成功 episode。
8. policy server 加载训练结果。
9. RoboCrew Agent 通过官方 VLA 工具调用策略。

测试命令：

```powershell
E:\lerobot\envs\lerobot-gpu\Scripts\python.exe E:\lerobot\robocrew_client_agent\remote_pick_place_dataset_recorder.py --episodes 1 --seconds 3 --fps 5
```

正式演示数据不要使用键盘脚本采集。键盘脚本只用于确认远程控制和数据格式，
正式数据以官方 `record.py + so101_leader` 或 `record.py + xlerobot_vr` 为准。

录完后继续用检查脚本确认任务、视频字段和颜色：

```powershell
E:\lerobot\envs\lerobot-gpu\Scripts\python.exe E:\lerobot\robocrew_client_agent\检查LeRobot数据集.py 数据集目录 --save-preview
```

正式 VLA 数据必须保留图像字段。脚本默认使用 LeRobot 官方视频格式保存摄像头，不要加 `--no-videos`。

Windows 上多摄像头并行编码可能生成当前用户不可读的 mp4。本脚本保存 episode 时使用官方
`dataset.save_episode(parallel_encoding=False)`，只关闭并行编码，不改变 LeRobot 数据格式。

颜色说明：LLM 预览路径和 VLA 数据集路径不同。LLM 预览最终用 OpenCV 编码 JPEG，需要按
`CAMERA_SWAP_RED_BLUE` 做修正；VLA 数据集最终用 LeRobot/HF 图像写入，输入按 RGB 语义处理，
因此远程单臂数据源不再额外交换红蓝通道。旧的颜色反转数据不要用于训练。

## 当前边界

这个阶段先不追求立刻抓成功，而是先把“数据 -> 训练 -> policy server -> agent 工具”的通道打通。

只要通道通了，后面提升成功率主要靠：

- 更稳定的任务定义；
- 更一致的相机视角；
- 更多高质量 episode；
- 更干净的光照和背景；
- 更少的动作抖动。

