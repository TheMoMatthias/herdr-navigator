# Restart herdr: stop the server, wait until it is gone, open herdr again in a new terminal window.
# Started by navigator.herdrctl.restart() as a process that belongs to no herdr pane, so it
# outlives the server it stops. -Terminal overrides the program that opens the window (tests).
param(
    [Parameter(Mandatory = $true)][string]$Herdr,
    [string]$Terminal = ""
)
$ErrorActionPreference = "Continue"
$log = Join-Path $env:TEMP "herdr-restart.log"
function Say($m) { "$((Get-Date).ToString('HH:mm:ss')) $m" | Add-Content -Path $log -Encoding UTF8 }
Say "restart: stopping the server"
& $Herdr server stop 2>&1 | ForEach-Object { Say "stop: $_" }
$gone = $false
for ($i = 0; $i -lt 60; $i++) {
    $s = (& $Herdr status server 2>&1 | Out-String)
    if ($s -notmatch 'status:\s*running') { $gone = $true; break }
    Start-Sleep -Milliseconds 500
}
if (-not $gone) { Say "the server did not stop within 30 s: not starting another"; exit 1 }
Start-Sleep -Seconds 1
if ($Terminal) {
    Start-Process -FilePath $Terminal -ArgumentList @($Herdr)
} elseif (Get-Command wt.exe -ErrorAction SilentlyContinue) {
    Start-Process -FilePath wt.exe -ArgumentList @("new-tab", "--title", "herdr", $Herdr)
} else {
    Start-Process -FilePath cmd.exe -ArgumentList @("/k", "`"$Herdr`"")
}
Say "restart: opened herdr again"
