$ErrorActionPreference = "Stop"

# LeRobot 当前训练保存的 pretrained_model/config.json 有时会缺少 "type": "act"。
# 训练本身没坏，但部署时 PreTrainedConfig.from_pretrained 需要这个字段选择 policy 类。
# 用法：
#   & "E:\lerobot\robocrew_client_agent\修复ACT模型config缺少type字段.ps1" `
#     "E:\lerobot\outputs\train\某次训练\checkpoints\000200\pretrained_model"

param(
    [Parameter(Mandatory = $true)]
    [string]$PolicyDir
)

$configPath = Join-Path $PolicyDir "config.json"

if (-not (Test-Path $configPath)) {
    throw "没有找到 config.json：$configPath"
}

$json = Get-Content -Path $configPath -Raw | ConvertFrom-Json

if ($null -eq $json.type) {
    $ordered = [ordered]@{ type = "act" }
    foreach ($prop in $json.PSObject.Properties) {
        $ordered[$prop.Name] = $prop.Value
    }
    $ordered | ConvertTo-Json -Depth 20 | Set-Content -Path $configPath -Encoding UTF8
    Write-Host "[完成] 已补充 type=act：$configPath"
} else {
    Write-Host "[跳过] 已存在 type=$($json.type)：$configPath"
}
