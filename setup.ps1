<#
WARLOGS setup wizard.

Double-click Setup.bat, or run:
  powershell -ExecutionPolicy Bypass -File setup.ps1

Non-interactive (for scripts and AI agents):
  powershell -ExecutionPolicy Bypass -File setup.ps1 -Yes [-SteamId 7656...] [-PlayerName "MyName"] [-NoStartup]

Remove autostart and stop the tracker:
  powershell -ExecutionPolicy Bypass -File setup.ps1 -Uninstall

It installs nothing into the game and needs no admin rights. It writes config.json,
remembers which pythonw.exe to use, adds a Startup shortcut, starts the tracker and
opens the dashboard.
#>
param(
    [string]$SteamId = "",
    [string]$PlayerName = "",
    [switch]$Yes,
    [switch]$NoStartup,
    [switch]$NoOpen,
    [switch]$Uninstall
)
$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Shortcut = Join-Path ([Environment]::GetFolderPath("Startup")) "WARLOGS.lnk"
$MenuShortcut = Join-Path ([Environment]::GetFolderPath("Programs")) "WARLOGS.lnk"
# shortcuts from before the WARLOGS rename
$OldShortcuts = @((Join-Path ([Environment]::GetFolderPath("Startup")) "WARDOGS Match Tracker.lnk"),
                  (Join-Path ([Environment]::GetFolderPath("Programs")) "WARDOGS Dashboard.lnk"))
$Port = 8790

