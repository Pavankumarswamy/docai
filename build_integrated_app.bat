@echo off
echo ===================================================
echo   GGU AI Document Processing Agent - Integrated App Build
echo ===================================================

echo.
echo [1/4] Building React Frontend...
cd frontend-react
call npm install
call npm run build
cd ..

echo.
echo [2/4] Building Python Backend (PyInstaller)...
cd backend
# Assume .venv exists and has requirements installed
call .\.venv\Scripts\python.exe -m pip install pyinstaller
call .\.venv\Scripts\pyinstaller --noconfirm --onefile --console --name "backend" --add-data ".env.example;." run_backend.py
cd ..

echo.
echo [3/4] Building Electron App...
cd electron-app
call npm install
call npm run dist
cd ..

echo.
echo ===================================================
echo   BUILD COMPLETE!
echo   Installer location: electron-app\dist\GGU AI Document Processing Agent Setup 1.1.0.exe
echo ===================================================
pause

