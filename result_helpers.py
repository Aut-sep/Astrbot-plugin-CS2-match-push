"""Validate complete match scores without guessing missing results."""


def final_score(match: dict) -> tuple[int, int] | None:
    opponents = [(slot.get("opponent") or {}).get("id") for slot in match.get("opponents") or []]
    if len(opponents) != 2 or None in opponents:
        return None
    scores = {str(row.get("team_id")): row.get("score") for row in match.get("results") or []}
    values = tuple(scores.get(str(tid)) for tid in opponents)
    if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in values):
        return None
    if match.get("forfeit"):
        return values if values[0] != values[1] else None
    bo = match.get("number_of_games")
    if isinstance(bo, bool) or not isinstance(bo, int) or bo < 1:
        return None
    if match.get("draw"):
        return values if values[0] == values[1] else None
    needed = bo // 2 + 1
    if max(values) != needed or min(values) >= needed:
        return None
    winner = (match.get("winner") or {}).get("id")
    if winner is not None and str(winner) != str(opponents[values.index(max(values))]):
        return None
    return values
