# PowerShell script to set up Claude Code for Windows

# Common Git installation paths
$gitPaths = @(
    "$env:ProgramFiles\Git\bin\bash.exe",
    "${env:ProgramFiles(x86)}\Git\bin\bash.exe",
    "$env:LOCALAPPDATA\Programs\Git\bin\bash.exe"
)

# Find bash.exe
$bashPath = $null
foreach ($path in $gitPaths) {
    if (Test-Path $path) {
        $bashPath = $path
        break
    }
}

if ($bashPath) {
    Write-Host "Found Git Bash at: $bashPath" -ForegroundColor Green
    
    # Set user environment variable permanently
    [System.Environment]::SetEnvironmentVariable(
        "CLAUDE_CODE_GIT_BASH_PATH", 
        $bashPath, 
        [System.EnvironmentVariableTarget]::User
    )
    
    Write-Host "Environment variable CLAUDE_CODE_GIT_BASH_PATH has been set!" -ForegroundColor Green
    Write-Host "Please restart your terminal for changes to take effect." -ForegroundColor Yellow
} else {
    Write-Host "Git Bash not found. Please install it from: https://git-scm.com/downloads/win" -ForegroundColor Red
}