function Say($text, $color = "Gray") { Write-Host $text -ForegroundColor $color }
function Step($n, $text) { Write-Host ""; Write-Host "[$n] $text" -ForegroundColor Yellow }
function Ask($question, $default) {
    if ($Yes) { return $default }
    $suffix = if ($default) { " [$default]" } else { "" }
    $a = Read-Host "$question$suffix"
    if ([string]::IsNullOrWhiteSpace($a)) { return $default } else { return $a.Trim() }
}
function Confirm($question) {
    if ($Yes) { return $true }
    $a = Read-Host "$question [Y/n]"
    return -not ($a -match '^\s*n')
}
function Stop-Tracker {
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
        Where-Object { $_.CommandLine -like "*tracker.pyw*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

if ($Uninstall) {
    Stop-Tracker
    if (Test-Path $Shortcut) { Remove-Item $Shortcut }
    if (Test-Path $MenuShortcut) { Remove-Item $MenuShortcut }
    $OldShortcuts | Where-Object { Test-Path $_ } | Remove-Item
    Say "Tracker stopped and removed from Startup. Your match log is still in $Here\data." Green
    exit 0
}

Say ""
Say "  WARLOGS setup" Yellow
Say "  Logs XP and cash per match from your screen. It never touches the game process."
Say ""

# 1. Python ---------------------------------------------------------------------------
Step 1 "Looking for Python 3.10 or newer"
function Find-Python {
    $cands = @()
    if (Get-Command py -ErrorAction SilentlyContinue) { $cands += ,@("py", "-3") }
    if (Get-Command python -ErrorAction SilentlyContinue) { $cands += ,@("python") }
    foreach ($c in $cands) {
        try {
            $exe = $c[0]; $rest = @($c | Select-Object -Skip 1)
            $out = & $exe @rest -c "import sys; print(sys.version_info[0], sys.version_info[1], sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $out) {
                $p = "$out".Trim().Split(" ", 3)
                # the Microsoft Store stub prints nothing useful; real installs report a path
                if ([int]$p[0] -eq 3 -and [int]$p[1] -ge 10 -and (Test-Path $p[2])) { return $p[2] }
            }
        } catch { }
    }
    foreach ($v in "313", "312", "311", "310") {
        $p = Join-Path $env:LOCALAPPDATA "Programs\Python\Python$v\python.exe"
        if (Test-Path $p) { return $p }
    }
    return $null
}
$py = Find-Python
if (-not $py) {
    Say "Python isn't installed (or is older than 3.10)." Red
    if ((Get-Command winget -ErrorAction SilentlyContinue) -and (Confirm "Install Python 3.12 with winget now?")) {
        winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
        $py = Find-Python
    }
    if (-not $py) {
        Say "Install Python 3.12 from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'), then run setup again." Red
        exit 1
    }
}
$pyw = Join-Path (Split-Path $py) "pythonw.exe"
if (-not (Test-Path $pyw)) { $pyw = $py }
Say "Using $py" Green
Set-Content -Path (Join-Path $Here ".python-path") -Value $pyw -Encoding ASCII

# 2. Steam account ----------------------------------------------------------------------
Step 2 "Finding your Steam account"
$accounts = @()
$steamRoot = $null
try { $steamRoot = (Get-ItemProperty "HKCU:\Software\Valve\Steam" -ErrorAction Stop).SteamPath } catch { }
if (-not $steamRoot) { $steamRoot = "${env:ProgramFiles(x86)}\Steam" }
$steamRoot = $steamRoot -replace "/", "\"
$vdf = Join-Path $steamRoot "config\loginusers.vdf"
if (Test-Path $vdf) {
    $text = Get-Content $vdf -Raw
    foreach ($m in [regex]::Matches($text, '"(7656\d{13})"\s*\{([^}]*)\}')) {
        $name = if ($m.Groups[2].Value -match '"PersonaName"\s*"([^"]*)"') { $Matches[1] } else { "?" }
        $recent = $m.Groups[2].Value -match '"MostRecent"\s*"1"'
        $accounts += [pscustomobject]@{ Id = $m.Groups[1].Value; Name = $name; Recent = $recent }
    }
}
if (-not $SteamId) {
    $pick = $accounts | Sort-Object { -[int]$_.Recent } | Select-Object -First 1
    if ($accounts.Count -gt 1 -and -not $Yes) {
        Say "Steam accounts on this PC:"
        for ($i = 0; $i -lt $accounts.Count; $i++) { Say ("  {0}. {1} ({2})" -f ($i + 1), $accounts[$i].Name, $accounts[$i].Id) }
        $n = Ask "Which one plays WARDOGS? Number" "1"
        $pick = $accounts[[int]$n - 1]
    }
    $SteamId = Ask "Your SteamID64 (17 digits, starts with 7656)" $(if ($pick) { $pick.Id } else { "" })
    if (-not $PlayerName -and $pick) { $PlayerName = $pick.Name }
}
if ($SteamId -notmatch '^7656\d{13}$') {
    Say "That doesn't look like a SteamID64. Find yours at https://steamid.io and run setup again." Red
    exit 1
}
Say "Steam ID $SteamId" Green

# 3. In-game name -----------------------------------------------------------------------
Step 3 "Your in-game name"
Say "Used to spot your kills, deaths and scoreboard row. Type it exactly as WARDOGS shows it."
$PlayerName = Ask "In-game name" $PlayerName

# 4. config.json ------------------------------------------------------------------------
Step 4 "Writing config.json"
$cfgPath = Join-Path $Here "config.json"
$cfg = [ordered]@{}
if (Test-Path $cfgPath) {
    (Get-Content $cfgPath -Raw | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $cfg[$_.Name] = $_.Value }
}
$cfg["steam_id"] = $SteamId
$cfg["player_name"] = $PlayerName
if (-not $cfg.Contains("dashboard_port")) { $cfg["dashboard_port"] = $Port }
$Port = $cfg["dashboard_port"]
$json = $cfg | ConvertTo-Json
[IO.File]::WriteAllText($cfgPath, $json, (New-Object Text.UTF8Encoding $false))
Say "Saved $cfgPath" Green

# 5. Quick self-test --------------------------------------------------------------------
Step 5 "Checking the tracker runs on this PC"
# unittest reports on stderr, which Windows PowerShell treats as an error; run it through cmd
$res = cmd /c "cd /d `"$Here`" && `"$py`" -m unittest discover -s tests -t . 2>&1"
if ($LASTEXITCODE -eq 0) { Say "  self-test passed ($(($res | Select-String 'Ran \d+ tests').Line))" Green }
else { Say "  self-test failed; the tracker may still work. Last lines:" Red; $res | Select-Object -Last 5 | ForEach-Object { Say "    $_" } }

# 6. Startup + start --------------------------------------------------------------------
Step 6 "Starting the tracker"
$vbs = Join-Path $Here "Start Tracker.vbs"
if (-not $NoStartup -and (Confirm "Start the tracker automatically when you log in to Windows?")) {
    $sh = New-Object -ComObject WScript.Shell
    $lnk = $sh.CreateShortcut($Shortcut)
    $lnk.TargetPath = "$env:WINDIR\System32\wscript.exe"
    $lnk.Arguments = '"' + $vbs + '"'
    $lnk.WorkingDirectory = $Here
    $lnk.Description = "WARLOGS (runs in the background, no window)"
    $lnk.Save()
    Say "Added to Startup." Green
}
Stop-Tracker
Start-Process wscript.exe -ArgumentList ('"' + $vbs + '"')
$ok = $false
for ($i = 0; $i -lt 20 -and -not $ok; $i++) {
    Start-Sleep -Milliseconds 500
    try { $null = Invoke-WebRequest "http://127.0.0.1:$Port/api/version" -UseBasicParsing -TimeoutSec 2; $ok = $true } catch { }
}
if ($ok) { Say "Tracker running. Dashboard: http://127.0.0.1:$Port" Green }
else { Say "The dashboard didn't answer yet. Check $Here\data\tracker.log." Red }

# the dashboard opens in its own window (Edge or Chrome app mode) from the Start menu
$open = Join-Path $Here "Open Dashboard.vbs"
$sh = New-Object -ComObject WScript.Shell
$lnk = $sh.CreateShortcut($MenuShortcut)
$lnk.TargetPath = "$env:WINDIR\System32\wscript.exe"
$lnk.Arguments = '"' + $open + '"'
$lnk.WorkingDirectory = $Here
$lnk.Description = "WARLOGS dashboard"
$lnk.Save()
$oldUrl = Join-Path $Here "WARDOGS Dashboard.url"
if (Test-Path $oldUrl) { Remove-Item $oldUrl }
$OldShortcuts | Where-Object { Test-Path $_ } | Remove-Item
Say "Added WARLOGS to the Start menu." Green
if ($ok -and -not $NoOpen) { Start-Process wscript.exe -ArgumentList ('"' + $open + '"') }

Say ""
Say "Done. Two things to do once:" Yellow
Say "  1. In WARDOGS video settings use Borderless or Windowed fullscreen (exclusive fullscreen hides the game from screen capture)."
Say "  2. Open https://wardogstracker.gg/profile/$SteamId once and update your stats there; career totals and unlocks come from that site."
Say "Then just play. Matches log themselves; keep the dashboard on a second monitor."
