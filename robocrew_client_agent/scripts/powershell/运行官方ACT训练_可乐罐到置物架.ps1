# 使用合并后的 leader 数据集训练官方 ACT policy。
#
# 默认是小规模试训，确认数据加载、视频读取、模型前向和保存都没问题。
# 想正式训练时，把 $steps 改大，例如 20000 或 50000。

$ErrorActionPreference = "Stop"

$env:PYTHONPATH = "E:\lerobot\lerobot\src"
$env:POLARS_SKIP_CPU_CHECK = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:HF_HOME = "E:\lerobot\hf_cache"
$env:HF_DATASETS_CACHE = "E:\lerobot\hf_datasets_cache"
$env:HF_LEROBOT_HOME = "E:\lerobot\lerobot_home"
$env:TORCH_HOME = "E:\lerobot\torch_cache"

$python = "E:\lerobot\envs\lerobot-gpu\Scripts\python.exe"
$datasetRoot = "E:\lerobot\datasets\xlerobot_coke_can_to_shelf_leader_merged_20260503_v2"
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$outputDir = "E:\lerobot\outputs\train\xlerobot_coke_can_to_shelf_act_smoke_$stamp"

# 试训先用 200 step；正式训练再改大。
$steps = 200

Write-Host "训练数据集：$datasetRoot"
Write-Host "输出目录：$outputDir"
Write-Host "训练步数：$steps"

& $python -m lerobot.scripts.lerobot_train `
  --dataset.repo_id=local/xlerobot_coke_can_to_shelf_leader_merged `
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
