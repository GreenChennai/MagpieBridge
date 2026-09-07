@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ========================================
echo   MagpieBridge Builder
echo ========================================
echo.

REM Get version from pyproject.toml
for /f "tokens=2 delims==" %%a in ('findstr "version" pyproject.toml') do (
    set "VERSION=%%a"
)
set "VERSION=!VERSION: =!"
set "VERSION=!VERSION:"=!"
echo Version: %VERSION%
echo.

set "OUTPUT_DIR=..\构建\MagpieBridge-V%VERSION%"
echo Output: %OUTPUT_DIR%
echo.

echo [1/4] Installing Python dependencies...
pip install -r requirements.txt
pip install pyinstaller
if %errorlevel% neq 0 (
    echo Failed to install dependencies
    pause
    exit /b 1
)

echo.
echo [2/4] Building frontend...
cd web
call npm install
call npm run build
cd ..
if %errorlevel% neq 0 (
    echo Failed to build frontend
    pause
    exit /b 1
)

echo.
echo [3/4] Building executable with PyInstaller...
python -m PyInstaller MagpieBridge.spec --clean --noconfirm
if %errorlevel% neq 0 (
    echo Failed to build executable
    pause
    exit /b 1
)

echo.
echo [4/4] Copying to output directory...
if not exist "%OUTPUT_DIR%" mkdir "%OUTPUT_DIR%"
copy /Y "dist\MagpieBridge.exe" "%OUTPUT_DIR%\"
copy /Y "config.json" "%OUTPUT_DIR%\"

REM Bundle the sample/example plugins so the build is self-contained
if exist "plugins" (
    echo Copying plugins directory...
    xcopy /E /I /Y "plugins" "%OUTPUT_DIR%\plugins\" >nul
)
if exist "docs" (
    xcopy /E /I /Y "docs" "%OUTPUT_DIR%\docs\" >nul
)
if exist "README.md" (
    copy /Y "README.md" "%OUTPUT_DIR%\" >nul
)
echo Files copied to: %OUTPUT_DIR%

echo.
echo ========================================
echo   Build complete!
echo   Output: %OUTPUT_DIR%
echo ========================================
pause
