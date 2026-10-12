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

Performance tests download full 2010–11 and 2024–25 box-score seasons. Optional
runtime limits are `KORIS_PERF_MAX_SECONDS_2010` and `KORIS_PERF_MAX_SECONDS_2024`.
These are network checks and can take substantially longer than fixture tests.

`tests/generate_fixtures.py` refreshes live fixtures; review source IDs and the
resulting diffs before replacing regression data.

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
