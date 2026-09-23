$ErrorActionPreference = "Stop"

# 官方 ACT 数据采集入口：左臂 leader，右臂 follower。
# 不使用 VR，不使用自定义运动算法；只调用 lerobot.record。
#
# 树莓派 host 启动方式：
#   cd ~/xlerobot-dev/lerobot
#   source .venv/bin/activate
#   export XLEROBOT_PASSIVE_LEADER_ARM=left
#   python -m lerobot.robots.xlerobot.xlerobot_host
#
# 录制动作：
#   手动摆左臂，让右臂跟随完成“抓起红色可乐罐，放入篮子”。

$env:PYTHONPATH = "E:\lerobot\lerobot\src"
$env:POLARS_SKIP_CPU_CHECK = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:HF_HOME = "E:\lerobot\hf_cache"
$env:HF_DATASETS_CACHE = "E:\lerobot\hf_datasets_cache"
$env:HF_LEROBOT_HOME = "E:\lerobot\lerobot_home"
$env:TORCH_HOME = "E:\lerobot\torch_cache"
Remove-Item Env:\XLEROBOT_POLICY_ACTION_MASK -ErrorAction SilentlyContinue

$python = "E:\lerobot\envs\lerobot-gpu\Scripts\python.exe"
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$datasetRoot = "E:\lerobot\datasets\xlerobot_coke_can_to_basket_leader_$stamp"
$task = "Pick up the red Coca-Cola can from the table and place it into the basket."

Write-Host "[录制] 左臂 leader，右臂 follower。"
Write-Host "[录制] 任务：$task"
Write-Host "[录制] 数据集：$datasetRoot"

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
  --dataset.repo_id=local/xlerobot_coke_can_to_basket_leader `
  --dataset.root=$datasetRoot `
  --dataset.single_task=$task `
  --dataset.num_episodes=1 `
  --dataset.episode_time_s=20 `
  --dataset.reset_time_s=8 `
  --dataset.fps=20 `
  --dataset.video=true `
  --dataset.num_image_writer_processes=0 `
  --dataset.num_image_writer_threads_per_camera=2 `
  --display_data=true
