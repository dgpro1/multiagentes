@echo off
rem OpenLivery local: API (:8000) + Web produccion (:3000). Double-click to start.
rem Si cambio codigo del frontend, corre "npm run build" en apps\web antes.
rem Las ventanas quedan abiertas para ver logs; se cierran para parar.
cd /d "%~dp0..\apps\api"
start "OpenLivery API :8000" cmd /k ".venv\Scripts\python -m alembic upgrade head && .venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
cd /d "%~dp0..\apps\web"
start "OpenLivery Web :3000" cmd /k ""C:\Program Files\nodejs\node.exe" .next\standalone\server.js"
echo Servidores lanzados en ventanas propias.
