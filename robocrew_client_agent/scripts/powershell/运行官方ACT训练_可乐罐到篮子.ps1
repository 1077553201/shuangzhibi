$ErrorActionPreference = "Stop"

# 使用合并后的“可乐罐到篮子”数据集训练官方 ACT policy。
# 默认 200 step 用来快速验证；要正式训练，把 $steps 改成 5000、10000 或更高。

$env:PYTHONPATH = "E:\lerobot\lerobot\src"
$env:POLARS_SKIP_CPU_CHECK = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:HF_HOME = "E:\lerobot\hf_cache"
$env:HF_DATASETS_CACHE = "E:\lerobot\hf_datasets_cache"
$env:HF_LEROBOT_HOME = "E:\lerobot\lerobot_home"
$env:TORCH_HOME = "E:\lerobot\torch_cache"

$python = "E:\lerobot\envs\lerobot-gpu\Scripts\python.exe"
$latestMerged = Get-ChildItem -Path "E:\lerobot\datasets" -Directory |
  Where-Object { $_.Name -like "xlerobot_coke_can_to_basket_leader_merged_*" } |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1

if ($null -eq $latestMerged) {
    throw '没有找到合并后的篮子数据集。请先运行：合并官方VLA_ACT数据集_可乐罐到篮子.ps1'
}

$datasetRoot = $latestMerged.FullName
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$outputDir = "E:\lerobot\outputs\train\xlerobot_coke_can_to_basket_act_$stamp"
$steps = 200

Write-Host "[训练] 数据集：$datasetRoot"
Write-Host "[训练] 输出目录：$outputDir"
Write-Host "[训练] 步数：$steps"

& $python -m lerobot.scripts.lerobot_train `
  --dataset.repo_id=local/xlerobot_coke_can_to_basket_leader_merged `
  --dataset.root=$datasetRoot `
  --policy.type=act `
  --policy.pretrained_backbone_weights=null `
  --policy.push_to_hub=false `
  --output_dir=$outputDir `
  --steps=$steps `
  --batch_size=4 `
  --num_workers=0 `
  --save_freq=100 `
  --log_freq=20 `
  --eval_freq=1000000 `
  --wandb.enable=false
