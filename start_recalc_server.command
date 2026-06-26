#!/bin/zsh
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

PYTHON="$SCRIPT_DIR/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  echo "没有找到项目虚拟环境。请先双击 setup.command 完成安装。"
  echo
  echo "按 Enter 关闭窗口。"
  read -r _
  exit 1
fi

if [[ ! -f "$SCRIPT_DIR/.env" ]]; then
  echo "还没有配置 Base。请先双击 configure_account.command，粘贴你的 Base 链接。"
  echo
  echo "按 Enter 关闭窗口。"
  read -r _
  exit 1
fi

URL="http://127.0.0.1:8765/"

echo "启动股票模拟账户本地重算服务"
echo "项目目录: $SCRIPT_DIR"
echo "本地按钮: $URL"
echo
echo "保持这个窗口打开；关闭窗口后，本地触发服务会停止。"
echo

(sleep 1; open "$URL") &
"$PYTHON" "$SCRIPT_DIR/local_recalc_server.py"
