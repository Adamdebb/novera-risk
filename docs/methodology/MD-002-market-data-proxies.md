# Market-data proxies  (ID: MD-002)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Market Data |
| Approval status | Draft |
| Code | `novera.market_data.proxies.apply_proxies`, `apply_stored_proxies` |
| Last validated | 2026-09-14 |

## Definition
Before pricing, the EOD run derives a proxied copy of the raw snapshot:

1. **Interpolated** — a missing curve or surface node (`IR:`, `CMD:`, `VOL:`, `SWVOL:`
   families) is filled linearly in tenor/expiry from its neighbours on the same curve (same
   moneyness or tenor slice for surfaces and cubes), flat beyond the ends.
2. **Rolled** — a missing single factor (FX, equity, index, credit spread, crypto) is carried
   from the previous business day's snapshot.
3. **Re-levelled** — a stale family (observed before the business date) is moved with a proxy
   family of the same type since the stale date: relative mean move for surfaces, cubes and
   commodity curves, absolute node move for zero curves. Preferred proxies are listed in
   `PROXY_PREFERENCE` (EUR/USD vol → GBP/USD vol, …); otherwise the first non-stale family of
   the same type. Without a usable reference on the stale date the values are kept and the
   action is recorded as `KEPT_STALE`.

Every action (factor, kind, source, original, value, reason) is stored with the run as the
`md_proxies` frame and surfaces as an INFO finding (`MD_PROXY_APPLIED`) or a MINOR finding
(`MD_PROXY_UNAVAILABLE`). The MAJOR findings on the raw snapshot are kept, so the run
verdict still reflects the underlying data problem.

## Governance
The raw snapshot id is the run's `market_snapshot_id`; the proxied snapshot is rebuilt
deterministically from the stored actions (`DuckDBRepository.load_run_market`) for every
downstream engine (counterparty exposure, regulatory capital, what-ifs, reconciliation), so
all numbers of a run come from the same priced market. `NOVERA_PROXIES_ENABLED=false` or
`EODConfig(proxies=False)` prices off the raw snapshot.

## Limitations
Linear interpolation ignores curve shape between nodes; re-levelling assumes the stale family
moved with its proxy (basis risk is not measured). Proxies do not change the regulatory
non-modellable risk-factor classification (REG-002), which still keys off the raw findings.

## Validation tests
`test_proxies_interpolate_roll_and_relevel`: the two planted problems (missing USD 7Y, stale
EUR/USD surface) are interpolated and re-levelled with GBP/USD; a missing spot rolls; stored
actions rebuild the identical snapshot; clean snapshots are untouched.
