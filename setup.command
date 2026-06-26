#!/bin/zsh
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

echo "股票模拟账户：首次安装"
echo "项目目录: $SCRIPT_DIR"
echo

PYTHON_BIN="$(command -v python3 || true)"
if [[ -z "$PYTHON_BIN" ]]; then
  echo "没有找到 Python 3。请先安装 Python 3，然后重新运行这个文件。"
  echo "按 Enter 关闭窗口。"
  read -r _
  exit 1
fi

if [[ ! -x "$SCRIPT_DIR/.venv/bin/python" ]]; then
  echo "创建 Python 虚拟环境..."
  "$PYTHON_BIN" -m venv "$SCRIPT_DIR/.venv" || exit 1
fi

PYTHON="$SCRIPT_DIR/.venv/bin/python"
echo "安装 Python 依赖..."
"$PYTHON" -m pip install --upgrade pip || exit 1
"$PYTHON" -m pip install yfinance || exit 1

if ! command -v lark-cli >/dev/null 2>&1; then
  echo
  echo "没有找到 lark-cli，尝试通过 npm 安装..."
  if command -v npm >/dev/null 2>&1; then
    npm install -g @larksuite/cli || exit 1
  else
    echo "没有找到 npm。请让 WorkBuddy 帮你安装 Node.js，再重新运行 setup.command。"
    echo "按 Enter 关闭窗口。"
    read -r _
    exit 1
  fi
fi

echo
echo "检查 lark-cli..."
lark-cli --version || exit 1

if ! lark-cli doctor --offline >/tmp/fund_simulator_lark_doctor.log 2>&1; then
  echo
  echo "lark-cli 还没有完成初始化，开始初始化。"
  echo "如果终端显示链接或二维码，请按提示完成。"
  lark-cli config init --new || exit 1
fi

echo
echo "接下来会打开/显示飞书授权。请按提示完成授权。"
echo "如果已经授权过，这一步通常会很快完成。"
lark-cli auth login --domain base || exit 1

echo
echo "安装完成。下一步请运行 configure_account.command，粘贴你的 Base 链接。"
echo
echo "按 Enter 关闭窗口。"
read -r _ || true
exit 0
