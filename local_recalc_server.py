#!/usr/bin/env python3
"""Local HTTP trigger for recalculating the Feishu Base account."""

from __future__ import annotations

import argparse
import datetime as dt
import html
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

RUN_LOCK = threading.Lock()


def project_python() -> str:
    venv_python = PROJECT_DIR / ".venv" / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def render_page(title: str, body: str) -> bytes:
    page = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 40px; line-height: 1.5; }}
    label {{ display: block; margin: 16px 0 6px; font-weight: 600; }}
    input {{ border: 1px solid #d1d5db; border-radius: 8px; padding: 9px 10px; font-size: 15px; }}
    button, a.button {{ display: inline-block; border: 0; border-radius: 8px; background: #2563eb; color: white; padding: 10px 16px; text-decoration: none; font-size: 15px; cursor: pointer; }}
    pre {{ background: #111827; color: #f9fafb; padding: 16px; border-radius: 8px; overflow: auto; white-space: pre-wrap; }}
    .muted {{ color: #6b7280; }}
  </style>
</head>
<body>
{body}
</body>
</html>
"""
    return page.encode("utf-8")


def default_as_of() -> str:
    return f"{dt.date.today():%Y-%m-%d}"


def run_recalc(*, as_of: str, update_prices: bool, mark_transactions: bool) -> subprocess.CompletedProcess[str]:
    cmd = [
        project_python(),
        str(PROJECT_DIR / "stock_sim_account_recalc.py"),
        "--as-of",
        as_of,
    ]
    if update_prices:
        cmd.append("--update-prices")
    if mark_transactions:
        cmd.append("--mark-transactions")
    return subprocess.run(cmd, cwd=PROJECT_DIR, text=True, capture_output=True, timeout=900)


class RecalcHandler(BaseHTTPRequestHandler):
    server_version = "FundSimulatorRecalc/1.0"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path in {"/", ""}:
            as_of = default_as_of()
            self.respond_html(
                200,
                render_page(
                    "本地重算按钮",
                    f"""
<h1>股票模拟账户本地重算</h1>
<p class="muted">点击下面按钮后，会在本机执行：实时取价、重算持仓和收益、写回飞书 Base，并标记交易为已重算。</p>
<form method="post" action="/recalc">
  <label for="as_of">重算日期</label>
  <input id="as_of" name="as_of" type="date" value="{html.escape(as_of)}">
  <p></p>
  <button type="submit">实时取价并重新统计</button>
</form>
<p class="muted">也可以直接打开 <code>/recalc</code> 触发一次重算。关闭这个终端窗口后，本地触发服务就会停止。</p>
""",
                ),
            )
            return
        if parsed.path == "/recalc":
            query = parse_qs(parsed.query)
            self.handle_recalc(
                as_of=query.get("as_of", [default_as_of()])[0],
                update_prices=query.get("update_prices", ["1"])[0] != "0",
                mark_transactions=query.get("mark_transactions", ["1"])[0] != "0",
            )
            return
        self.respond_text(404, "Not found")

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/recalc":
            length = int(self.headers.get("Content-Length", "0") or "0")
            form = parse_qs(self.rfile.read(length).decode("utf-8")) if length else {}
            self.handle_recalc(
                as_of=form.get("as_of", [default_as_of()])[0],
                update_prices=True,
                mark_transactions=True,
            )
            return
        self.respond_text(404, "Not found")

    def handle_recalc(self, *, as_of: str, update_prices: bool, mark_transactions: bool) -> None:
        if not RUN_LOCK.acquire(blocking=False):
            self.respond_html(
                409,
                render_page(
                    "重算正在运行",
                    "<h1>重算正在运行</h1><p>请等当前任务完成后再点一次。</p>",
                ),
            )
            return
        try:
            result = run_recalc(as_of=as_of, update_prices=update_prices, mark_transactions=mark_transactions)
        except subprocess.TimeoutExpired as exc:
            output = f"Timeout after {exc.timeout} seconds"
            status = 500
        finally:
            RUN_LOCK.release()

        if "result" in locals():
            output = (result.stdout or "") + ("\nSTDERR:\n" + result.stderr if result.stderr else "")
            status = 200 if result.returncode == 0 else 500

        escaped = html.escape(output.strip() or "(no output)")
        self.respond_html(
            status,
            render_page(
                "重算结果",
                f"""
<h1>{'重算完成' if status == 200 else '重算失败'}</h1>
<p><a class="button" href="/">返回本地按钮</a></p>
<pre>{escaped}</pre>
""",
            ),
        )

    def respond_html(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def respond_text(self, status: int, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write(f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {fmt % args}\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a local recalc trigger server.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    server = ThreadingHTTPServer((args.host, args.port), RecalcHandler)
    url = f"http://{args.host}:{args.port}/"
    print(f"Local recalc server listening on {url}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
