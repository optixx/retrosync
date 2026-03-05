@echo off
uv --version >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo uv is not installed. Please install uv and try again.
    exit /b 1
)

uv venv --python 3.12
call .venv\Scripts\activate

echo Installing required Python packages with uv...
uv sync --all-groups --python 3.12

echo Setup complete.
