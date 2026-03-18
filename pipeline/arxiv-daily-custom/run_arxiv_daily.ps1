$ErrorActionPreference = "Stop"

$scriptPath = "C:\Users\pli77\.openclaw\workspace\arxiv_management\pipeline\arxiv-daily-custom\arxiv_daily.py"
$configPath = "C:\Users\pli77\.openclaw\workspace\arxiv_management\arxiv_daily_config.json"
$logDir = "C:\Users\pli77\.openclaw\workspace\arxiv_management\arxiv-daily\logs"

New-Item -ItemType Directory -Path $logDir -Force | Out-Null

$ts = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
$logFile = Join-Path $logDir ("run_" + $ts + ".log")

$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
$pyLauncher = Get-Command py -ErrorAction SilentlyContinue

if ($pythonCmd) {
  & python "$scriptPath" --config "$configPath" *> "$logFile"
} elseif ($pyLauncher) {
  & py -3 "$scriptPath" --config "$configPath" *> "$logFile"
} else {
  throw "Python not found in PATH. Please install Python or add it to PATH."
}

if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}
