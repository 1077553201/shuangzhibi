$ErrorActionPreference = "Stop"

# 使用当前训练好的 ACT policy 做一次短时间真机试跑，并保存试跑数据。
# 这是看效果的推荐入口：它走官方 lerobot.record --policy.path 路线，
# 和训练时的数据结构一致，都是完整 XLeRobot observation/action。
#
# 运行前：
# 1. 树莓派 host 使用正常模式启动，不要设置 XLEROBOT_PASSIVE_LEADER_ARM。
#    cd ~/xlerobot-dev/lerobot
#    source .venv/bin/activate
#    unset XLEROBOT_PASSIVE_LEADER_ARM
#    python -m lerobot.robots.xlerobot.xlerobot_host
# 2. 把可乐罐和置物架摆到接近训练数据的位置。
# 3. 第一次试跑请手放在 Ctrl+C / 急停 / 断电附近。

$env:PYTHONPATH = "E:\lerobot\lerobot\src"
$env:POLARS_SKIP_CPU_CHECK = "1"
$env:HF_HOME = "E:\lerobot\hf_cache"
$env:HF_DATASETS_CACHE = "E:\lerobot\hf_datasets_cache"
$env:HF_LEROBOT_HOME = "E:\lerobot\lerobot_home"
$env:TORCH_HOME = "E:\lerobot\torch_cache"
$env:PYTHONIOENCODING = "utf-8"
$env:XLEROBOT_POLICY_ACTION_MASK = "right_arm"

$python = "E:\lerobot\envs\lerobot-gpu\Scripts\python.exe"
$policyPath = "E:\lerobot\outputs\train\xlerobot_coke_can_to_shelf_act_smoke_20260503_204142\checkpoints\000200\pretrained_model"
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$datasetRoot = "E:\lerobot\datasets\eval_xlerobot_coke_can_to_shelf_act_000200_$stamp"

if (-not (Test-Path $policyPath)) {
    throw "没有找到 policy：$policyPath"
}

Write-Host "[试跑] 使用 ACT policy：$policyPath"
Write-Host "[试跑] 结果数据保存到：$datasetRoot"
Write-Host "[安全] 当前是 200 step 小模型，只看右臂动作趋势；已限制只发送 right_arm_*，异常立刻 Ctrl+C。"

& $python -m lerobot.record `
  --robot.type=xlerobot_client `
  --robot.remote_ip=192.168.10.239 `
  --robot.id=my_xlerobot_pc `
  --robot.calibration_dir=E:\lerobot\calibration\robots `
  --robot.cameras="{ camera_0: {type: opencv, index_or_path: 'camera_0', width: 640, height: 480, fps: 20}, camera_2: {type: opencv, index_or_path: 'camera_2', width: 640, height: 480, fps: 20}, head: {type: opencv, index_or_path: 'head', width: 640, height: 480, fps: 20} }" `
  --policy.path=$policyPath `
  --policy.device=cuda `
  --dataset.repo_id=local/eval_xlerobot_coke_can_to_shelf_act_000200 `
  --dataset.root=$datasetRoot `
  --dataset.single_task="Pick up the red Coca-Cola can from the table and place it on the robot shelf." `
  --dataset.num_episodes=1 `
  --dataset.episode_time_s=5 `
  --dataset.reset_time_s=3 `
  --dataset.fps=20 `
  --dataset.video=true `
  --dataset.num_image_writer_processes=0 `
  --dataset.num_image_writer_threads_per_camera=2 `
  --display_data=true
