# 使用官方 XLeRobot VLA_ACT record.py，但机器人硬件层走 host-client。
#
# 运行前准备：
# 1. 树莓派上已经启动：
#    cd ~/xlerobot-dev/lerobot
#    source .venv/bin/activate
#    python -m lerobot.robots.xlerobot.xlerobot_host
# 2. Windows 上当前环境是 E:\lerobot\envs\lerobot-gpu。
# 3. XLeVR 目录存在：E:\lerobot\XLeRobot\XLeVR。
#
# 注意：
# - 不要直接运行 E:\lerobot\lerobot\src\lerobot\record.py 文件路径。
#   在 Windows 上直接跑包内文件会和标准库 types/enum 发生导入冲突。
# - 这里使用 python -m lerobot.record，这是同一个官方 record.py 的模块运行方式。

$ErrorActionPreference = "Stop"

$env:PYTHONPATH = "E:\lerobot\lerobot\src"
$env:POLARS_SKIP_CPU_CHECK = "1"
$env:XLEVR_PATH = "E:\lerobot\XLeRobot\XLeVR"
$env:XLEVR_HOST_IP = "192.168.10.249"

$python = "E:\lerobot\envs\lerobot-gpu\Scripts\python.exe"
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$datasetRoot = "E:\lerobot\datasets\xlerobot_coke_can_to_shelf_vr_$stamp"

Write-Host "VR 头显浏览器请打开：https://$env:XLEVR_HOST_IP`:8443"
Write-Host "如果这个地址打不开，请先确认头显和 Windows 在同一个 192.168.10.x 局域网。"

& $python -m lerobot.record `
  --robot.type=xlerobot_client `
  --robot.remote_ip=192.168.10.240 `
  --robot.id=my_xlerobot_pc `
  --robot.calibration_dir=E:\lerobot\calibration\robots `
  --robot.cameras="{ camera_0: {type: opencv, index_or_path: 'camera_0', width: 640, height: 480, fps: 20}, camera_2: {type: opencv, index_or_path: 'camera_2', width: 640, height: 480, fps: 20}, head: {type: opencv, index_or_path: 'head', width: 640, height: 480, fps: 20} }" `
  --teleop.type=xlerobot_vr `
  --teleop.xlevr_path=E:\lerobot\XLeRobot\XLeVR `
  --teleop.calibration_dir=E:\lerobot\calibration\teleoperators `
  --teleop.vr_connection_timeout=60 `
  --dataset.repo_id=local/xlerobot_coke_can_to_shelf `
  --dataset.root=$datasetRoot `
  --dataset.single_task="Pick up the red Coca-Cola can from the table and place it on the robot shelf." `
  --dataset.num_episodes=1 `
  --dataset.episode_time_s=20 `
  --dataset.reset_time_s=10 `
  --dataset.fps=20 `
  --dataset.video=true `
  --dataset.num_image_writer_processes=0 `
  --dataset.num_image_writer_threads_per_camera=2 `
  --display_data=false
