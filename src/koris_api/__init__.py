import requests

from .basketfi_api import BasketFiAPI
from .baskethotel_api import BasketHotelAPI
from .genius_api import GeniusSportsAPI
from .services.baskethotel_season_boxscores import (
    download_baskethotel_season_boxscores,
)
from .services.common import (
    _fetch_baskethotel_schedule_game_ids,
    _fetch_baskethotel_team_map,
    _resolve_baskethotel_season_id,
    load_genius_ids,
)
from .services.league_boxscores import (
    download_league_all_seasons,
    download_league_boxscores_all_seasons,
)
from .services.league_comprehensive import download_league_comprehensive
from .services.old_games import (
    download_old_game,
    download_old_games_bulk,
    download_old_games_from_file,
)
from .services.players import download_players_by_team, download_players_season
from .services.season_advanced_averages import download_season_advanced_averages
from .services.season_boxscores import (
    download_matches_with_boxscores,
    retry_advanced_boxscores_404s,
)
from .services.season_comprehensive import download_season_comprehensive
from .services.season_game_leaders import download_season_game_leaders
from .services.team_season import download_team_season
from .cli import main

# Backward compatibility alias
KorisAPI = BasketFiAPI

__version__ = "0.1.0"
__all__ = [
    "BasketFiAPI",
    "KorisAPI",  # Backward compatibility
    "BasketHotelAPI",
    "GeniusSportsAPI",
    "requests",
    "_fetch_baskethotel_schedule_game_ids",
    "_fetch_baskethotel_team_map",
    "_resolve_baskethotel_season_id",
    "load_genius_ids",
    "download_season_comprehensive",
    "download_baskethotel_season_boxscores",
    "download_matches_with_boxscores",
    "download_team_season",
    "download_league_comprehensive",
    "download_league_boxscores_all_seasons",
    "download_league_all_seasons",
    "download_season_advanced_averages",
    "download_season_game_leaders",
    "download_players_season",
    "download_players_by_team",
    "download_old_game",
    "download_old_games_bulk",
    "download_old_games_from_file",
    "retry_advanced_boxscores_404s",
    "main",
]
