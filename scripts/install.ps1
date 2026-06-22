#Requires -Version 5.1
<#
.SYNOPSIS
    Windows-native installer for the OptivAI Claude Plugin.

.DESCRIPTION
    Mirrors install.sh's core install logic for Windows-native PowerShell
    (run outside WSL). Authored cross-referencing install.sh; both must be
    kept in sync when file lists change.

    IMPORTANT: This script has been authored on macOS and cannot be smoke-
    tested from there. It must be validated with a real Windows smoke test
    before declaring production-ready. See bead fblai-8mg8z for the follow-up
    tracking item.

.PARAMETER SkipDaemon
    Skip Windows Task Scheduler setup for the pg_sync daemon.

.PARAMETER Force
    Overwrite existing installation (re-run merge_settings idempotently).

.PARAMETER Uninstall
    Remove plugin files and hooks from settings.json. Logs and user data are
    preserved.

.PARAMETER Email
    User email for CLAUDE_USER_EMAIL attribution (optional; prompted if omitted).

.PARAMETER Org
    Organisation name for CLAUDE_ORG_NAME (optional; defaults to FeedbackLoopAI).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -SkipDaemon -Force

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -Uninstall
#>

[CmdletBinding()]
param(
    [switch]$SkipDaemon,
    [switch]$Force,
    [switch]$Uninstall,
    [string]$Email = "",
    [string]$Org   = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Resolve paths
# ---------------------------------------------------------------------------
$ScriptDir = $PSScriptRoot
$RepoDir   = Split-Path -Parent $ScriptDir
$ClaudeDir = Join-Path $env:USERPROFILE ".claude"
$HooksDir  = Join-Path $ClaudeDir "hooks"
$CommandsDir = Join-Path $ClaudeDir "commands"
$AgentsDir = Join-Path $ClaudeDir "agents"
$SkillsDir = Join-Path $ClaudeDir "skills"
$LogsDir   = Join-Path $ClaudeDir "logs"
$MemoryDir = Join-Path $ClaudeDir "optivai-memory"
$SqlDir    = Join-Path $ClaudeDir "sql"
$SettingsFile = Join-Path $ClaudeDir "settings.json"
$MergeScript  = Join-Path $ScriptDir "merge_settings.py"

$PluginName     = "OptivAI Claude Plugin"
$TaskName       = "OptivAI-PgSync"
$SystemdService = "claude-pg-sync"   # informational only on Windows

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

function Write-Header([string]$text) {
    Write-Host ""
    Write-Host "=== $text ===" -ForegroundColor Blue
    Write-Host ""
}

function Write-OK([string]$msg) {
    Write-Host "[OK] $msg" -ForegroundColor Green
}

function Write-Warn([string]$msg) {
    Write-Host "[!]  $msg" -ForegroundColor Yellow
}

function Write-Err([string]$msg) {
    Write-Host "[X]  $msg" -ForegroundColor Red
}

function Write-Info([string]$msg) {
    Write-Host "[*]  $msg" -ForegroundColor Cyan
}

# Resolve the Python executable to use. On Windows the launcher is "py" or
# "python" — "python3" is not guaranteed to exist.
function Get-PythonExe() {
    foreach ($candidate in @("python", "py", "python3")) {
        $found = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($found) {
            # Verify it's Python 3
            $ver = & $candidate --version 2>&1
            if ($ver -match "Python 3") {
                return $candidate
            }
        }
    }
    return $null
}

# Copy a file if it exists at the source path.
function Copy-IfExists([string]$src, [string]$dest) {
    if (Test-Path $src) {
        Copy-Item -Path $src -Destination $dest -Force
        return $true
    }
    return $false
}

# Copy a directory recursively if the source exists.
function Copy-DirIfExists([string]$src, [string]$destParent) {
    if (Test-Path $src -PathType Container) {
        Copy-Item -Path $src -Destination $destParent -Recurse -Force
        return $true
    }
    return $false
}

# ---------------------------------------------------------------------------
# Environment variable validation
# ---------------------------------------------------------------------------
function Test-EnvVars() {
    Write-Header "Checking Environment Variables"

    $missing = @()

    if (-not $env:DATABASE_URL) {
        $missing += "DATABASE_URL"
    } else {
        Write-OK "DATABASE_URL is set"
    }

    if (-not $env:ANTHROPIC_API_KEY) {
        $missing += "ANTHROPIC_API_KEY"
    } else {
        Write-OK "ANTHROPIC_API_KEY is set"
    }

    if ($missing.Count -gt 0) {
        Write-Warn "The following environment variables are not set:"
        foreach ($v in $missing) {
            Write-Host "    $v" -ForegroundColor Yellow
        }
        Write-Host ""
        Write-Info "On Windows, set them as persistent user environment variables:"
        Write-Host "    setx DATABASE_URL    `"postgresql://user:pass@host/db?sslmode=require`""
        Write-Host "    setx ANTHROPIC_API_KEY `"sk-ant-...`""
        Write-Host ""
        Write-Info "Or via: System Properties -> Environment Variables -> User variables."
        Write-Host ""
        Write-Warn "The plugin will fall back to reading credentials from the config file."
        Write-Warn "Install continues — set the variables before running Claude Code."
    }
}

# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------
function Invoke-Uninstall([string]$pythonExe) {
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Yellow
    Write-Host "  $PluginName Uninstaller" -ForegroundColor Yellow
    Write-Host "========================================" -ForegroundColor Yellow
    Write-Host ""
    Write-Info "Uninstalling $PluginName..."
    Write-Info "Note: Logs and user data will be preserved"
    Write-Host ""

    # Remove Windows Task Scheduler entry
    $taskExists = schtasks /query /tn $TaskName 2>$null
    if ($LASTEXITCODE -eq 0) {
        schtasks /delete /tn $TaskName /f | Out-Null
        Write-OK "Removed Windows Task Scheduler entry: $TaskName"
    } else {
        Write-Warn "Task Scheduler entry not found (already removed)"
    }

    # Remove hook scripts
    $hookFiles = @(
        "auto_recall_hook.py", "beads_writer.py", "brain_hook.py",
        "citation_walker.py", "context_primer.py", "dispatch_gate.py", "log_writer.py",
        "memory_writer.py", "open_brain.py", "pg_sync.py",
        "post_tool_use.py", "pre-tool-use.py", "redact_secrets.py",
        "session_summary.py", "stop-hook.py", "stop-hook.sh",
        "subagent_context.py", "till_done.py", "time_travel.py",
        "user-prompt-submit.py", "vf_probe.py"
    )
    if (Test-Path $HooksDir) {
        foreach ($f in $hookFiles) {
            $target = Join-Path $HooksDir $f
            if (Test-Path $target) { Remove-Item $target -Force }
        }
        $redactDir = Join-Path $HooksDir "redact"
        if (Test-Path $redactDir) { Remove-Item $redactDir -Recurse -Force }
        # Mirror install.sh: clean up the Python bytecode cache too.
        Remove-Item -Recurse -Force (Join-Path $HooksDir "__pycache__") -ErrorAction SilentlyContinue
        Write-OK "Removed hook scripts"
    }

    # Remove commands
    if (Test-Path $CommandsDir) {
        $count = (Get-ChildItem $CommandsDir -File -Recurse).Count
        Get-ChildItem $CommandsDir | Remove-Item -Recurse -Force
        Write-OK "Removed $count command files"
    }

    # Remove agents
    if (Test-Path $AgentsDir) {
        $count = (Get-ChildItem $AgentsDir -Filter "*.md" -File).Count
        Get-ChildItem $AgentsDir -Filter "*.md" | Remove-Item -Force
        Write-OK "Removed $count agent templates"
    }

    # Remove skills
    if (Test-Path $SkillsDir) {
        $count = (Get-ChildItem $SkillsDir -Recurse -File).Count
        if ($count -gt 0) {
            Get-ChildItem $SkillsDir | Remove-Item -Recurse -Force
            Write-OK "Removed $count skill files"
        }
    }

    # Remove plugin hooks from settings.json via merge_settings.py --uninstall
    if (Test-Path $SettingsFile) {
        $ts = (Get-Date -Format "yyyyMMdd-HHmmss")
        $backup = "$SettingsFile.uninstall-backup.$ts"
        Copy-Item $SettingsFile $backup
        Write-OK "Backed up settings.json to: $backup"
        & $pythonExe $MergeScript $SettingsFile --uninstall
        if ($LASTEXITCODE -ne 0) {
            Write-Warn "merge_settings.py --uninstall exited with code $LASTEXITCODE"
        } else {
            Write-OK "Removed plugin hooks from settings.json (user preferences preserved)"
        }
    }

    Write-Host ""
    Write-Host "========================================" -ForegroundColor Green
    Write-OK "Uninstall complete!"
    Write-Host "========================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "Preserved directories:"
    Write-Host "  - $LogsDir (activity logs)"
    Write-Host "  - $MemoryDir (memory system data)"
    Write-Host ""
    Write-Host "To completely remove all data, manually delete:"
    Write-Host "  $ClaudeDir"
    Write-Host ""
}

# ---------------------------------------------------------------------------
# Daemon: Windows Task Scheduler
# ---------------------------------------------------------------------------
function Install-Daemon([string]$pythonExe) {
    Write-Header "Installing PostgreSQL Sync Daemon (Windows Task Scheduler)"

    $syncScript = Join-Path $HooksDir "pg_sync.py"
    $logFile    = Join-Path $LogsDir "sync_daemon.log"

    # Build the task action command
    $taskAction = "& `"$pythonExe`" `"$syncScript`" --once >> `"$logFile`" 2>&1"

    # Check if task already exists
    $existing = schtasks /query /tn $TaskName 2>$null
    if ($LASTEXITCODE -eq 0) {
        if ($Force) {
            schtasks /delete /tn $TaskName /f | Out-Null
            Write-Info "Removed existing task (--Force)"
        } else {
            Write-OK "Task Scheduler entry already exists: $TaskName (use -Force to recreate)"
            return
        }
    }

    # Create the task: run every 5 minutes as the current user.
    # Use an argument array + splatting rather than backtick line-continuation.
    # A trailing space after any backtick silently turns it into a literal and
    # breaks the parse on some PowerShell versions; splatting avoids that footgun.
    $schtasksArgs = @(
        '/create',
        '/tn', $TaskName,
        '/tr', $taskAction,
        '/sc', 'MINUTE',
        '/mo', '5',
        '/ru', $env:USERNAME,
        '/rl', 'LIMITED',
        '/f'
    )
    schtasks @schtasksArgs 2>&1 | Out-Null

    if ($LASTEXITCODE -eq 0) {
        Write-OK "Created Task Scheduler entry: $TaskName (runs every 5 minutes)"
        Write-Host "  Verify: schtasks /query /tn $TaskName"
    } else {
        Write-Warn "schtasks /create failed (exit $LASTEXITCODE). You can set it up manually:"
        Write-Host "  schtasks /create /tn `"$TaskName`" /tr `"$taskAction`" /sc MINUTE /mo 5 /ru `"$env:USERNAME`" /f"
        Write-Info "Or run the sync manually when needed:"
        Write-Host "  $pythonExe `"$syncScript`" --once"
    }
}

# ---------------------------------------------------------------------------
# Main install
# ---------------------------------------------------------------------------
function Invoke-Install() {
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Blue
    Write-Host "   $PluginName - Installation" -ForegroundColor Blue
    Write-Host "   Platform: Windows (native PowerShell)" -ForegroundColor Blue
    Write-Host "============================================================" -ForegroundColor Blue
    Write-Host ""

    # ── Prerequisites ──────────────────────────────────────────────────────
    Write-Header "Checking Prerequisites"

    $pythonExe = Get-PythonExe
    if (-not $pythonExe) {
        Write-Err "Python 3 is required but not found."
        Write-Host "Download from: https://www.python.org/downloads/windows/"
        Write-Host "Ensure 'Add Python to PATH' is checked during installation."
        exit 1
    }
    $pythonVer = & $pythonExe --version 2>&1
    Write-OK "Python found: $pythonVer  (executable: $pythonExe)"

    # Validate environment variables before proceeding
    Test-EnvVars

    # ── Create directories ──────────────────────────────────────────────────
    Write-Header "Creating Directories"

    foreach ($dir in @($HooksDir, $CommandsDir, $AgentsDir, $SkillsDir, $LogsDir, $MemoryDir, $SqlDir)) {
        if (-not (Test-Path $dir)) {
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
        }
    }
    Write-OK "Created ~/.claude directory structure"

    # ── pip install ─────────────────────────────────────────────────────────
    Write-Header "Installing Python Dependencies"

    $reqFile = Join-Path $ScriptDir "requirements.txt"
    if (Test-Path $reqFile) {
        Write-Info "Running: $pythonExe -m pip install -r $reqFile"
        & $pythonExe -m pip install -r $reqFile
        if ($LASTEXITCODE -ne 0) {
            Write-Warn "pip install -r requirements.txt failed (exit $LASTEXITCODE)"
            Write-Warn "Try manually: $pythonExe -m pip install -r `"$reqFile`""
        } else {
            Write-OK "Python dependencies installed"
        }
    } else {
        Write-Warn "requirements.txt not found at $reqFile — skipping pip install"
    }

    # Install Beads CLI via setup.py (editable install)
    if ((Test-Path (Join-Path $RepoDir "setup.py")) -and (Test-Path (Join-Path $ScriptDir "beads"))) {
        Write-Info "Installing Beads CLI (editable install)..."
        & $pythonExe -m pip install -e $RepoDir
        if ($LASTEXITCODE -ne 0) {
            Write-Warn "Beads CLI install failed. Try: $pythonExe -m pip install -e `"$RepoDir`""
        } else {
            Write-OK "Installed Beads CLI"
        }
    }

    # ── Hook scripts ────────────────────────────────────────────────────────
    Write-Header "Installing Hook Scripts"

    # Files from scripts/hooks/ -> ~/.claude/hooks/
    # Keep this list in sync with install.sh's hook_files array.
    $hookFiles = @(
        "auto_recall_hook.py",
        "beads_writer.py",
        "brain_hook.py",
        "context_primer.py",
        "dispatch_gate.py",
        "log_writer.py",
        "memory_writer.py",
        "post_tool_use.py",
        "pre-tool-use.py",
        "redact_secrets.py",
        "session_summary.py",
        "stop-hook.py",
        "stop-hook.sh",
        "subagent_context.py",
        "till_done.py",
        "user-prompt-submit.py"
    )

    $repoHooksDir = Join-Path $ScriptDir "hooks"
    $count = 0
    foreach ($f in $hookFiles) {
        $src = Join-Path $repoHooksDir $f
        if (Copy-IfExists $src $HooksDir) { $count++ }
    }

    # Files from scripts/ top-level -> ~/.claude/hooks/
    # Keep this list in sync with install.sh's scripts_top_files array.
    $topLevelFiles = @(
        "open_brain.py",
        "citation_walker.py",
        "time_travel.py",
        "vf_probe.py",
        "pg_sync.py"
    )
    foreach ($f in $topLevelFiles) {
        $src = Join-Path $ScriptDir $f
        if (Copy-IfExists $src $HooksDir) { $count++ }
    }

    # Deploy redact/ package
    $redactSrc = Join-Path $ScriptDir "redact"
    if (Copy-DirIfExists $redactSrc $HooksDir) {
        $count++
        Write-OK "Installed redact/ PII-redaction package"
    }

    # Deploy SQL schema files
    $sqlSrc = Join-Path $RepoDir "sql"
    foreach ($sqlFile in @("BRAIN_SCHEMA_PG.sql", "VW_CLAUDE_CODE_VIEWS_PG.sql")) {
        $src = Join-Path $sqlSrc $sqlFile
        Copy-IfExists $src $SqlDir | Out-Null
    }

    Write-OK "Installed $count hook scripts to $HooksDir"

    # ── Slash commands ──────────────────────────────────────────────────────
    Write-Header "Installing Slash Commands"

    $cmdSrc = Join-Path $RepoDir ".claude\commands"
    if (Test-Path $cmdSrc) {
        Copy-Item -Path "$cmdSrc\*" -Destination $CommandsDir -Recurse -Force
    }
    $cmdCount = (Get-ChildItem $CommandsDir -Filter "*.md" -Recurse -File -ErrorAction SilentlyContinue).Count
    Write-OK "Installed $cmdCount slash commands to $CommandsDir"

    # Bead commands
    foreach ($pattern in @("bead-*.md", "mol-*.md")) {
        $beadCmdSrc = Join-Path $RepoDir ".claude\commands"
        Get-ChildItem $beadCmdSrc -Filter $pattern -ErrorAction SilentlyContinue |
            Copy-Item -Destination $CommandsDir -Force
    }

    # ── Agent templates ─────────────────────────────────────────────────────
    Write-Header "Installing Agent Templates"

    $agentSrc = Join-Path $RepoDir "agents"
    if (Test-Path $agentSrc) {
        Get-ChildItem $agentSrc -Filter "*.md" | Copy-Item -Destination $AgentsDir -Force
    }
    $agentCount = (Get-ChildItem $AgentsDir -Filter "*.md" -File -ErrorAction SilentlyContinue).Count
    Write-OK "Installed $agentCount agent templates to $AgentsDir"

    # ── Skills ──────────────────────────────────────────────────────────────
    Write-Header "Installing Skills"

    $skillSrc = Join-Path $RepoDir "skills"
    if (Test-Path $skillSrc) {
        Copy-Item -Path "$skillSrc\*" -Destination $SkillsDir -Recurse -Force
        $skillCount = (Get-ChildItem $SkillsDir -ErrorAction SilentlyContinue).Count
        if ($skillCount -gt 0) {
            Write-OK "Installed $skillCount skill entries to $SkillsDir"
        }
    }

    # NOTE: Do NOT create ~/.claude/beads — the canonical beads store is
    # ~/.beads/issues.jsonl, which the beads CLI resolves by walk-up and creates
    # on first use. A ~/.claude/beads directory would be a misleading dead dir
    # that the CLI never reads.

    # ── settings.json merge ─────────────────────────────────────────────────
    Write-Header "Configuring Claude Code Settings"

    # Prompt for email/org if not supplied as parameters
    if (-not $Email) {
        $Email = Read-Host "Your company email (for attribution, optional — press Enter to skip)"
    }
    if (-not $Org) {
        $orgInput = Read-Host "Organisation name [FeedbackLoopAI]"
        $Org = if ($orgInput) { $orgInput } else { "FeedbackLoopAI" }
    }

    # Safety backup before any settings.json write
    if (Test-Path $SettingsFile) {
        $ts = (Get-Date -Format "yyyyMMdd-HHmmss")
        $backup = "$SettingsFile.pre-merge-backup.$ts"
        Copy-Item $SettingsFile $backup
        Write-OK "Backed up existing settings.json to: $backup"
    }

    # Build merge_settings.py argument list and run it
    # merge_settings.py is stdlib Python — runs identically on Windows
    $mergeArgs = @($MergeScript, $SettingsFile)
    if ($Email) { $mergeArgs += @("--email", $Email) }
    if ($Org)   { $mergeArgs += @("--org",   $Org)   }

    & $pythonExe @mergeArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "merge_settings.py exited with code $LASTEXITCODE — settings may be incomplete"
    } else {
        Write-OK "Merged plugin hooks/env into settings.json (user preferences preserved)"
    }

    # ── Daemon ──────────────────────────────────────────────────────────────
    if (-not $SkipDaemon) {
        Install-Daemon $pythonExe
    } else {
        Write-Info "Skipping daemon installation (-SkipDaemon specified)"
        Write-Info "To run the sync manually:"
        Write-Host "  $pythonExe `"$(Join-Path $HooksDir 'pg_sync.py')`" --once"
    }

    # ── Complete ─────────────────────────────────────────────────────────────
    Write-Header "Installation Complete"

    Write-Host "Next steps:"
    Write-Host "  1. Set DATABASE_URL and ANTHROPIC_API_KEY as user env vars if not already set"
    Write-Host "  2. Restart Claude Code (new conversation — plugin uses file-copy deployment)"
    Write-Host ""
    Write-Host "Verify installation:"
    Write-Host "  - Check Task Scheduler: schtasks /query /tn $TaskName"
    Write-Host "  - Check logs: Get-ChildItem $LogsDir"
    Write-Host "  - Test sync: $pythonExe `"$(Join-Path $HooksDir 'pg_sync.py')`" --status"
    Write-Host ""
    Write-Host ""
    Write-Host "NOTE: This installer was authored cross-referencing install.sh on macOS." -ForegroundColor Yellow
    Write-Host "A real Windows smoke test is required before declaring it production-ready." -ForegroundColor Yellow
    Write-Host "Track: bead fblai-8mg8z (FU-2) — Windows install smoke test follow-up." -ForegroundColor Yellow
    Write-Host ""
    Write-OK "Installation complete!"
}

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

# Resolve python before branching so Uninstall also has it
$python = Get-PythonExe
if (-not $python) {
    Write-Err "Python 3 is required but not found."
    Write-Host "Download from: https://www.python.org/downloads/windows/"
    exit 1
}

if ($Uninstall) {
    Invoke-Uninstall $python
} else {
    Invoke-Install
}
