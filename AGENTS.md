# CLI Smoke Test Notes

Date: 2026-02-08

Commands executed:
1. `uv run koris-api --help`  
Result: Help rendered correctly.

2. `uv run koris-api genius match 2514938 --output /tmp/genius_match_2514938.json`  
Result: Success. Output file created.

3. `uv run koris-api season-boxscores --category-id 4 --season-id huki2526 --limit-games 1 --output /tmp/season_boxscores_test.json`  
Result: Success. Output written to `/tmp/season_boxscores_test_huki2526.json` (season suffix added).

4. `uv run koris-api season-comprehensive --category-id 4 --season-id huki2526 --output /tmp/season_comprehensive_test.json --quiet`  
Result: Success (quiet). Output written to `/tmp/season_comprehensive_test_huki2526.json`.

5. `uv run koris-api season-baskethotel-boxscores --category-id 4 --season-id 2015-2016 --limit-games 1 --output /tmp/season_bh_test.json`  
Result: Success. Output written to `/tmp/season_bh_test_2015-2016.json`.

6. `uv run koris-api team-season --team-id 19281 --season-id 2024-2025 --output /tmp/team_season_test.json --quiet`  
Result: Success (quiet). Output written to `/tmp/team_season_test_19281_2024-2025.json`.

7. `uv run koris-api league-comprehensive --category-id 4 --output-dir /tmp/league_comp_test --season-id huki2526 --quiet`  
Result: Success (quiet). Output directory `/tmp/league_comp_test` populated.

8. `uv run koris-api league-boxscores-all-seasons --category-id 4 --output-dir /tmp/league_boxscores_test --limit-seasons 1 --quiet`  
Result: Success (quiet). Output directory `/tmp/league_boxscores_test` populated.

9. `uv run koris-api season-advanced-averages --category-id 4 --season-id huki2526 --output /tmp/season_avgs_test.json --cache-file /tmp/season_avgs_cache.json --quiet`  
Result: Success (quiet). Output written to `/tmp/season_avgs_test_huki2526.json`, cache to `/tmp/season_avgs_cache.json`.

10. `uv run koris-api season-game-leaders --category-id 4 --season-id 2015-2016 --output /tmp/season_leaders_test.json --quiet`  
Result: Success (quiet). Output written to `/tmp/season_leaders_test_2015-2016.json`.

11. `uv run koris-api retry-advanced-404s --input /tmp/does_not_exist.json`  
Result: Expected error: input file not found.

12. `uv run koris-api season-boxscores --category-id 4 --season-id huki2526 --adv-players --output iterative_data.json`  
Result: Success. Output written to `iterative_data_huki2526.json`. 50 advanced boxscore 404s recorded.

13. `uv run koris-api season-boxscores --category-id 4 --season-id huki2526 --adv-players --output iterative_data.json`  
Result: Resume behavior confirmed. No fetches attempted (0 pending). Output preserved with 50 failures.

14. `uv run pytest -q`  
Result: Failed during collection. `ModuleNotFoundError: No module named 'koris_api'` in `tests/test_golden.py`, `tests/test_integration.py`, `tests/test_performance_cli.py`.

15. `uv run pytest -q`  
Result: 35 passed, 2 skipped (performance tests skipped: set `RUN_PERFORMANCE_TESTS=1` to run).
