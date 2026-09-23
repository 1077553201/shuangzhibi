# 使用官方 LeRobot/XLeRobot record.py 录制 VLA_ACT 数据。
#
# 本脚本不使用 VR。它把树莓派 host 上报的左臂关节作为 leader，
# 映射成右臂 follower 动作，然后交给官方 record.py 录制数据集。
#
# 运行前准备：
# 1. 树莓派上启动 host：
#    cd ~/xlerobot-dev/lerobot
#    source .venv/bin/activate
#    export XLEROBOT_PASSIVE_LEADER_ARM=left
#    python -m lerobot.robots.xlerobot.xlerobot_host
# 2. Windows 端使用 E:\lerobot\envs\lerobot-gpu 环境。
# 3. 如果左臂和右臂在同一台 XLeRobot 上，需要在树莓派 host 前设置：
#    export XLEROBOT_PASSIVE_LEADER_ARM=left
#    这样左臂只读位置、右臂作为 follower 执行动作。

$ErrorActionPreference = "Stop"

$env:PYTHONPATH = "E:\lerobot\lerobot\src"
$env:POLARS_SKIP_CPU_CHECK = "1"

$python = "E:\lerobot\envs\lerobot-gpu\Scripts\python.exe"
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$datasetRoot = "E:\lerobot\datasets\xlerobot_coke_can_to_shelf_leader_$stamp"

Write-Host "使用左臂作为 leader，右臂作为 follower。"
Write-Host "录制开始后请移动左臂；record.py 会把左臂关节值写成右臂目标动作。"
Write-Host "数据集将保存到：$datasetRoot"

& $python -m lerobot.record `
  --robot.type=xlerobot_client `
  --robot.remote_ip=192.168.10.239 `
  --robot.id=my_xlerobot_pc `
  --robot.calibration_dir=E:\lerobot\calibration\robots `
  --robot.cameras="{ camera_0: {type: opencv, index_or_path: 'camera_0', width: 640, height: 480, fps: 20}, camera_2: {type: opencv, index_or_path: 'camera_2', width: 640, height: 480, fps: 20}, head: {type: opencv, index_or_path: 'head', width: 640, height: 480, fps: 20} }" `
  --teleop.type=remote_xlerobot_arm_leader `
  --teleop.id=left_leader_right_follower `
  --teleop.calibration_dir=E:\lerobot\calibration\teleoperators `
  --teleop.leader_arm=left `
  --teleop.follower_arm=right `
  --teleop.include_base_stop=true `
  --dataset.repo_id=local/xlerobot_coke_can_to_shelf_leader `
  --dataset.root=$datasetRoot `
  --dataset.single_task="Pick up the red Coca-Cola can from the table and place it on the robot shelf." `
  --dataset.num_episodes=1 `
  --dataset.episode_time_s=20 `
  --dataset.reset_time_s=10 `
  --dataset.fps=20 `
  --dataset.video=true `
  --dataset.num_image_writer_processes=0 `
  --dataset.num_image_writer_threads_per_camera=2 `
  --display_data=true
