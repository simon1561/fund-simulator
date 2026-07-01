#!/usr/bin/env python3
"""Recalculate the Feishu Base stock simulation account MVP.

This script intentionally keeps the accounting model simple:
- USD cash only.
- Stock long positions only.
- Cash-secured puts only; no option mark-to-market in NAV.
- Moving-average stock cost.
- TWR is calculated from daily NAV snapshots.

Run:
  python3 stock_sim_account_recalc.py
  python3 stock_sim_account_recalc.py --update-prices
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


PROJECT_DIR = Path(__file__).resolve().parent


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_env_file(PROJECT_DIR / ".env")

BASE_TOKEN = os.environ.get("BASE_TOKEN", "").strip()

TABLE_NAMES = {
    "account": "设置",
    "securities": "投资标的库",
    "transactions": "交易流水",
    "holdings": "持仓统计",
    "puts": "现金担保Put",
    "snapshots": "资产快照",
    "asset_allocation": "资产构成",
}

TABLE: dict[str, str] = {}

FX_TICKER = {
    "USD": None,
    "HKD": "HKDUSD=X",
    "CNY": "CNYUSD=X",
    "EUR": "EURUSD=X",
    "JPY": "JPYUSD=X",
    "GBP": "GBPUSD=X",
}

# Yahoo Finance now returns HTTP 429 for requests without a browser User-Agent,
# and rate-limits bursts even with one. Always send a UA and retry with backoff.
YAHOO_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
YAHOO_MAX_RETRIES = 4
YAHOO_BACKOFF_BASE = 1.5  # seconds; grows exponentially per retry, plus jitter
YAHOO_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


@dataclass
class Holding:
    security_id: str
    qty: float = 0.0
    cost: float = 0.0
    native_cost: float = 0.0
    realized: float = 0.0
    dividend: float = 0.0          # 累计分红 USD（摊薄成本法：冲减成本，不计入已实现）
    native_dividend: float = 0.0   # 累计分红 原币
    note: str = ""


@dataclass
class PutPosition:
    security_id: str
    open_tx_id: str
    open_date: dt.date | None
    expiry: dt.date | None
    strike: float
    contracts: float
    multiplier: float
    premium_per_share: float
    premium_total: float
    frozen_cash: float
    status: str = "未到期"
    settle_date: dt.date | None = None
    assigned_qty: float = 0.0
    assigned_cost: float = 0.0
    note: str = ""


@dataclass
class MarketQuote:
    price_usd: float
    price_date: dt.date
    close: float
    currency: str
    fx: float
    fx_date: dt.date


class LarkError(RuntimeError):
    pass


def extract_base_token(value: str) -> str:
    text = value.strip()
    match = re.search(r"/base/([A-Za-z0-9]+)", text)
    if match:
        return match.group(1)
    if re.fullmatch(r"[A-Za-z0-9]+", text):
        return text
    return ""


def parse_json_stdout(stdout: str) -> dict[str, Any]:
    text = stdout.strip()
    if not text:
        return {}
    try:
        obj, _ = json.JSONDecoder().raw_decode(text)
        return obj
    except json.JSONDecodeError as exc:
        raise LarkError(f"Cannot parse lark-cli JSON output: {stdout[:500]}") from exc


def run_lark(args: list[str], *, dry_run: bool = False) -> dict[str, Any]:
    cmd = ["lark-cli", *args]
    if dry_run and not any(arg == "--dry-run" for arg in cmd):
        cmd.append("--dry-run")
    proc = subprocess.run(cmd, text=True, capture_output=True)
    payload = parse_json_stdout(proc.stdout)
    if proc.returncode != 0 or not payload.get("ok", False):
        err = payload.get("error") or proc.stderr or proc.stdout
        raise LarkError(json.dumps(err, ensure_ascii=False, indent=2))
    return payload.get("data", {})


def require_base_token() -> str:
    if not BASE_TOKEN:
        raise RuntimeError(
            "Missing BASE_TOKEN. Run configure_account.command, or set BASE_TOKEN in .env."
        )
    return BASE_TOKEN


def resolve_table_ids() -> None:
    global TABLE
    base_token = require_base_token()
    data = run_lark(
        [
            "base",
            "+table-list",
            "--as",
            "user",
            "--base-token",
            base_token,
            "--format",
            "json",
        ]
    )
    by_name = {item.get("name"): item.get("id") for item in data.get("tables", [])}
    missing = [name for name in TABLE_NAMES.values() if name not in by_name]
    if missing:
        raise RuntimeError(f"Base is missing required tables: {', '.join(missing)}")
    TABLE = {key: str(by_name[name]) for key, name in TABLE_NAMES.items()}


def list_records(table_id: str, fields: list[str] | None = None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    offset = 0
    while True:
        args = [
            "base",
            "+record-list",
            "--as",
            "user",
            "--base-token",
            require_base_token(),
            "--table-id",
            table_id,
            "--limit",
            "200",
            "--offset",
            str(offset),
            "--format",
            "json",
        ]
        for field in fields or []:
            args.extend(["--field-id", field])
        data = run_lark(args)
        field_names = data.get("fields", [])
        rows = data.get("data", [])
        record_ids = data.get("record_id_list", [])
        for record_id, row in zip(record_ids, rows):
            item = dict(zip(field_names, row))
            item["record_id"] = record_id
            records.append(item)
        if not data.get("has_more"):
            return records
        offset += len(record_ids)


def upsert_record(table_id: str, payload: dict[str, Any], record_id: str | None, *, dry_run: bool) -> None:
    if dry_run:
        action = "update" if record_id else "create"
        print(
            json.dumps(
                {"action": action, "table_id": table_id, "record_id": record_id, "payload": payload},
                ensure_ascii=False,
            )
        )
        return
    args = [
        "base",
        "+record-upsert",
        "--as",
        "user",
        "--base-token",
        require_base_token(),
        "--table-id",
        table_id,
        "--json",
        json.dumps(payload, ensure_ascii=False),
        "--format",
        "json",
    ]
    if record_id:
        args.extend(["--record-id", record_id])
    run_lark(args, dry_run=dry_run)


def batch_mark_transactions(record_ids: list[str], *, dry_run: bool) -> None:
    for i in range(0, len(record_ids), 200):
        batch = record_ids[i : i + 200]
        if not batch:
            continue
        payload = {"record_id_list": batch, "patch": {"处理状态": "已重算"}}
        if dry_run:
            print(
                json.dumps(
                    {"action": "batch_update", "table_id": TABLE["transactions"], "payload": payload},
                    ensure_ascii=False,
                )
            )
            continue
        args = [
            "base",
            "+record-batch-update",
            "--as",
            "user",
            "--base-token",
            require_base_token(),
            "--table-id",
            TABLE["transactions"],
            "--json",
            json.dumps(payload, ensure_ascii=False),
            "--format",
            "json",
        ]
        run_lark(args, dry_run=dry_run)


def delete_records(table_id: str, record_ids: list[str], *, dry_run: bool) -> None:
    for i in range(0, len(record_ids), 200):
        batch = record_ids[i : i + 200]
        if not batch:
            continue
        if dry_run:
            print(
                json.dumps(
                    {"action": "delete", "table_id": table_id, "record_id_list": batch},
                    ensure_ascii=False,
                )
            )
            continue
        args = [
            "base",
            "+record-delete",
            "--as",
            "user",
            "--base-token",
            require_base_token(),
            "--table-id",
            table_id,
            "--json",
            json.dumps({"record_id_list": batch}, ensure_ascii=False),
            "--yes",
            "--format",
            "json",
        ]
        run_lark(args, dry_run=dry_run)


def first_select(value: Any) -> str:
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value or "")


def link_id(value: Any) -> str:
    if isinstance(value, list) and value:
        first = value[0]
        if isinstance(first, dict):
            return str(first.get("id") or "")
        return str(first)
    return ""


def link_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    ids: list[str] = []
    for item in value:
        if isinstance(item, dict) and item.get("id"):
            ids.append(str(item["id"]))
        elif isinstance(item, str):
            ids.append(item)
    return ids


def num(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def num_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_datetime(value: Any) -> dt.datetime | None:
    if not value:
        return None
    text = str(value)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(text, fmt)
        except ValueError:
            pass
    return None


def fmt_date(value: dt.date | None) -> str | None:
    if not value:
        return None
    return f"{value:%Y-%m-%d} 00:00:00"


def fetch_yahoo_json(url: str, *, timeout: int = 20) -> dict[str, Any]:
    """GET a Yahoo Finance JSON endpoint with a browser User-Agent and retry/backoff.

    Without a User-Agent header Yahoo returns HTTP 429 for every request, so the
    UA is mandatory; bursts can still be rate-limited, so 429/5xx and transient
    network errors are retried with exponential backoff plus jitter.
    """
    last_exc: Exception | None = None
    for attempt in range(YAHOO_MAX_RETRIES):
        request = urllib.request.Request(url, headers={"User-Agent": YAHOO_USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code not in YAHOO_RETRYABLE_STATUS or attempt == YAHOO_MAX_RETRIES - 1:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last_exc = exc
            if attempt == YAHOO_MAX_RETRIES - 1:
                raise
        time.sleep(YAHOO_BACKOFF_BASE * (2**attempt) + random.uniform(0, 0.5))
    if last_exc:
        raise last_exc
    raise RuntimeError(f"Failed to fetch {url}")


def yahoo_chart_quote_on_or_before(
    ticker: str,
    target_date: dt.date,
    *,
    lookback_days: int = 14,
) -> tuple[float, dt.date]:
    start = target_date - dt.timedelta(days=lookback_days)
    end = target_date + dt.timedelta(days=1)
    params = urllib.parse.urlencode(
        {
            "interval": "1d",
            "period1": int(dt.datetime.combine(start, dt.time.min, tzinfo=dt.UTC).timestamp()),
            "period2": int(dt.datetime.combine(end, dt.time.min, tzinfo=dt.UTC).timestamp()),
        }
    )
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(ticker)}?{params}"
    payload = fetch_yahoo_json(url)
    chart = payload.get("chart") or {}
    if chart.get("error"):
        err = chart["error"]
        raise RuntimeError(f"{err.get('code')}: {err.get('description')}")
    result = (chart.get("result") or [None])[0]
    if not result:
        raise RuntimeError("No Yahoo chart result")

    meta = result.get("meta") or {}
    timezone_name = meta.get("exchangeTimezoneName") or "UTC"
    try:
        timezone = ZoneInfo(timezone_name)
    except Exception:  # noqa: BLE001 - Yahoo sometimes returns non-IANA aliases.
        timezone = dt.UTC
    timestamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    latest: tuple[float, dt.date] | None = None
    for timestamp, close in zip(timestamps, closes):
        if close is None:
            continue
        price_date = dt.datetime.fromtimestamp(timestamp, timezone).date()
        if price_date <= target_date:
            latest = (float(close), price_date)
    if latest:
        return latest

    regular_price = meta.get("regularMarketPrice")
    regular_time = meta.get("regularMarketTime")
    if regular_price is not None and regular_time:
        price_date = dt.datetime.fromtimestamp(int(regular_time), timezone).date()
        if price_date <= target_date:
            return float(regular_price), price_date
    raise RuntimeError(f"No price data for {ticker} on or before {target_date:%Y-%m-%d}")


def quote_on_or_before(ticker: str, target_date: dt.date, *, lookback_days: int = 14) -> tuple[float, dt.date]:
    try:
        import yfinance as yf  # type: ignore
    except ImportError as exc:
        try:
            return yahoo_chart_quote_on_or_before(ticker, target_date, lookback_days=lookback_days)
        except Exception as fallback_exc:
            raise RuntimeError("Missing dependency: pip install yfinance") from fallback_exc

    start = target_date - dt.timedelta(days=lookback_days)
    end = target_date + dt.timedelta(days=1)
    try:
        hist = yf.Ticker(ticker).history(
            start=f"{start:%Y-%m-%d}",
            end=f"{end:%Y-%m-%d}",
            interval="1d",
            auto_adjust=False,
        )
        if hist.empty or "Close" not in hist:
            raise RuntimeError(f"No price data for {ticker} on or before {target_date:%Y-%m-%d}")
        hist = hist.dropna(subset=["Close"])
        hist = hist.loc[[item.date() <= target_date for item in hist.index]]
        if hist.empty:
            raise RuntimeError(f"No price data for {ticker} on or before {target_date:%Y-%m-%d}")
        last = hist.iloc[-1]
        close = float(last["Close"])
        when = hist.index[-1].date()
        return close, when
    except Exception as exc:  # noqa: BLE001 - fall back to Yahoo chart when yfinance parsing fails.
        try:
            return yahoo_chart_quote_on_or_before(ticker, target_date, lookback_days=lookback_days)
        except Exception as fallback_exc:
            raise RuntimeError(
                f"No price data for {ticker} on or before {target_date:%Y-%m-%d}: "
                f"{exc}; Yahoo chart fallback failed: {fallback_exc}"
            ) from fallback_exc


def usd_fx_on_or_before(
    currency: str,
    target_date: dt.date,
    cache: dict[tuple[str, dt.date], tuple[float, dt.date]],
) -> tuple[float, dt.date]:
    currency = currency.upper()
    if currency == "USD":
        return 1.0, target_date
    cache_key = (currency, target_date)
    if cache_key in cache:
        return cache[cache_key]
    ticker = FX_TICKER.get(currency)
    if not ticker:
        raise RuntimeError(f"No FX ticker configured for {currency}")
    close, quote_date = quote_on_or_before(ticker, target_date)
    cache[cache_key] = (close, quote_date)
    return close, quote_date


def security_ticker(security: dict[str, Any]) -> str:
    return str(security.get("股票代码") or security.get("行情源代码") or "").strip()


def security_name(security: dict[str, Any]) -> str:
    return str(security.get("股票名称") or security.get("名称") or "").strip()


def security_label(security: dict[str, Any]) -> str:
    name = security_name(security)
    ticker = security_ticker(security)
    if name and ticker:
        return f"{name} / {ticker}"
    return name or ticker or str(security.get("标的") or "").strip()


def market_quote_usd(
    security: dict[str, Any],
    *,
    as_of: dt.date,
    fx_cache: dict[tuple[str, dt.date], tuple[float, dt.date]],
) -> MarketQuote:
    ticker = security_ticker(security)
    if not ticker:
        raise RuntimeError("missing 股票代码")
    close, price_date = quote_on_or_before(ticker, as_of)
    currency = first_select(security.get("币种")) or "USD"
    fx, fx_date = usd_fx_on_or_before(currency, price_date, fx_cache)
    return MarketQuote(
        price_usd=close * fx,
        price_date=price_date,
        close=close,
        currency=currency,
        fx=fx,
        fx_date=fx_date,
    )


def live_market_prices(
    securities_by_id: dict[str, dict[str, Any]],
    security_ids: set[str],
    *,
    as_of: dt.date,
    warnings: list[str],
) -> dict[str, MarketQuote]:
    result: dict[str, MarketQuote] = {}
    fx_cache: dict[tuple[str, dt.date], tuple[float, dt.date]] = {}
    for security_id in sorted(security_ids):
        security = securities_by_id.get(security_id)
        if not security:
            warnings.append(f"{security_id} missing in 投资标的库")
            continue
        try:
            result[security_id] = market_quote_usd(security, as_of=as_of, fx_cache=fx_cache)
        except Exception as exc:  # noqa: BLE001 - keep recalculation usable and surface the gap.
            code = security_label(security) or security_id
            warnings.append(f"{code} cannot fetch live market price for {as_of:%Y-%m-%d}: {exc}")
    return result


def match_open_put(
    puts: list[PutPosition],
    security_id: str,
    expiry: dt.date | None,
    strike: float,
) -> PutPosition | None:
    for pos in puts:
        if pos.status != "未到期":
            continue
        if pos.security_id != security_id:
            continue
        if expiry and pos.expiry != expiry:
            continue
        if abs(pos.strike - strike) > 1e-8:
            continue
        return pos
    return None


def initial_baseline_snapshot_payload(baseline_date: dt.date, initial_cash: float) -> dict[str, Any]:
    return {
        "日期": fmt_date(baseline_date),
        "现金USD": initial_cash,
        "冻结现金USD": 0,
        "可用现金USD": initial_cash,
        "股票市值USD": 0,
        "NAVUSD": initial_cash,
        "外部现金流USD": 0,
        "当日TWR": 0,
        "累计TWR": 0,
        "恒生科技累计收益率": 0,
        "沪深300累计收益率": 0,
        "标普500累计收益率": 0,
        "备注": "初始基准点",
    }


def fetch_dividends(ticker: str, start_date: dt.date, end_date: dt.date) -> list[tuple[dt.date, float]]:
    """返回 [(除息日, 每股派息·原币), ...]；优先 yfinance，失败回退 Yahoo chart(events=div)。"""
    start = start_date - dt.timedelta(days=1)
    end = end_date + dt.timedelta(days=1)
    try:
        import yfinance as yf  # type: ignore
    except ImportError:
        yf = None
    if yf is not None:
        try:
            hist = yf.Ticker(ticker).history(
                start=f"{start:%Y-%m-%d}",
                end=f"{end:%Y-%m-%d}",
                interval="1d",
                auto_adjust=False,
                actions=True,
            )
            if not hist.empty:
                if "Dividends" not in hist:
                    return []
                out: list[tuple[dt.date, float]] = []
                for idx, value in hist["Dividends"].items():
                    amount = float(value or 0.0)
                    if amount > 0:
                        out.append((idx.date(), amount))
                return sorted(out)
        except Exception:  # noqa: BLE001 - fall back to Yahoo chart events=div
            pass
    p1 = int(dt.datetime(start.year, start.month, start.day, tzinfo=dt.UTC).timestamp())
    p2 = int(dt.datetime(end.year, end.month, end.day, tzinfo=dt.UTC).timestamp())
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        f"?period1={p1}&period2={p2}&interval=1d&events=div"
    )
    payload = fetch_yahoo_json(url)
    result = ((payload.get("chart") or {}).get("result") or [{}])[0]
    events = (result.get("events") or {}).get("dividends") or {}
    out = []
    for event in events.values():
        ts = event.get("date")
        amount = event.get("amount")
        if ts is None or amount is None:
            continue
        out.append((dt.datetime.fromtimestamp(int(ts), dt.UTC).date(), float(amount)))
    return sorted(out)


def compute_shares_on(security_id: str, ex_date: dt.date, transactions: list[dict[str, Any]]) -> float:
    """回放除息日之前的买入/卖出/拆股/被指派，得到除息日应享分红的持股数。"""
    shares = 0.0
    for tx in transactions:
        tx_dt = parse_datetime(tx.get("交易日期"))
        if not tx_dt or tx_dt.date() >= ex_date:
            continue
        if link_id(tx.get("证券")) != security_id:
            continue
        tx_type = first_select(tx.get("交易类型"))
        qty = num(tx.get("数量"))
        if tx_type == "买入股票":
            shares += qty
        elif tx_type == "卖出股票":
            shares -= qty
        elif tx_type == "Put被指派":
            shares += qty
        elif tx_type == "拆股/合股":
            ratio = num(tx.get("拆合股比例"), 1.0)
            if ratio > 0:
                shares *= ratio
    return shares


def autofill_cash_dividends(
    transactions: list[dict[str, Any]],
    securities_by_id: dict[str, dict[str, Any]],
    as_of: dt.date,
    *,
    dry_run: bool,
    warnings: list[str],
) -> list[dict[str, Any]]:
    """为每个有买入的证券补齐 yfinance 现金分红，写回交易流水（去重：证券+除息日）。

    返回新建的交易（内存形态，供 dry-run 注入回放；正式运行会重新拉取交易流水）。
    只覆盖现金分红；实物分红（如派股票）yfinance 无数据，需人工补录。
    """
    existing: set[tuple[str, dt.date]] = set()
    earliest_buy: dict[str, dt.date] = {}
    for tx in transactions:
        tx_dt = parse_datetime(tx.get("交易日期"))
        if not tx_dt:
            continue
        security_id = link_id(tx.get("证券"))
        if not security_id:
            continue
        tx_type = first_select(tx.get("交易类型"))
        if tx_type == "分红":
            existing.add((security_id, tx_dt.date()))
        elif tx_type == "买入股票":
            day = tx_dt.date()
            if security_id not in earliest_buy or day < earliest_buy[security_id]:
                earliest_buy[security_id] = day

    created: list[dict[str, Any]] = []
    seq = 0
    for security_id in sorted(earliest_buy):
        security = securities_by_id.get(security_id)
        if not security:
            continue
        ticker = security_ticker(security)
        if not ticker:
            continue
        start = earliest_buy[security_id]
        try:
            dividends = fetch_dividends(ticker, start, as_of)
        except Exception as exc:  # noqa: BLE001 - a dividend fetch must never break the recalc
            warnings.append(f"{security_label(security) or ticker} 自动分红抓取失败，已跳过：{exc}")
            continue
        for ex_date, per_share in dividends:
            if ex_date < start or ex_date > as_of:
                continue
            if (security_id, ex_date) in existing:
                continue
            shares = compute_shares_on(security_id, ex_date, transactions)
            if shares <= 1e-8:
                continue
            payload = {
                "交易编号": f"AUTO-DIV-{ticker}-{ex_date:%Y%m%d}",
                "交易类型": "分红",
                "证券": [{"id": security_id}],
                "交易日期": f"{ex_date:%Y-%m-%d} 09:30:00",
                "数量": shares,
                "成交价": per_share,
                "备注": "自动生成（yfinance 现金分红，可手动改金额；删除会在下次重算重建，作废请把数量改为 0）",
            }
            upsert_record(TABLE["transactions"], payload, None, dry_run=dry_run)
            existing.add((security_id, ex_date))
            created.append(
                {
                    "record_id": f"AUTODIV-{ex_date:%Y%m%d}-{seq}",
                    "交易编号": payload["交易编号"],
                    "交易类型": "分红",
                    "证券": [{"id": security_id}],
                    "交易日期": payload["交易日期"],
                    "数量": shares,
                    "成交价": per_share,
                    "备注": payload["备注"],
                }
            )
            seq += 1
    if created:
        warnings.append(f"自动补录现金分红 {len(created)} 笔（标注「自动生成」，可在交易流水核对/调整）")
    return created


def recalc(*, as_of: dt.date, dry_run: bool, mark_transactions: bool, auto_dividends: bool = True) -> None:
    setting_rows = list_records(TABLE["account"])
    account = next((row for row in setting_rows if first_select(row.get("设置分类")) == "账户设置"), None)
    if account is None:
        account = next((row for row in setting_rows if num_or_none(row.get("初始资金USD")) is not None), None)
    if account is None:
        raise RuntimeError("设置 has no 账户设置 row")
    initial_cash = num(account.get("初始资金USD"), 1_000_000)

    securities = list_records(TABLE["securities"])
    securities_by_id = {row["record_id"]: row for row in securities}
    code_by_id = {row["record_id"]: security_label(row) or row["record_id"] for row in securities}
    currency_by_id = {row["record_id"]: first_select(row.get("币种")) or "USD" for row in securities}
    asset_type_by_id = {row["record_id"]: first_select(row.get("资产类型")) or "股票" for row in securities}

    transactions = list_records(TABLE["transactions"])
    transactions.sort(key=lambda row: parse_datetime(row.get("交易日期")) or dt.datetime.min)

    cash = initial_cash
    holdings: dict[str, Holding] = {}
    puts: list[PutPosition] = []
    warnings: list[str] = []
    historical_fx_cache: dict[tuple[str, dt.date], tuple[float, dt.date]] = {}
    filled_fx_count = 0
    external_flow_today = 0.0
    processed_tx_ids: list[str] = []

    def holding_for(security_id: str) -> Holding:
        if security_id not in holdings:
            holdings[security_id] = Holding(security_id)
        return holdings[security_id]

    def transaction_fx(tx: dict[str, Any], security_id: str, tx_date: dt.date) -> float:
        nonlocal filled_fx_count
        manual_fx = num_or_none(tx.get("USD汇率"))
        if manual_fx is not None:
            if manual_fx > 0:
                return manual_fx
            warnings.append(f"{tx['record_id']} invalid USD汇率 {manual_fx}; trying automatic FX")
        currency = first_select(tx.get("成交币种")) or currency_by_id.get(security_id, "USD")
        if currency == "USD":
            return 1.0
        try:
            fx, fx_date = usd_fx_on_or_before(currency, tx_date, historical_fx_cache)
        except Exception as exc:
            raise RuntimeError(
                f"{tx['record_id']} cannot fetch {currency}->USD FX for {tx_date:%Y-%m-%d}; "
                "please fill 交易流水.USD汇率 manually"
            ) from exc
        else:
            if fx_date < tx_date:
                warnings.append(
                    f"{tx['record_id']} missing USD汇率; using {fx_date:%Y-%m-%d} "
                    f"{currency}->USD FX because {tx_date:%Y-%m-%d} has no quote"
                )
            try:
                upsert_record(TABLE["transactions"], {"USD汇率": fx}, tx["record_id"], dry_run=dry_run)
                filled_fx_count += 1
            except LarkError as exc:
                warnings.append(f"{tx['record_id']} computed USD汇率 {fx}, but could not write it back: {exc}")
            return fx

    if auto_dividends:
        auto_created = autofill_cash_dividends(
            transactions, securities_by_id, as_of, dry_run=dry_run, warnings=warnings
        )
        if auto_created:
            transactions = (
                transactions + auto_created if dry_run else list_records(TABLE["transactions"])
            )
            transactions.sort(key=lambda row: parse_datetime(row.get("交易日期")) or dt.datetime.min)

    for tx in transactions:
        tx_dt = parse_datetime(tx.get("交易日期"))
        if not tx_dt:
            warnings.append(f"{tx.get('record_id')} missing 交易日期")
            continue
        if tx_dt.date() > as_of:
            continue
        processed_tx_ids.append(tx["record_id"])
        tx_type = first_select(tx.get("交易类型"))
        security_id = link_id(tx.get("证券"))
        qty = num(tx.get("数量"))
        price = num(tx.get("成交价"))
        cash_impact = num_or_none(tx.get("现金影响USD"))
        external_flow = num_or_none(tx.get("外部现金流USD"))
        fx = (
            transaction_fx(tx, security_id, tx_dt.date())
            if tx_type in {"买入股票", "卖出股票", "分红"} and security_id
            else 1.0
        )

        if tx_type in {"入金", "出金"}:
            amount = external_flow if external_flow is not None else cash_impact
            if amount is None:
                warnings.append(f"{tx['record_id']} {tx_type} missing cash flow")
                continue
            flow = abs(amount) if tx_type == "入金" else -abs(amount)
            cash += flow
            if tx_dt.date() == as_of:
                external_flow_today += flow
            continue

        if tx_type in {"买入股票", "卖出股票", "分红", "拆股/合股", "Put被指派"} and not security_id:
            warnings.append(f"{tx['record_id']} {tx_type} missing 证券")
            continue

        if tx_type == "买入股票":
            gross_cost = -cash_impact if cash_impact is not None else qty * price * fx
            native_cost = qty * price if qty and price else (gross_cost / fx if fx else 0.0)
            cash += cash_impact if cash_impact is not None else -gross_cost
            h = holding_for(security_id)
            h.qty += qty
            h.cost += gross_cost
            h.native_cost += native_cost
        elif tx_type == "卖出股票":
            proceeds = cash_impact if cash_impact is not None else qty * price * fx
            h = holding_for(security_id)
            if h.qty <= 0:
                warnings.append(f"{tx['record_id']} sell without position: {code_by_id.get(security_id, security_id)}")
                avg_cost = 0.0
                avg_native_cost = 0.0
            else:
                avg_cost = h.cost / h.qty
                avg_native_cost = h.native_cost / h.qty if abs(h.native_cost) > 1e-8 else 0.0
            cost_reduction = avg_cost * qty
            native_cost_reduction = avg_native_cost * qty
            h.qty -= qty
            h.cost -= cost_reduction
            h.native_cost -= native_cost_reduction
            h.realized += proceeds - cost_reduction
            cash += proceeds
            if h.qty < -1e-8:
                h.note = "卖出数量超过持仓，需检查"
        elif tx_type == "分红":
            # 分红与卖出同口径：现金流入 = 数量(应分红股数) × 成交价(每股股息·原币) × 汇率。
            # 现金影响USD 仅作为可选覆盖：填了就用它，留空则按结构化字段自动计算。
            # 记账采用「摊薄成本法」（对齐雪球）：分红不计入已实现盈亏，而是冲减持仓成本。
            amount = cash_impact if cash_impact is not None else qty * price * fx
            native_amount = qty * price if qty and price else (amount / fx if fx else 0.0)
            cash += amount
            h = holding_for(security_id)
            h.dividend += amount
            h.native_dividend += native_amount
        elif tx_type == "拆股/合股":
            ratio = num(tx.get("拆合股比例"), 1.0)
            if ratio <= 0:
                warnings.append(f"{tx['record_id']} invalid split ratio")
            else:
                holding_for(security_id).qty *= ratio
        elif tx_type == "卖Put开仓":
            if not security_id:
                warnings.append(f"{tx['record_id']} 卖Put开仓 missing 证券")
                continue
            contracts = num(tx.get("合约数"))
            multiplier = num(tx.get("合约乘数"), 100.0)
            strike = num(tx.get("行权价USD"))
            premium = num(tx.get("权利金每股USD"))
            premium_total = cash_impact if cash_impact is not None else premium * contracts * multiplier
            frozen = strike * contracts * multiplier
            expiry_dt = parse_datetime(tx.get("到期日"))
            pos = PutPosition(
                security_id=security_id,
                open_tx_id=tx["record_id"],
                open_date=tx_dt.date(),
                expiry=expiry_dt.date() if expiry_dt else None,
                strike=strike,
                contracts=contracts,
                multiplier=multiplier,
                premium_per_share=premium,
                premium_total=premium_total,
                frozen_cash=frozen,
            )
            puts.append(pos)
            cash += premium_total
        elif tx_type in {"Put到期归零", "Put被指派"}:
            contracts = num(tx.get("合约数"))
            multiplier = num(tx.get("合约乘数"), 100.0)
            strike = num(tx.get("行权价USD"))
            expiry_dt = parse_datetime(tx.get("到期日"))
            pos = match_open_put(puts, security_id, expiry_dt.date() if expiry_dt else None, strike)
            if not pos:
                warnings.append(f"{tx['record_id']} cannot match open cash-secured put")
                continue
            close_contracts = contracts or pos.contracts
            if abs(close_contracts - pos.contracts) > 1e-8:
                warnings.append(f"{tx['record_id']} partial put settlement not fully supported; treating as full")
            pos.status = "到期归零" if tx_type == "Put到期归零" else "已指派"
            pos.settle_date = tx_dt.date()
            if tx_type == "Put被指派":
                shares = close_contracts * multiplier
                strike_cost = strike * shares
                net_cost = strike_cost - pos.premium_total
                cash -= strike_cost
                h = holding_for(security_id)
                h.qty += shares
                h.cost += net_cost
                currency = currency_by_id.get(security_id, "USD")
                if currency == "USD":
                    h.native_cost += net_cost
                else:
                    try:
                        assignment_fx, _fx_date = usd_fx_on_or_before(currency, tx_dt.date(), historical_fx_cache)
                    except Exception as exc:  # noqa: BLE001 - keep the account recalc usable.
                        warnings.append(
                            f"{tx['record_id']} cannot convert assigned put cost to {currency}: {exc}"
                        )
                    else:
                        h.native_cost += net_cost / assignment_fx if assignment_fx else 0.0
                pos.assigned_qty = shares
                pos.assigned_cost = net_cost
        elif tx_type == "冲正":
            cash += cash_impact or 0.0
        else:
            warnings.append(f"{tx['record_id']} unknown 交易类型: {tx_type}")

    frozen_cash = sum(pos.frozen_cash for pos in puts if pos.status == "未到期")
    stock_value = 0.0
    pure_stock_value = 0.0  # 不含类现金标的（如 SGOV），用于「股票仓位率」
    holding_payloads: list[tuple[str, dict[str, Any]]] = []
    existing_holding_rows = list_records(
        TABLE["holdings"],
        ["标的", "证券", "最新价原币", "最新价USD", "备注"],
    )
    existing_holdings_by_title = {str(row.get("标的") or ""): row["record_id"] for row in existing_holding_rows}
    existing_holdings_by_security = {
        link_id(row.get("证券")): row["record_id"]
        for row in existing_holding_rows
        if link_id(row.get("证券"))
    }
    existing_holding_by_security = {
        link_id(row.get("证券")): row
        for row in existing_holding_rows
        if link_id(row.get("证券"))
    }
    held_security_ids = {security_id for security_id, h in holdings.items() if abs(h.qty) > 1e-8}
    price_by_security = live_market_prices(securities_by_id, held_security_ids, as_of=as_of, warnings=warnings)

    for security_id, h in sorted(holdings.items(), key=lambda item: code_by_id.get(item[0], item[0])):
        code = code_by_id.get(security_id, security_id)
        latest_price = price_by_security.get(security_id)
        existing_holding = existing_holding_by_security.get(security_id, {})
        price = latest_price.price_usd if latest_price else num(existing_holding.get("最新价USD"))
        native_price = latest_price.close if latest_price else num(existing_holding.get("最新价原币"), price)
        market_value = h.qty * price
        stock_value += market_value
        if asset_type_by_id.get(security_id, "股票") != "类现金":
            pure_stock_value += market_value
        status = "持仓中" if abs(h.qty) > 1e-8 else "已清仓"
        note = h.note
        if status == "持仓中" and latest_price is None and price <= 0:
            status = "需检查"
            note = (note + "；" if note else "") + "缺少实时行情"
        elif status == "持仓中" and latest_price is None:
            note = (note + "；" if note else "") + "行情抓取失败，沿用上次价格"
        diluted_cost = h.cost - h.dividend
        diluted_native_cost = h.native_cost - h.native_dividend
        floating = market_value - diluted_cost
        floating_return = floating / diluted_cost if abs(diluted_cost) > 1e-8 else None
        payload = {
            "标的": code,
            "证券": [{"id": security_id}],
            "股数": h.qty,
            "最新价原币": native_price,
            "买入均价原币": h.native_cost / h.qty if abs(h.qty) > 1e-8 else 0,
            "平均成本原币": diluted_native_cost / h.qty if abs(h.qty) > 1e-8 else 0,
            "平均成本USD": diluted_cost / h.qty if abs(h.qty) > 1e-8 else 0,
            "成本": diluted_cost,
            "最新价USD": price,
            "市值": market_value,
            "盈亏额度": floating,
            "盈亏比例": floating_return,
            "已实现盈亏USD": h.realized,
            "状态": status,
            "最后重算时间": f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}",
            "备注": note,
        }
        holding_payloads.append((existing_holdings_by_security.get(security_id) or existing_holdings_by_title.get(code), payload))

    nav = cash + stock_value
    for _, payload in holding_payloads:
        payload["组合权重"] = payload["市值"] / nav if abs(nav) > 1e-8 else 0
        upsert_record(TABLE["holdings"], payload, _, dry_run=dry_run)

    existing_allocation_rows = list_records(TABLE["asset_allocation"])
    existing_allocation_by_item = {
        str(row.get("构成项") or ""): row["record_id"]
        for row in existing_allocation_rows
        if str(row.get("构成项") or "").strip()
    }
    now_text = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}"
    allocation_payloads: list[dict[str, Any]] = []

    def add_allocation_item(item: str, category: str, amount: float, order: int, note: str = "") -> None:
        if amount <= 1e-8:
            return
        allocation_payloads.append(
            {
                "构成项": item,
                "类别": category,
                "金额USD": amount,
                "占NAV比例": amount / nav if nav > 1e-8 else 0,
                "排序": order,
                "最后重算时间": now_text,
                "备注": note,
            }
        )

    allocation_order = 100
    for _, payload in holding_payloads:
        if payload.get("状态") != "持仓中":
            continue
        amount = num(payload.get("市值"))
        security_id = link_id(payload.get("证券"))
        asset_type = asset_type_by_id.get(security_id, "股票")
        category = "类现金标的" if asset_type == "类现金" else "股票/ETF"
        add_allocation_item(str(payload.get("标的") or ""), category, amount, allocation_order)
        allocation_order += 10

    available_cash = cash - frozen_cash
    add_allocation_item("可用现金", "现金", available_cash, 10)
    add_allocation_item("现金担保Put冻结现金", "现金担保Put", frozen_cash, 20, "现金已计入 NAV；这里单独展示被冻结的现金部分。")

    current_allocation_items = {payload["构成项"] for payload in allocation_payloads}
    stale_allocation_ids = [
        row["record_id"]
        for row in existing_allocation_rows
        if str(row.get("构成项") or "") not in current_allocation_items
    ]
    if stale_allocation_ids:
        delete_records(TABLE["asset_allocation"], stale_allocation_ids, dry_run=dry_run)
    for payload in allocation_payloads:
        upsert_record(
            TABLE["asset_allocation"],
            payload,
            existing_allocation_by_item.get(payload["构成项"]),
            dry_run=dry_run,
        )

    existing_puts = {
        (link_id(row.get("开仓交易")) or ""): row["record_id"]
        for row in list_records(TABLE["puts"], ["开仓交易"])
    }
    for pos in puts:
        days = max(((pos.expiry - pos.open_date).days if pos.expiry and pos.open_date else 0), 1)
        annual_return = pos.premium_total / pos.frozen_cash * 365 / days if pos.frozen_cash else None
        payload = {
            "标的证券": [{"id": pos.security_id}],
            "开仓交易": [{"id": pos.open_tx_id}],
            "开仓日": fmt_date(pos.open_date),
            "到期日": fmt_date(pos.expiry),
            "行权价USD": pos.strike,
            "合约数": pos.contracts,
            "合约乘数": pos.multiplier,
            "权利金每股USD": pos.premium_per_share,
            "权利金总额USD": pos.premium_total,
            "冻结现金USD": pos.frozen_cash if pos.status == "未到期" else 0,
            "年化收益率": annual_return,
            "状态": pos.status,
            "结算日": fmt_date(pos.settle_date),
            "指派买入数量": pos.assigned_qty,
            "指派买入成本USD": pos.assigned_cost,
            "备注": pos.note,
        }
        upsert_record(TABLE["puts"], payload, existing_puts.get(pos.open_tx_id), dry_run=dry_run)

    # Securities holding flags.
    holding_ids = {sid for sid, h in holdings.items() if h.qty > 1e-8}
    for sec in securities:
        security_patch: dict[str, Any] = {}
        label = security_label(sec)
        if label and str(sec.get("标的") or "").strip() != label:
            security_patch["标的"] = label
        should_hold = sec["record_id"] in holding_ids
        if bool(sec.get("是否持仓")) != should_hold:
            security_patch["是否持仓"] = should_hold
        if security_patch:
            upsert_record(TABLE["securities"], security_patch, sec["record_id"], dry_run=dry_run)

    # TWR snapshot.
    snapshots = list_records(TABLE["snapshots"])
    snapshot_by_date: dict[dt.date, dict[str, Any]] = {}
    for row in snapshots:
        when = parse_datetime(row.get("日期"))
        if when:
            snapshot_by_date[when.date()] = row

    twr_start_dt = parse_datetime(account.get("TWR起始日"))
    if twr_start_dt and as_of >= twr_start_dt.date():
        baseline_date = twr_start_dt.date() - dt.timedelta(days=1)
        baseline_payload = initial_baseline_snapshot_payload(baseline_date, initial_cash)
        baseline_snapshot = snapshot_by_date.get(baseline_date)
        baseline_note = str(baseline_snapshot.get("备注") or "").strip() if baseline_snapshot else ""
        if baseline_snapshot is None or baseline_note in {"", "初始基准点", "脚本重算生成"}:
            upsert_record(
                TABLE["snapshots"],
                baseline_payload,
                baseline_snapshot["record_id"] if baseline_snapshot else None,
                dry_run=dry_run,
            )
            snapshot_by_date[baseline_date] = {
                **baseline_payload,
                "record_id": baseline_snapshot["record_id"] if baseline_snapshot else "",
            }

    previous_dates = [date for date in snapshot_by_date if date < as_of]
    if previous_dates:
        prev = snapshot_by_date[max(previous_dates)]
        prev_nav = num(prev.get("NAVUSD"), initial_cash)
        prev_cum = num(prev.get("累计TWR"), 0.0)
        daily_twr = (nav - external_flow_today) / prev_nav - 1 if prev_nav else 0.0
        cumulative_twr = (1 + prev_cum) * (1 + daily_twr) - 1
    else:
        daily_twr = 0.0
        cumulative_twr = 0.0

    # 派生指标：股票仓位率（不含类现金）、年化收益率、最大回撤（写回供「收益统计」看板展示）。
    cash_equiv_value = stock_value - pure_stock_value  # 类现金标的（如 SGOV）市值
    stock_position_ratio = pure_stock_value / nav if abs(nav) > 1e-8 else 0.0
    twr_days = (as_of - twr_start_dt.date()).days if twr_start_dt else 0
    twr_base = 1 + cumulative_twr
    annualized_return = (
        twr_base ** (365 / twr_days) - 1 if twr_days >= 1 and twr_base > 0 else cumulative_twr
    )
    nav_history = [
        num(snapshot_by_date[date].get("NAVUSD"), initial_cash)
        for date in sorted(snapshot_by_date)
        if date < as_of
    ]
    nav_history.append(nav)
    peak = nav_history[0]
    max_drawdown = 0.0
    for value in nav_history:
        if value > peak:
            peak = value
        if peak > 0:
            max_drawdown = min(max_drawdown, value / peak - 1)

    # 账户当前状态（NAV / 现金 / 股票市值 / 仓位率 / 年化 / 回撤等）不再回写「设置」表——
    # 已统一写入「资产快照」最新行，看板从那里读。「设置」表回归纯配置输入。

    current_snapshot = snapshot_by_date.get(as_of)
    benchmark_returns = latest_benchmark_returns(as_of, warnings)
    benchmark_fields = {
        "恒生科技": "恒生科技累计收益率",
        "沪深300": "沪深300累计收益率",
        "标普500": "标普500累计收益率",
    }
    for benchmark_name, field_name in benchmark_fields.items():
        if benchmark_returns.get(benchmark_name) is None and current_snapshot:
            existing_value = num_or_none(current_snapshot.get(field_name))
            if existing_value is not None:
                benchmark_returns[benchmark_name] = existing_value

    snapshot_payload = {
        "日期": fmt_date(as_of),
        "现金USD": cash,
        "冻结现金USD": frozen_cash,
        "可用现金USD": cash - frozen_cash,
        "股票市值USD": pure_stock_value,
        "类现金市值USD": cash_equiv_value,
        "NAVUSD": nav,
        "外部现金流USD": external_flow_today,
        "当日TWR": daily_twr,
        "累计TWR": cumulative_twr,
        "恒生科技累计收益率": benchmark_returns.get("恒生科技"),
        "沪深300累计收益率": benchmark_returns.get("沪深300"),
        "标普500累计收益率": benchmark_returns.get("标普500"),
        "股票仓位率": stock_position_ratio,
        "年化收益率": annualized_return,
        "最大回撤": max_drawdown,
        "是否最新": True,
        "备注": "脚本重算生成",
    }
    upsert_record(
        TABLE["snapshots"],
        snapshot_payload,
        current_snapshot["record_id"] if current_snapshot else None,
        dry_run=dry_run,
    )
    # 只保留当前行的「是否最新」标记，历史行复位为 False（供看板锁定最新状态）。
    for snap_date, snap_row in snapshot_by_date.items():
        if snap_date != as_of and snap_row.get("是否最新") and snap_row.get("record_id"):
            upsert_record(TABLE["snapshots"], {"是否最新": False}, snap_row["record_id"], dry_run=dry_run)

    if mark_transactions and processed_tx_ids:
        batch_mark_transactions(processed_tx_ids, dry_run=dry_run)

    print(f"Cash: {cash:,.2f}")
    print(f"Frozen cash: {frozen_cash:,.2f}")
    print(f"Stock value: {pure_stock_value:,.2f}")
    print(f"Cash-equiv value: {cash_equiv_value:,.2f}")
    print(f"NAV: {nav:,.2f}")
    print(f"Daily TWR: {daily_twr:.4%}")
    print(f"Cumulative TWR: {cumulative_twr:.4%}")
    if filled_fx_count:
        print(f"Filled missing USD FX rates: {filled_fx_count}")
    if warnings:
        print("\nWarnings:")
        for warning in warnings:
            print(f"- {warning}")


def latest_benchmark_returns(as_of: dt.date, warnings: list[str]) -> dict[str, float | None]:
    rows = [
        row
        for row in list_records(TABLE["account"])
        if first_select(row.get("设置分类")) == "基准设置"
    ]
    result: dict[str, float | None] = {}
    for row in rows:
        name = str(row.get("设置项") or "").strip()
        ticker = str(row.get("行情源代码") or "").strip()
        if not name or not ticker:
            continue
        try:
            close, _price_date = quote_on_or_before(ticker, as_of)
        except Exception as exc:  # noqa: BLE001 - benchmark gaps should not block account recalc.
            warnings.append(f"{name} benchmark cannot fetch live quote for {as_of:%Y-%m-%d}: {exc}")
            result[name] = None
            continue
        start = num_or_none(row.get("起始价格"))
        if not start:
            warnings.append(f"{name} benchmark missing 起始价格; cumulative return is unavailable")
            result[name] = None
            continue
        result[name] = close / start - 1
    return result


def run_cycle(
    *,
    as_of: dt.date,
    update_prices_flag: bool,
    dry_run: bool,
    mark_transactions: bool,
    auto_dividends: bool = True,
) -> None:
    if update_prices_flag:
        print("Market data is fetched live during recalculation; no 行情快照 records are written.")
    resolve_table_ids()
    recalc(as_of=as_of, dry_run=dry_run, mark_transactions=mark_transactions, auto_dividends=auto_dividends)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Recalculate Feishu stock simulation account MVP.")
    parser.add_argument(
        "--base-token",
        help="Feishu Base token or Base URL. Defaults to BASE_TOKEN in .env.",
    )
    parser.add_argument("--as-of", default=f"{dt.date.today():%Y-%m-%d}", help="Snapshot date, default: today.")
    parser.add_argument(
        "--update-prices",
        action="store_true",
        help="Compatibility flag: market data is fetched live during recalculation and not stored.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print lark-cli dry-run payloads without writing.")
    parser.add_argument(
        "--mark-transactions",
        action="store_true",
        help="Mark processed transaction rows as 已重算 after a successful run.",
    )
    parser.add_argument(
        "--no-auto-dividends",
        action="store_true",
        help="跳过自动抓取现金分红（默认开启：把 yfinance 现金分红补录进交易流水，去重后回放）。",
    )
    return parser.parse_args()


def main() -> int:
    global BASE_TOKEN
    args = parse_args()
    if shutil.which("lark-cli") is None:
        print("lark-cli not found on PATH", file=sys.stderr)
        return 1
    if args.base_token:
        BASE_TOKEN = extract_base_token(args.base_token)
        if not BASE_TOKEN:
            print("--base-token must be a Base URL or token", file=sys.stderr)
            return 1
    try:
        as_of = dt.datetime.strptime(args.as_of, "%Y-%m-%d").date()
    except ValueError:
        print("--as-of must use YYYY-MM-DD", file=sys.stderr)
        return 1

    try:
        run_cycle(
            as_of=as_of,
            update_prices_flag=args.update_prices,
            dry_run=args.dry_run,
            mark_transactions=args.mark_transactions,
            auto_dividends=not args.no_auto_dividends,
        )
    except (LarkError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
