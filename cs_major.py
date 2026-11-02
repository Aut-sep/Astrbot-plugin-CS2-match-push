"""Read-only CS Major discovery and advancement charts; never infer Swiss edges."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import re
import time

from .formatters import CST
from .bracket_data import build_stage
from .tournament_events import _date, event_name

STAGE_ALIASES = {'': 'current', '当前': 'current', 'current': 'current', '全部': 'all', 'all': 'all',
                 '第一阶段': 'stage1', '阶段1': 'stage1', 'stage1': 'stage1', '1': 'stage1',
                 '第二阶段': 'stage2', '阶段2': 'stage2', 'stage2': 'stage2', '2': 'stage2',
                 '第三阶段': 'stage3', '阶段3': 'stage3', 'stage3': 'stage3', '3': 'stage3',
                 '瑞士轮': 'swiss', 'swiss': 'swiss',
                 '淘汰赛': 'knockout', 'knockout': 'knockout', 'playoffs': 'knockout'}


def is_major(edition):
    league = edition.get('league') or {}
    text = ' '.join(str(edition.get(key) or '') for key in ('name', 'full_name', 'slug'))
    text += ' ' + ' '.join(str(league.get(key) or '') for key in ('name', 'slug'))
    if re.search(r'\b(?:qualifier\w*|rmr|minor|academy|women)\b|to[\s-]+major|资格|预选', text, re.I):
        return False
    return bool(re.search(r'\bmajor\b', text, re.I))


def select_edition(editions, now):
    dated = [(row, _date(row.get('begin_at')), _date(row.get('end_at'))) for row in editions if is_major(row)]
    dated = [(row, begin, end) for row, begin, end in dated if begin and (not end or end >= begin)]
    current = [item for item in dated if item[1] <= now <= (item[2] or item[1])]
    if current:
        return max(current, key=lambda item: item[1])[0], '正在进行'
    upcoming = [item for item in dated if item[1] > now]
    if upcoming:
        return min(upcoming, key=lambda item: item[1])[0], '即将开始'
    if dated:
        return max(dated, key=lambda item: item[2] or item[1])[0], '最近结束'
    return None, ''


def major_stage(stage):
    name = str(stage.get('name') or '').casefold()
    numbered = re.search(r'\bstage\s*([123])\b', name)
    if numbered:
        number = int(numbered[1])
        return f'stage{number}', ('swiss', f"第{'一二三'[number-1]}阶段 · 瑞士轮")
    if 'challenger' in name or 'opening' in name:
        return 'stage1', ('swiss', '第一阶段 · 瑞士轮')
    if 'legend' in name or 'elimination' in name:
        return 'stage2', ('swiss', '第二阶段 · 瑞士轮')
    if 'playoff' in name or 'champion' in name or 'knockout' in name:
        return 'knockout', ('knockout', '淘汰赛')
    if 'swiss' in name:
        return 'swiss', ('swiss', stage.get('name') or '瑞士轮')
    return 'other', ('other', stage.get('name') or '赛事阶段')


class MajorBracketService:
    def __init__(self, plugin):
        self.plugin = plugin
        self.lock = asyncio.Lock()
        self.cached = None
        self.cached_at = 0

    async def fetch(self):
        async with self.lock:
            now = datetime.now(timezone.utc)
            if self.cached and self.cached.get('date') == now.date().isoformat() and time.monotonic() - self.cached_at < 60:
                return deepcopy(self.cached)
            editions = await self.plugin.client.get_major_series(now.year)
            if editions is None:
                return None
            edition, state = select_edition(editions, now)
            if edition is None:
                return None
            tournaments = await self.plugin.client.get_series_tournaments(edition['id'])
            if tournaments is None:
                return None
            stages = []
            for tournament in tournaments:
                sid = (tournament.get('serie') or {}).get('id') or tournament.get('serie_id')
                if sid is not None and str(sid) != str(edition['id']):
                    raise ValueError('mixed Major editions')
                if re.search(r'\b(?:qualifier\w*|rmr|minor)\b', tournament.get('name') or '', re.I):
                    continue
                matches = await self.plugin.client.get_tournament_brackets(tournament['id'])
                if matches is None:
                    return None
                key, kind_title = major_stage(tournament)
                stage = build_stage(tournament, matches, kind_title=kind_title)
                stage['key'] = key
                stages.append(stage)
            order = {'stage1': 0, 'stage2': 1, 'stage3': 2, 'swiss': 3, 'knockout': 4, 'other': 5}
            stages.sort(key=lambda stage: order[stage['key']])
            title = event_name({'league': edition.get('league'), 'serie': edition})
            snapshot = {'date': now.date().isoformat(), 'serie_id': edition['id'],
                        'title': f'{title}（{state}）', 'stages': stages,
                        'fetched_at': now.astimezone(CST).strftime('%m-%d %H:%M')}
            self.cached, self.cached_at = deepcopy(snapshot), time.monotonic()
            return snapshot
