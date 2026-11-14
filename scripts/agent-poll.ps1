# Picks ONE open issue labeled ready-for-agent and runs the Goose ticket-worker recipe on it,
# inside a dedicated git worktree so the main checkout is never touched.
# Windows PowerShell 5.1. Try it first with:  powershell -File scripts\agent-poll.ps1 -DryRun
#
# Known limitation: the worker runs with GOOSE_MODE=auto and your normal gh login, and issue text is
# editable after labeling. Rules in the recipe are instructions, not a boundary. Real isolation
# (sandbox + scoped credentials) is planned for Week 4 (OpenSandbox). Until then: owner-only issues,
# a per-issue worktree, a retry limit, and a human decides every merge.
# Accepted risk: an existing origin/agent/<n> branch is trusted on a retry. Only the repo owner can
# push to this repository (no collaborators), so that is the same trust level as the owner's issues.
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

# gh --body-file reads UTF-8; Set-Content on PowerShell 5.1 would use the system code page.
function Write-Utf8 {
    param([string]$Path, [string[]]$Lines)
    [IO.File]::WriteAllLines($Path, $Lines, (New-Object Text.UTF8Encoding($false)))
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
            $ownerPid = 0
            $text = (Get-Content $lock -ErrorAction SilentlyContinue) -join ""
            # A missing, empty or malformed lock cannot belong to a live process: treat it as stale.
            if ([int]::TryParse($text.Trim(), [ref]$ownerPid) -and $ownerPid -gt 0 -and
                (Get-Process -Id $ownerPid -ErrorAction SilentlyContinue)) { return $false }
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

    $owner = ((Invoke-Native gh @("repo", "view", "--json", "owner", "--jq", ".owner.login")) -join "").Trim()
    $author = $issues[0].author.login
    $runs = @(Get-ChildItem $logs -Filter "agent-$n-*.log" -ErrorAction SilentlyContinue).Count
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $log = Join-Path $logs "agent-$n-$stamp.log"
    $errLog = "$log.err"
    $branch = "agent/$n"
    $wt = Join-Path $wtRoot "issue-$n"
    $recipe = Join-Path $repo ".goose\recipes\ticket-worker.yaml"
    $prBody = Join-Path $logs "pr-$n.md"
    $gooseArgs = "run --recipe `"$recipe`" --params issue_number=$n --params `"pr_body_path=$prBody`" " +
                 "--provider ollama --model $Model --no-session --max-turns 60 --max-tool-repetitions 3"

    # Issue text drives a shell with no approval prompts, so only the repo owner's issues are worked.
    $skipOwner = ($author -ne $owner)
    $skipRuns = ($runs -ge $MaxRuns)

    if ($DryRun) {   # read-only: no labels, comments, worktrees or runs
        if ($skipOwner) { Write-Host "DRY RUN: would skip #$n (author $author is not $owner)." }
        elseif ($skipRuns) { Write-Host "DRY RUN: would mark #$n needs-human ($runs runs already)." }
        else {
            Write-Host "DRY RUN: would take issue #$n (author $author)."
            Write-Host "  worktree: $wt  branch: $branch"
            Write-Host "  run (cwd = worktree, GOOSE_MODE=auto): goose $gooseArgs"
        }
        return 0
    }
    if ($skipOwner) {
        Set-NeedsHuman $n "Skipped: the agent only works on issues authored by $owner."
        return 0
    }
    if ($skipRuns) {
        Set-NeedsHuman $n "Agent already ran $runs times on this issue. Needs a human."
        return 0
    }

    Invoke-Native gh @("issue", "edit", "$n", "--remove-label", "ready-for-agent", "--add-label", "agent-working") | Out-Null
    $oldMode = $env:GOOSE_MODE
    $prDone = $false
    try {
        # Worktree: continue the remote branch on a retry, otherwise start from the latest origin/main.
        Invoke-Native git @("fetch", "origin") | Out-Null
        if (-not (Test-Path $wt)) {
            New-Item -ItemType Directory -Force $wtRoot | Out-Null
            $remote = (Invoke-Native git @("ls-remote", "--heads", "origin", $branch)) -join ""
            if ($remote) {
                Invoke-Native git @("worktree", "add", "-B", $branch, $wt, "origin/$branch") | Out-Null
            } else {
                # -B: a local branch left over from an earlier, finished run must not block a fresh start.
                Invoke-Native git @("worktree", "add", "-B", $branch, $wt, "origin/main") | Out-Null
            }
        }
        # An existing worktree path is only trusted if it really is a checkout of this issue's branch.
        $current = ((Invoke-Native git @("-C", $wt, "branch", "--show-current")) -join "").Trim()
        if ($current -ne $branch) { throw "Worktree $wt is on '$current', expected '$branch'" }
        Invoke-Native uv @("--directory", $wt, "sync") | Out-Null

        # Start-Process sets the working directory itself (it throws if the worktree is missing, so the
        # worker can never start in the main checkout) and avoids cmd.exe quoting and %VAR% expansion.
        # Headless runs cannot answer approval prompts, so Auto is required. Scoped to this process tree.
        $env:GOOSE_MODE = "auto"
        $proc = Start-Process -FilePath "goose" -ArgumentList $gooseArgs -WorkingDirectory $wt `
            -RedirectStandardOutput $log -RedirectStandardError $errLog -NoNewWindow -Wait -PassThru
        $exit = $proc.ExitCode
        $env:GOOSE_MODE = $oldMode

        # Success means an open PR exists for the branch, not that the model printed a link.
        $tail = @(Get-Content $log -Tail 30 -Encoding UTF8) + @(Get-Content $errLog -Tail 10 -Encoding UTF8)
        $prUrl = ((Invoke-Native gh @("pr", "list", "--head", $branch, "--base", "main", "--state", "open", "--json", "url", "--jq", ".[0].url")) -join "").Trim()
        if (($exit -eq 0) -and $prUrl -and (($tail -join "`n") -notmatch "BLOCKED")) {
            $prDone = $true   # from here on a bookkeeping failure must not relabel a finished issue
            Invoke-Native gh @("issue", "edit", "$n", "--remove-label", "agent-working") | Out-Null
            Invoke-Native gh @("issue", "comment", "$n", "--body", "Agent run finished: $prUrl") | Out-Null
            # Only remove a clean worktree; uncommitted edits stay for a human to look at.
            $left = @(Invoke-Native git @("-C", $wt, "status", "--short"))
            if ($left.Count -eq 0) {
                Invoke-Native git @("worktree", "remove", $wt) | Out-Null
            } else {
                Write-Host "Worktree $wt has uncommitted changes; leaving it in place."
            }
        } else {
            $body = Join-Path $logs "fail-$n-$stamp.md"
            Write-Utf8 $body (@("Agent run did not produce a PR (exit $exit). Work is kept in $wt.", "Last lines of the log:", '```') + $tail + '```')
            Invoke-Native gh @("issue", "edit", "$n", "--remove-label", "agent-working", "--add-label", "needs-human") | Out-Null
            Invoke-Native gh @("issue", "comment", "$n", "--body-file", $body) | Out-Null
        }
    } catch {
        $env:GOOSE_MODE = $oldMode
        Write-Error "Run for issue #$n failed: $_" -ErrorAction Continue
        if (-not $prDone) {
            try { Set-NeedsHuman $n "Agent poller failed: $_" } catch { Write-Error "Could not mark #$n needs-human: $_" -ErrorAction Continue }
        }
        return 1
    }
    return 0
}

if (-not (Enter-Lock)) {
    Write-Host "Another run holds the lock; exiting."
    exit 0
}
$code = 0
try { $code = @(Invoke-Poll)[-1] } finally { Remove-Item $lock -ErrorAction SilentlyContinue }
exit $code
