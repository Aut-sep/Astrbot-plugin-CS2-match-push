"""Bounded, persistent evidence and a read-only watchdog for known matches.

This database is deliberately separate from delivery receipts. Recording or
auditing never sends messages, queries upstream, or changes delivery state.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
import traceback

from .formatters import parse_dt, sched_str, is_push_ready_match, team_name
from .group_rules import Rules, groups_for

RETENTION_DAYS = 30
MAX_EVENTS = 50000
GRACE_SECONDS = 120

REASONS = {
    'schedule_received': '收到待赛数据', 'schedule_filtered': '赛程未进入提醒列表',
    'single_match_received': '收到单场状态数据', 'schedule_failed': '日程请求失败，保留原任务',
    'reminder_scheduled': '已安排提醒任务', 'result_tracking': '已进入赛果追踪',
    'not_started_time_elapsed': '仍未开赛，但返回的开赛时间已过去',
    'not_started_time_invalid': '仍未开赛，但返回的开赛时间缺失或无效',
    'selection_rejected': '单场数据未通过关注或赛事筛选',
    'status_canceled': '数据源明确标记比赛取消', 'status_postponed': '数据源标记比赛延期',
    'status_unexpected': '单场返回未处理的状态', 'state_unverified': '状态未核实，保留原追踪',
    'handoff_running': '比赛已开始，赛前任务交接至赛果追踪',
    'handoff_finished': '比赛已结束，赛前任务交接至赛果追踪',
    'outside_window': '比赛改至抓取窗口之外', 'task_replaced': '提醒任务因时间或对手变更被重建',
    'manual_rebuild': '用户手动重建任务', 'reminder_updated': '用户修改提醒时间',
    'shutdown': '插件正常停止', 'task_exception': '后台任务发生异常',
    'result_deadline': '赛果查询已超时', 'result_canceled': '比赛取消，停止赛果查询',
    'request_failed': '数据源请求异常', 'response_invalid': '数据源返回结构异常',
    'send_attempt': '准备发送通知', 'send_success': '平台接受消息且回执已保存',
    'send_failed': '平台发送失败', 'receipt_failed': '平台接受消息，但保存回执失败',
    'notification_failed': '通知渲染或发送流程异常', 'send_blocked': '发送前规则复核阻止投递',
    'notification_skipped': '通知未发送', 'missing_upcoming': '提醒已到期，仍无该群发送回执',
    'result_tracking_lost': '比赛已到开赛时间，但赛果追踪任务缺失',
    'missing_finished': '已观察到比赛结束，仍无该群赛果发送回执',
    'daily_overdue': '日报待发送超过宽限时间', 'issue_resolved': '此前异常已恢复或已不再要求发送',
    'recorder_started': '持久化诊断已启动', 'watchdog_failed': '诊断巡检发生异常',
    'task_canceled': '提醒任务被取消，未通过正常完成路径交接',
    'notification_reset': '用户清除了通知记录，重置诊断观察范围',
}


EVENT_GROUPS = {
    'schedule': (
        'reminder_scheduled', 'result_tracking', 'not_started_time_elapsed', 'not_started_time_invalid',
        'selection_rejected', 'status_canceled', 'status_postponed', 'status_unexpected',
        'state_unverified', 'handoff_running', 'handoff_finished', 'outside_window', 'task_replaced',
        'manual_rebuild', 'reminder_updated', 'result_deadline', 'result_canceled', 'task_canceled',
    ),
    'delivery': ('send_success', 'send_failed', 'receipt_failed', 'notification_failed',
                 'send_blocked', 'notification_skipped'),
    'updates': ('schedule_received', 'single_match_received', 'schedule_filtered'),
    'attempts': ('send_attempt',),
}
# Watchdog events are represented by their issue rows in the grouped view.
ISSUE_EVENT_REASONS = ('missing_upcoming', 'missing_finished', 'result_tracking_lost', 'daily_overdue', 'issue_resolved')


def match_evidence(match):
    """Allowlist the fields needed to replay a scheduling decision."""
    if not isinstance(match, dict):
        return {'invalid_response_type': type(match).__name__}
    def fields(value, names):
        if isinstance(value, dict):
            return {key: value.get(key) for key in names}
        return {'invalid_field_type': type(value).__name__, 'value': value}
    opponents = match.get('opponents')
    if isinstance(opponents, list):
        opponents = [{'opponent': fields(slot.get('opponent'), ('id', 'name'))} if isinstance(slot, dict)
                     else {'invalid_slot_type': type(slot).__name__, 'value': slot} for slot in opponents[:4]]
    elif opponents is not None:
        opponents = {'invalid_field_type': type(opponents).__name__, 'value': opponents}
    return {key: match.get(key) for key in
            ('id', 'status', 'scheduled_at', 'begin_at', 'end_at', 'opponents',
             'tournament_id', 'number_of_games', 'winner_id', 'results')} | {
        'opponents': opponents,
        'tournament': fields(match.get('tournament'), ('id', 'name', 'tier')),
        'league': fields(match.get('league'), ('id', 'name')),
    }


def selection_evidence(store, match):
    def describe(rules):
        teams = [(s.get('opponent') or {}) for s in match.get('opponents') or []]
        tier = ((match.get('tournament') or {}).get('tier') or 'unranked').lower()
        return {'accepted': rules.accepts(match), 'enabled': rules.enabled(),
                'upcoming_enabled': rules.enabled('upcoming'), 'finished_enabled': rules.enabled('finished'),
                'filter_mode': rules.get('filter_mode'), 'followed': rules.follows(teams),
                'tier': tier, 'min_tiers': rules.get_min_tiers(),
                'followed_ids': sorted(str(i) for i in rules.get_followed_team_ids()),
                'blocked_team_terms': [word for word in rules.get('blacklist_teams', [])
                                       if any(word.casefold() in str(t.get('name') or '').casefold() for t in teams)],
                'blocked_league_terms': [word for word in rules.get('blacklist_leagues', [])
                                         if word.casefold() in str((match.get('league') or {}).get('name') or '').casefold()],
                'blacklist_teams': rules.get('blacklist_teams', []),
                'blacklist_leagues': rules.get('blacklist_leagues', [])}
    try:
        return {'default': describe(Rules(store)),
                'groups': {gid: describe(Rules(store, gid)) for gid in store.get_groups()}}
    except Exception as exc:
        return {'invalid_selection_data': type(exc).__name__}


def _teams_from_evidence(evidence):
    snapshot = evidence.get('old_snapshot')
    if not isinstance(snapshot, dict):
        snapshot = {}
    candidates = (evidence.get('match'), evidence.get('latest'), evidence.get('planned'),
                  {'_t1': snapshot.get('t1'), '_t2': snapshot.get('t2')})
    return min(([team_name(match, 0), team_name(match, 1)] for match in candidates),
               key=lambda teams: teams.count('TBD'))


def cleanup_reason(match, accepted, now, cutoff):
    status = match.get('status')
    if not accepted:
        return 'selection_rejected'
    if status in {'running', 'finished'}:
        return 'handoff_' + status
    if status in {'canceled', 'postponed'}:
        return 'status_' + status
    if status == 'not_started':
        try:
            start = parse_dt(sched_str(match))
        except (TypeError, ValueError):
            return 'not_started_time_invalid'
        if start <= now:
            return 'not_started_time_elapsed'
        if start > cutoff:
            return 'outside_window'
    return 'status_unexpected'


class PushDiagnostics:
    def __init__(self, path, *, clock=time.time, secrets=(), on_error=None):
        self.path = Path(path)
        self.clock = clock
        self.secrets = tuple(str(s) for s in secrets if s)
        self.on_error = on_error or (lambda message: None)
        self.last_error = None
        self.last_audit = None
        self._maintenance_at = 0
        self._error_reported_at = -3600
        self.db = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(str(self.path), timeout=0.2)
            self.db.row_factory = sqlite3.Row
            self.db.execute('PRAGMA journal_mode=WAL')
            self.db.executescript('''
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, ts REAL NOT NULL, level TEXT NOT NULL,
                    reason TEXT NOT NULL, match_id INTEGER, group_id TEXT, event_key TEXT,
                    evidence TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS events_match ON events(match_id, id);
                CREATE TABLE IF NOT EXISTS watches (
                    match_id INTEGER PRIMARY KEY, ts REAL NOT NULL, planned TEXT NOT NULL,
                    latest TEXT NOT NULL, audience TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS issues (
                    issue_key TEXT PRIMARY KEY, match_id INTEGER, group_id TEXT,
                    reason TEXT NOT NULL, first_seen REAL NOT NULL, last_seen REAL NOT NULL,
                    resolved_at REAL, evidence TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS fingerprints (key TEXT PRIMARY KEY, hash TEXT, ts REAL);
                CREATE TABLE IF NOT EXISTS deliveries (
                    event_key TEXT, group_id TEXT, ts REAL, PRIMARY KEY(event_key,group_id));
            ''')
            self.db.commit()
            root = Path(__file__).parent
            fingerprints = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in
                            ('main.py', 'client.py', 'group_notifications.py', 'group_rules.py', 'push_diagnostics.py')}
            self.record('recorder_started', evidence={'schema_version': 1, 'code_sha256': fingerprints})
        except Exception as exc:
            self._failed(exc)

    def _failed(self, exc):
        if self.db is not None:
            try:
                self.db.rollback()
            except Exception:
                pass
        self.last_error = self.clean(f'{type(exc).__name__}: {exc}')
        if self.clock() - self._error_reported_at >= 60:
            self._error_reported_at = self.clock()
            try:
                self.on_error('CS 诊断记录不可用：' + self.last_error)
            except Exception:
                pass

    def clean(self, value, depth=0):
        if depth > 10:
            return '[truncated]'
        if isinstance(value, dict):
            return {str(k): '[redacted]' if re.search(r'token|secret|authorization|cookie|password|api_key', str(k), re.I)
                    else self.clean(v, depth + 1) for k, v in list(value.items())[:100]}
        if isinstance(value, (list, tuple)):
            return [self.clean(v, depth + 1) for v in value[:100]]
        if isinstance(value, str):
            for secret in self.secrets:
                value = value.replace(secret, '[redacted]')
            value = re.sub(r'(?i)(bearer\s+)[^\s\"\'&,}]+', r'\1[redacted]', value)
            value = re.sub(r'(?i)((?:token|api_key|password|secret)\s*[=:]\s*)[^\s&,\"\'}]+',
                           r'\1[redacted]', value)
            return value[:8000]
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return self.clean(str(value), depth + 1)

    def record(self, reason, *, match=None, match_id=None, group_id=None, key=None,
               level='INFO', evidence=None, error=None, dedupe=None):
        if self.db is None:
            return
        try:
            payload = dict(evidence or {})
            if match is not None:
                payload['match'] = match_evidence(match)
                if match_id is None:
                    match_id = match.get('id')
            if match_id is None and key and key.split(':')[0] in {'upcoming', 'finished', 'reschedule'}:
                match_id = int(key.split(':')[1])
            if match_id is None:
                endpoint = str(payload.get('endpoint') or '')
                found = re.search(r'/matches/(\d+)$', endpoint)
                if found:
                    match_id = int(found[1])
            if error is not None:
                payload['error_type'] = type(error).__name__
                payload['error'] = str(error)
                payload['traceback'] = ''.join(traceback.format_exception(type(error), error, error.__traceback__))
            encoded = json.dumps(self.clean(payload), ensure_ascii=False, sort_keys=True)
            now = self.clock()
            if dedupe:
                fingerprint = hashlib.sha256(encoded.encode()).hexdigest()
                old = self.db.execute('SELECT hash FROM fingerprints WHERE key=?', (dedupe,)).fetchone()
                if old and old['hash'] == fingerprint:
                    return
                self.db.execute('INSERT OR REPLACE INTO fingerprints VALUES (?,?,?)', (dedupe, fingerprint, now))
            self.db.execute('INSERT INTO events(ts,level,reason,match_id,group_id,event_key,evidence) VALUES (?,?,?,?,?,?,?)',
                            (now, level, reason, match_id, str(group_id) if group_id is not None else None, key, encoded))
            if match is not None and reason == 'single_match_received' and str(match.get('id')) == str(match_id):
                latest = self.clean(match_evidence(match)) | {'_observed_at': now}
                self.db.execute('UPDATE watches SET latest=? WHERE match_id=?',
                                (json.dumps(latest, ensure_ascii=False), match_id))
            if reason == 'send_success' and key and group_id is not None:
                self.db.execute('INSERT OR REPLACE INTO deliveries VALUES (?,?,?)', (key, str(group_id), now))
            self.db.commit()
            self.last_error = None
            if now - self._maintenance_at > 300:
                self._prune(now)
        except Exception as exc:
            self._failed(exc)

    def _prune(self, now):
        cutoff = now - RETENTION_DAYS * 86400
        with self.db:
            self.db.execute('DELETE FROM events WHERE ts<?', (cutoff,))
            self.db.execute('DELETE FROM events WHERE id <= (SELECT id FROM events ORDER BY id DESC LIMIT 1 OFFSET ?)', (MAX_EVENTS,))
            self.db.execute('DELETE FROM watches WHERE ts<?', (cutoff,))
            self.db.execute('DELETE FROM issues WHERE last_seen<?', (cutoff,))
            self.db.execute('DELETE FROM fingerprints WHERE ts<?', (cutoff,))
            self.db.execute('DELETE FROM deliveries WHERE ts<?', (cutoff,))
        self._maintenance_at = now

    def track(self, plugin, match, *, phase='reminder'):
        if self.db is None:
            return
        try:
            mid = match['id']
            start = parse_dt(sched_str(match))
            old = self.db.execute('SELECT audience FROM watches WHERE match_id=?', (mid,)).fetchone()
            if phase == 'observed' and old is not None:
                return
            audience = json.loads(old['audience']) if old else {}
            for gid in plugin.store.get_groups():
                rules = Rules(plugin.store, gid)
                custom = plugin.store.get_custom_remind(mid)
                minutes = custom if custom is not None else rules.get('remind_minutes', 10)
                due = start.timestamp() - minutes * 60
                since = plugin.store.get('group_rule_since', {}).get(gid)
                historical = since and due < parse_dt(since).timestamp()
                entry = audience.setdefault(gid, {})
                if phase == 'reminder' and gid in groups_for(plugin.store, 'upcoming', match):
                    entry['upcoming'] = {'due': due, 'historical': bool(historical)}
                if gid in groups_for(plugin.store, 'finished', match):
                    entry['finished'] = True
            encoded = json.dumps(self.clean(match_evidence(match)), ensure_ascii=False)
            self.db.execute('INSERT INTO watches VALUES (?,?,?,?,?) ON CONFLICT(match_id) DO UPDATE SET '
                            'ts=excluded.ts,planned=excluded.planned,latest=excluded.latest,audience=excluded.audience',
                            (mid, self.clock(), encoded, encoded, json.dumps(audience)))
            self.db.commit()
        except Exception as exc:
            self._failed(exc)

    def _issue(self, mid, gid, reason, evidence):
        issue_key = f"{mid}:{gid}:{reason}:{evidence.get('event_key', '')}"
        now = self.clock()
        old = self.db.execute('SELECT resolved_at FROM issues WHERE issue_key=?', (issue_key,)).fetchone()
        self.db.execute('INSERT INTO issues VALUES (?,?,?,?,?,?,NULL,?) ON CONFLICT(issue_key) DO UPDATE SET '
                        'last_seen=excluded.last_seen,resolved_at=NULL,evidence=excluded.evidence',
                        (issue_key, mid, gid, reason, now, now, json.dumps(self.clean(evidence), ensure_ascii=False)))
        if old is None or old['resolved_at'] is not None:
            self.record(reason, match_id=mid, group_id=gid, level='WARN', evidence=evidence)
        return issue_key

    def audit(self, plugin):
        """Look only at durable watches and memory; no upstream or delivery I/O."""
        if self.db is None:
            return
        try:
            now, active = self.clock(), set()
            receipts = plugin.store.get('delivery_receipts', {})
            dirty = bool(getattr(plugin.store, '_delivery_receipts_dirty', False))
            for row in self.db.execute('SELECT * FROM watches').fetchall():
                planned, latest = json.loads(row['planned']), json.loads(row['latest'])
                mid, audience = row['match_id'], json.loads(row['audience'])
                start = parse_dt(sched_str(planned)).timestamp()
                for gid, expectations in audience.items():
                    rules = Rules(plugin.store, gid)
                    # Check policy against the last valid plan. A malformed fresh
                    # response must not silently erase the expectation it broke.
                    if gid not in plugin.store.get_groups() or not rules.accepts(planned):
                        continue
                    if latest.get('status') in {'canceled', 'postponed'}:
                        continue
                    tasks = {'reminder_active': self._active(plugin._match_tasks.get(mid)),
                             'result_active': self._active(plugin._result_tasks.get(mid))}
                    evidence = {'planned': planned, 'latest': latest, **tasks, 'receipt_dirty': dirty}
                    upcoming = expectations.get('upcoming')
                    if upcoming and not upcoming['historical'] and rules.enabled('upcoming'):
                        due = upcoming['due']
                        since = plugin.store.get('group_rule_since', {}).get(gid)
                        historical = since and due < parse_dt(since).timestamp()
                        frozen = plugin.store.get('notification_targets', {}).get(f'upcoming:{mid}')
                        legacy = plugin.store.is_upcoming_notified(mid) and frozen is None
                        sent = not dirty and f'group:{gid}' in receipts.get(f'upcoming:{mid}', [])
                        sent = sent or self._delivered(f'upcoming:{mid}', gid)
                        if (not historical and now > due + GRACE_SECONDS and not sent and not legacy
                                and (frozen is None or gid in frozen) and is_push_ready_match(planned)):
                            active.add(self._issue(mid, gid, 'missing_upcoming', dict(evidence, due_at=due)))
                    if not expectations.get('finished') or not rules.enabled('finished'):
                        continue
                    if str(mid) in plugin.store.get('result_recovery_ignored', {}):
                        continue
                    frozen = plugin.store.get('notification_targets', {}).get(f'finished:{mid}')
                    sent = not dirty and f'group:{gid}' in receipts.get(f'finished:{mid}', [])
                    sent = sent or self._delivered(f'finished:{mid}', gid)
                    legacy = plugin.store.is_finished_notified(mid) and frozen is None
                    if sent or legacy or (frozen is not None and gid not in frozen):
                        continue
                    if latest.get('status') == 'finished':
                        try:
                            ended = parse_dt(latest.get('end_at')).timestamp()
                        except (TypeError, ValueError):
                            ended = latest.get('_observed_at', start)
                        since = plugin.store.get('group_rule_since', {}).get(gid)
                        if since and ended < parse_dt(since).timestamp():
                            continue
                        if now > ended + GRACE_SECONDS:
                            active.add(self._issue(mid, gid, 'missing_finished', evidence))
                    elif now > start + GRACE_SECONDS and not tasks['reminder_active'] and not tasks['result_active']:
                        active.add(self._issue(mid, gid, 'result_tracking_lost', evidence))
            # Daily events already carry their durable expected audience/time.
            for key, status in plugin.store.get('daily_push_events', {}).items():
                if status != 'pending' or key.count(':') != 3:
                    continue
                _, gid, date, minute = key.split(':')
                from .formatters import CST
                from datetime import datetime
                due = datetime.fromisoformat(date.replace('_', 'T') + ':' + minute).replace(tzinfo=CST).timestamp()
                if due + GRACE_SECONDS < now and gid in groups_for(plugin.store, 'daily'):
                    active.add(self._issue(None, gid, 'daily_overdue', {'event_key': key, 'due_at': due}))
            for issue in self.db.execute('SELECT issue_key,match_id,group_id FROM issues WHERE resolved_at IS NULL').fetchall():
                if issue['issue_key'] not in active:
                    self.db.execute('UPDATE issues SET resolved_at=? WHERE issue_key=?', (now, issue['issue_key']))
                    self.record('issue_resolved', match_id=issue['match_id'], group_id=issue['group_id'],
                                evidence={'issue_key': issue['issue_key']})
            self.db.commit()
            self.last_audit = now
            self.last_error = None
            if now - self._maintenance_at > 300:
                self._prune(now)
        except Exception as exc:
            self._failed(exc)

    def _delivered(self, key, gid):
        return self.db.execute('SELECT 1 FROM deliveries WHERE event_key=? AND group_id=?', (key, gid)).fetchone() is not None

    def reset_watches(self):
        if self.db is None:
            return
        try:
            with self.db:
                self.db.execute('DELETE FROM watches')
                self.db.execute('DELETE FROM deliveries')
                self.db.execute('UPDATE issues SET resolved_at=? WHERE resolved_at IS NULL', (self.clock(),))
            self.record('notification_reset')
        except Exception as exc:
            self._failed(exc)

    @staticmethod
    def _active(task):
        return task is not None and not task.done()

    def _add_match_teams(self, records):
        """Fill ID-only records from the stored schedule and matching events."""
        incomplete = {}
        for record in records:
            mid = record['match_id']
            if mid is None:
                continue
            teams = _teams_from_evidence(record.get('evidence', {}))
            record['match_teams'] = teams
            if 'TBD' in teams:
                incomplete.setdefault(mid, []).append(record)

        for mid, rows in incomplete.items():
            watch = self.db.execute('SELECT latest, planned FROM watches WHERE match_id=?', (mid,)).fetchone()
            best = _teams_from_evidence({key: json.loads(watch[key]) for key in watch.keys()} if watch else {})
            if 'TBD' in best:
                history = self.db.execute('SELECT evidence FROM events WHERE match_id=? ORDER BY id DESC', (mid,))
                for event in history:
                    teams = _teams_from_evidence(json.loads(event['evidence']))
                    if teams.count('TBD') < best.count('TBD'):
                        best = teams
                    if 'TBD' not in best:
                        break
            for record in rows:
                if best.count('TBD') < record['match_teams'].count('TBD'):
                    record['match_teams'] = best

    @staticmethod
    def _event_fields(row):
        item = dict(row)
        item['evidence'] = json.loads(item['evidence'])
        item['message'] = REASONS.get(item['reason'], item['reason'])
        return item

    def _match_overview(self, match_id, limit):
        if match_id is None:
            where, args = 'match_id IS NOT NULL', ()
            event_args = EVENT_GROUPS['updates'] + EVENT_GROUPS['attempts'] + ISSUE_EVENT_REASONS
            placeholders = ','.join('?' for _ in event_args)
            event_where = f'{where} AND reason NOT IN ({placeholders})'
        else:
            where = event_where = 'match_id=?'
            args = event_args = (match_id,)
        rows = self.db.execute(f'''
            WITH activity AS (
                SELECT match_id, MAX(ts) AS seen, 0 AS open_count
                FROM events WHERE {event_where} GROUP BY match_id
                UNION ALL
                SELECT match_id, MAX(last_seen), SUM(resolved_at IS NULL)
                FROM issues WHERE {where} GROUP BY match_id
            )
            SELECT match_id, MAX(seen) AS last_seen, SUM(open_count) AS open_issues
            FROM activity GROUP BY match_id
            ORDER BY (SUM(open_count) > 0) DESC, last_seen DESC, match_id DESC LIMIT ?
        ''', (*event_args, *args, limit)).fetchall()
        matches = [dict(row) for row in rows]
        self._add_match_teams(matches)
        return matches

    def _grouped_detail(self, match_id, limit):
        where, args = ('match_id=?', [match_id]) if match_id is not None else ('match_id IS NULL', [])
        issues = [self._event_fields(row) for row in self.db.execute(
            f'SELECT * FROM issues WHERE {where} ORDER BY resolved_at IS NULL DESC,last_seen DESC LIMIT ?',
            (*args, limit),
        )]
        open_issues = self.db.execute(
            f'SELECT COUNT(*) FROM issues WHERE {where} AND resolved_at IS NULL', args,
        ).fetchone()[0]

        other_reasons = ISSUE_EVENT_REASONS + tuple(reason for group in EVENT_GROUPS.values() for reason in group)
        groups = {}
        for group, reasons in (EVENT_GROUPS | {'system': other_reasons}).items():
            operator = 'NOT IN' if group == 'system' else 'IN'
            placeholders = ','.join('?' for _ in reasons)
            rows = self.db.execute(
                f'SELECT * FROM events WHERE {where} AND reason {operator} ({placeholders}) ORDER BY id DESC LIMIT ?',
                (*args, *reasons, limit),
            )
            groups[group] = [self._event_fields(row) for row in rows]
        return {'issues': issues, 'open_issues': open_issues, 'event_groups': groups}

    def query(self, *, match_id=None, limit=100, view=None):
        result = {'events': [], 'issues': [], 'system_events': [], 'health': {'available': self.db is not None and self.last_error is None,
                  'last_error': self.last_error, 'last_audit': self.last_audit, 'retention_days': RETENTION_DAYS,
                  'max_events': MAX_EVENTS, 'grace_seconds': GRACE_SECONDS}}
        if self.db is None:
            return result
        try:
            if view == 'matches':
                result['matches'] = self._match_overview(match_id, limit)
                return result
            if view == 'detail':
                result.update(self._grouped_detail(match_id, limit))
                return result
            where, args = (' WHERE match_id=?', [match_id]) if match_id is not None else ('', [])
            result['event_count'] = self.db.execute('SELECT COUNT(*) FROM events' + where, args).fetchone()[0]
            result['open_issues'] = self.db.execute('SELECT COUNT(*) FROM issues' + where +
                                                  (' AND' if where else ' WHERE') + ' resolved_at IS NULL', args).fetchone()[0]
            for table, order in (('events', 'id DESC'), ('issues', 'resolved_at IS NULL DESC,last_seen DESC')):
                rows = self.db.execute(f'SELECT * FROM {table}{where} ORDER BY {order} LIMIT ?', (*args, limit)).fetchall()
                for row in rows:
                    item = self._event_fields(row)
                    result[table].append(item)
            self._add_match_teams(result['events'] + result['issues'])
            if match_id is not None:
                for row in self.db.execute('SELECT * FROM events WHERE match_id IS NULL AND reason IN '
                                           "('recorder_started','shutdown','notification_reset','schedule_failed','task_exception','request_failed','response_invalid') "
                                           'ORDER BY id DESC LIMIT 20').fetchall():
                    item = self._event_fields(row)
                    result['system_events'].append(item)
        except Exception as exc:
            self._failed(exc)
            result['health'].update(available=False, last_error=self.last_error)
        return result

    def close(self):
        if self.db is not None:
            try:
                self.db.close()
            except Exception as exc:
                self._failed(exc)
            self.db = None


def emit(plugin, reason, **kwargs):
    """Optional hook keeps old host integrations and offline doubles compatible."""
    diagnostics = getattr(plugin, 'diagnostics', None)
    if diagnostics is not None:
        try:
            diagnostics.record(reason, **kwargs)
        except Exception:
            # A diagnostic hook must never change notification behavior.
            pass
