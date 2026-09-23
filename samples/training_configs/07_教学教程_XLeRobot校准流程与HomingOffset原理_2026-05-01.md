# XLeRobot 校准流程与 Homing Offset 原理

日期：2026-05-01  
相关校准文件：

```text
/home/mh/.cache/huggingface/lerobot/calibration/robots/xlerobot/my_xlerobot_pc.json
```

## 1. 核心结论

运行 calibration 时，第一次提示按 Enter 前，你把关节摆到什么位置，会影响 `homing_offset`。

但这一步不是保存 Home Pose，也不是保存以后 reset 要回去的姿态。

更准确地说：

```text
第一次 Enter 前的位置
  -> 用来计算 homing_offset
  -> 让当前原始舵机位置变成半圈参考点，通常接近 2047

第二次摇完整个活动范围
  -> 用来记录 range_min 和 range_max
  -> 决定软件里的 -100、0、100 怎么映射到舵机位置
```

因此：

```text
校准中位 ≠ Home Pose
校准中位 ≠ reset 姿态
校准中位 ≠ 程序里所有关节 .pos=0 后一定会回到的位置
```

## 2. 校准流程的两个关键阶段

### 2.1 第一阶段：摆到中位后按 Enter

提示类似：

```text
Move left arm and head motors to the middle of their range of motion and press ENTER....
```

这一步应该做：

```text
把每个关节摆到机械活动范围的中间附近。
不要顶到极限。
两边都要留出活动余量。
姿态应该稳定、安全、居中。
```

按 Enter 后，程序会读取当前舵机原始位置，然后计算 `homing_offset`。

Feetech 中的关系是：

```text
Present_Position = Actual_Position - Homing_Offset
```

代码会让当前这个位置变成约半圈参考点：

```text
2047
```

对 12-bit 舵机来说：

```text
0 到 4095 是一圈编码范围
2047 约等于半圈中心
```

所以如果当前原始位置是：

```text
2311
```

那么大致会得到：

```text
homing_offset = 2311 - 2047 = 264
```

这与你看到的校准文件类似：

```json
"left_arm_shoulder_pan": {
  "homing_offset": 264
}
```

含义是：

```text
当前这个校准中位姿态，会被舵机/软件坐标校正到接近 2047。
```

### 2.2 第二阶段：摇完整个安全活动范围

提示类似：

```text
Move all joints sequentially through their entire ranges of motion.
Recording positions. Press ENTER to stop...
```

这一步应该做：

```text
把每个关节从一侧安全极限慢慢摆到另一侧安全极限。
不要撞机械结构。
不要硬掰。
每个关节都要覆盖完整、对称、真实的活动范围。
```

程序会记录：

```text
range_min
range_max
```

例如：

```json
"left_arm_shoulder_lift": {
  "homing_offset": -733,
  "range_min": 2032,
  "range_max": 3529
}
```

这些范围决定软件坐标怎么换算。

## 3. 为什么 .pos=0 不一定回到第一次 Enter 的姿态

XLeRobot 当前默认：

```python
use_degrees = False
```

普通关节使用：

```text
MotorNormMode.RANGE_M100_100
```

对应关系是：

```text
.pos = -100 -> range_min
.pos = 0    -> (range_min + range_max) / 2
.pos = 100  -> range_max
```

所以 `.pos=0` 实际代表的是：

```text
range_min 和 range_max 的中点
```

不是：

```text
第一次 Enter 时的姿态
```

如果第二阶段记录出来的范围不以第一次中位姿态为中心，那么 `.pos=0` 就会偏。

例如：

```text
range_min = 2032
range_max = 3529
```

那么：

```text
.pos=0 -> (2032 + 3529) / 2 = 2780.5
```

这个位置可能离第一次 Enter 时校正的 2047 很远。

所以你会看到：

```text
明明校准时摆正过
但是脚本归零时机械臂还是扭曲
```

不是因为校准文件没有用，而是因为：

```text
归零用的是 .pos=0。
.pos=0 来自 range_min/range_max 中点。
这个中点不一定等于第一次 Enter 的摆正姿态。
```

## 4. 如何让 .pos=0 更接近校准中位

如果你希望 `.pos=0` 更接近第一次 Enter 的姿态，需要在第二阶段尽量做到：

```text
围绕第一次中位姿态，两边活动范围尽量对称。
```

也就是说：

```text
往正方向摇多少安全范围
往反方向也尽量记录差不多的安全范围
```

但机械结构不一定天然对称，所以不能强求。

更重要的是：

```text
不要把 .pos=0 当成 Home Pose。
```

## 5. 正确理解 calibration 文件字段

一个电机校准项通常类似：

```json
"left_arm_shoulder_pan": {
  "id": 1,
  "drive_mode": 0,
  "homing_offset": 264,
  "range_min": 1430,
  "range_max": 2687
}
```

字段含义：

```text
id:
  舵机 ID。

drive_mode:
  方向/驱动模式。当前通常是 0。

homing_offset:
  把校准中位位置校正到半圈参考点的偏移量。

range_min:
  校准时记录到的安全最小位置。

range_max:
  校准时记录到的安全最大位置。
```

## 6. 推荐校准方法

建议每次校准按这个思路：

### 6.1 第一 Enter 前

```text
1. 所有关节尽量摆到机械活动范围中间。
2. 不追求好看，追求居中。
3. 不要把夹爪、手腕、肘关节摆到极限。
4. 确保两边都有运动余量。
5. 再按 Enter。
```

### 6.2 记录范围时

```text
1. 一个关节一个关节慢慢摇。
2. 摇到安全极限就停，不要硬碰。
3. 尽量两边都覆盖。
4. 不要漏掉某个关节。
5. 完成后按 Enter。
```

### 6.3 校准后

不要立刻相信：

```text
reset = 所有关节 0
```

应该先：

```text
1. 读取 observation。
2. 小幅度单关节测试。
3. 确认方向正确。
4. 确认范围没有异常。
5. 再定义单独 Home Pose。
```

## 7. 为什么还需要 Home Pose

因为 calibration 解决的是：

```text
坐标映射问题。
```

Home Pose 解决的是：

```text
机器人应该回到哪个安全姿态。
```

这两个不是一回事。

建议后续做：

```text
home_pose.json
```

保存内容类似：

```json
{
  "left_arm_shoulder_pan.pos": 0.0,
  "left_arm_shoulder_lift.pos": 35.0,
  "left_arm_elbow_flex.pos": -50.0,
  "left_arm_wrist_flex.pos": 15.0,
  "left_arm_wrist_roll.pos": 0.0,
  "left_arm_gripper.pos": 50.0,
  "right_arm_shoulder_pan.pos": 0.0,
  "right_arm_shoulder_lift.pos": 35.0,
  "right_arm_elbow_flex.pos": -50.0,
  "right_arm_wrist_flex.pos": 15.0,
  "right_arm_wrist_roll.pos": 0.0,
  "right_arm_gripper.pos": 50.0,
  "head_motor_1.pos": 0.0,
  "head_motor_2.pos": 0.0
}
```

这些数值应该来自你实际调好的安全姿态，而不是照抄。

## 8. 一句话记忆

```text
第一次 Enter：定义中位参考，用来算 homing_offset。
第二次摇范围：定义 range_min/range_max。
.pos=0：range_min/range_max 的中点。
Home Pose：需要单独保存，不能等同于 calibration。
```

