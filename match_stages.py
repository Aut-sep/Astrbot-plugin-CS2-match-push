"""Stage labels shared by charts and notifications; never treat map scores as records."""
import re

ACTIVE_RECORDS = ('0-0', '1-0', '0-1', '2-0', '1-1', '0-2', '2-1', '1-2', '2-2')
TERMINAL_RECORDS = ('3-0', '3-1', '3-2', '0-3', '1-3', '2-3')


def descriptor(match):
    # A provider may append the matchup after the stage description.
    return re.split(r'\s*[:：]\s*(?=[^:：]*\s+vs\.?\s+)', str(match.get('name') or ''), maxsplit=1, flags=re.I)[0].strip()


def round_number(match):
    found = re.search(r'\bround\s*(\d+)\b|第\s*(\d+)\s*轮', descriptor(match), re.I)
    return int(found[1] or found[2]) if found else None


def swiss_record(match, *, known_swiss=False):
    value = match.get('_swiss_record')
    if value in ACTIVE_RECORDS:
        return value
    name = descriptor(match)
    tournament = str((match.get('tournament') or {}).get('name') or '')
    is_swiss = known_swiss or bool(re.search(r'swiss|瑞士|opening stage|elimination stage|legends stage', name+' '+tournament, re.I))
    if not is_swiss:
        return None
    found = re.search(r'(?<![\d:])([0-2])\s*[-–－:：]\s*([0-2])(?![\d:])', name)
    if found and (record := f'{found[1]}-{found[2]}') in ACTIVE_RECORDS:
        return record
    number = round_number(match)
    if number == 1:
        return '0-0'
    if number == 5:
        return '2-2'
    section = ('high' if re.search(r'\bhigh\b|高位|胜者组', name, re.I) else
               'low' if re.search(r'\blow\b|低位|败者组', name, re.I) else
               'mid' if re.search(r'\bmid(?:dle)?\b|中位', name, re.I) else None)
    return {(2, 'high'): '1-0', (2, 'low'): '0-1',
            (3, 'high'): '2-0', (3, 'mid'): '1-1', (3, 'low'): '0-2',
            (4, 'high'): '2-1', (4, 'low'): '1-2'}.get((number, section))


def general_stage_label(name, tournament='', title=''):
    """Keep group and winner/loser context for any CS tournament."""
    sources = [name, title, tournament]
    combined = ' '.join(sources)
    parts = []
    for pattern,label in ((r'closed qualifier|封闭预选赛', '封闭预选赛'),
                          (r'open qualifier|公开预选赛', '公开预选赛'),
                          (r'qualifier|qualification|预选赛|资格赛', '预选赛'),
                          (r'play[ -]?in|入围赛', '入围赛'),
                          (r'playoffs?|knockout|淘汰赛', '淘汰赛'),
                          (r'group stage|小组赛|\bgroup\s+[A-Z]\b|[A-Z]组', '小组赛'),
                          (r'regular season|常规赛', '常规赛'),
                          (r'round robin|循环赛', '循环赛')):
        if re.search(pattern,combined,re.I):
            parts.append(label)
            break
    for source in sources:
        group = re.search(r'\bgroup\s+([A-Z])\b|([A-Z])\s*组',source,re.I)
        if group:
            parts.append((group[1] or group[2]).upper()+'组')
            break
    route = ''
    for source in sources:
        found = re.search(r'upper bracket|winners?[\u2019\x27]? bracket|胜者组|lower bracket|losers?[\u2019\x27]? bracket|败者组',source,re.I)
        if found:
            route = '败者组' if re.search(r'lower|loser|败者',found[0],re.I) else '胜者组'
            break
    detail = ''
    patterns = ((r'grand[ -]?finals?|总决赛','总决赛'),
                (r'consolidation final|third[ -]place|3rd[ -]place|季军赛','季军赛'),
                (r'quarter[ -]?finals?|四分之一决赛|八强','四分之一决赛'),
                (r'semi[ -]?finals?|半决赛','半决赛'),
                (r'\bfinals?\b|决赛','决赛'),
                (r'round of 32|三十二强','三十二强'),
                (r'round of 16|十六强','十六强'),
                (r'winners?[\u2019\x27]? match|胜者组比赛','胜者组比赛'),
                (r'losers?[\u2019\x27]? match|败者组比赛','败者组比赛'),
                (r'decid(?:er|ing) match|出线战','出线战'),
                (r'elimination match|淘汰战','淘汰战'),
                (r'opening match(?:es)?|揭幕战','揭幕战'))
    for source in sources:
        for pattern,label in patterns:
            if re.search(pattern,source,re.I):
                detail = label
                break
        if detail:
            break
        number = round_number({'name':source})
        if number is not None:
            detail = f'第{number}轮'
            break
    if route and not detail.startswith(('胜者组','败者组')):
        detail = route+detail
    if detail:
        parts.append(detail)
    return ' · '.join(dict.fromkeys(parts))


def match_stage_label(match):
    name = descriptor(match)
    if re.search(r'\s+vs\.?\s+',name,re.I):
        name = ''
    tournament = str((match.get('tournament') or {}).get('name') or '')
    title = str(match.get('_stage_title') or '')
    combined = title+' '+tournament+' '+name
    numbered = re.search(r'\bstage\s*([123])\b|第([一二三123])阶段', combined, re.I)
    phase = ''
    if numbered:
        digit = numbered[1] or numbered[2]
        digit = {'一': '1', '二': '2', '三': '3'}.get(digit, digit)
        phase = f"第{'一二三'[int(digit)-1]}阶段"
    elif re.search(r'opening|challenger', tournament, re.I):
        phase = '第一阶段'
    elif re.search(r'legend|elimination stage', tournament, re.I):
        phase = '第二阶段'
    record = swiss_record(match, known_swiss='瑞士轮' in title)
    if record or re.search(r'swiss|瑞士轮|opening stage|elimination stage|legends stage', combined, re.I):
        detail = '瑞士轮 '+record if record else '瑞士轮 · 战绩组待确认'
        return f'{phase} · {detail}' if phase else detail
    return general_stage_label(name,tournament,title) or title or phase
