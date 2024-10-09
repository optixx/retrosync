@echo off
python --version >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo Python is not installed. Please install Python and try again.
    exit /b 1
)

python -m venv venv
call venv\Scripts\activate

echo Installing required Python packages...
pip install -r requirements.txt

echo Setup complete."
