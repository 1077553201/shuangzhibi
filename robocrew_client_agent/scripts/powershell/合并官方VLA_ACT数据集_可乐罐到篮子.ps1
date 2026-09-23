$ErrorActionPreference = "Stop"

# 把多次单条录制的数据集合并成一个训练数据集。
# 只调用官方 lerobot.scripts.lerobot_edit_dataset 的 merge 操作。

$env:PYTHONPATH = "E:\lerobot\lerobot\src"
$env:POLARS_SKIP_CPU_CHECK = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:HF_HOME = "E:\lerobot\hf_cache"
$env:HF_DATASETS_CACHE = "E:\lerobot\hf_datasets_cache"
$env:HF_LEROBOT_HOME = "E:\lerobot\lerobot_home"
$env:TORCH_HOME = "E:\lerobot\torch_cache"

$python = "E:\lerobot\envs\lerobot-gpu\Scripts\python.exe"
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$mergedRoot = "E:\lerobot\datasets\xlerobot_coke_can_to_basket_leader_merged_$stamp"

$datasets = Get-ChildItem -Path "E:\lerobot\datasets" -Directory |
  Where-Object {
    $_.Name -like "xlerobot_coke_can_to_basket_leader_*" -and
    $_.Name -notlike "*merged*" -and
    (Test-Path (Join-Path $_.FullName "meta"))
  } |
  Sort-Object Name

if ($datasets.Count -lt 1) {
    throw '没有找到可合并的数据集。请先运行：运行官方VLA_ACT_host_client左臂leader右臂执行录制_可乐罐到篮子.ps1'
}

$repoIds = @()
$roots = @()
foreach ($dataset in $datasets) {
    $repoIds += "local/$($dataset.Name)"
    $roots += $dataset.FullName
}

$repoIdsLiteral = "[" + (($repoIds | ForEach-Object { "'$_'" }) -join ", ") + "]"
$rootsLiteral = "[" + (($roots | ForEach-Object { "'" + ($_ -replace "\\", "\\") + "'" }) -join ", ") + "]"

Write-Host "[合并] 输入数据集数量：$($datasets.Count)"
$datasets | ForEach-Object { Write-Host "  - $($_.FullName)" }
Write-Host "[合并] 输出数据集：$mergedRoot"

& $python -m lerobot.scripts.lerobot_edit_dataset `
  --new_repo_id=local/xlerobot_coke_can_to_basket_leader_merged `
  --new_root=$mergedRoot `
  --operation.type=merge `
  --operation.repo_ids=$repoIdsLiteral `
  --operation.roots=$rootsLiteral

Write-Host "[合并] 完成：$mergedRoot"
