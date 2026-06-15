@echo off
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in your system PATH.
    pause
    exit /b
)
if not exist "venv\Scripts\activate.bat" (
    echo Creating venv.
    python -m venv venv
    call venv\Scripts\activate.bat
    echo Installing requirements.txt.
    pip install -r requirements.txt --quiet
) else (
    call venv\Scripts\activate.bat
)
echo Compiling.
pyinstaller --noconfirm --onefile --windowed ^
    --log-level=WARN ^
    --icon "%~dp0pen.ico" ^
    --add-data "%~dp0pen.ico;." ^
    --name "Windows-Macro-Recorder" ^
    --distpath . ^
    --specpath build ^
    --hidden-import=pynput.keyboard._win32 ^
    --hidden-import=pynput.mouse._win32 ^
    "main.py"
echo Finished. Try opening the app!