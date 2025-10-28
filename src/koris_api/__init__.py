import argparse
import json
from pathlib import Path
from .api import KorisAPI

__version__ = "0.1.0"


def main() -> None:
    """CLI entry point for koris-api."""
    parser = argparse.ArgumentParser(
        description="Access Koripallo API from command line"
    )
    parser.add_argument(
        "--action",
        choices=["matches", "team", "match", "category"],
        required=True,
        help="Action to perform",
    )
    parser.add_argument(
        "--competition-id",
        default="huki2526",
        help="Competition ID (default: huki2526)",
    )
    parser.add_argument(
        "--category-id", default="4", help="Category ID (default: 4 for Korisliiga)"
    )
    parser.add_argument("--team-id", help="Team ID for team info")
    parser.add_argument("--match-id", help="Match ID for match details")
    parser.add_argument("--output", help="Output file path (default: print to stdout)")

    args = parser.parse_args()

    try:
        if args.action == "matches":
            data = KorisAPI.get_matches(
                competition_id=args.competition_id, category_id=args.category_id
            )
        elif args.action == "team":
            if not args.team_id:
                parser.error("--team-id is required for team action")
            data = KorisAPI.get_team(args.team_id)
        elif args.action == "match":
            if not args.match_id:
                parser.error("--match-id is required for match action")
            data = KorisAPI.get_match(args.match_id)
        elif args.action == "category":
            data = KorisAPI.get_category(args.competition_id, args.category_id)
        else:
            parser.error(f"Unknown action: {args.action}")
            return

        # Output the data
        json_str = json.dumps(data, indent=2, ensure_ascii=False)

        if args.output:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json_str, encoding="utf-8")
            print(f"Data saved to {output_path}")
        else:
            print(json_str)

    except Exception as e:
        print(f"Error: {e}")
