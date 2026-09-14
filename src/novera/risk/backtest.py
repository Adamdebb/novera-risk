"""VaR backtesting. Methodology record MR-011.

Static-portfolio hypothetical backtest: today's portfolio, its P&L under each of the last
N daily moves (the historical-VaR scenario vector, chronological), each day compared with
the VaR estimated from the preceding lookback window of the same vector. Tests:
Kupiec proportion-of-failures, Christoffersen independence and the conditional-coverage
combination, Basel traffic-light zone on 250 days.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd
from scipy import stats

MODEL_VERSION = "1.0.0"


@dataclass
class BacktestResult:
    kind: str  # STATIC_HYPOTHETICAL or LIVE
    confidence: float
    days: int
    exceptions: int
    expected_exceptions: float
    kupiec_lr: float
    kupiec_pvalue: float
    christoffersen_lr: float
    christoffersen_pvalue: float
    conditional_lr: float
    conditional_pvalue: float
    zone: str  # GREEN, AMBER, RED (Basel, scaled to 250 days)
    series: pd.DataFrame = field(repr=False)  # date, pnl, var, exception
    lookback_days: int = 0

    def summary(self) -> dict:
        return {
            "kind": self.kind,
            "confidence": self.confidence,
            "days": self.days,
            "exceptions": self.exceptions,
            "expected_exceptions": self.expected_exceptions,
            "kupiec_lr": self.kupiec_lr,
            "kupiec_pvalue": self.kupiec_pvalue,
            "christoffersen_lr": self.christoffersen_lr,
            "christoffersen_pvalue": self.christoffersen_pvalue,
            "conditional_lr": self.conditional_lr,
            "conditional_pvalue": self.conditional_pvalue,
            "zone": self.zone,
            "lookback_days": self.lookback_days,
        }


def kupiec_pof(exceptions: int, days: int, p: float) -> tuple[float, float]:
    """Likelihood-ratio test that the exception rate equals 1 - p."""
    if days == 0:
        return 0.0, 1.0
    q = 1.0 - p
    x, n = exceptions, days
    pi = x / n
    with np.errstate(divide="ignore", invalid="ignore"):
        ll0 = (n - x) * np.log(1 - q) + x * np.log(q)
        ll1 = (n - x) * np.log(1 - pi) + x * np.log(pi) if 0 < x < n else (0.0 if x == 0 else n * np.log(pi))
    lr = float(-2 * (ll0 - ll1))
    lr = max(lr, 0.0)
    return lr, float(1 - stats.chi2.cdf(lr, df=1))


def christoffersen_independence(hits: np.ndarray) -> tuple[float, float]:
    """LR test that exceptions do not cluster (first-order Markov)."""
    h = hits.astype(int)
    if len(h) < 2:
        return 0.0, 1.0
    n00 = int(((h[:-1] == 0) & (h[1:] == 0)).sum())
    n01 = int(((h[:-1] == 0) & (h[1:] == 1)).sum())
    n10 = int(((h[:-1] == 1) & (h[1:] == 0)).sum())
    n11 = int(((h[:-1] == 1) & (h[1:] == 1)).sum())
    pi01 = n01 / (n00 + n01) if n00 + n01 else 0.0
    pi11 = n11 / (n10 + n11) if n10 + n11 else 0.0
    pi = (n01 + n11) / max(n00 + n01 + n10 + n11, 1)

    def ll(p0, p1):
        with np.errstate(divide="ignore", invalid="ignore"):
            t = 0.0
            for cnt, prob in ((n00, 1 - p0), (n01, p0), (n10, 1 - p1), (n11, p1)):
                t += cnt * np.log(prob) if cnt and prob > 0 else 0.0
            return t

    lr = float(max(-2 * (ll(pi, pi) - ll(pi01, pi11)), 0.0))
    return lr, float(1 - stats.chi2.cdf(lr, df=1))


def traffic_light(exceptions: int, days: int) -> str:
    scaled = exceptions * 250 / max(days, 1)
    return "GREEN" if scaled < 5 else ("AMBER" if scaled < 10 else "RED")


def evaluate(series: pd.DataFrame, confidence: float, kind: str, lookback: int = 0) -> BacktestResult:
    s = series.dropna(subset=["pnl", "var"]).copy()
    s["exception"] = s["pnl"] < -s["var"]
    x, n = int(s["exception"].sum()), int(len(s))
    k_lr, k_p = kupiec_pof(x, n, confidence)
    c_lr, c_p = christoffersen_independence(s["exception"].to_numpy())
    cc_lr = k_lr + c_lr
    cc_p = float(1 - stats.chi2.cdf(cc_lr, df=2)) if n else 1.0
    return BacktestResult(
        kind,
        confidence,
        n,
        x,
        n * (1 - confidence),
        k_lr,
        k_p,
        c_lr,
        c_p,
        cc_lr,
        cc_p,
        traffic_light(x, n),
        s,
        lookback,
    )


def static_backtest(
    portfolio_pnl: pd.Series, confidence: float = 0.99, test_days: int = 250, lookback: int = 250
) -> BacktestResult:
    """``portfolio_pnl`` is the chronological scenario P&L vector of today's portfolio
    (index: scenario date). Needs at least ``lookback + test_days`` observations; otherwise
    the test window shrinks."""
    pnl = portfolio_pnl.sort_index()
    n = len(pnl)
    test_days = max(min(test_days, n - lookback), 0)
    rows = []
    values = pnl.to_numpy()
    for i in range(n - test_days, n):
        window = values[i - lookback : i]
        var = float(-np.percentile(window, (1 - confidence) * 100, method="linear"))
        rows.append({"date": pnl.index[i], "pnl": float(values[i]), "var": var})
    series = pd.DataFrame(rows, columns=["date", "pnl", "var"])
    return evaluate(series, confidence, "STATIC_HYPOTHETICAL", lookback)


def live_backtest(runs: list, confidence: float = 0.99) -> BacktestResult:
    """Each completed run's actual P&L against the previous run's VaR."""
    ordered = sorted(runs, key=lambda r: (r.business_date, r.started_at))
    rows: list[dict] = []
    prev = None
    seen: set[date] = set()
    for r in ordered:
        if r.business_date in seen:
            continue
        seen.add(r.business_date)
        if prev is not None and r.summary.get("pnl_total") is not None:
            rows.append(
                {
                    "date": r.business_date,
                    "pnl": float(r.summary["pnl_total"]),
                    "var": float(prev.summary["var"]),
                }
            )
        prev = r
    return evaluate(pd.DataFrame(rows, columns=["date", "pnl", "var"]), confidence, "LIVE")
