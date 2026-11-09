"""Read-only, bounded LLM queries. Never schedule jobs, send messages or save config."""
import asyncio
from datetime import date, datetime, timedelta
import json

from astrbot.api import logger

from .bracket_images import selected_stages
from .client import SCHEDULE_PAGE_SIZE
from .config import ConfigError, DEFAULT_SCHEDULE_DAYS, MAX_SCHEDULE_DAYS, integer, string
from .cs_major import MajorBracketService, STAGE_ALIASES
from .formatters import CST, now_utc, parse_dt, sched_str
from .official_extension import denial_reason
from .result_helpers import final_score
from .team_search import TeamSearch, normalize


LIMIT = 100
_TEAMS = TeamSearch(None)  # Only the shipped alias catalog; no network search/cache.


def _iso(value):
    try:
        parsed = parse_dt(value) if value else None
    except ValueError:
        return None
    return parsed.astimezone(CST).isoformat() if parsed else None


def _match(row):
    teams = [slot.get('opponent') or {} for slot in row.get('opponents') or []]
    score = final_score(row) if row.get('status') == 'finished' else None
    return {
        'id': row['id'], 'status': row.get('status'),
        'scheduled_at': _iso(sched_str(row)), 'begin_at': _iso(row.get('begin_at')),
        'teams': [{key: team.get(key) for key in ('id', 'name', 'slug')} for team in teams],
        'league': (row.get('league') or {}).get('name'),
        'tournament': (row.get('tournament') or {}).get('name'),
        'best_of': row.get('number_of_games'), 'final_score': score,
        'winner_id': (row.get('winner') or {}).get('id') if score else None,
        'forfeit': bool(row.get('forfeit')), 'draw': bool(row.get('draw')),
        'data_updated_at': _iso(row.get('modified_at')),
    }


def _window(start_date, days, *, results):
    days = integer(days, 'days', 1, 30 if results else MAX_SCHEDULE_DAYS)
    raw = string(start_date, 'start_date')
    today = now_utc().astimezone(CST).date()
    if raw:
        try:
            start = date.fromisoformat(raw)
            if raw != start.isoformat():
                raise ValueError('expected YYYY-MM-DD')
        except ValueError as exc:
            raise ConfigError('start_date 必须是北京时间 YYYY-MM-DD') from exc
    else:
        start = today - timedelta(days=days - 1) if results else today
    begin = datetime.combine(start, datetime.min.time(), CST)
    return begin, begin + timedelta(days=days)


def _team_predicate(query, field='team'):
    query = string(query, field)
    if len(query) > 80:
        raise ConfigError(f'{field} 最多 80 个字符')
    if not query:
        return lambda team: True
    key = normalize(query)
    if not key:
        raise ConfigError(f'{field} 不能只包含符号')
    exact = [row for row in _TEAMS.candidates(query) if row[0] == 1000]
    if len(exact) > 1:
        raise ConfigError('战队别名不唯一，请提供战队全名或 PandaScore ID')
    record = _TEAMS._record(exact[0][2]) if exact else None
    identity = str(record['id']) if record else query if query.isdigit() else None

    if identity is not None:
        return lambda team: str(team.get('id')) == identity
    return lambda team: key in {normalize(team.get('name')), normalize(team.get('slug'))}


def _matchup_predicate(team, opponent):
    if not team and not opponent:
        return lambda row: True
    first = _team_predicate(team)
    second = _team_predicate(opponent, 'opponent')

    def matches(row):
        teams = [slot.get('opponent') for slot in row.get('opponents') or [] if slot.get('opponent')]
        if not team:
            return any(second(side) for side in teams)
        if not opponent:
            return any(first(side) for side in teams)
        return any(first(side) and second(other) for i, side in enumerate(teams)
                   for j, other in enumerate(teams) if i != j)
    return matches


