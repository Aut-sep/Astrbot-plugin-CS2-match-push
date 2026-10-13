"""Community aliases and conservative typo matching for verified CS2 teams."""

import asyncio
import json
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path


def normalize(value):
    return ''.join(c for c in unicodedata.normalize('NFKC', str(value or '')).casefold() if c.isalnum())


def _distance(a, b):
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def alias_match(query, alias):
    """Short codes must be exact; longer Latin names tolerate a small typo."""
    if query == alias:
        return 1000, 'alias'
    if (len(query) >= 3 or (len(query) >= 2 and not query.isascii())) and query in alias:
        return 800 + int(100 * len(query) / len(alias)), 'partial'
    if query.isascii() and alias.isascii() and min(len(query), len(alias)) >= 4:
        limit = 1 if min(len(query), len(alias)) < 6 else 2
        distance = _distance(query, alias)
        mismatches = [i for i, (a, b) in enumerate(zip(query, alias)) if a != b]
        if len(query) == len(alias) and len(mismatches) == 2:
            i, j = mismatches
            if j == i + 1 and query[i] == alias[j] and query[j] == alias[i]:
                distance = 1
        if abs(len(query) - len(alias)) <= limit and distance <= limit:
            ratio = SequenceMatcher(None, query, alias).ratio()
            if ratio >= (.70 if len(query) == 4 else .78):
                return 600 + int(100 * ratio), 'fuzzy'
    return 0, ''


class TeamSearch:
    def __init__(self, raw_search, path=None):
        self.raw_search = raw_search
        self.entries = {}
        self.featured = {}
        self._cache = {}
        self._locks = {}
        path = Path(path) if path else Path(__file__).with_name('team_search.json')
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            self.entries = data.get('teams', {})
            self.featured = data.get('featured', {})
        except (OSError, ValueError, AttributeError):
            pass
        self._identity = {}
        for canonical, entry in self.entries.items():
            for alias in [canonical, *entry.get('aliases', []), entry.get('name', '')]:
                key = normalize(alias)
                if key:
                    self._identity.setdefault(key, canonical)

    def candidates(self, query):
        key = normalize(query)
        if not key:
            return []
        result = []
        for canonical, entry in self.entries.items():
            matches = [(alias_match(key, normalize(alias)), alias)
                       for alias in [canonical, *entry.get('aliases', []), entry.get('name', '')]
                       if normalize(alias)]
            (score, kind), alias = max(matches, key=lambda m: m[0][0])
            if score:
                result.append((score, int(entry.get('priority') or 999), canonical, kind, alias))
        return sorted(result, key=lambda r: (-r[0], r[1], r[2].casefold()))

    async def _raw(self, query):
        key = normalize(query)
        now = time.monotonic()
        cached = self._cache.get(key)
        if cached and cached[0] > now:
            return [dict(team) for team in cached[1]]
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = self._cache.get(key)
            if cached and cached[0] > time.monotonic():
                return [dict(team) for team in cached[1]]
            try:
                teams = await asyncio.wait_for(self.raw_search(query, per_page=50), timeout=8)
            except (asyncio.TimeoutError, OSError):
                teams = []
            teams = [dict(t) for t in teams if isinstance(t, dict) and isinstance(t.get('id'), int)
                     and not isinstance(t['id'], bool) and t['id'] > 0 and t.get('name')]
            if len(self._cache) >= 128:
                oldest = min(self._cache, key=lambda k: self._cache[k][0])
                self._cache.pop(oldest, None)
                self._locks.pop(oldest, None)
            self._cache[key] = (time.monotonic() + (600 if teams else 30), teams)
            return [dict(team) for team in teams]

    def _record(self, canonical):
        entry = self.entries[canonical]
        team_id = entry.get('id')
        if not isinstance(team_id, int) or isinstance(team_id, bool) or team_id <= 0:
            return None
        return {key: entry.get(key) for key in ('id', 'name', 'slug', 'image_url')}

    async def search(self, query, per_page=20):
        query = str(query or '').strip()[:80]
        if not normalize(query):
            return []
        candidates = self.candidates(query)
        # Main identities are shipped only after verifying their provider ID.
        # Unlisted teams and academy rosters remain available via live search.
        combined = {}
        for score, rank, canonical, kind, alias in candidates[:8]:
            team = self._record(canonical)
            if team:
                team.update(is_main=True, matched_alias=alias, match_kind=kind, canonical=canonical)
                combined[team['id']] = (score, 1, rank, team)
        raw_query = query
        if candidates and (candidates[0][3] == 'fuzzy'
                           or (candidates[0][3] == 'alias' and
                               (len(normalize(query)) <= 3 or not normalize(query).isascii()))
                           or (len(candidates) == 1 and not normalize(query).isascii())):
            entry = self.entries[candidates[0][2]]
            raw_query = entry.get('query') or entry.get('name') or candidates[0][2]
        raw = await self._raw(raw_query)
        key = normalize(query)
        for team in raw:
            if team['id'] in combined:
                combined[team['id']][3].update({k: team.get(k) for k in ('name', 'slug', 'image_url')})
                continue
            name = normalize(team['name'])
            canonical = self._identity.get(name)
            entry = self.entries.get(canonical, {})
            is_main = entry.get('id') == team['id']
            score = 1000 if name == key else 500 if key in name else 200
            # Explicit academy names still match precisely; generic club names
            # rank the verified main roster above alphabetically early juniors.
            team.update(is_main=is_main)
            if canonical and is_main:
                matched = next((r for r in candidates if r[2] == canonical), None)
                if matched:
                    score = matched[0]
                    team.update(matched_alias=matched[4], match_kind=matched[3], canonical=canonical)
            combined[team['id']] = (score, int(is_main), int(entry.get('priority') or 999), team)
        ordered = sorted(combined.values(), key=lambda r: (-r[0], -r[1], r[2], str(r[3]['name']).casefold()))
        return [item[3] for item in ordered[:max(1, min(int(per_page), 50))]]

    def popular(self):
        teams = []
        for canonical in self.featured.get('teams', []):
            if canonical not in self.entries:
                continue
            team = self._record(canonical)
            if team:
                team.update(is_main=True, canonical=canonical,
                            display_name=self.entries[canonical].get('display_name') or team['name'])
                teams.append(team)
        return {'teams': teams, 'source': '常看战队',
                'description': '主流队伍与热门队伍，选中即可关注。'}
