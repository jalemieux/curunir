"""Anchor helper for task C1: live forward-P/E gap between two tickers.

Recomputes |forward_pe(a) - forward_pe(b)| from the SAME yfinance CLI the agent
uses, so the expected gap stays valid as prices move. Prints {"gap": <float>}.

    python eval/finance/_pe_gap.py PEP KO
"""

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def fwd_pe(ticker: str) -> float:
    out = subprocess.run(
        ["python", "skills/yfinance/yfin.py", "multiples", ticker],
        capture_output=True, text=True, timeout=60, cwd=REPO,
    ).stdout
    return float(json.loads(out)["forward_pe"])


def main() -> None:
    a, b = sys.argv[1], sys.argv[2]
    print(json.dumps({"gap": round(abs(fwd_pe(a) - fwd_pe(b)), 4)}))


if __name__ == "__main__":
    main()
