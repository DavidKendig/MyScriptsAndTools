@echo off
setlocal EnableExtensions
title AutoTranslate - Stop

REM Closes only Python processes whose command line points at this app's main.py.
echo Looking for running AutoTranslate processes...

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$here = '%~dp0' -replace '\\$',''; " ^
  "$procs = Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" | " ^
  "  Where-Object { $_.CommandLine -and $_.CommandLine -like ('*' + $here + '*main.py*') }; " ^
  "if (-not $procs) { Write-Host 'Nothing to stop.'; exit 0 }; " ^
  "foreach ($p in $procs) { Write-Host ('Stopping PID ' + $p.ProcessId); Stop-Process -Id $p.ProcessId -Force }; " ^
  "Write-Host 'Stopped.'"

echo.
pause
