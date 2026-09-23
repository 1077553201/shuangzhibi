# 官方 VLA_ACT host-client 左臂 leader 右臂执行录制说明

## 当前目标

不用 VR，继续走官方 `lerobot.record` 录制流程。

Windows 端运行 `record.py`，树莓派运行 `xlerobot_host`。Windows 通过 `xlerobot_client` 读取树莓派上报的观测数据，并用 `remote_xlerobot_arm_leader` 把一只远程手臂当作 leader：

- leader：左臂 `left_arm_*.pos`
- follower：右臂 `right_arm_*.pos`
- 数据集：保存完整 robot observation、action、视频

这个适配器只做关节字段映射，不做 IK、轨迹规划、自定义协调算法。

## 运行脚本

树莓派先启动 host：

```bash
cd ~/xlerobot-dev/lerobot
source .venv/bin/activate
export XLEROBOT_PASSIVE_LEADER_ARM=left
python -m lerobot.robots.xlerobot.xlerobot_host
```

`XLEROBOT_PASSIVE_LEADER_ARM=left` 的作用是让左臂关闭扭矩、只读取关节位置，适合左臂当手动 leader。右臂仍然会作为 follower 接收动作。

Windows 运行：

```powershell
& "E:\lerobot\robocrew_client_agent\scripts\powershell\运行官方VLA_ACT_host_client左臂leader右臂执行录制.ps1"
```

脚本内部使用：

```powershell
--robot.type=xlerobot_client
--robot.remote_ip=192.168.10.239
--teleop.type=remote_xlerobot_arm_leader
--teleop.leader_arm=left
--teleop.follower_arm=right
```

## 关键限制

如果 leader 左臂和 follower 右臂在同一台 XLeRobot 上，host 默认会给左右臂上电并开启位置控制，左臂不一定能被手动拖动。所以同机 leader/follower 录制时，要用：

```bash
export XLEROBOT_PASSIVE_LEADER_ARM=left
```

这只是让 host 释放 leader 左臂扭矩，不改变官方 record.py 的录制流程。

更推荐的正式采集方式：

1. 一台 XLeRobot 做 follower，运行普通 `xlerobot_host`。
2. 另一台 XLeRobot 做 leader，只读取左臂关节值。
3. Windows `record.py` 同时连接 follower host 和 leader host。

后续如果使用第二台设备作为 leader，只需要给脚本增加：

```powershell
--teleop.leader_ip=第二台树莓派IP
```

这样 `remote_xlerobot_arm_leader` 会从第二台 host 读取左臂数据，再发给第一台 follower 的右臂。

## 已验证

本地已验证：

- `remote_xlerobot_arm_leader` 能被 LeRobot 配置系统识别。
- `record.py --help` 中已经出现 `remote_xlerobot_arm_leader`、`leader_arm`、`follower_arm` 参数。
- 字段映射结果为：

```text
left_arm_shoulder_pan.pos  -> right_arm_shoulder_pan.pos
left_arm_shoulder_lift.pos -> right_arm_shoulder_lift.pos
left_arm_elbow_flex.pos    -> right_arm_elbow_flex.pos
left_arm_wrist_flex.pos    -> right_arm_wrist_flex.pos
left_arm_wrist_roll.pos    -> right_arm_wrist_roll.pos
left_arm_gripper.pos       -> right_arm_gripper.pos
```

