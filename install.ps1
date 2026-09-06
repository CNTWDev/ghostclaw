# ─────────────────────────────────────────────────────────────────────────────
# HushClaw Installer  —  Windows (PowerShell 5.1+)
#
# Usage (run in PowerShell as normal user):
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#   .\install.ps1
#   .\install.ps1 -Update       # stop old process, pull latest code, restart in background
#   .\install.ps1 -StartOnly    # skip install, start existing installation in background
#   .\install.ps1 -Stop         # stop the running server and exit
#   .\install.ps1 -Foreground   # install and start server in foreground (debug mode)
#
# One-liner (auto-bypasses execution policy for this session only):
#   powershell -ExecutionPolicy Bypass -File .\install.ps1
#
# Environment overrides (set before running):
#   $env:HUSHCLAW_HOME   = "C:\Users\you\.hushclaw"  (default: ~\.hushclaw)
#   $env:HUSHCLAW_PORT   = "8765"
#   $env:HUSHCLAW_HOST   = "0.0.0.0"
#   $env:HUSHCLAW_NO_BROWSER = "1"   (skip auto-open browser)
# ─────────────────────────────────────────────────────────────────────────────

param(
    [switch]$Update,
    [switch]$StartOnly,
    [switch]$Stop,
    [switch]$Foreground,
    [switch]$OverwriteInstall,
    [switch]$BackupBeforeOverwrite,
    [ValidateSet("personal")]
    [string]$Distro = "personal",
    [switch]$SkillForceOfficial,
    [switch]$SkillPreserveLocal,
    [switch]$Help
)

$ErrorActionPreference = "Stop"

# Ensure Unicode output renders correctly on all Windows 11 terminals
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding            = [System.Text.Encoding]::UTF8

# ── Config ────────────────────────────────────────────────────────────────────
$RepoUrl    = "https://github.com/CNTWDev/hushclaw.git"
$InstallDir = if ($env:HUSHCLAW_HOME) { $env:HUSHCLAW_HOME } else { "$HOME\.hushclaw" }
$Port       = if ($env:HUSHCLAW_PORT) { $env:HUSHCLAW_PORT } else { "8765" }
$BindHost   = if ($env:HUSHCLAW_HOST) { $env:HUSHCLAW_HOST } else { "0.0.0.0" }
$NoBrowser  = $env:HUSHCLAW_NO_BROWSER -eq "1"

$PidFile    = "$InstallDir\hushclaw.pid"
$LogFile    = "$InstallDir\hushclaw.log"
$InstallStateFile = "$InstallDir\install-state.json"
$WinAppData      = if ($env:APPDATA)      { $env:APPDATA }      else { Join-Path $HOME "AppData\Roaming" }
$WinLocalAppData = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $HOME "AppData\Local" }
$UserDataDir = Join-Path $WinAppData "hushclaw"
$UserConfigDir = $UserDataDir

if ($OverwriteInstall -and -not $BackupBeforeOverwrite) {
    $BackupBeforeOverwrite = $true
}

# ── Helpers ───────────────────────────────────────────────────────────────────
function Write-Section($msg) {
    Write-Host ""
    Write-Host "══ $msg " -ForegroundColor Blue -NoNewline
    Write-Host ""
}
function Write-Ok($msg)   { Write-Host "  ✓  $msg" -ForegroundColor Green }
function Write-Info($msg) { Write-Host "  ▸  $msg" -ForegroundColor Cyan }
function Write-Warn($msg) { Write-Host "  !  $msg" -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "  ✗  $msg" -ForegroundColor Red }
function Die($msg)        { Write-Err $msg; exit 1 }
function Write-Detail($msg) { Write-Host "    ·  $msg" -ForegroundColor Blue }
function Write-DetailOk($msg) { Write-Host "    ✓  $msg" -ForegroundColor Green }
function Write-DetailWarn($msg) { Write-Host "    !  $msg" -ForegroundColor Yellow }

function Test-RepoDirty($RepoDir) {
    if (-not (Test-Path "$RepoDir\.git")) { return $false }
    $status = git -C $RepoDir status --short 2>$null
    return -not [string]::IsNullOrWhiteSpace(($status | Out-String))
}

