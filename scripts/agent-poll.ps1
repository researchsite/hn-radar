# Picks ONE open issue labeled ready-for-agent and runs the Goose ticket-worker recipe on it.
# Windows PowerShell 5.1. Run by hand first:  powershell -File scripts\agent-poll.ps1 -DryRun
param([switch]$DryRun)

$ErrorActionPreference = "Stop"
$MaxRuns = 2
$Model = "qwen2.5:14b"
$LockMaxAge = [TimeSpan]::FromHours(2)

$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$logs = Join-Path $repo "logs"
New-Item -ItemType Directory -Force $logs | Out-Null

# Skip if a previous run is still going (the scheduler fires every 30 minutes).
$lock = Join-Path $logs "agent.lock"
if ((Test-Path $lock) -and ((Get-Date) - (Get-Item $lock).LastWriteTime) -lt $LockMaxAge) {
    Write-Output "Another run holds the lock; exiting."
    exit 0
}

$issues = gh issue list --label ready-for-agent --state open --json number --limit 1 | ConvertFrom-Json
if (-not $issues) { exit 0 }   # nothing to do, exit quietly
$n = $issues[0].number

# Never run over uncommitted work or from a feature branch.
$dirty = git status --short
$branch = git branch --show-current
if ($dirty -or $branch -ne "main") {
    Write-Output "Repo must be clean and on main (branch=$branch). Not starting issue #$n."
    exit 0
}

$runs = @(Get-ChildItem $logs -Filter "agent-$n-*.log" -ErrorAction SilentlyContinue).Count
if ($runs -ge $MaxRuns) {
    gh issue edit $n --remove-label ready-for-agent --add-label needs-human | Out-Null
    gh issue comment $n --body "Agent already ran $runs times on this issue. Needs a human." | Out-Null
    exit 0
}

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$log = Join-Path $logs "agent-$n-$stamp.log"
$cmd = "goose run --recipe .goose\recipes\ticket-worker.yaml --params issue_number=$n " +
       "--provider ollama --model $Model --no-session --max-turns 60 --max-tool-repetitions 3"

if ($DryRun) {
    Write-Output "DRY RUN: would take issue #$n, set GOOSE_MODE=auto and run:"
    Write-Output "  $cmd > $log"
    exit 0
}

gh issue edit $n --remove-label ready-for-agent --add-label agent-working | Out-Null
Set-Content $lock $stamp
$oldMode = $env:GOOSE_MODE
try {
    # Headless runs cannot answer approval prompts, so Auto is required. Scoped to this process only.
    $env:GOOSE_MODE = "auto"
    cmd.exe /c "$cmd > `"$log`" 2>&1"
    $exit = $LASTEXITCODE
} finally {
    $env:GOOSE_MODE = $oldMode
    Remove-Item $lock -ErrorAction SilentlyContinue
    git checkout main 2>$null | Out-Null   # leave the repo ready for the next poll
}

$tail = @(Get-Content $log -Tail 30)
$ok = ($exit -eq 0) -and (($tail -join "`n") -match "PR: https://") -and (($tail -join "`n") -notmatch "BLOCKED")
if ($ok) {
    gh issue edit $n --remove-label agent-working | Out-Null
    gh issue comment $n --body "Agent run finished. See the linked PR. Log: logs\agent-$n-$stamp.log (local)." | Out-Null
} else {
    $body = Join-Path $logs "fail-$n-$stamp.md"
    Set-Content $body (@("Agent run did not finish (exit $exit). Last lines of the log:", '```') + $tail + '```')
    gh issue edit $n --remove-label agent-working --add-label needs-human | Out-Null
    gh issue comment $n --body-file $body | Out-Null
}
