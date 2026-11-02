"""Select a single active tournament stage without falling back to historical stages."""
from datetime import datetime, timezone

from .tournament_events import _date


def current_stages(stages, now):
    candidates = []
    for index, stage in enumerate(stages):
        matches = stage.get('matches') or []
        live = any(match.get('status') == 'running' for match in matches)
        complete = bool(matches) and all(match.get('status') in {'finished', 'canceled'} for match in matches)
        if complete:
            continue
        begin, end = _date(stage.get('begin_at')), _date(stage.get('end_at'))
        starts = [date for match in matches
                  if (date := _date(match.get('scheduled_at') or match.get('begin_at')))]
        begin = begin or min(starts, default=None)
        # A running match is stronger evidence than a stale scheduled end date.
        if not live and end and end < now:
            continue
        played = any(match.get('status') == 'finished' for match in matches)
        started = (begin is not None and begin <= now) or played
        if live or started:
            # Prefer live play, then the most recently started stage if dates overlap.
            candidates.append((live, begin or datetime.min.replace(tzinfo=timezone.utc), index, stage))
    return [max(candidates, key=lambda item: item[:3])[3]] if candidates else []
