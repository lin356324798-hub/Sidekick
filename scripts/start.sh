#!/data/data/com.termux/files/usr/bin/bash
# Sidekick 启动脚本 —— 重启 Termux 后运行本脚本恢复服务
# 用法： bash ~/.termux-agent/start.sh
cd "$HOME/.termux-agent" || exit 1

pkill -f "agent.py web --serve" >/dev/null 2>&1
pkill -f "supervisor.sh" >/dev/null 2>&1
sleep 1

nohup bash supervisor.sh >/dev/null 2>&1 &
sleep 4

echo "---- 服务自检 ----"
if curl -sf --max-time 5 http://127.0.0.1:8765/api/state >/dev/null 2>&1; then
  echo "OK  Sidekick 已就绪： http://127.0.0.1:8765/"
else
  echo "..  服务未就绪，稍等几秒再刷新页面"
fi