async def query(plugin, event, action, **params):
    """One envelope for successes, validation errors, denials and upstream failures."""
    local = action == 'followed'
    reply = {
        'status': 'unavailable', 'timezone': 'Asia/Shanghai',
        'source': 'local_plugin_config' if local else 'PandaScore',
        'queried_at': now_utc().astimezone(CST).isoformat(),
        'fetched_at': None, 'data_updated_at': None, 'is_cached': False,
        'data': None,
        'note': 'data_updated_at 是来源提供的修改时间，未知时为 null；fetched_at 仅表示成功读取时间。'
                '失败不代表没有比赛；空结果仅表示所查时间与筛选范围内没有收录结果。不得推断未公布对阵、比分或胜者。',
    }
    try:
        feature = {'schedule': 'schedule', 'result': 'results', 'followed': 'followed', 'major': 'bracket'}[action]
        denied = denial_reason(plugin, event, feature)
        if denied:
            reply.update(status='denied', error=denied)
            return json.dumps(reply, ensure_ascii=False)
        rules = plugin.query_rules(event)
        reply['scope'] = ('published_major' if action == 'major' else
                          'current_group_rules' if rules.group_id is not None else 'default_rules')
        if local:
            reply['data'] = {'teams': [{key: team.get(key) for key in ('id', 'name', 'slug')}
                                       for team in rules.get_followed_teams()]}
        else:
            if not plugin._token:
                reply.update(status='not_configured', error='请管理员配置 PandaScore Token。')
                return json.dumps(reply, ensure_ascii=False)
            # A fresh query service avoids presenting the background/image cache as live data.
            reply['data'] = await asyncio.wait_for(_remote(plugin, rules, action, params), timeout=60)
        reply.update(status='ok', fetched_at=now_utc().astimezone(CST).isoformat())
    except ConfigError as exc:
        reply.update(status='invalid_request', error=str(exc))
    except Exception as exc:
        # Provider errors may contain credentials or URLs: never expose their text to the model.
        logger.warning(f'[CS AI] {action} query failed: {type(exc).__name__}')
        reply.update(status='unavailable', error='数据源查询失败或超时，未取得完整数据，请稍后重试。')
    return json.dumps(reply, ensure_ascii=False)


async def _remote(plugin, rules, action, params):
    if action == 'major':
        stage = STAGE_ALIASES.get(string(params.get('stage', 'current'), 'stage').lower())
        if stage is None:
            raise ConfigError('stage 支持 current/next/all/stage1/stage2/stage3/swiss/knockout')
        snapshot = await MajorBracketService(plugin).fetch()
        if snapshot is None:
            raise RuntimeError('Major data unavailable')
        stages = selected_stages(snapshot, stage)
        selected = stage
        if not stages and stage == 'current':
            stages, selected = selected_stages(snapshot, 'next'), 'next'
        result = []
        for item in stages:
            rows = item['matches']
            visible = rows[:LIMIT]
            ids = {str(row['id']) for row in visible}
            result.append({'id': item['id'], 'title': item['title'], 'kind': item['kind'],
                           'matches': [_match(row) for row in visible],
                           'edges': [edge for edge in item['edges'] if edge['source'] in ids and edge['target'] in ids],
                           'notes': item['notes'], 'total': len(rows), 'truncated': len(rows) > LIMIT})
        return {'title': snapshot['title'], 'serie_id': snapshot['serie_id'],
                'requested_stage': stage, 'selected_stage': selected, 'stages': result,
                'filter_scope': '整届 Major 已公布对阵，不应用关注和赛事级别筛选；不推断瑞士轮后续对手。'}

    team = string(params.get('team', ''), 'team')
    opponent = string(params.get('opponent', ''), 'opponent')
    predicate = _matchup_predicate(team, opponent)
    limit = integer(params.get('limit', LIMIT), 'limit', 1, LIMIT)
    identity = string(params.get('match_id', ''), 'match_id')
    if action == 'result' and identity:
        identity = integer(identity, 'match_id', 1)
        match = await plugin.client.get_match_result(identity)
        if not isinstance(match, dict) or str(match.get('id')) != str(identity):
            raise RuntimeError('match unavailable')
        # Preserve non-finished status so the model cannot mistake it for a loss/absence.
        rows = [match]
        window = None
    else:
        start, end = _window(params.get('start_date', ''), params.get('days', 1 if action == 'result' else DEFAULT_SCHEDULE_DAYS), results=action == 'result')
        paging = {'per_page': SCHEDULE_PAGE_SIZE} if action == 'schedule' else {}
        rows = await plugin.client.get_matches_between(start, end, **paging)
        if rows is None:
            raise RuntimeError('incomplete upstream response')
        window = {'start': start.isoformat(), 'end_exclusive': end.isoformat(),
                  'basis': 'scheduled_at（计划开赛日期，不是结束日期）'}
        if action == 'result':
            rows = [row for row in rows if row.get('status') == 'finished']
        else:
            now = now_utc()
            rows = [row for row in rows if row.get('status') == 'not_started'
                    and parse_dt(sched_str(row)) >= now]
    rows = [row for row in rows if rules.accepts(row) and predicate(row)]
    rows.sort(key=lambda row: (_iso(sched_str(row)) or '', str(row['id'])), reverse=action == 'result')
    return {'window': window, 'matches': [_match(row) for row in rows[:limit]],
            'total': len(rows), 'limit': limit, 'truncated': len(rows) > limit,
            'filter_scope': '沿用当前会话默认或本群赛事级别、关注模式和黑名单；空结果不代表全网无比赛。'}
