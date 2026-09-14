# Real market-data adapters  (ID: MD-001)

| Field | Value |
|---|---|
| Version | 1.0.0 |
| Owner | Market Data |
| Approval status | Draft |
| Code | `novera.market_data.adapters`, `novera.market_data.crises` |
| Last validated | 2026-09-14 (Yahoo and Coinbase checked live; FRED with recorded responses) |

## Sources and mapping
| Source | Factors | Notes |
|---|---|---|
| FRED (key required) | `IR:USD:*` from DGS1MO..DGS30 | Treasury constant-maturity par yields used as zero rates; 15Y interpolated |
| Yahoo Finance chart | equity indices and names, G10 and EM FX, commodity front months | front month fills the 1M node only |
| Coinbase Exchange | `CRYPTO:BTC`, `CRYPTO:ETH` | daily closes in USD |

`novera fetch` merges fetched rows into the history table, replacing synthetic values on
the same dates, and records provenance per factor and source. Everything not fetched
stays synthetic, including all vol surfaces, non-USD curves and CDS indices.

## Named crises
When the stored history covers them, these windows become HISTORICAL stress scenarios:
2008 GFC, 2011 euro sovereign, 2015 China, 2016 Brexit, 2020 COVID, 2022 rates shock,
2023 banking stress.

## Limitations
Mixed real and synthetic factors break the synthetic correlation structure across the
join; a run on mixed history is a demonstration of the plumbing, not a calibrated VaR.
Yahoo's endpoint is unofficial and may change. No holiday alignment across sources.

## Validation tests
`tests/test_operations.py::test_adapters_with_recorded_responses`, `test_named_crises_require_coverage`.
