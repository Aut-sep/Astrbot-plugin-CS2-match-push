"""Build CS stage diagrams only from explicit advancement relationships."""
from copy import deepcopy
import re

from .formatters import translate_match_stage
from .team_logos import normalize_name
from .swiss_data import build_swiss


# Use recognizable CS names rather than the data source's arbitrary acronyms.
# Unknown teams keep their full name instead of inventing a new abbreviation.
_TEAM_NAMES = {
    'Spirit': ('Team Spirit', 'Spirit'),
    'NAVI': ('Natus Vincere', 'NAVI'),
    'Vitality': ('Team Vitality', 'Vitality'),
    'Falcons': ('Team Falcons', 'Falcons', 'Falcons Esports'),
    'Liquid': ('Team Liquid', 'Liquid'),
    'Aurora': ('Aurora Gaming', 'Aurora'),
    'BetBoom': ('BetBoom Team', 'BetBoom'),
    'The MongolZ': ('The MongolZ', 'MongolZ'),
    'G2': ('G2 Esports', 'G2'),
    'FaZe': ('FaZe Clan', 'FaZe'),
    'VP': ('Virtus.pro', 'Virtus Pro', 'VP'),
    'NIP': ('Ninjas in Pyjamas', 'NIP'),
    'paiN': ('paiN Gaming', 'paiN'),
    'FURIA': ('FURIA Esports', 'FURIA'),
    'FUT': ('FUT Esports', 'FUT'),
    'GamerLegion': ('GamerLegion', 'Gamer Legion'),
    'Complexity': ('Complexity Gaming', 'Complexity'),
    'MOUZ': ('mousesports', 'MOUZ'),
    'PARIVISION': ('PARIVISION',),
    'HEROIC': ('HEROIC',),
    'TYLOO': ('TYLOO',),
    '9z': ('9z Team', '9z Globant', '9z'),
    'Legacy': ('Legacy Esports', 'Legacy'),
    'B8': ('B8 Esports', 'B8'),
    'MIBR': ('Made in Brazil', 'MIBR'),
    'NRG': ('NRG Esports', 'NRG'),
    'ENCE': ('ENCE eSports', 'ENCE'),
    'SINNERS': ('SINNERS Esports', 'SINNERS'),
    'Nemiga': ('Nemiga Gaming', 'Nemiga'),
    'Sangal': ('Sangal Esports', 'Sangal'),
    'Sashi': ('Sashi Esport', 'Sashi Esports', 'Sashi'),
}
_TEAM_LABELS = {normalize_name(alias): label
                for label, aliases in _TEAM_NAMES.items() for alias in aliases}


def bracket_team_name(team):
    name = str(team.get('name') or '').strip()
    if not name or name.casefold() in {'tbd', 'to be determined'}:
        return '待定'
    return _TEAM_LABELS.get(normalize_name(name), name)


def _order(match):
    return (match.get('scheduled_at') or match.get('begin_at') or '', str(match['id']))


