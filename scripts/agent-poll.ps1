# Picks ONE open issue labeled ready-for-agent and runs the Goose ticket-worker recipe on it,
# inside a dedicated git worktree so the main checkout is never touched.
# Windows PowerShell 5.1. Try it first with:  powershell -File scripts\agent-poll.ps1 -DryRun
param([switch]$DryRun)

$ErrorActionPreference = "Stop"
$MaxRuns = 2
$Model = "qwen2.5:14b"

$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$logs = Join-Path $repo "logs"
New-Item -ItemType Directory -Force $logs | Out-Null
$wtRoot = Join-Path (Split-Path $repo -Parent) "hn-radar-worktrees"

# Run a native command; a non-zero exit code is an error (PowerShell does not do this on its own).
function Invoke-Native {
    param([string]$Exe, [string[]]$CmdArgs)
    $out = & $Exe @CmdArgs
    if ($LASTEXITCODE -ne 0) { throw "$Exe $($CmdArgs -join ' ') failed (exit $LASTEXITCODE)" }
    return $out
}

# Exclusive lock owned by this process. A lock whose owner process is gone is stale and is replaced.
$lock = Join-Path $logs "agent.lock"
function Enter-Lock {
    for ($i = 0; $i -lt 2; $i++) {
        try {
            $fs = [IO.File]::Open($lock, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
            $sw = New-Object IO.StreamWriter($fs)
            $sw.Write($PID)
            $sw.Close()
            return $true
        } catch [IO.IOException] {
            $owner = Get-Content $lock -ErrorAction SilentlyContinue
            if ($owner -and (Get-Process -Id ([int]$owner) -ErrorAction SilentlyContinue)) { return $false }
            Remove-Item $lock -ErrorAction SilentlyContinue
        }
    }
    return $false
}

function Set-NeedsHuman {
    param([int]$N, [string]$Message)
    Invoke-Native gh @("issue", "edit", "$N", "--remove-label", "ready-for-agent", "--remove-label", "agent-working", "--add-label", "needs-human") | Out-Null
    Invoke-Native gh @("issue", "comment", "$N", "--body", $Message) | Out-Null
}

function Invoke-Poll {
    $json = Invoke-Native gh @("issue", "list", "--label", "ready-for-agent", "--state", "open", "--json", "number,author", "--limit", "1")
    # "[]" parses to nothing (or a null item on PowerShell 5.1), so keep only real issues.
    $issues = @(($json -join "`n") | ConvertFrom-Json | Where-Object { $_ -and $_.number })
    if ($issues.Count -eq 0) { return 0 }   # successful, empty queue: exit quietly
    $n = [int]$issues[0].number

    # Issue text drives a shell with no approval prompts, so only the repo owner's issues are worked.
    $owner = ((Invoke-Native gh @("repo", "view", "--json", "owner", "--jq", ".owner.login")) -join "").Trim()
    if ($issues[0].author.login -ne $owner) {
        Set-NeedsHuman $n "Skipped: the agent only works on issues authored by $owner."
        return 0
    }

    $runs = @(Get-ChildItem $logs -Filter "agent-$n-*.log" -ErrorAction SilentlyContinue).Count
    if ($runs -ge $MaxRuns) {
        Set-NeedsHuman $n "Agent already ran $runs times on this issue. Needs a human."
        return 0
    }

    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $log = Join-Path $logs "agent-$n-$stamp.log"
    $branch = "agent/$n"
    $wt = Join-Path $wtRoot "issue-$n"
    $recipe = Join-Path $repo ".goose\recipes\ticket-worker.yaml"
    $prBody = Join-Path $logs "pr-$n.md"
    $goose = "goose run --recipe `"$recipe`" --params issue_number=$n --params `"pr_body_path=$prBody`" " +
             "--provider ollama --model $Model --no-session --max-turns 60 --max-tool-repetitions 3"

    if ($DryRun) {
        Write-Output "DRY RUN: would take issue #$n (author $owner)."
        Write-Output "  worktree: $wt  branch: $branch"
        Write-Output "  run: $goose > $log"
        return 0
    }

    Invoke-Native gh @("issue", "edit", "$n", "--remove-label", "ready-for-agent", "--add-label", "agent-working") | Out-Null
    $oldMode = $env:GOOSE_MODE
    try {
        # Worktree: continue the remote branch on a retry, otherwise start from the latest origin/main.
        Invoke-Native git @("fetch", "origin") | Out-Null
        if (-not (Test-Path $wt)) {
            New-Item -ItemType Directory -Force $wtRoot | Out-Null
            $remote = (Invoke-Native git @("ls-remote", "--heads", "origin", $branch)) -join ""
            if ($remote) {
                Invoke-Native git @("worktree", "add", "-B", $branch, $wt, "origin/$branch") | Out-Null
            } else {
                Invoke-Native git @("worktree", "add", "-b", $branch, $wt, "origin/main") | Out-Null
            }
        }
        Invoke-Native uv @("--directory", $wt, "sync") | Out-Null

        # A small .cmd file avoids PowerShell/cmd quoting problems.
        $bat = Join-Path $logs "run-$n-$stamp.cmd"
        Set-Content $bat @("@echo off", "cd /d `"$wt`"", "$goose > `"$log`" 2>&1") -Encoding ASCII
        # Headless runs cannot answer approval prompts, so Auto is required. Scoped to this process only.
        $env:GOOSE_MODE = "auto"
        cmd.exe /c $bat
        $exit = $LASTEXITCODE
        $env:GOOSE_MODE = $oldMode

        # Success means an open PR exists for the branch, not that the model printed a link.
        $tail = @(Get-Content $log -Tail 30)
        $prUrl = ((Invoke-Native gh @("pr", "list", "--head", $branch, "--base", "main", "--state", "open", "--json", "url", "--jq", ".[0].url")) -join "").Trim()
        if (($exit -eq 0) -and $prUrl -and (($tail -join "`n") -notmatch "BLOCKED")) {
            Invoke-Native gh @("issue", "edit", "$n", "--remove-label", "agent-working") | Out-Null
            Invoke-Native gh @("issue", "comment", "$n", "--body", "Agent run finished: $prUrl") | Out-Null
            Invoke-Native git @("worktree", "remove", "--force", $wt) | Out-Null
        } else {
            $body = Join-Path $logs "fail-$n-$stamp.md"
            Set-Content $body (@("Agent run did not produce a PR (exit $exit). Work is kept in $wt.", "Last lines of the log:", '```') + $tail + '```')
            Invoke-Native gh @("issue", "edit", "$n", "--remove-label", "agent-working", "--add-label", "needs-human") | Out-Null
            Invoke-Native gh @("issue", "comment", "$n", "--body-file", $body) | Out-Null
        }
    } catch {
        $env:GOOSE_MODE = $oldMode
        Write-Error "Run for issue #$n failed: $_" -ErrorAction Continue
        try { Set-NeedsHuman $n "Agent poller failed: $_" } catch { Write-Error "Could not mark #$n needs-human: $_" -ErrorAction Continue }
        return 1
    }
    return 0
}

if (-not (Enter-Lock)) {
    Write-Output "Another run holds the lock; exiting."
    exit 0
}
$code = 0
try { $code = Invoke-Poll } finally { Remove-Item $lock -ErrorAction SilentlyContinue }
exit $code