function Get-RepoDirtyFiles($RepoDir) {
    if (-not (Test-Path "$RepoDir\.git")) { return @() }
    $status = git -C $RepoDir status --short 2>$null
    return @($status | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
}

function Get-BackupRootDir {
    $ts = Get-Date -Format "yyyyMMdd-HHmmss"
    return Join-Path $InstallDir "backups\$ts"
}

function Backup-UserData($Root) {
    New-Item -ItemType Directory -Force -Path $Root | Out-Null
    if (Test-Path $UserDataDir) {
        Copy-Item $UserDataDir (Join-Path $Root "data") -Recurse -Force
    }
    if ((Test-Path $UserConfigDir) -and ($UserConfigDir -ne $UserDataDir)) {
        Copy-Item $UserConfigDir (Join-Path $Root "config") -Recurse -Force
    }
    if (Test-Path $InstallStateFile) {
        Copy-Item $InstallStateFile (Join-Path $Root "install-state.json") -Force
    }
    $manifest = @{
        created_at = [DateTime]::UtcNow.ToString("o")
        data_dir = $UserDataDir
        config_dir = $UserConfigDir
        install_state = $InstallStateFile
    } | ConvertTo-Json -Depth 3
    Set-Content -Path (Join-Path $Root "manifest.json") -Value $manifest -Encoding UTF8
}

function Backup-RepoOverlay($RepoDir, $Root) {
    New-Item -ItemType Directory -Force -Path $Root | Out-Null
    git -C $RepoDir status --short 2>$null | Set-Content (Join-Path $Root "git-status.txt") -Encoding UTF8
    git -C $RepoDir diff 2>$null | Set-Content (Join-Path $Root "git-diff.patch") -Encoding UTF8
    git -C $RepoDir diff --cached 2>$null | Set-Content (Join-Path $Root "git-diff-cached.patch") -Encoding UTF8
    git -C $RepoDir ls-files --others --exclude-standard 2>$null | Set-Content (Join-Path $Root "untracked.txt") -Encoding UTF8
}

function Write-InstallState($BackupPath = "", $Result = "ok") {
    $currentCommit = ""
    $repoDir = "$InstallDir\repo"
    if (Test-Path "$repoDir\.git") {
        $currentCommit = (git -C $repoDir rev-parse --short HEAD 2>$null | Select-Object -First 1)
    }
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    $payload = @{
        last_upgrade_at = [DateTime]::UtcNow.ToString("o")
        current_commit = "$currentCommit"
        backup_path = "$BackupPath"
        overwrite_install_used = [bool]$OverwriteInstall
        backup_before_overwrite = [bool]$BackupBeforeOverwrite
        last_upgrade_result = "$Result"
    } | ConvertTo-Json -Depth 3
    Set-Content -Path $InstallStateFile -Value $payload -Encoding UTF8
}

function Get-WebPathForDistro($distro) {
    return "/personal"
}

function Write-StructuredDetail($line) {
    if (-not $line) { return }
    if ($line.StartsWith("ok|")) {
        Write-DetailOk ($line.Substring(3))
    } elseif ($line.StartsWith("warn|")) {
        Write-DetailWarn ($line.Substring(5))
    } elseif ($line.StartsWith("summary|")) {
        Write-Detail ($line.Substring(8))
    } elseif ($line.StartsWith("info|")) {
        Write-Detail ($line.Substring(5))
    } else {
        Write-Detail $line
    }
}

function Write-SkillSyncDetail($line) {
    if (-not $line) { return }
    if ($line.StartsWith("[installed] ")) {
        Write-DetailOk ("Installed " + $line.Substring(12))
    } elseif ($line.StartsWith("[updated] ")) {
        Write-DetailOk ("Updated " + $line.Substring(10))
    } elseif ($line.StartsWith("[forced_updated] ")) {
        Write-DetailWarn ("Replaced " + $line.Substring(17))
    } elseif ($line.StartsWith("[skipped_dirty] ")) {
        Write-DetailWarn ("Preserved local copy " + $line.Substring(16))
    } elseif ($line.StartsWith("[skipped_error] ")) {
        Write-DetailWarn ("Skipped " + $line.Substring(16))
    } elseif ($line.StartsWith("summary ")) {
        Write-Detail ("Summary: " + $line.Substring(8))
    } else {
        Write-Detail $line
    }
}

# Run a multi-line Python script reliably on all Windows versions.
# Passing large here-strings via `python -c` is fragile in PowerShell 5.1 on
# Windows 11 — newlines get mangled and Python raises SyntaxError.
# This helper writes the code to a UTF-8 temp file and runs python <file>.
#   -MergeStderr  merge stderr into the returned output (default: discard stderr)
function Invoke-PythonScript {
    param(
        [string]$Code,
        [string[]]$Arguments = @(),
        [switch]$MergeStderr
    )
    $tmp = [System.IO.Path]::GetTempFileName() -replace '\.tmp$', '.py'
    try {
        [System.IO.File]::WriteAllText($tmp, $Code, [System.Text.Encoding]::UTF8)
        if ($MergeStderr) {
            return & $PythonExe $tmp @Arguments 2>&1
        } else {
            return & $PythonExe $tmp @Arguments 2>$null
        }
    } finally {
        Remove-Item $tmp -Force -ErrorAction SilentlyContinue
    }
}

# Refresh PATH in current session after winget/system installs
function Refresh-EnvPath {
    $machinePath = [System.Environment]::GetEnvironmentVariable("PATH", "Machine")
    $userPath    = [System.Environment]::GetEnvironmentVariable("PATH", "User")
    $env:PATH    = "$machinePath;$userPath"
}

# ── Process management helpers ────────────────────────────────────────────────

function Get-HushClawPid {
    # 1. Check PID file first
    if (Test-Path $PidFile) {
        $savedPid = [int](Get-Content $PidFile -ErrorAction SilentlyContinue)
        if ($savedPid -gt 0) {
            $proc = Get-Process -Id $savedPid -ErrorAction SilentlyContinue
            if ($proc) {
                return $savedPid
            }
        }
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    }
    # 2. Fallback: scan processes by command line (Get-CimInstance preferred on Win10+)
    try {
        $procs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
                 Where-Object { $_.CommandLine -match "hushclaw.*serve" -or $_.CommandLine -match "python.*hushclaw.*serve" } |
                 Select-Object -First 1
        if ($procs) { return [int]$procs.ProcessId }
    } catch {
        # Last resort for very old systems
        try {
            $procs = Get-WmiObject Win32_Process -ErrorAction SilentlyContinue |
                     Where-Object { $_.CommandLine -match "hushclaw.*serve" -or $_.CommandLine -match "python.*hushclaw.*serve" } |
                     Select-Object -First 1
            if ($procs) { return [int]$procs.ProcessId }
        } catch {}
    }
    # 3. Final fallback: detect the process listening on the configured port.
    # This covers installs where the running server shows up as python.exe.
    try {
        $tcp = Get-NetTCPConnection -LocalPort ([int]$Port) -State Listen -ErrorAction SilentlyContinue |
               Select-Object -First 1
        if ($tcp -and $tcp.OwningProcess) { return [int]$tcp.OwningProcess }
    } catch {}
    try {
        $line = netstat -ano -p tcp 2>$null |
                Where-Object { $_ -match "LISTENING" -and $_ -match (":{0}\s" -f [regex]::Escape($Port)) } |
                Select-Object -First 1
        if ($line) {
            $m = [regex]::Match($line.ToString(), "LISTENING\s+(\d+)$")
            if ($m.Success) { return [int]$m.Groups[1].Value }
        }
    } catch {}
    return $null
}

function Stop-HushClaw($pidToStop) {
    Write-Info "Stopping HushClaw (PID $pidToStop)…"
    Stop-Process -Id $pidToStop -ErrorAction SilentlyContinue
    $deadline = (Get-Date).AddSeconds(10)
    while ((Get-Date) -lt $deadline) {
        if (-not (Get-Process -Id $pidToStop -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Milliseconds 500
    }
    if (Test-Path $PidFile) { Remove-Item $PidFile -Force -ErrorAction SilentlyContinue }
    Write-Ok "Server stopped"
}

function Start-HushClawBackground($gcExe) {
    $taskName = "HushClaw"
    # Try Windows Task Scheduler first (survives reboots, no window)
    try {
        $action   = New-ScheduledTaskAction -Execute $gcExe `
                        -Argument "serve --host $BindHost --port $Port --distro $Distro"
        $trigger  = New-ScheduledTaskTrigger -AtStartup
        $settings = New-ScheduledTaskSettingsSet `
                        -RestartCount 3 `
                        -RestartInterval (New-TimeSpan -Minutes 1) `
                        -ExecutionTimeLimit ([TimeSpan]::Zero)
        Register-ScheduledTask -TaskName $taskName -Action $action `
            -Trigger $trigger -Settings $settings `
            -RunLevel Highest -Force -ErrorAction Stop | Out-Null
        Start-ScheduledTask -TaskName $taskName -ErrorAction Stop
        Write-Ok "Registered and started as Windows Scheduled Task '$taskName'"
        Write-Info "Manage: Task Scheduler → '$taskName'"
        Write-Info "Stop:   .\install.ps1 -Stop"
    } catch {
        # Fallback: hidden background process + PID file
        Write-Warn "Task Scheduler registration failed ($($_.Exception.Message)) — using hidden process fallback"
        $proc = Start-Process -FilePath $gcExe `
                    -ArgumentList "serve","--host",$BindHost,"--port",$Port,"--distro",$Distro `
                    -WindowStyle Hidden -PassThru -RedirectStandardOutput $LogFile `
                    -ErrorAction Stop
        $proc.Id | Set-Content $PidFile
        Write-Ok "Server started in background (PID $($proc.Id))"
        Write-Info "Logs: $LogFile"
        Write-Info "Stop: .\install.ps1 -Stop"
    }
}

# ── Banner ────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "    __  __           __    ________" -ForegroundColor Cyan
Write-Host "   / / / /_  _______/ /_  / ____/ /___ __      __" -ForegroundColor Cyan
Write-Host "  / /_/ / / / / ___/ __ \/ /   / / __ ``/ | /| / /" -ForegroundColor Cyan
Write-Host " / __  / /_/ (__  ) / / / /___/ / /_/ /| |/ |/ /" -ForegroundColor Cyan
Write-Host "/_/ /_/\__,_/____/_/ /_/\____/_/\__,_/ |__/|__/" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Lightweight AI Agent Framework with Persistent Memory" -ForegroundColor White
Write-Host ""
Write-Host "  ───────────────────────────────────────────────────────" -ForegroundColor Blue
Write-Host "  https://github.com/CNTWDev/hushclaw  ·  tuanweishi@gmail.com" -ForegroundColor DarkCyan
Write-Host "  ───────────────────────────────────────────────────────" -ForegroundColor Blue
Write-Host ""

if ($Help) {
    Write-Host "Usage: .\install.ps1 [-Update] [-StartOnly] [-Stop] [-Foreground] [-OverwriteInstall] [-BackupBeforeOverwrite] [-Distro personal] [-SkillForceOfficial] [-SkillPreserveLocal] [-Help]"
    Write-Host "  (no flag)   Install HushClaw and start server in background"
    Write-Host "  -Update     Stop old process, pull latest code, restart in background"
    Write-Host "  -StartOnly  Skip install, start existing installation in background"
    Write-Host "  -Stop       Stop the running HushClaw server and exit"
    Write-Host "  -Foreground Install and start server in foreground (debug mode)"
    Write-Host "  -Distro personal    Run as personal assistant (default)"
    Write-Host "  -SkillForceOfficial Force overwrite bundled skills even if locally modified"
    Write-Host "  -SkillPreserveLocal Keep locally modified bundled skills (default)"
    exit 0
}

$Mode = if ($StartOnly) { "start" } elseif ($Update) { "update" } elseif ($Stop) { "stop" } else { "install" }
$SkillPolicy = if ($SkillForceOfficial) { "force_official" } else { "preserve_skip" }
if ($SkillPreserveLocal) { $SkillPolicy = "preserve_skip" }

# ── --Stop mode: early exit ───────────────────────────────────────────────────
if ($Mode -eq "stop") {
    # Check Task Scheduler first
    $taskName = "HushClaw"
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($task -and $task.State -eq "Running") {
        Write-Info "Stopping HushClaw scheduled task…"
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        Write-Ok "Scheduled task stopped"
        exit 0
    }
    # Fallback: PID file / process scan
    $runningPid = Get-HushClawPid
    if (-not $runningPid) {
        Die "HushClaw is not running."
    }
    Stop-HushClaw $runningPid
    Write-Ok "HushClaw stopped."
    exit 0
}

# ── Auto-install helpers (winget) ─────────────────────────────────────────────

function Get-WingetCmd {
    # winget is available on Windows 10 1709+ via App Installer
    $wg = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $wg) {
        # Also check common install location
        $wgPath = "$env:LOCALAPPDATA\Microsoft\WindowsApps\winget.exe"
        if (Test-Path $wgPath) { return $wgPath }
    }
    return $wg
}

function Install-PythonAuto {
    Write-Warn "Python 3.11+ not found — attempting auto-install…"
    $wg = Get-WingetCmd
    if (-not $wg) {
        Write-Err "winget is not available on this system."
        Write-Host ""
        Write-Info "Please install Python 3.13 manually:"
        Write-Info "  https://www.python.org/downloads/"
        Write-Info "  ► Tick 'Add Python to PATH' during installation."
        Write-Info "  ► Then re-run this script."
        exit 1
    }
    $installed = $false
    foreach ($pyId in @("Python.Python.3.13", "Python.Python.3.12", "Python.Python.3.11")) {
        Write-Info "Installing $pyId via winget…"
        & winget install -e --id $pyId --silent `
            --accept-package-agreements --accept-source-agreements 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Ok "$pyId installed"
            $installed = $true
            break
        }
    }
    if (-not $installed) {
        Write-Err "Automatic Python installation failed."
        Write-Info "Download manually: https://www.python.org/downloads/"
        exit 1
    }
    Refresh-EnvPath
}

function Install-GitAuto {
    Write-Warn "Git not found — attempting auto-install…"
    $wg = Get-WingetCmd
    if (-not $wg) {
        Write-Err "winget is not available on this system."
        Write-Info "Download Git from: https://git-scm.com/download/win"
        exit 1
    }
    Write-Info "Installing Git.Git via winget…"
    & winget install -e --id Git.Git --silent `
        --accept-package-agreements --accept-source-agreements 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Git installation failed."
        Write-Info "Download manually: https://git-scm.com/download/win"
        exit 1
    }
    Write-Ok "Git installed"
    Refresh-EnvPath
}

# ── Python detection ──────────────────────────────────────────────────────────
function Find-Python {
    # Check py launcher first (standard Windows Python Launcher, resolves to actual exe)
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        try {
            $exePath = & py -3 -c "import sys; print(sys.executable)" 2>$null
            $verStr  = & py -3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
            if ($exePath -and $verStr) {
                $parts = $verStr.Trim().Split(".")
                if ([int]$parts[0] -ge 3 -and [int]$parts[1] -ge 11) {
                    return $exePath.Trim()
                }
            }
        } catch {}
    }
    # Fall back to named commands
    foreach ($cmd in @("python3.13", "python3.12", "python3.11", "python3", "python")) {
        $found = Get-Command $cmd -ErrorAction SilentlyContinue
        if ($found) {
            try {
                $verStr = & $cmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
                $parts  = $verStr.Trim().Split(".")
                if ([int]$parts[0] -ge 3 -and [int]$parts[1] -ge 11) {
                    return $found.Source
                }
            } catch {}
        }
    }
    return $null
}

Write-Section "Checking Python"
$PythonExe = Find-Python

if (-not $PythonExe) {
    Install-PythonAuto
    # Re-scan after install (winget adds to PATH; current session needs refresh)
    $PythonExe = Find-Python
    if (-not $PythonExe) {
        Write-Warn "Python installed but not yet visible in this session."
        Write-Warn "Please open a NEW terminal window and re-run this script."
        exit 1
    }
}

$pyVer = & $PythonExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')" 2>$null
Write-Ok "Found Python $pyVer at $PythonExe"

# ── Git detection ─────────────────────────────────────────────────────────────
Write-Section "Checking Git"
$GitExe = Get-Command git -ErrorAction SilentlyContinue
if (-not $GitExe) {
    Install-GitAuto
    $GitExe = Get-Command git -ErrorAction SilentlyContinue
    if (-not $GitExe) {
        Write-Warn "Git installed but not yet visible in this session."
        Write-Warn "Please open a NEW terminal window and re-run this script."
        exit 1
    }
}
Write-Ok "Git $((git --version) -replace 'git version ','')"

# ── Process Check ─────────────────────────────────────────────────────────────
Write-Section "Process Check"
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

# Check Task Scheduler task first
$taskName = "HushClaw"
$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
$runningPid   = Get-HushClawPid

if ($Mode -eq "start") {
    if (($existingTask -and $existingTask.State -eq "Running") -or $runningPid) {
        Write-Warn "HushClaw is already running — nothing to do."
        exit 0
    }
} elseif ($existingTask -and $existingTask.State -eq "Running") {
    Write-Info "Stopping running scheduled task for $Mode…"
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Write-Ok "Scheduled task stopped"
} elseif ($runningPid) {
    Write-Info "Stopping running server (PID $runningPid) for $Mode…"
    Stop-HushClaw $runningPid
} else {
    Write-Ok "No running HushClaw instance detected"
}

# ── Install / Update ──────────────────────────────────────────────────────────
if ($Mode -eq "start") {
    if (-not (Test-Path "$InstallDir")) {
        Die "HushClaw not found at $InstallDir. Run without -StartOnly to install first."
    }
} else {
    Write-Section "Installing HushClaw → $InstallDir"

    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

    $RepoDir = "$InstallDir\repo"
    $LastBackupPath = ""
    if (Test-Path "$RepoDir\.git") {
        if (Test-RepoDirty $RepoDir) {
            Write-Warn "Installation repository has local modifications"
            Get-RepoDirtyFiles $RepoDir | ForEach-Object { Write-DetailWarn $_ }
            if ($BackupBeforeOverwrite -or $OverwriteInstall) {
                $LastBackupPath = Get-BackupRootDir
                Write-Info "Backing up user data before overwriting code…"
                Backup-UserData $LastBackupPath
                Write-Info "Saving install repo overlay snapshot…"
                Backup-RepoOverlay $RepoDir (Join-Path $LastBackupPath "repo-overlay")
                Write-Ok "Backup completed: $LastBackupPath"
            } else {
                Write-Info "Proceeding to overwrite installation code. Runtime data lives outside the install repository."
                Write-Info "Use -BackupBeforeOverwrite to save a pre-overwrite snapshot."
            }
        }
        Write-Info "Updating repository…"
        git -C $RepoDir fetch --quiet origin
        git -C $RepoDir reset --hard origin/main --quiet 2>$null
        if ($LASTEXITCODE -ne 0) {
            git -C $RepoDir reset --hard origin/master --quiet
        }
        Write-Ok "Repository updated"
    } else {
        Write-Info "Cloning repository…"
        git clone --depth=1 $RepoUrl $RepoDir --quiet
        Write-Ok "Repository cloned"
    }

    # Virtual environment
    $VenvDir = "$InstallDir\venv"
    if (-not (Test-Path $VenvDir)) {
        Write-Info "Creating virtual environment…"
        & $PythonExe -m venv $VenvDir
        Write-Ok "Virtual environment created"
    }

    $PipExe = "$VenvDir\Scripts\pip.exe"
    $GcExe  = "$VenvDir\Scripts\hushclaw.exe"

    Write-Info "Installing/upgrading packages…"
    & $PipExe install --upgrade pip --quiet
    # Use Push-Location so pip sees the local extras with no path quoting issues
    Push-Location $RepoDir
    & $PipExe install -e ".[server,calendar,encryption]" --quiet
    Pop-Location
    Write-Ok "HushClaw installed"
    Write-InstallState $LastBackupPath "ok"

    # ── Add hushclaw to user PATH ────────────────────────────────────────────
    Write-Section "Setting Up PATH"

    $ScriptsDir = "$VenvDir\Scripts"
    $currentUserPath = [Environment]::GetEnvironmentVariable("PATH", "User")

    if ($currentUserPath -split ";" | Where-Object { $_ -eq $ScriptsDir }) {
        Write-Ok "'hushclaw' is already in user PATH"
    } else {
        $newPath = if ($currentUserPath) { "$currentUserPath;$ScriptsDir" } else { $ScriptsDir }
        [Environment]::SetEnvironmentVariable("PATH", $newPath, "User")
        # Also update current session
        $env:PATH = "$env:PATH;$ScriptsDir"
        Write-Ok "Added to user PATH: $ScriptsDir"
        Write-Warn "PATH updated — open a NEW terminal window to use 'hushclaw' command globally."
    }

    # Create a batch launcher
    $LauncherBat = "$InstallDir\hushclaw-start.bat"
    @"
@echo off
set HUSHCLAW_PORT=$Port
set HUSHCLAW_HOST=$BindHost
"$GcExe" serve --host %HUSHCLAW_HOST% --port %HUSHCLAW_PORT% --distro $Distro %*
"@ | Set-Content $LauncherBat -Encoding ASCII
    Write-Ok "Launcher created: $LauncherBat"
}

$GcExe = "$InstallDir\venv\Scripts\hushclaw.exe"
if (-not (Test-Path $GcExe)) {
    Die "hushclaw.exe not found at $GcExe. Installation may have failed."
}

# ── Migrate memory-stored skills → SKILL.md files (one-time) ─────────────────
# Older hushclaw stored agent-created skills as _skill-tagged notes in SQLite.
# This one-time migration exports qualifying skills to disk as SKILL.md files.
# Quality gate: body >= 100 chars; preserves existing files; idempotent.
if ($Mode -ne "start") {
    $MigrateDbPath              = Join-Path $WinAppData      "hushclaw\memory.db"
    $MigrateCfgPath             = Join-Path $WinAppData      "hushclaw\hushclaw.toml"
    $MigrateDefaultSkillDir     = Join-Path $WinLocalAppData "hushclaw\user-skills"

    if (Test-Path $MigrateDbPath) {
        Write-Section "Migrating Memory Skills -> Files"
        $migratePy = @'
import json, re, sqlite3, sys, time
from pathlib import Path

db_path     = Path(sys.argv[1])
config_file = Path(sys.argv[2])
default_dir = Path(sys.argv[3]).expanduser()

# Resolve target skill dir: only user_skill_dir (never skill_dir — that's
# the bundled dir). Fall back to default data-dir-based path.
target_dir = default_dir
try:
    import tomllib
    data = tomllib.loads(config_file.read_text(encoding="utf-8"))
    tools = data.get("tools", {}) if isinstance(data, dict) else {}
    v = tools.get("user_skill_dir", "")
    if isinstance(v, str) and v.strip():
        target_dir = Path(v.strip()).expanduser()
except Exception:
    pass

# Idempotent: skip if already migrated
marker = target_dir / ".memory-skill-migration.json"
if marker.exists():
    print("info|Already migrated -- skipping")
    raise SystemExit(0)

if not db_path.exists():
    print("info|No memory.db -- nothing to migrate")
    raise SystemExit(0)

# notes table: note_id (PK), title, path (markdown file on disk), tags
# body content lives in the markdown file, not in the DB column
try:
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT note_id, title, path FROM notes WHERE tags LIKE '%_skill%'"
    ).fetchall()
    conn.close()
except Exception as exc:
    print(f"warn|DB read error: {exc} (skipping migration)")
    raise SystemExit(0)

def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower().strip())
    return s.strip("-")[:64]

def read_body(md_path: str) -> str:
    try:
        text = Path(md_path).read_text(encoding="utf-8").strip()
    except Exception:
        return ""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2].strip()
    return text

MIN_BODY = 100  # skip stubs shorter than this
migrated, skipped = [], []

for note_id, title, md_path in rows:
    title = (title or "").strip()
    body  = read_body(md_path or "")
    if not title:
        skipped.append({"id": note_id, "reason": "no_title"})
        continue
    if len(body) < MIN_BODY:
        skipped.append({"id": note_id, "title": title, "reason": "body_too_short"})
        continue
    slug = slugify(title)
    if not slug:
        skipped.append({"id": note_id, "title": title, "reason": "bad_slug"})
        continue
    skill_path = target_dir / slug / "SKILL.md"
    if skill_path.exists():
        skipped.append({"id": note_id, "title": title, "reason": "already_exists"})
        continue
    try:
        skill_path.parent.mkdir(parents=True, exist_ok=True)
        safe_title = title.replace('"', "'")
        skill_path.write_text(
            f'---\nname: {safe_title}\ndescription: Migrated from memory\n'
            f'author: user\nversion: "1.0.0"\n---\n\n{body}\n',
            encoding="utf-8",
        )
        migrated.append({"id": note_id, "title": title, "slug": slug})
        print(f"ok|{title} -> {slug}/SKILL.md")
    except Exception as exc:
        skipped.append({"id": note_id, "title": title, "reason": f"write_error: {exc})"})

target_dir.mkdir(parents=True, exist_ok=True)
marker.write_text(
    json.dumps(
        {"migrated_at": int(time.time()), "migrated": migrated, "skipped": skipped},
        indent=2, ensure_ascii=False,
    ),
    encoding="utf-8",
)
if migrated:
    print(f"summary|{len(migrated)} skill(s) migrated, {len(skipped)} skipped")
else:
    print(f"summary|No qualifying skills found ({len(skipped)} checked)")
'@
        $migrateOutput = Invoke-PythonScript -Code $migratePy -Arguments @("$MigrateDbPath", "$MigrateCfgPath", "$MigrateDefaultSkillDir") -MergeStderr
        $migrateOutput | ForEach-Object { Write-StructuredDetail "$_" }
        Write-Ok "Skill migration complete"
    }
}

# Encrypt only after the legacy plaintext skill migration has read memory.db.
# The command is idempotent and atomically migrates both the live DB and its
# schema-migration recovery snapshots.
if ($Mode -ne "start") {
    Write-Section "Encrypting Local Database"
    & $GcExe database encrypt
    if ($LASTEXITCODE -ne 0) {
        Die "Database encryption failed. Existing data was left recoverable; run 'hushclaw database status' for details."
    }
    Write-Ok "Local database and migration snapshots are encrypted"
}

# ── Sync bundled skill packages → skill_dir ───────────────────────────────────
if ($Mode -ne "start") {
    Write-Section "Syncing Bundled Skills"

    $RepoSkills = "$InstallDir\repo\skill-packages"
    $LocalAppData = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $HOME "AppData\Local" }
    $AppData = if ($env:APPDATA) { $env:APPDATA } else { Join-Path $HOME "AppData\Roaming" }
    $DefaultSkillDir = Join-Path $LocalAppData "hushclaw\skills"
    $ConfigFile = Join-Path $AppData "hushclaw\hushclaw.toml"
    $resolveSkillDirPy = @'
import sys
from pathlib import Path
try:
    import tomllib
except Exception:
    print("")
    raise SystemExit(0)

cfg = Path(sys.argv[1]).expanduser()
if not cfg.exists():
    print("")
    raise SystemExit(0)
try:
    data = tomllib.loads(cfg.read_text(encoding="utf-8"))
except Exception:
    print("")
    raise SystemExit(0)
tools = data.get("tools", {}) if isinstance(data, dict) else {}
skill_dir = tools.get("skill_dir", "") if isinstance(tools, dict) else ""
if isinstance(skill_dir, str) and skill_dir.strip():
    print(str(Path(skill_dir.strip()).expanduser()))
else:
    print("")
'@
    $ConfiguredSkillDir = (Invoke-PythonScript -Code $resolveSkillDirPy -Arguments @("$ConfigFile") | Select-Object -First 1).Trim()
    $SkillDir = if ($ConfiguredSkillDir) { $ConfiguredSkillDir } else { $DefaultSkillDir }
    if ($ConfiguredSkillDir) {
        Write-Info "Bundled skill target dir (configured): $SkillDir"
    } else {
        Write-Info "Bundled skill target dir (default): $SkillDir"
    }

    if (Test-Path $RepoSkills) {
        New-Item -ItemType Directory -Force -Path $SkillDir | Out-Null
        Write-Info "Bundled skill policy: $SkillPolicy"
        $syncPy = @'
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

repo_skills = Path(sys.argv[1]).expanduser()
skill_dir = Path(sys.argv[2]).expanduser()
policy = (sys.argv[3] or "preserve_skip").strip().lower()
if policy not in {"preserve_skip", "force_official"}:
    policy = "preserve_skip"

state_path = skill_dir / ".bundled-skill-state.json"
backup_root = skill_dir / ".bundled-skill-backups"
schema_version = 1

def load_state() -> dict:
    if not state_path.exists():
        return {"schema_version": schema_version, "updated_at": int(time.time()), "skills": {}}
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("state root must be object")
        data.setdefault("schema_version", schema_version)
        data.setdefault("skills", {})
        if not isinstance(data["skills"], dict):
            data["skills"] = {}
        return data
    except Exception:
        return {"schema_version": schema_version, "updated_at": int(time.time()), "skills": {}}

def parse_version(skill_md: Path) -> str:
    if not skill_md.exists():
        return ""
    try:
        for line in skill_md.read_text(encoding="utf-8", errors="ignore").splitlines():
            s = line.strip()
            if s.startswith("version:"):
                return s.split(":", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return ""

def hash_skill_dir(root: Path) -> str:
    files = []
    for rel in ("SKILL.md", "requirements.txt", "README.md"):
        p = root / rel
        if p.is_file():
            files.append(p)
    tools_dir = root / "tools"
    if tools_dir.is_dir():
        files.extend(sorted(p for p in tools_dir.rglob("*.py") if p.is_file()))
    files = sorted(files, key=lambda p: str(p.relative_to(root)).replace("\\", "/"))

    h = hashlib.sha256()
    for p in files:
        rel = str(p.relative_to(root)).replace("\\", "/")
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    return f"sha256:{h.hexdigest()}"

def replace_dir(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)

def unique_backup_dir(name: str) -> Path:
    backup_root.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    base = backup_root / f"{name}-{ts}"
    if not base.exists():
        return base
    i = 1
    while True:
        cand = backup_root / f"{name}-{ts}-{i}"
        if not cand.exists():
            return cand
        i += 1

state = load_state()
skills_state = state.get("skills", {})
counts = {
    "installed": 0,
    "updated": 0,
    "forced_updated": 0,
    "skipped_dirty": 0,
    "skipped_error": 0,
}

for pkg in sorted(repo_skills.iterdir(), key=lambda p: p.name.lower()):
    if not pkg.is_dir():
        continue
    name = pkg.name
    local_dir = skill_dir / name
    official_hash = hash_skill_dir(pkg)
    official_version = parse_version(pkg / "SKILL.md")
    prev = skills_state.get(name, {}) if isinstance(skills_state.get(name, {}), dict) else {}
    prev_last = str(prev.get("last_deployed_hash", "") or "")

    try:
        if not local_dir.exists():
            replace_dir(pkg, local_dir)
            counts["installed"] += 1
            print(f"[installed] /{name} version={official_version or '-'}")
            skills_state[name] = {
                "source": "bundled",
                "policy": policy,
                "official_version": official_version,
                "official_hash": official_hash,
                "last_deployed_hash": official_hash,
                "local_hash": official_hash,
                "dirty": False,
            }
            continue

        local_hash = hash_skill_dir(local_dir)
        dirty = (not prev_last) or (local_hash != prev_last)

        if dirty and policy != "force_official":
            counts["skipped_dirty"] += 1
            reason = "no_state" if not prev_last else "local_modified"
            print(f"[skipped_dirty] /{name} reason={reason}")
            skills_state[name] = {
                "source": "bundled",
                "policy": policy,
                "official_version": official_version,
                "official_hash": official_hash,
                "last_deployed_hash": prev_last,
                "local_hash": local_hash,
                "dirty": True,
            }
            continue

        if dirty and policy == "force_official":
            backup_dir = unique_backup_dir(name)
            shutil.copytree(local_dir, backup_dir)
            replace_dir(pkg, local_dir)
            counts["forced_updated"] += 1
            print(f"[forced_updated] /{name} backup={backup_dir}")
        else:
            replace_dir(pkg, local_dir)
            counts["updated"] += 1
            print(f"[updated] /{name} version={official_version or '-'}")

        skills_state[name] = {
            "source": "bundled",
            "policy": policy,
            "official_version": official_version,
            "official_hash": official_hash,
            "last_deployed_hash": official_hash,
            "local_hash": official_hash,
            "dirty": False,
        }
    except Exception as exc:
        counts["skipped_error"] += 1
        print(f"[skipped_error] /{name} error={exc}")

state["schema_version"] = schema_version
state["updated_at"] = int(time.time())
state["skills"] = skills_state
state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
print("summary " + " ".join(f"{k}={v}" for k, v in counts.items()))
'@
        $syncOutput = Invoke-PythonScript -Code $syncPy -Arguments @("$RepoSkills", "$SkillDir", "$SkillPolicy") -MergeStderr
        if ($syncOutput) {
            $syncOutput | ForEach-Object { Write-SkillSyncDetail "$_" }
        }
        if ($LASTEXITCODE -ne 0) {
            Write-Warn "Bundled skill sync encountered an unexpected failure"
        } else {
            Write-Ok "Bundled skill sync completed → $SkillDir"
        }
    }
}

# ── Ollama (optional — only when embed_provider = ollama in config) ───────────
$AppDataRoaming = if ($env:APPDATA) { $env:APPDATA } else { Join-Path $HOME "AppData\Roaming" }
$HushclawCfg    = Join-Path $AppDataRoaming "hushclaw\hushclaw.toml"

$readEmbedPy = @'
import sys
from pathlib import Path
try:
    import tomllib
except Exception:
    print("local"); print(""); raise SystemExit(0)
cfg = Path(sys.argv[1])
if not cfg.exists():
    print("local"); print(""); raise SystemExit(0)
try:
    data = tomllib.loads(cfg.read_text(encoding="utf-8"))
except Exception:
    print("local"); print(""); raise SystemExit(0)
mem = data.get("memory", {}) if isinstance(data, dict) else {}
print(str(mem.get("embed_provider", "local")).strip())
print(str(mem.get("embed_model", "")).strip())
'@

$embedOut = & $PythonExe -c $readEmbedPy $HushclawCfg 2>$null
$EmbedProvider = if ($embedOut -and $embedOut[0]) { $embedOut[0].Trim() } else { "local" }
$EmbedModel    = if ($embedOut -and $embedOut[1]) { $embedOut[1].Trim() } else { "" }

if ($EmbedProvider -eq "ollama") {
    Write-Section "Ollama (embed_provider = ollama)"

    $ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
    if (-not $ollamaCmd) {
        Write-Info "Ollama not found — downloading installer…"
        $ollamaInstaller = Join-Path $env:TEMP "ollama-setup.exe"
        try {
            Invoke-WebRequest -Uri "https://ollama.com/download/OllamaSetup.exe" `
                -OutFile $ollamaInstaller -UseBasicParsing -ErrorAction Stop
            Write-Info "Running Ollama installer (may show a UAC prompt)…"
            Start-Process -FilePath $ollamaInstaller -ArgumentList "/SILENT" -Wait
            # Refresh PATH so ollama is found in current session
            $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" +
                        [System.Environment]::GetEnvironmentVariable("PATH", "User")
            $ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
            if ($ollamaCmd) {
                Write-Ok "Ollama installed"
            } else {
                Write-Warn "Ollama installer ran but 'ollama' not found in PATH — open a new terminal after setup."
            }
        } catch {
            Write-Warn "Failed to download Ollama installer: $_"
            Write-Warn "Install manually from https://ollama.com/download then re-run this script."
        }
    } else {
        Write-Ok "Ollama already installed"
    }

    # Ollama on Windows runs as a user-mode background process (tray app) that
    # auto-starts at login via the installer's registry entry — no manual service
    # registration needed.  Just ensure it's running now.
    $ollamaRunning = Get-Process -Name "ollama" -ErrorAction SilentlyContinue
    if (-not $ollamaRunning) {
        Write-Info "Starting Ollama…"
        Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Hidden -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
        Write-Ok "Ollama started"
    } else {
        Write-Ok "Ollama already running"
    }

    # Pull the configured embedding model
    $modelToPull = if ($EmbedModel) { $EmbedModel } else { "nomic-embed-text" }
    $modelList = & ollama list 2>$null | Out-String
    if ($modelList -match [regex]::Escape($modelToPull)) {
        Write-Ok "Ollama model '$modelToPull' already available"
    } else {
        Write-Info "Pulling Ollama model '$modelToPull' (may take a few minutes)…"
        & ollama pull $modelToPull 2>&1 | ForEach-Object {
            if ($_ -ne $null -and "$_".Trim()) {
                Write-Detail "$_"
            }
        }
        if ($LASTEXITCODE -eq 0) {
            Write-Ok "Model '$modelToPull' ready"
        } else {
            Write-Warn "Failed to pull model '$modelToPull'. Retry manually: ollama pull $modelToPull"
        }
    }
}

