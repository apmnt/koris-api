# Tests

Run the offline regressions:

```bash
uv run pytest -q tests
```

Fixtures cover Basket.fi metadata, Genius HTML/LiveStats, BasketHotel, and
Sportradar. Pipeline tests check request sharing, input immutability, source
identity, player/shot linkage, retry limits, partial failure, raw checkpoint reuse,
resume selection, and stable ordering under concurrent completion.

Live checks are opt-in. When enabled, request failures fail the tests rather than
being silently skipped:

```bash
RUN_INTEGRATION_TESTS=1 uv run pytest tests/test_integration.py -v
RUN_PERFORMANCE_TESTS=1 uv run pytest tests/test_performance_cli.py -v
```

Full-season tests download 2010–11, the current season, and 2024–25 box scores.
They verify every selected game, player scoring totals, and failure accounting.
Incomplete archived records must retain their source data, report the failure,
and produce a nonzero CLI exit status. `KORIS_PERF_MAX_SECONDS` optionally limits
each run. Override current seasons with `KORIS_LIVE_SEASON` (integration) or
`KORIS_PERF_MODERN_SEASON` (full-season). These checks require working upstream
services and can take substantially longer than fixture tests.

Offline tests block requests/httpx/urllib HTTP calls unless the test has the
`integration` marker. Transport fakes still exercise retry and cache behavior.
Coverage includes handwritten production code, including the TUI; generated
protobuf bindings are omitted:

```bash
uv run pytest -q tests --cov=koris_api --cov-report=term-missing
```

`tests/generate_fixtures.py` captures live responses into a separate staging
directory; it does not overwrite regression fixtures by default:

```bash
uv run python tests/generate_fixtures.py --output-dir /tmp/koris-api-fixtures
```

Review source IDs and diffs before replacing fixtures. The real LiveStats
`2514938` fixture covers official team rebounds, player minutes, events, and shot
linkage. The Sportradar `incomplete_roster.json` fixture preserves an upstream
final game with a 76-point home total but only 60 points in its player rows.
See [the test audit](../docs/test-audit.md) for measured coverage, script checks,
and known limits.

Run the pinned lint/type tools separately or use the warning ratchet:

```bash
uv run ruff check src tests
uv run basedpyright
uv run python scripts/check_quality.py
```

Basedpyright uses standard checks for src/ and tests/ and strict checks for the
pipeline, converters, analysis, typed parsers, and CLI. Generated protobuf
bindings are ignored. Scripts and modeling code are outside this check's scope.
The current configuration reports zero errors and warnings. The quality script
requires Ruff to pass and rejects new type errors or warnings against the zero
baseline in docs/quality-baseline.json.
