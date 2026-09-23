# 快速落地：官方 ACT 抓可乐罐到篮子

目标：只走官方 LeRobot 接口，快速闭环“采集 -> 合并 -> 训练 -> 真机试跑”。

任务文本固定为：

```text
Pick up the red Coca-Cola can from the table and place it into the basket.
```

## 1. 树莓派启动 leader 录制 host

```bash
cd ~/xlerobot-dev/lerobot
source .venv/bin/activate
export XLEROBOT_PASSIVE_LEADER_ARM=left
python -m lerobot.robots.xlerobot.xlerobot_host
```

含义：左臂释放扭矩当 leader，右臂执行动作。

## 2. Windows 录制数据

每运行一次录一条 episode：

```powershell
cd E:\lerobot
& "E:\lerobot\robocrew_client_agent\scripts\powershell\运行官方VLA_ACT_host_client左臂leader右臂执行录制_可乐罐到篮子.ps1"
```

建议先录 10 条能完成动作的数据，时间紧就先 5 条冒烟。

## 3. 合并数据集

```powershell
cd E:\lerobot
& "E:\lerobot\robocrew_client_agent\scripts\powershell\合并官方VLA_ACT数据集_可乐罐到篮子.ps1"
```

输出目录形如：

```text
E:\lerobot\datasets\xlerobot_coke_can_to_basket_leader_merged_时间戳
```

## 4. 训练 ACT

```powershell
cd E:\lerobot
& "E:\lerobot\robocrew_client_agent\scripts\powershell\运行官方ACT训练_可乐罐到篮子.ps1"
```

脚本默认 200 step，只用于确认链路可跑。要看真实效果，把脚本里的：

```powershell
$steps = 200
```

改成：

```powershell
$steps = 5000
```

## 5. 真机试跑

树莓派换成正常 host：

```bash
cd ~/xlerobot-dev/lerobot
source .venv/bin/activate
unset XLEROBOT_PASSIVE_LEADER_ARM
python -m lerobot.robots.xlerobot.xlerobot_host
```

Windows 运行：

```powershell
cd E:\lerobot
& "E:\lerobot\robocrew_client_agent\scripts\powershell\运行官方ACT模型真机试跑_可乐罐到篮子.ps1"
```

第一次只看趋势：有没有朝可乐罐移动、夹爪有没有开合、有没有往篮子方向移动。

## 6. 判断是否继续补数据

失败不要先改算法，先补数据：

- 抓不到：补“靠近可乐罐”的 episode。
- 夹不住：补“夹爪开合明显”的 episode。
- 放不到篮子：补“从抓住到放入篮子”的后半段 episode。
- 视角看不到：固定可乐罐和篮子在摄像头里清楚可见。

