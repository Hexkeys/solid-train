@echo off
REM Build RemotePad-Agent.exe locally (needs Python 3.10+ installed).
cd /d "%~dp0"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --onefile --noconsole --name RemotePad-Agent ^
  --hidden-import pynput.keyboard._win32 --hidden-import pynput.mouse._win32 ^
  remotepad_agent.py
echo.
echo Done. Your exe is in agent\dist\RemotePad-Agent.exe
pause
