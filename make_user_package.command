#!/bin/zsh
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

PACKAGE_NAME="fund-simulator-user-package"
OUTPUT_ZIP="$SCRIPT_DIR/${PACKAGE_NAME}.zip"
TMP_DIR="$(mktemp -d)"
PACKAGE_DIR="$TMP_DIR/$PACKAGE_NAME"

mkdir -p "$PACKAGE_DIR"

cp "$SCRIPT_DIR/stock_sim_account_recalc.py" "$PACKAGE_DIR/"
cp "$SCRIPT_DIR/local_recalc_server.py" "$PACKAGE_DIR/"
cp "$SCRIPT_DIR/setup.command" "$PACKAGE_DIR/"
cp "$SCRIPT_DIR/configure_account.command" "$PACKAGE_DIR/"
cp "$SCRIPT_DIR/recalc_account.command" "$PACKAGE_DIR/"
cp "$SCRIPT_DIR/start_recalc_server.command" "$PACKAGE_DIR/"
cp "$SCRIPT_DIR/.env.example" "$PACKAGE_DIR/"
cp "$SCRIPT_DIR/USER_GUIDE.md" "$PACKAGE_DIR/"
cp "$SCRIPT_DIR/WORKBUDDY_PROMPT.md" "$PACKAGE_DIR/"

chmod +x "$PACKAGE_DIR"/*.command

rm -f "$OUTPUT_ZIP"
(cd "$TMP_DIR" && zip -qr "$OUTPUT_ZIP" "$PACKAGE_NAME")
rm -rf "$TMP_DIR"

echo "已生成给其他使用者的压缩包："
echo "$OUTPUT_ZIP"
echo
echo "这个压缩包不会包含你的 .env、虚拟环境或本机授权信息。"
echo
echo "按 Enter 关闭窗口。"
read -r _ || true
exit 0
