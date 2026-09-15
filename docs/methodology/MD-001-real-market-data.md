# Real market-data adapters  (ID: MD-001)

| Field | Value |
|---|---|
| Version | 1.1.0 |
| Owner | Market Data |
| Approval status | Draft |
| Code | `novera.market_data.adapters`, `novera.market_data.crises`, `novera.market_data.catalogue` |
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

## Source catalogue
`novera.market_data.catalogue.market_data_sources` classifies every stored risk factor and
serves the dashboard's "Market data" page and `GET /reference/market-data-sources`:

| Status | Meaning | Decided from |
|---|---|---|
| REAL | real observations were merged by `novera fetch` | a `market_provenance` row for the factor |
| AVAILABLE | an adapter maps the factor but nothing was fetched | the adapters' own symbol maps |
| SYNTHETIC | no adapter maps it; the simulator (SIM-001) is its only source | neither of the above |

A family (curve, surface, cube or single factor) is PARTIAL when its nodes differ, for
example a commodity curve whose front month is wired and whose later tenors are not.
Statuses are never set by hand; adding a symbol to an adapter changes the page.

Each factor group also carries a free and a paid candidate source, maintained in the same
module. They are reference text, reviewed 2026-09-15, not a claim that the source was
tested. Summary of the gaps: no free daily history exists for OTC FX vol surfaces,
swaption cubes and their SABR smiles, CDS indices or single-name CDS; equity and commodity vol have free
at-the-money indices (VIX, VSTOXX, OVX, GVZ) but no free surface history; non-USD rate
curves have free government or OIS curves from central banks but no free swap curve.

## Validation tests
`tests/test_operations.py::test_adapters_with_recorded_responses` (statuses move to REAL
after a recorded fetch), `test_source_catalogue_covers_every_factor_and_follows_the_adapter_maps`,
`test_named_crises_require_coverage`; `tests/test_api.py` checks the route and its schema.

## Change history
| Version | Date | Change | Author |
|---------|------|--------|--------|
| 1.0.0 | 2026-09-14 | FRED, Yahoo and Coinbase adapters, named crises | Novera |
| 1.1.0 | 2026-09-15 | Source catalogue: status per factor and family, free and paid candidates, dashboard page and route | Novera |
