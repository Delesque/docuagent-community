#!/bin/bash
# DocuAgent 启动脚本 (Linux/macOS)
# 同时启动 Python 后端和 React 前端开发服务器

set -e

echo "========================================"
echo "  DocuAgent 启动中..."
echo "========================================"
echo ""

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "[错误] 找不到 Python3，请先安装 Python 3.12+"
    exit 1
fi

# 检查前端依赖
if [ ! -d "frontend/node_modules" ]; then
    echo "[提示] 首次运行，正在安装前端依赖..."
    cd frontend
    npm install
    cd ..
    echo ""
fi

# 启动后端（后台）
echo "[1/2] 启动 Python 后端..."
python3 docuagent.py --host 127.0.0.1 --port 8765 --no-browser &
BACKEND_PID=$!
echo "后端 PID: $BACKEND_PID"

# 等待后端启动
sleep 2

# 启动前端（后台）
echo "[2/2] 启动前端开发服务器..."
cd frontend
npm run dev &
FRONTEND_PID=$!
cd ..
echo "前端 PID: $FRONTEND_PID"

echo ""
echo "========================================"
echo "  启动完成！"
echo "========================================"
echo ""
echo "  后端地址: http://localhost:8765"
echo "  前端地址: http://localhost:3100"
echo ""
echo "  按 Ctrl+C 停止所有服务"
echo "========================================"
echo ""

# 清理函数
cleanup() {
    echo ""
    echo "正在停止服务..."
    kill $BACKEND_PID $FRONTEND_PID 2>/dev/null
    echo "已停止"
    exit 0
}

trap cleanup INT TERM

# 保持脚本运行
wait
