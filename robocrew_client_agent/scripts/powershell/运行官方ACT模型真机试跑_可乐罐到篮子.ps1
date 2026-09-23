$ErrorActionPreference = "Stop"

# 使用最新训练出的“可乐罐到篮子” ACT policy 做短时间真机试跑。
# 只调用官方 lerobot.record --policy.path。
#
# 树莓派 host 启动方式：
#   cd ~/xlerobot-dev/lerobot
#   source .venv/bin/activate
#   unset XLEROBOT_PASSIVE_LEADER_ARM
#   python -m lerobot.robots.xlerobot.xlerobot_host

$env:PYTHONPATH = "E:\lerobot\lerobot\src"
$env:POLARS_SKIP_CPU_CHECK = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:HF_HOME = "E:\lerobot\hf_cache"
$env:HF_DATASETS_CACHE = "E:\lerobot\hf_datasets_cache"
$env:HF_LEROBOT_HOME = "E:\lerobot\lerobot_home"
$env:TORCH_HOME = "E:\lerobot\torch_cache"
Remove-Item Env:\XLEROBOT_POLICY_ACTION_MASK -ErrorAction SilentlyContinue

$python = "E:\lerobot\envs\lerobot-gpu\Scripts\python.exe"
$latestPolicy = Get-ChildItem -Path "E:\lerobot\outputs\train" -Directory |
Where-Object { $_.Name -like "model_*" } |
Sort-Object LastWriteTime -Descending |
Select-Object -First 1

if ($null -eq $latestPolicy) {
  throw '没有找到篮子任务 policy。请先运行：运行官方ACT训练_可乐罐到篮子.ps1'
}

$checkpoint = Get-ChildItem -Path (Join-Path $latestPolicy.FullName "checkpoints") -Directory |
Sort-Object Name -Descending |
Select-Object -First 1

if ($null -eq $checkpoint) {
  throw "没有找到 checkpoint：$($latestPolicy.FullName)"
}

$policyPath = Join-Path $checkpoint.FullName "pretrained_model"
if (-not (Test-Path $policyPath)) {
  throw "没有找到 pretrained_model：$policyPath"
}

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$datasetRoot = "E:\lerobot\datasets\eval_xlerobot_coke_can_to_basket_$($checkpoint.Name)_$stamp"
$task = "Pick up the red Coca-Cola can from the table and place it into the basket."

Write-Host "[试跑] policy：$policyPath"
Write-Host "[试跑] 结果数据集：$datasetRoot"
Write-Host "[安全] 短试跑 5 秒。异常立刻 Ctrl+C。"

& $python -m lerobot.record `
  --robot.type=xlerobot_client `
  --robot.remote_ip=192.168.1.239 `
  --robot.id=my_xlerobot_pc `
  --robot.calibration_dir=E:\lerobot\calibration\robots `
  --robot.cameras="{ camera_0: {type: opencv, index_or_path: 'camera_0', width: 640, height: 480, fps: 20}, camera_2: {type: opencv, index_or_path: 'camera_2', width: 640, height: 480, fps: 20}, head: {type: opencv, index_or_path: 'head', width: 640, height: 480, fps: 20} }" `
  --policy.path=$policyPath `
  --policy.device=cuda `
  --dataset.repo_id=local/eval_xlerobot_coke_can_to_basket `
  --dataset.root=$datasetRoot `
  --dataset.single_task=$task `
  --dataset.num_episodes=1 `
  --dataset.episode_time_s=5 `
  --dataset.reset_time_s=3 `
  --dataset.fps=20 `
  --dataset.video=true `
  --dataset.num_image_writer_processes=0 `
  --dataset.num_image_writer_threads_per_camera=2 `
  --display_data=true
