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

echo "股票模拟账户：配置 Base"
echo
echo "请粘贴你的飞书 Base 链接或 Base token，然后按 Enter："
read -r BASE_INPUT

BASE_TOKEN="$(printf '%s' "$BASE_INPUT" | sed -nE 's#.*\/base\/([A-Za-z0-9]+).*#\1#p')"
if [[ -z "$BASE_TOKEN" ]]; then
  BASE_TOKEN="$(printf '%s' "$BASE_INPUT" | tr -d '[:space:]')"
fi

if [[ ! "$BASE_TOKEN" =~ ^[A-Za-z0-9]+$ ]]; then
  echo "没有识别出有效的 Base token。"
  echo "按 Enter 关闭窗口。"
  read -r _
  exit 1
fi

cat > "$SCRIPT_DIR/.env" <<EOF
BASE_TOKEN=$BASE_TOKEN
EOF

echo
echo "已写入 .env。现在验证这份 Base 是否能访问、表结构是否完整..."
CHECK_STATUS=0
if "$PYTHON" "$SCRIPT_DIR/stock_sim_account_recalc.py" --dry-run --as-of "$(date +%F)" >/tmp/fund_simulator_config_check.log 2>&1; then
  echo "验证通过。以后双击 recalc_account.command 就可以重算。"
else
  CHECK_STATUS=1
  echo "验证失败。下面是错误信息："
  echo
  cat /tmp/fund_simulator_config_check.log
  echo
  echo "常见原因：没有完成飞书授权；Base 不是从模板复制的；当前账号没有这份 Base 的编辑权限。"
fi

echo
echo "按 Enter 关闭窗口。"
read -r _ || true
exit $CHECK_STATUS
