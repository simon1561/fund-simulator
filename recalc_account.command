#!/bin/zsh
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

AS_OF="$(date +%F)"
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

echo "重算股票模拟账户"
echo "项目目录: $SCRIPT_DIR"
echo "重算日期: $AS_OF"
echo

"$PYTHON" "$SCRIPT_DIR/stock_sim_account_recalc.py" --as-of "$AS_OF" --mark-transactions
STATUS=$?

echo
if [[ $STATUS -eq 0 ]]; then
  echo "重算完成。"
else
  echo "重算失败，退出码: $STATUS"
fi

echo
echo "按 Enter 关闭窗口。"
read -r _
exit $STATUS
