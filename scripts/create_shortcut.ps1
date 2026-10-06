# Creates a "Video Transcriber" shortcut on the Desktop (and optionally the Start menu).
#
#   powershell -ExecutionPolicy Bypass -File scripts\create_shortcut.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\create_shortcut.ps1 -StartMenu
#
# The shortcut runs start.bat in a minimised console window. Closing that window stops the app.
param([switch]$StartMenu)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$startBat = Join-Path $repo "start.bat"
$icon = Join-Path $repo "transcriber\static\icon.ico"

$targets = @([Environment]::GetFolderPath("Desktop"))
if ($StartMenu) { $targets += Join-Path ([Environment]::GetFolderPath("Programs")) "" }

$shell = New-Object -ComObject WScript.Shell
foreach ($dir in $targets) {
    $path = Join-Path $dir "Video Transcriber.lnk"
    $lnk = $shell.CreateShortcut($path)
    # Launch through cmd.exe so the shortcut can also be pinned to the taskbar.
    $lnk.TargetPath = $env:ComSpec
    $lnk.Arguments = "/c `"$startBat`""
    $lnk.WorkingDirectory = $repo
    $lnk.IconLocation = "$icon,0"
    $lnk.WindowStyle = 7  # minimised
    $lnk.Description = "Turn videos into text transcripts, locally"
    $lnk.Save()
    Write-Host "Created $path"
}
