"""Group published Swiss matches by pre-match record and derive confirmed outcomes."""
from copy import deepcopy
from .match_stages import ACTIVE_RECORDS, TERMINAL_RECORDS, round_number, swiss_record

CAPACITY = {'0-0': 8, '1-0': 4, '0-1': 4, '2-0': 2, '1-1': 4, '0-2': 2,
            '2-1': 3, '1-2': 3, '2-2': 3, '3-0': 2, '0-3': 2,
            '3-1': 3, '1-3': 3, '3-2': 3, '2-3': 3}


def team_key(team):
    return str(team['id']) if team.get('id') is not None else None


def confirmed_winner(match, teams):
    if match.get('status') != 'finished' or match.get('draw'):
        return None
    winner = match.get('winner_id') or (match.get('winner') or {}).get('id')
    ids = {team_key(team) for team in teams}
    if winner is not None:
        return str(winner) if str(winner) in ids else None
    scores = {str(row.get('team_id')): row.get('score') for row in match.get('results') or []}
    values = [scores.get(team_key(team)) for team in teams]
    if all(isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in values) and values[0] != values[1]:
        return team_key(teams[0 if values[0] > values[1] else 1])
    return None


def build_swiss(matches, title):
    rows = deepcopy(matches)
    groups = {record: {'record': record, 'matches': [], 'teams': [], 'capacity': CAPACITY[record],
                       'terminal': record in TERMINAL_RECORDS}
              for record in (*ACTIVE_RECORDS, *TERMINAL_RECORDS)}
    history, teams_by_id, unknown = {}, {}, []
    # Named rounds outrank a provider's shared/placeholder timestamps.
    ordered = sorted(rows, key=lambda row: (round_number(row) or 99,
                     row.get('begin_at') or row.get('scheduled_at') or '', str(row['id'])))
    for match in ordered:
        match['_stage_title'] = title
        teams = [(slot.get('opponent') or {}) for slot in match.get('opponents') or []][:2]
        ids = [team_key(team) for team in teams]
        teams_by_id.update({team_key(team): team for team in teams if team_key(team) is not None})
        record = swiss_record(match, known_swiss=True)
        if record is None and len(ids) == 2 and all(key in history for key in ids):
            left, right = (history[key] for key in ids)
            number = round_number(match)
            if left == right and f'{left[0]}-{left[1]}' in ACTIVE_RECORDS and number == sum(left)+1:
                record = f'{left[0]}-{left[1]}'
        if record is None:
            unknown.append(match)
            # An unresolved played match breaks the history chain for its teams.
            if match.get('status') in {'finished', 'running'}:
                for key in ids:history.pop(key, None)
            continue
        match['_swiss_record'] = record
        groups[record]['matches'].append(match)
        winner = confirmed_winner(match, teams) if len(ids) == 2 and len(set(ids)) == 2 and None not in ids else None
        if winner is not None:
            wins, losses = map(int, record.split('-'))
            for key in ids:
                history[key] = (wins+1, losses) if key == winner else (wins, losses+1)
    for key, record in history.items():
        label = f'{record[0]}-{record[1]}'
        if label in TERMINAL_RECORDS:
            groups[label]['teams'].append(teams_by_id[key])
    return rows, list(groups.values()), unknown