# ── Firewall: open port ───────────────────────────────────────────────────────
Write-Section "Firewall"

$RuleName = "HushClaw-Port-$Port"
$existingRule = Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
if ($existingRule) {
    Write-Ok "Windows Firewall: rule '$RuleName' already exists (port $Port)"
} else {
    Write-Info "Adding Windows Firewall inbound rule for port $Port…"
    try {
        New-NetFirewallRule `
            -DisplayName $RuleName `
            -Direction Inbound `
            -Protocol TCP `
            -LocalPort $Port `
            -Action Allow `
            -ErrorAction Stop | Out-Null
        Write-Ok "Windows Firewall: port $Port opened (rule: $RuleName)"
    } catch {
        Write-Warn "Could not add firewall rule (may need to run as Administrator)."
        Write-Info "To open the port manually, run as Admin:"
        Write-Info "  New-NetFirewallRule -DisplayName '$RuleName' -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow"
    }
}

# ── Network info ──────────────────────────────────────────────────────────────
Write-Section "Network Addresses"

# Local IP (prefer non-loopback IPv4)
$LocalIP = ""
try {
    $ips = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
           Where-Object { $_.IPAddress -notmatch '^127\.' -and $_.IPAddress -notmatch '^169\.254\.' } |
           Sort-Object -Property PrefixLength |
           Select-Object -First 1 -ExpandProperty IPAddress
    if ($ips) { $LocalIP = $ips }
} catch {
    try {
        $LocalIP = ([System.Net.Dns]::GetHostAddresses([System.Net.Dns]::GetHostName()) |
                    Where-Object { $_.AddressFamily -eq "InterNetwork" -and
                                   $_.ToString() -notmatch '^127\.' } |
                    Select-Object -First 1).ToString()
    } catch {}
}

