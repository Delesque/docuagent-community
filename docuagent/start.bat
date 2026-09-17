@echo off
REM DocuAgent 启动脚本
REM 同时启动 Python 后端和 React 前端开发服务器

echo ========================================
echo   DocuAgent 启动中...
echo ========================================
echo.

REM 检查 Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 找不到 Python，请先安装 Python 3.12+
    pause
    exit /b 1
)

REM 检查前端依赖
if not exist "frontend\node_modules" (
    echo [提示] 首次运行，正在安装前端依赖...
    cd frontend
    call npm install
    cd ..
    echo.
)

REM 启动后端（新窗口）
echo [1/2] 启动 Python 后端...
start "DocuAgent 后端" cmd /k "python docuagent.py --host 127.0.0.1 --port 8765 --no-browser"

REM 等待后端启动
timeout /t 2 /nobreak >nul

REM 启动前端（新窗口）
echo [2/2] 启动前端开发服务器...
cd frontend
start "DocuAgent 前端" cmd /k "npm run dev"
cd ..

echo.
echo ========================================
echo   启动完成！
echo ========================================
echo.
echo   后端地址: http://localhost:8765
echo   前端地址: http://localhost:3100
echo.
echo   按任意键关闭此窗口（后台服务继续运行）
echo   或直接关闭后端/前端窗口来停止服务
echo ========================================
pause >nul
