#!/bin/bash
set -e

echo "========================================"
echo "  MagpieBridge Builder"
echo "========================================"
echo ""

echo "[1/3] Installing Python dependencies..."
pip install -r requirements.txt
pip install pyinstaller

echo ""
echo "[2/3] Building frontend..."
cd web
npm install
npm run build
cd ..

echo ""
echo "[3/3] Building executable with PyInstaller..."
pyinstaller MagpieBridge.spec --clean --noconfirm

echo ""
echo "========================================"
echo "  Build complete!"
echo "  Output: dist/MagpieBridge"
echo "========================================"
