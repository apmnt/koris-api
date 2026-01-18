# KorisAPI

Create datasets from Basket.fi data with a Python command line tool or view statistics with a Textual TUI app.

## Features

- **CLI Tool**: Download match data, team information, and detailed statistics
- **Interactive TUI**: Browse matches, teams, and player statistics in a beautiful terminal interface
- **Basket.fi API**: Access team, league, and match data from Basket.fi with API requests
- **Advanced Stats**: Fetch detailed box scores and player stats from Genius Sports through HTML parsing
- **Multiple Export Formats**: Export data to JSON, CSV, or Excel
- **Concurrent Downloads**: Fast batch requests with parallel processing

## Installation

This project uses `uv` to build and run. Install `uv` for MacOS:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

or Windows:

```bash
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

After `uv` is installed, install the project by:

```bash
git clone https://github.com/apmnt/koris-api.git
cd koris-api
uv pip install -e ".[dev]"
```

## CLI Usage

Use the `koris-api` command with different actions. Run the help command to display all options:

```bash
uv run koris-api --help
```

## JSON Output Structure

Below is the full key layout for the sample output file in `eda/data/season_boxscores/korisliiga_2024-2025_sample.json`.

```mermaid
erDiagram
  OUTPUT_JSON ||--|| METADATA : has
  OUTPUT_JSON ||--o{ MATCH : has
  MATCH ||--|| BOXSCORE : has
  BOXSCORE ||--|| MATCH_TOTALS : has
  BOXSCORE ||--o{ TEAM : has
  TEAM ||--|| TEAM_TOTALS : has
  TEAM ||--o{ PLAYER : has

  METADATA {
    string category_id
    string season_name
    string download_date
    bool include_advanced_stats
    string_or_null league_id
    int limit_games
    int matches_failed
    int matches_with_boxscore
    int played_matches_saved
    string season_id
    string source
    int_or_null total_games_requested
    int total_matches_in_season
  }
  MATCH {
    string away_score
    string away_team
    string away_team_id
    string category
    string competition
    string date
    string home_score
    string home_team
    string home_team_id
    string match_external_id
    string match_id
    string season
    string status
    string time
    string venue
  }
  BOXSCORE {
    string source
  }
  MATCH_TOTALS {
    float k_2p_pct
    int k_2pa
    int k_2pm
    float k_3p_pct
    int k_3pa
    int k_3pm
    int ast
    int blk
    int def
    float ft_pct
    int fta
    int ftm
    int idx
    int off
    int pf
    int points
    int reb
    int stl
    int to
  }
  TEAM {
    string team_name
  }
  TEAM_TOTALS {
    float k_2p_pct
    int k_2pa
    int k_2pm
    float k_3p_pct
    int k_3pa
    int k_3pm
    int ast
    int blk
    int def
    float ft_pct
    int fta
    int ftm
    int idx
    int off
    int pf
    int points
    int reb
    int stl
    int to
  }
  PLAYER {
    int plus_minus
    float k_2p_pct
    int k_2pa
    int k_2pm
    float k_3p_pct
    int k_3pa
    int k_3pm
    int ast
    int blk
    int def
    float ft_pct
    int fta
    int ftm
    int idx
    string minutes
    int off
    int pf
    int points
    int reb
    int stl
    int to
    string player
    int player_number
    string team
  }
```

Legend for normalized keys (Mermaid ER diagrams require simple identifiers):
`k_2p_pct` = `2P%`, `k_2pa` = `2PA`, `k_2pm` = `2PM`, `k_3p_pct` = `3P%`, `k_3pa` = `3PA`, `k_3pm` = `3PM`, `ft_pct` = `FT%`, `idx` = `Index`, `plus_minus` = `+/-`.

## TUI Usage

Launch the interactive terminal user interface to browse matches, teams, and statistics. The TUI provides an interface for viewing and exporting game data.

### Starting the TUI

```bash
uv run koris-tui
```

## Python API Usage

You can also use KorisAPI directly in your Python code:

```python
from koris_api import KorisAPI

# Get all matches for a competition
matches = KorisAPI.get_matches(
    competition_id="huki2526",
    category_id="4"
)

# Get team information
team = KorisAPI.get_team(team_id="12345")

# Get match details
match = KorisAPI.get_match(match_id="2701885")

# Get advanced box score
boxscore = KorisAPI.get_match_boxscore(match_id="2701885")

# Get category information with seasons
category = KorisAPI.get_category(
    competition_id="huki2526",
    category_id="4"
)
```

## Dependencies:

- `requests`: HTTP client
- `textual`: TUI framework
- `pandas`: Data manipulation
- `openpyxl`: Excel file support
- `beautifulsoup4`: HTML parsing
- `lxml`: XML/HTML parser
- `tqdm`: Progress bars