def build_stage(stage, matches, *, kind_title):
    """Never infer advancement from dates, seeds, winners or list position."""
    kind, title = kind_title
    unique = {}
    for match in matches:
        mid = str(match.get('id') or '')
        if not mid or mid in unique:
            raise ValueError('invalid or duplicate bracket match ID')
        if match.get('tournament_id') is not None and str(match['tournament_id']) != str(stage['id']):
            raise ValueError('mixed tournament bracket response')
        unique[mid] = deepcopy(match)
    rows = sorted(unique.values(), key=_order)
    edges, notes = [], []
    for row in rows:
        if row.get('previous_matches') is not None and not isinstance(row.get('previous_matches'), list):
            raise ValueError('invalid bracket predecessors')
        for link in row.get('previous_matches') or []:
            if not isinstance(link, dict):
                raise ValueError('invalid bracket link')
            origin = str(link.get('match_id') or '')
            route = link.get('type')
            if origin not in unique or route not in {'winner', 'loser'}:
                notes.append('部分晋级关系暂未提供')
                continue
            if origin == str(row['id']):
                raise ValueError('self-referencing bracket')
            edge = {'source': origin, 'target': str(row['id']), 'type': route}
            if edge not in edges:
                edges.append(edge)
    if kind == 'swiss':
        # Swiss pairings are redrawn. They are not a fixed elimination tree.
        edges = []
        notes.append('按赛前战绩分组；标签表示胜负去向，不预设后续对手')
        rows, swiss_groups, unknown = build_swiss(rows, title)
        columns = [(group['record'], group['matches']) for group in swiss_groups if group['matches']]
        if unknown:
            columns.append(('战绩组待确认', unknown))
    elif edges:
        # Longest path creates columns for both winner and loser routes.
        ranks, todo = {}, set(unique)
        while todo:
            progressed = []
            for mid in sorted(todo):
                parents = [edge['source'] for edge in edges if edge['target'] == mid]
                if all(parent in ranks for parent in parents):
                    ranks[mid] = max((ranks[p] + 1 for p in parents), default=0)
                    progressed.append(mid)
            if not progressed:
                raise ValueError('cyclic bracket response')
            todo.difference_update(progressed)
        columns = []
        for rank in range(max(ranks.values(), default=-1) + 1):
            current = [row for row in rows if ranks[str(row['id'])] == rank]
            if kind == 'knockout':
                captions = list(dict.fromkeys(translate_match_stage(re.sub(r'\s+\d+$', '', row.get('name', '').split(':')[0])) for row in current))
                label = ' / '.join(captions) or f'晋级轮次 {rank + 1}'
            else:
                label = '首轮' if rank == 0 else '晋级对阵'
            columns.append((label, current))
    else:
        notes.append('晋级关系尚未公布，暂不绘制连线')
        columns = [('已公布对阵', rows)] if rows else []
    result = {'id': str(stage['id']), 'kind': kind, 'title': title,
            'status': stage.get('status'),
            'begin_at': stage.get('begin_at'), 'end_at': stage.get('end_at'),
            'columns': [{'label': label, 'matches': current} for label, current in columns],
            'edges': edges, 'notes': list(dict.fromkeys(notes)), 'matches': rows}
    if kind == 'swiss':
        result['swiss_groups'] = swiss_groups
        result['unclassified_matches'] = unknown
    return result


def format_bracket(snapshot, stages):
    lines = [f"【{snapshot['title']} · 晋级对阵图】"]
    for stage in stages:
        lines.extend(['', stage['title']])
        for column in stage['columns']:
            lines.append(column['label'])
            for match in column['matches']:
                names = [bracket_team_name(slot.get('opponent') or {})
                         for slot in match.get('opponents') or []][:2]
                names += ['待定'] * (2 - len(names))
                scores = {str(r.get('team_id')): r.get('score') for r in match.get('results') or []}
                participants = [(slot.get('opponent') or {}).get('id') for slot in match.get('opponents') or []][:2]
                score = [str(scores[str(tid)]) if tid is not None and isinstance(scores.get(str(tid)), int)
                         and not isinstance(scores.get(str(tid)), bool) and scores[str(tid)] >= 0 else '—' for tid in participants]
                show = match.get('status') in {'running', 'finished'} and len(score) == 2
                middle = ' : '.join(score) if show else 'vs'
                lines.append(f"  {translate_match_stage(match.get('name') or '')}：{names[0]} {middle} {names[1]}")
        lines.extend(stage['notes'])
        for group in stage.get('swiss_groups', []):
            if group['terminal'] and group['teams']:
                outcome = '晋级' if group['record'].startswith('3-') else '淘汰'
                lines.append(f"{group['record']} · {outcome}："+'、'.join(bracket_team_name(team) for team in group['teams']))
    return '\n'.join(lines)
