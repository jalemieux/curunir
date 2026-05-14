#!/usr/bin/env python3
"""Yahoo Finance driver CLI.

Thin wrapper over the `yfinance` package. Every subcommand prints JSON to
stdout. Errors print ``{"error": "...", "hint": "..."}`` and exit 1; usage
errors exit 2.

The agent invokes this via the ``bash`` tool. Tests import it as a module
and call ``cmd_*`` functions directly.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from typing import Any

try:
    import yfinance as yf
except ImportError:  # pragma: no cover
    print(json.dumps({
        "error": "yfinance not installed",
        "hint": "pip install yfinance (already in requirements.txt)",
    }))
    sys.exit(1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _today() -> str:
    return date.today().isoformat()


def _safe(d: dict, key: str, default: Any = None) -> Any:
    """Yahoo info dicts have keys that come and go. Return default if missing."""
    val = d.get(key, default)
    # NaN sneaks in from pandas; treat as None.
    try:
        if val != val:  # NaN != NaN
            return default
    except TypeError:
        pass
    return val


def _df_records(df) -> list[dict]:
    """Convert a pandas DataFrame to a list of {date, ...cols} records."""
    if df is None or df.empty:
        return []
    out = []
    for idx, row in df.iterrows():
        rec: dict[str, Any] = {}
        if isinstance(idx, (datetime, date)):
            rec["date"] = idx.date().isoformat() if isinstance(idx, datetime) else idx.isoformat()
        else:
            rec["date"] = str(idx)
        for col, val in row.items():
            try:
                if val != val:  # NaN
                    val = None
            except TypeError:
                pass
            rec[str(col)] = val
        out.append(rec)
    return out


def _financials_df_to_records(df) -> list[dict]:
    """yfinance financials are line items × period. Transpose to period rows."""
    if df is None or df.empty:
        return []
    df = df.transpose()
    return _df_records(df)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_profile(ticker: str) -> dict:
    t = yf.Ticker(ticker)
    info = t.info or {}
    if not info or not _safe(info, "symbol"):
        raise LookupError(f"ticker not found: {ticker}")
    return {
        "ticker": ticker.upper(),
        "as_of": _today(),
        "name": _safe(info, "longName") or _safe(info, "shortName"),
        "sector": _safe(info, "sector"),
        "industry": _safe(info, "industry"),
        "country": _safe(info, "country"),
        "currency": _safe(info, "currency"),
        "exchange": _safe(info, "exchange"),
        "market_cap": _safe(info, "marketCap"),
        "employees": _safe(info, "fullTimeEmployees"),
        "website": _safe(info, "website"),
        "summary": _safe(info, "longBusinessSummary"),
    }


def cmd_quote(ticker: str) -> dict:
    t = yf.Ticker(ticker)
    info = t.info or {}
    if not info or not _safe(info, "symbol"):
        raise LookupError(f"ticker not found: {ticker}")
    price = _safe(info, "regularMarketPrice") or _safe(info, "currentPrice")
    prev = _safe(info, "regularMarketPreviousClose") or _safe(info, "previousClose")
    change = (price - prev) if (price is not None and prev is not None) else None
    change_pct = (change / prev * 100) if (change is not None and prev) else None
    return {
        "ticker": ticker.upper(),
        "as_of": _today(),
        "price": price,
        "previous_close": prev,
        "change": change,
        "change_pct": change_pct,
        "day_high": _safe(info, "dayHigh"),
        "day_low": _safe(info, "dayLow"),
        "volume": _safe(info, "regularMarketVolume") or _safe(info, "volume"),
        "currency": _safe(info, "currency"),
    }


def cmd_multiples(ticker: str) -> dict:
    t = yf.Ticker(ticker)
    info = t.info or {}
    if not info or not _safe(info, "symbol"):
        raise LookupError(f"ticker not found: {ticker}")
    return {
        "ticker": ticker.upper(),
        "as_of": _today(),
        "trailing_pe": _safe(info, "trailingPE"),
        "forward_pe": _safe(info, "forwardPE"),
        "ev_ebitda": _safe(info, "enterpriseToEbitda"),
        "ev_revenue": _safe(info, "enterpriseToRevenue"),
        "ps": _safe(info, "priceToSalesTrailing12Months"),
        "pb": _safe(info, "priceToBook"),
        "peg": _safe(info, "pegRatio") or _safe(info, "trailingPegRatio"),
        "market_cap": _safe(info, "marketCap"),
        "enterprise_value": _safe(info, "enterpriseValue"),
        "shares_outstanding": _safe(info, "sharesOutstanding"),
        "trailing_eps": _safe(info, "trailingEps"),
        "forward_eps": _safe(info, "forwardEps"),
        "dividend_yield": _safe(info, "dividendYield"),
        "currency": _safe(info, "currency"),
    }


def cmd_financials(ticker: str, period: str = "annual") -> dict:
    t = yf.Ticker(ticker)
    if period == "quarterly":
        income = t.quarterly_financials
        balance = t.quarterly_balance_sheet
        cash = t.quarterly_cashflow
    else:
        income = t.financials
        balance = t.balance_sheet
        cash = t.cashflow
    if income is None or income.empty:
        raise LookupError(f"no financials for ticker: {ticker}")
    info = t.info or {}
    return {
        "ticker": ticker.upper(),
        "as_of": _today(),
        "period": period,
        "currency": _safe(info, "financialCurrency") or _safe(info, "currency"),
        "income_statement": _financials_df_to_records(income),
        "balance_sheet": _financials_df_to_records(balance),
        "cash_flow": _financials_df_to_records(cash),
    }


def cmd_prices(ticker: str, period: str = "1y", interval: str = "1d") -> dict:
    t = yf.Ticker(ticker)
    hist = t.history(period=period, interval=interval, auto_adjust=False)
    if hist is None or hist.empty:
        raise LookupError(f"no price history for ticker: {ticker}")
    return {
        "ticker": ticker.upper(),
        "as_of": _today(),
        "period": period,
        "interval": interval,
        "rows": _df_records(hist),
    }


def cmd_dividends(ticker: str) -> dict:
    t = yf.Ticker(ticker)
    series = t.dividends
    if series is None or series.empty:
        return {"ticker": ticker.upper(), "as_of": _today(), "dividends": []}
    items = [
        {"date": idx.date().isoformat(), "amount": float(val)}
        for idx, val in series.items()
    ]
    return {"ticker": ticker.upper(), "as_of": _today(), "dividends": items}


def cmd_splits(ticker: str) -> dict:
    t = yf.Ticker(ticker)
    series = t.splits
    if series is None or series.empty:
        return {"ticker": ticker.upper(), "as_of": _today(), "splits": []}
    items = [
        {"date": idx.date().isoformat(), "ratio": float(val)}
        for idx, val in series.items()
    ]
    return {"ticker": ticker.upper(), "as_of": _today(), "splits": items}


def cmd_peers(ticker: str) -> dict:
    """Best-effort sector peers.

    yfinance doesn't have a first-class peers API; we use the recommendations
    fallback (related tickers in the same sector) when present, otherwise we
    fall back to a known curated list for a few mega-cap sectors. The agent
    should treat this as a starting point and refine.
    """
    t = yf.Ticker(ticker)
    info = t.info or {}
    if not info or not _safe(info, "symbol"):
        raise LookupError(f"ticker not found: {ticker}")
    # yfinance exposes `recommendations` (analyst notes, not peers) and
    # `get_recommendations_summary`. Some versions ship `get_related_tickers`.
    peers: list[str] = []
    for attr in ("get_related_tickers", "related_tickers"):
        fn = getattr(t, attr, None)
        if callable(fn):
            try:
                peers = list(fn() or [])
                break
            except Exception:
                pass
        elif fn is not None:
            try:
                peers = list(fn or [])
                break
            except Exception:
                pass
    return {
        "ticker": ticker.upper(),
        "as_of": _today(),
        "sector": _safe(info, "sector"),
        "industry": _safe(info, "industry"),
        "peers": [p.upper() for p in peers if p],
        "note": "yfinance peers are best-effort. For a cleaner peer set, look up the company's industry on its 10-K (see sec-edgar) or pick a curated list of large-cap names in the same sector.",
    }


def cmd_options(ticker: str, expiry: str | None = None) -> dict:
    t = yf.Ticker(ticker)
    expiries = list(t.options or [])
    if not expiries:
        raise LookupError(f"no options chain for ticker: {ticker}")
    if expiry is None:
        return {"ticker": ticker.upper(), "as_of": _today(), "expiries": expiries}
    if expiry not in expiries:
        raise LookupError(f"expiry not found: {expiry} (available: {expiries[:5]}...)")
    chain = t.option_chain(expiry)
    return {
        "ticker": ticker.upper(),
        "as_of": _today(),
        "expiry": expiry,
        "calls": _df_records(chain.calls),
        "puts": _df_records(chain.puts),
    }


def cmd_analyst(ticker: str) -> dict:
    t = yf.Ticker(ticker)
    info = t.info or {}
    if not info or not _safe(info, "symbol"):
        raise LookupError(f"ticker not found: {ticker}")
    recs = None
    try:
        recs_df = t.recommendations
        if recs_df is not None and not recs_df.empty:
            recs = _df_records(recs_df.tail(20))
    except Exception:
        recs = None
    return {
        "ticker": ticker.upper(),
        "as_of": _today(),
        "recommendation_mean": _safe(info, "recommendationMean"),
        "recommendation_key": _safe(info, "recommendationKey"),
        "number_of_analyst_opinions": _safe(info, "numberOfAnalystOpinions"),
        "target_mean_price": _safe(info, "targetMeanPrice"),
        "target_high_price": _safe(info, "targetHighPrice"),
        "target_low_price": _safe(info, "targetLowPrice"),
        "target_median_price": _safe(info, "targetMedianPrice"),
        "earnings_growth": _safe(info, "earningsGrowth"),
        "revenue_growth": _safe(info, "revenueGrowth"),
        "recent_recommendations": recs,
    }


def cmd_info(ticker: str) -> dict:
    t = yf.Ticker(ticker)
    info = t.info or {}
    if not info or not _safe(info, "symbol"):
        raise LookupError(f"ticker not found: {ticker}")
    # NaN-clean the dict.
    cleaned = {}
    for k, v in info.items():
        try:
            if v != v:
                v = None
        except TypeError:
            pass
        cleaned[k] = v
    return {"ticker": ticker.upper(), "as_of": _today(), "info": cleaned}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

COMMANDS = {
    "profile": (cmd_profile, ["ticker"]),
    "quote": (cmd_quote, ["ticker"]),
    "multiples": (cmd_multiples, ["ticker"]),
    "financials": (cmd_financials, ["ticker", "--period"]),
    "prices": (cmd_prices, ["ticker", "--period", "--interval"]),
    "dividends": (cmd_dividends, ["ticker"]),
    "splits": (cmd_splits, ["ticker"]),
    "peers": (cmd_peers, ["ticker"]),
    "options": (cmd_options, ["ticker", "--expiry"]),
    "analyst": (cmd_analyst, ["ticker"]),
    "info": (cmd_info, ["ticker"]),
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="yfin.py", description="Yahoo Finance driver.")
    sub = p.add_subparsers(dest="cmd", required=True)

    for name in ("profile", "quote", "multiples", "dividends", "splits", "peers", "analyst", "info"):
        sp = sub.add_parser(name)
        sp.add_argument("ticker")

    sp = sub.add_parser("financials")
    sp.add_argument("ticker")
    sp.add_argument("--period", choices=["annual", "quarterly"], default="annual")

    sp = sub.add_parser("prices")
    sp.add_argument("ticker")
    sp.add_argument("--period", default="1y")
    sp.add_argument("--interval", default="1d")

    sp = sub.add_parser("options")
    sp.add_argument("ticker")
    sp.add_argument("--expiry", default=None)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 2

    fn, _ = COMMANDS[args.cmd]
    try:
        if args.cmd == "financials":
            result = fn(args.ticker, period=args.period)
        elif args.cmd == "prices":
            result = fn(args.ticker, period=args.period, interval=args.interval)
        elif args.cmd == "options":
            result = fn(args.ticker, expiry=args.expiry)
        else:
            result = fn(args.ticker)
    except LookupError as e:
        print(json.dumps({"error": str(e), "hint": "check the ticker symbol; non-US tickers need exchange suffix (.TO, .L, .HK, .T)"}))
        return 1
    except Exception as e:
        print(json.dumps({"error": f"{type(e).__name__}: {e}", "hint": "yfinance call failed; Yahoo may be throttling — retry after a few seconds"}))
        return 1
    print(json.dumps(result, default=str, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
