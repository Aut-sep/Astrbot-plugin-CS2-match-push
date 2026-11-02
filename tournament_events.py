"""Combine PandaScore tournament stages into one edition of a competition."""

from datetime import datetime, timezone


def event_name(tour: dict) -> str:
    league = str((tour.get("league") or {}).get("name") or "").strip()
    serie = tour.get("serie") or {}
    edition = str(serie.get("full_name") or serie.get("name") or "").strip()
    if not edition:
        edition = " ".join(str(serie[key]) for key in ("season", "year") if serie.get(key))
    if league and league.casefold() not in edition.casefold():
        return " ".join(part for part in (league, edition) if part)
    return edition or league or "未知赛事"


def _date(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError, AttributeError):
        return None


def build_event(seed: dict, stages: list[dict]) -> dict | None:
    serie = seed.get("serie") or {}
    sid = serie.get("id") or seed.get("serie_id")
    if not sid or not stages:
        return None
    # Never combine different editions, even when they share a league name.
    stages = [stage for stage in stages if
              str((stage.get("serie") or {}).get("id") or stage.get("serie_id")) == str(sid)]
    if not stages:
        return None
    fresh_serie = next((s["serie"] for s in stages if s.get("serie")), {})
    serie = {**serie, **{key: value for key, value in fresh_serie.items() if value is not None}}
    starts = [dt for s in stages if (dt := _date(s.get("begin_at")))]
    ends = [dt for s in stages if (dt := _date(s.get("end_at")))]
    begin = _date(serie.get("begin_at")) or (min(starts) if starts else None)
    if not begin:
        return None
    teams = {}
    for stage in stages:
        for team in stage.get("teams") or []:
            if isinstance(team, dict) and team.get("name"):
                key = ("id", str(team["id"])) if team.get("id") else ("name", team["name"].casefold())
                teams[key] = {**teams.get(key, {}), **team}
    tiers = [str(s.get("tier") or "unranked").lower() for s in stages]
    rank = {value: i for i, value in enumerate(("s", "a", "b", "c", "d", "unranked"))}
    event = {**seed, "id": str(sid), "serie": serie,
             "begin_at": begin.isoformat(),
             "end_at": (_date(serie.get("end_at")) or (max(ends) if ends else begin)).isoformat(),
             "tier": min(tiers, key=lambda value: rank.get(value, 99)),
             "teams": sorted(teams.values(), key=lambda team: team["name"].casefold()),
             "stage_ids": [s["id"] for s in stages if s.get("id")]}
    event["name"] = event_name(event)
    # Country/prize pool on a single stage need not describe the whole edition.
    for key in ("country", "location", "prize_pool"):
        event.pop(key, None)
    return event
