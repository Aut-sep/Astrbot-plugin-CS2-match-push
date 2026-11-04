"""Immutable per-group configuration and shared match-selection rules."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy

from .formatters import match_tier, team_name

RULE_KEYS = frozenset({
    'push_enabled', 'filter_mode', 'followed_teams', 'min_tiers',
    'blacklist_teams', 'blacklist_leagues', 'upcoming_enabled', 'finished_enabled',
    'remind_minutes', 'reschedule_notify', 'tournament_announce_enabled',
    'tournament_announce_hours', 'daily_push_enabled', 'daily_push_times',
    'daily_push_days', 'image_push_enabled', 'major_stage_notify_enabled',
})
KIND_SWITCH = {'upcoming': 'upcoming_enabled', 'finished': 'finished_enabled',
               'reschedule': 'reschedule_notify', 'opening': 'tournament_announce_enabled',
               'daily': 'daily_push_enabled', 'major_stage': 'major_stage_notify_enabled'}
# Context is task-local. Concurrent rendering never mutates the shared store.
_SCOPE = ContextVar('cs_group_rule_scope', default=None)


class Rules:
    def __init__(self, store, group_id=None, *, values=None):
        self.group_id = str(group_id) if group_id is not None else None
        self.values = {key: deepcopy(store.get(key)) for key in RULE_KEYS}
        self.values['filter_mode'] = store.get('filter_mode') or (
            'tiers_or_followed' if store.get('notify_all_followed', True) else 'tiers')
        self.values.update({key: deepcopy(store.get(key, True)) for key in
                            ('push_enabled', 'upcoming_enabled', 'finished_enabled')})
        if self.group_id is not None:
            self.values.update(deepcopy(store.get('group_rules', {}).get(self.group_id, {})))
        if values is not None:
            self.values.update(deepcopy(values))

    def get(self, key, default=None):
        value = self.values.get(key)
        return default if value is None else value

    def get_followed_teams(self):
        return [deepcopy(team) if isinstance(team, dict) else
                {'id': None, 'name': str(team), 'slug': str(team)}
                for team in self.get('followed_teams', [])]

    def get_followed_team_ids(self):
        return {team['id'] for team in self.get_followed_teams() if team.get('id') is not None}

    def get_followed_team_names(self):
        return [team.get('name') or team.get('slug') or '' for team in self.get_followed_teams()]

    def get_min_tiers(self):
        return self.get('min_tiers', ['s', 'a'])

    def enabled(self, kind=None):
        return bool(self.get('push_enabled', True) and
                    (kind is None or self.get(KIND_SWITCH[kind], True)))

    def follows(self, teams):
        ids = {str(team.get('id')) for team in teams if team.get('id') is not None}
        names = {str(team.get('name') or '').casefold() for team in teams}
        return any(str(team['id']) in ids if team.get('id') is not None
                   else str(team.get('name') or '').casefold() in names
                   for team in self.get_followed_teams())

    def accepts(self, match):
        teams = [(slot.get('opponent') or {}) for slot in match.get('opponents') or []]
        return self.accepts_teams(teams, match_tier(match),
                                  (match.get('league') or {}).get('name') or '')

    def accepts_event(self, event):
        return self.accepts_teams(event.get('teams') or [], match_tier({'tournament': event}),
                                  (event.get('league') or {}).get('name') or '')

    def accepts_teams(self, teams, tier, league):
        names = [str(team.get('name') or '').casefold() for team in teams]
        if any(word.casefold() in name for word in self.get('blacklist_teams', []) for name in names):
            return False
        if any(word.casefold() in league.casefold() for word in self.get('blacklist_leagues', [])):
            return False
        mode = self.get('filter_mode', 'tiers_or_followed')
        followed, ranked = self.follows(teams), tier in self.get_min_tiers()
        return followed if mode == 'followed' else ranked if mode == 'tiers' else ranked or followed


@contextmanager
def scope(rules, *, gate=None):
    """Gate is rechecked immediately before a real send, after rendering awaits."""
    token = _SCOPE.set((rules, gate))
    try:
        yield rules
    finally:
        _SCOPE.reset(token)


def current(store):
    context = _SCOPE.get()
    return context[0] if context is not None else Rules(store)


def send_allowed():
    context = _SCOPE.get()
    return context is None or context[1] is None or context[1]()


def has_send_gate():
    context = _SCOPE.get()
    return context is not None and context[1] is not None


def selected_group():
    context = _SCOPE.get()
    return context[0].group_id if context else None


def group_ids(store):
    return [str(gid) for gid in store.get_groups()]


def groups_for(store, kind=None, match=None, *, event=None):
    result = []
    for gid in group_ids(store):
        rules = Rules(store, gid)
        if rules.enabled(kind) and (match is None or rules.accepts(match)) and (
                event is None or rules.accepts_event(event)):
            result.append(gid)
    return result


def any_match(store, match):
    # Keep default-query matches as well as every group's independent inventory.
    return Rules(store).accepts(match) or any(Rules(store, gid).accepts(match) for gid in group_ids(store))