# Public IP (best-effort)
$PublicIP = ""
try {
    $PublicIP = (Invoke-WebRequest -Uri "https://api.ipify.org" -UseBasicParsing -TimeoutSec 4).Content.Trim()
} catch {}

Write-Host ""
Write-Host "  ●  Local (this machine)" -ForegroundColor Green
Write-Host "     http://127.0.0.1:$Port" -ForegroundColor Cyan

if ($LocalIP) {
    Write-Host ""
    Write-Host "  ●  LAN (same network)" -ForegroundColor Green
    Write-Host "     http://${LocalIP}:${Port}" -ForegroundColor Cyan
}

if ($PublicIP) {
    Write-Host ""
    Write-Host "  ●  Internet (public IP — only if port $Port is open in firewall)" -ForegroundColor Yellow
    Write-Host "     http://${PublicIP}:${Port}" -ForegroundColor Cyan
}

Write-Host ""
Write-Warn "Tip: On first launch the browser opens the Settings modal to configure your API key."
Write-Warn "     Use the Settings button (top-right) to adjust Model, Channels, System, or Memory settings."
Write-Host ""

# ── Open browser ──────────────────────────────────────────────────────────────
function Open-Browser($url) {
    if ($NoBrowser) { return }
    $hasDisplay = [System.Environment]::UserInteractive
    if (-not $hasDisplay) {
        Write-Warn "Non-interactive session detected — browser auto-open skipped."
        return
    }
    Start-Job -ScriptBlock {
        param($u)
        Start-Sleep -Seconds 2
        Start-Process $u
    } -ArgumentList $url | Out-Null
}

# ── Start server ──────────────────────────────────────────────────────────────
Write-Section "Starting HushClaw Server"
$WebPath = Get-WebPathForDistro $Distro
$LocalWebUrl = "http://127.0.0.1:${Port}${WebPath}"
Write-Info "Personal WebUI on http://${BindHost}:${Port}${WebPath}"
Write-Host ""

if ($Foreground) {
    Write-Warn "Running in foreground mode (Ctrl-C to stop)"
    Open-Browser $LocalWebUrl
    & $GcExe serve --host $BindHost --port $Port --distro $Distro
} else {
    Start-HushClawBackground $GcExe
    Open-Browser $LocalWebUrl
    Write-Ok "Installation complete. HushClaw is running in the background."
}
