"""Build local portrait variants from verified achievement metadata."""

import argparse
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "compact_player_portraits", ROOT / "player_portraits.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--font", type=Path, required=True)
    args = parser.parse_args()
    players = json.loads((ROOT / "qq_players.json").read_text(encoding="utf-8"))["players"]
    achievements = json.loads((ROOT / "player_achievements.json").read_text(encoding="utf-8"))[
        "players"
    ]
    assert set(achievements) == {player["id"] for player in players}
    for player in players:
        MODULE.build_compact_portrait(
            ROOT / player["image_file"],
            ROOT / "player_cards" / f"{player['id']}_compact.png",
            achievements[player["id"]]["caption"],
            args.font,
        )
    print(
        f"Built {len(players)} compact portraits: "
        f"{MODULE.DISPLAY_WIDTH}px headshots and {MODULE.CAPTION_FONT_SIZE}px captions"
    )


if __name__ == "__main__":
    main()
