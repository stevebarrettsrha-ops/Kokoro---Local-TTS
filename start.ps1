# Kokoro Studio launcher for Windows (double-click start.bat, which runs this).
# Kokoro 0.9.4 requires Python 3.10, 3.11 or 3.12. This script makes the Python
# environment (.venv); the rest of the setup is bootstrap/launch.py, shared with Linux and macOS.
# 'Continue': Windows PowerShell 5.1 would otherwise treat any program writing to stderr (uv's progress) as a failure.
$ErrorActionPreference = 'Continue'
Set-Location -LiteralPath $PSScriptRoot

function Test-Truthy($v) { return @('1', 'true', 'yes', 'on') -contains "$v".Trim().ToLower() }
function Test-Command($name) { return [bool](Get-Command $name -ErrorAction SilentlyContinue) }
function Update-PathFromSystem {
  # Programs installed a moment ago (winget, uv) are on the saved PATH, not yet on this window's.
  $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
  $user = [Environment]::GetEnvironmentVariable('Path', 'User')
  $env:Path = "$env:USERPROFILE\.local\bin;$user;$machine;$env:Path"
}
function Ask($question) {
  # Yes unless the user answers n. No console to ask in -> no.
  if (-not [Environment]::UserInteractive -or [Console]::IsInputRedirected) { return $false }
  $answer = Read-Host "$question [Y/n]"
  return -not ("$answer" -match '^[nN]')
}

$Offline = Test-Truthy $env:OFFLINE
$env:OFFLINE = if ($Offline) { '1' } else { '0' }
$env:HF_HOME = Join-Path $PSScriptRoot 'data\huggingface'
New-Item -ItemType Directory -Force -Path $env:HF_HOME, 'outputs', 'data\logs' | Out-Null
$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"

# ---- ffmpeg: video, MP3, recordings. (Kokoro brings its own espeak-ng.) A "no" is remembered.
$declined = 'data\.declined-system-packages'
if (-not (Test-Command 'ffmpeg')) {
  $saidNo = (Test-Path $declined) -and ((Get-Content $declined -Raw).Trim() -eq 'ffmpeg')
  if (-not $Offline -and -not $saidNo -and (Test-Command 'winget')) {
    if (Ask 'Kokoro Studio needs ffmpeg (for video, MP3 and recordings). Install it with winget now?') {
      try {
        winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
      } catch { Write-Warning "Installing ffmpeg failed; continuing. $_" }
      Update-PathFromSystem
    } elseif ([Environment]::UserInteractive) {
      Set-Content -Path $declined -Value 'ffmpeg'
    }
  }
  if (-not (Test-Command 'ffmpeg')) {
    Write-Host 'NOTICE: ffmpeg is missing (needed for video, MP3 and recordings). Install it with: winget install -e --id Gyan.FFmpeg' -ForegroundColor Yellow
  }
}

# ---- uv fetches the right Python (3.12) and installs packages quickly; voice cloning needs it.
if (-not (Test-Command 'uv') -and -not $Offline) {
  if (Ask 'Install uv (the Python installer from astral.sh, into your user folder)?') {
    try {
      powershell -NoProfile -ExecutionPolicy ByPass -Command 'irm https://astral.sh/uv/install.ps1 | iex'
    } catch { Write-Warning "Installing uv failed; continuing. $_" }
    Update-PathFromSystem
  }
}

$py = '.venv\Scripts\python.exe'
function Test-CompatiblePython($exe, [string[]]$pre = @()) {
  try {
    & $exe @pre -c 'import sys; sys.exit(0 if (3, 10) <= sys.version_info[:2] < (3, 13) else 1)' 2>$null | Out-Null
    return $LASTEXITCODE -eq 0
  } catch { return $false }
}

# An environment made with an unsupported Python is kept as a backup, not deleted.
if ((Test-Path $py) -and -not (Test-CompatiblePython $py)) {
  $backup = ".venv-incompatible-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
  Write-Host "Existing .venv uses an unsupported Python; Kokoro requires Python 3.10-3.12. Keeping it as $backup"
  Rename-Item '.venv' $backup
}

if (-not (Test-Path $py)) {
  if ($Offline) { Write-Error 'A compatible Python environment is missing. Run start.bat while online first.'; exit 1 }
  Write-Host 'Creating Kokoro Python environment (3.12 preferred)...'
  if (Test-Command 'uv') {
    uv venv --python 3.12 .venv
    if ($LASTEXITCODE -ne 0) { Write-Error 'uv could not create the Python environment (see above).'; exit 1 }
  } else {
    # Without uv: the Python launcher (py) or python on PATH, if it is 3.10-3.12.
    $made = $false
    foreach ($v in '3.12', '3.11', '3.10') {
      if ((Test-Command 'py') -and (Test-CompatiblePython 'py' @("-$v"))) { & py "-$v" -m venv .venv; $made = $LASTEXITCODE -eq 0; break }
    }
    if (-not $made -and (Test-Command 'python') -and (Test-CompatiblePython 'python')) { & python -m venv .venv; $made = $LASTEXITCODE -eq 0 }
    if (-not $made) {
      if (Test-Path '.venv') { Remove-Item -Recurse -Force '.venv' }
      Write-Host @'
ERROR: No supported Python found (Kokoro needs Python 3.10-3.12).
Run start.bat again and answer yes to installing uv (it fetches Python 3.12 for the studio only),
or install uv yourself in PowerShell:
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
'@ -ForegroundColor Red
      exit 1
    }
  }
}

if (-not (Test-CompatiblePython $py)) { Write-Error 'The virtual environment must use Python 3.10, 3.11, or 3.12.'; exit 1 }

# ---- packages: (re)installed when missing or when requirements.txt changed (e.g. after an update).
& $py -c 'import importlib.util as u, sys; sys.exit(0 if all(u.find_spec(m) for m in ("flask", "kokoro", "misaki", "scipy", "soundfile", "huggingface_hub")) else 1)' 2>$null
$havePackages = $LASTEXITCODE -eq 0
$reqHash = (Get-FileHash 'requirements.txt' -Algorithm SHA256).Hash
$oldHash = if (Test-Path '.venv\.requirements') { (Get-Content '.venv\.requirements' -Raw).Trim() } else { '' }
if (-not $havePackages -or (-not $Offline -and $oldHash -ne $reqHash)) {
  if ($Offline) { Write-Error 'Python packages are missing; run start.bat online first.'; exit 1 }
  Write-Host 'Installing Kokoro and studio packages into .venv (first install downloads dependencies)...'
  if (Test-Command 'uv') {
    uv pip install --python $py -r requirements.txt
  } else {
    & $py -m pip install --upgrade pip
    & $py -m pip install -r requirements.txt
  }
  & $py -c 'import flask, kokoro, scipy, soundfile'
  if ($LASTEXITCODE -ne 0) { Write-Error 'Kokoro dependencies are still unavailable. See pip/uv errors above.'; exit 1 }
  Set-Content -Path '.venv\.requirements' -Value $reqHash
}

# The rest (spaCy model, Kokoro model and voices, the background voice-cloning install,
# opening the browser, the studio itself) is shared with Linux and macOS.
& $py -m bootstrap.launch
exit $LASTEXITCODE
