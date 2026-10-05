# Addestra (fine-tuning) la voce Piper del personaggio nel container Docker con la GPU, poi la esporta in ONNX dove
# Jake la cerca (data/piper/models/<personaggio>.onnx). Dati: python -m tools.piper_voice_dataset.
#   powershell -File training/piper/train.ps1 -Epochs 150
#   powershell -File training/piper/train.ps1 -ExportOnly
param(
    [string]$Character = "jake_the_dog",
    [int]$Epochs = 150,          # epoche IN PIU' rispetto al checkpoint di partenza
    [int]$BatchSize = 16,
    [switch]$ExportOnly
)
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$work = Join-Path $root "data\piper"
$image = "jake-piper-train"

if (-not (docker images -q $image)) {
    docker build -t $image $PSScriptRoot
}
$mount = "${work}:/work"
$runs = "/work/runs/$Character"

if (-not $ExportOnly) {
    # il checkpoint di partenza e' all'epoca 14: Lightning conta le epoche totali
    $maxEpochs = 15 + $Epochs
    $resume = "/work/ckpt/serena-medium.ckpt"
    $last = Get-ChildItem (Join-Path $work "runs\$Character") -Recurse -Filter "last.ckpt" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime | Select-Object -Last 1
    if ($last) { $resume = "/work/" + $last.FullName.Substring($work.Length + 1).Replace("\", "/") }
    docker run --rm --gpus all --shm-size 2g -v $mount $image python -m piper.train fit `
        --data.voice_name $Character `
        --data.csv_path /work/$Character/metadata.csv `
        --data.audio_dir /work/$Character/wavs `
        --model.sample_rate 22050 `
        --data.espeak_voice it `
        --data.cache_dir /work/cache/$Character `
        --data.config_path /work/$Character/config.json `
        --data.batch_size $BatchSize `
        --trainer.max_epochs $maxEpochs `
        --trainer.default_root_dir $runs `
        --ckpt_path $resume
}

$ckpt = Get-ChildItem (Join-Path $work "runs\$Character") -Recurse -Filter "*.ckpt" | Sort-Object LastWriteTime | Select-Object -Last 1
if (-not $ckpt) { throw "Nessun checkpoint in data\piper\runs\$Character" }
$inside = "/work/" + $ckpt.FullName.Substring($work.Length + 1).Replace("\", "/")
New-Item -ItemType Directory -Force (Join-Path $work "models") | Out-Null
docker run --rm -v $mount $image python -m piper.train.export_onnx --checkpoint $inside --output-file /work/models/$Character.onnx
Copy-Item (Join-Path $work "$Character\config.json") (Join-Path $work "models\$Character.onnx.json") -Force
Write-Host "Voce esportata: data\piper\models\$Character.onnx (da $($ckpt.Name)). Riavvia Jake per usarla."
