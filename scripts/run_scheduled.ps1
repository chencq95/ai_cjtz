[CmdletBinding()]
param(
    [ValidateSet("incremental", "full", "scheduler")]
    [string]$Mode = "incremental",

    [switch]$RunNow
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$DataDirectory = Join-Path $ProjectRoot "data"
$LogDirectory = Join-Path $ProjectRoot "logs"
$LockPath = Join-Path $DataDirectory "crawl.lock"
$LogPath = Join-Path $LogDirectory ("scheduled-{0:yyyyMMdd}.log" -f (Get-Date))

New-Item -ItemType Directory -Force -Path $DataDirectory, $LogDirectory | Out-Null

function Write-RunLog {
    param([Parameter(Mandatory = $true)][string]$Message)

    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-ddTHH:mm:ssK"), $Message
    $line | Tee-Object -FilePath $LogPath -Append
}

$lockStream = $null
$exitCode = 1

try {
    try {
        # OpenOrCreate plus FileShare.None is an OS-backed process lock. If a
        # previous process crashed, its handle is gone and the existing file is
        # safely reusable instead of becoming a permanent stale lock.
        $lockStream = [System.IO.File]::Open(
            $LockPath,
            [System.IO.FileMode]::OpenOrCreate,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::None
        )
    }
    catch [System.IO.IOException] {
        Write-RunLog "Another crawl owns data/crawl.lock; this invocation is skipped."
        exit 0
    }

    $lockStream.SetLength(0)
    $lockWriter = [System.IO.StreamWriter]::new(
        $lockStream,
        [System.Text.UTF8Encoding]::new($false),
        1024,
        $true
    )
    try {
        $lockWriter.WriteLine("pid={0}" -f $PID)
        $lockWriter.WriteLine("mode={0}" -f $Mode)
        $lockWriter.WriteLine("started_at={0}" -f (Get-Date -Format "o"))
        $lockWriter.Flush()
        $lockStream.Flush()
    }
    finally {
        $lockWriter.Dispose()
    }

    $venvScripts = Join-Path $ProjectRoot ".venv\Scripts"
    $dmpCandidates = @(
        (Join-Path $venvScripts "dmp.exe"),
        (Join-Path $venvScripts "data-market-probe.exe")
    )
    $dmpExecutable = $dmpCandidates |
        Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
        Select-Object -First 1

    switch ($Mode) {
        "incremental" { $dmpArguments = @("crawl", "--incremental") }
        "full" { $dmpArguments = @("crawl", "--full") }
        "scheduler" {
            $dmpArguments = @("schedule")
            if ($RunNow) {
                $dmpArguments += "--run-now"
            }
        }
    }

    if ($null -ne $dmpExecutable) {
        $executable = $dmpExecutable
        $arguments = $dmpArguments
    }
    else {
        $pythonExecutable = Join-Path $venvScripts "python.exe"
        if (-not (Test-Path -LiteralPath $pythonExecutable -PathType Leaf)) {
            throw "No dmp.exe or python.exe was found under $venvScripts. Create and install the project .venv first."
        }
        $executable = $pythonExecutable
        $arguments = @("-m", "data_market_probe.cli") + $dmpArguments
        $sourceDirectory = Join-Path $ProjectRoot "src"
        if ([string]::IsNullOrWhiteSpace($env:PYTHONPATH)) {
            $env:PYTHONPATH = $sourceDirectory
        }
        else {
            $env:PYTHONPATH = $sourceDirectory + [System.IO.Path]::PathSeparator + $env:PYTHONPATH
        }
    }

    Write-RunLog ("Starting mode={0}; executable={1}; project={2}" -f $Mode, $executable, $ProjectRoot)
    Push-Location -LiteralPath $ProjectRoot
    try {
        & $executable @arguments 2>&1 | Tee-Object -FilePath $LogPath -Append
        $exitCode = $LASTEXITCODE
        if ($null -eq $exitCode) {
            $exitCode = 0
        }
    }
    finally {
        Pop-Location
    }
    Write-RunLog ("Finished mode={0}; exit_code={1}" -f $Mode, $exitCode)
}
catch {
    $exitCode = 1
    Write-RunLog ("Failed mode={0}; error={1}" -f $Mode, $_.Exception.Message)
}
finally {
    if ($null -ne $lockStream) {
        $lockStream.Dispose()
        Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue
    }
}

exit $exitCode

