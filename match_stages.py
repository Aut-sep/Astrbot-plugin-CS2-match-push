"""Stage labels shared by charts and notifications; never treat map scores as records."""
import re

ACTIVE_RECORDS = ('0-0', '1-0', '0-1', '2-0', '1-1', '0-2', '2-1', '1-2', '2-2')
TERMINAL_RECORDS = ('3-0', '3-1', '3-2', '0-3', '1-3', '2-3')


def descriptor(match):
    # A provider may append the matchup after the stage description.
    return re.split(r'\s*[:：]\s*(?=[A-Za-z\u4e00-\u9fff])', str(match.get('name') or ''), maxsplit=1)[0].strip()


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


def match_stage_label(match):
    name = descriptor(match)
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
    for pattern, label in ((r'quarter[ -]?final|四分之一决赛|八强', '四分之一决赛'),
                           (r'semi[ -]?final|半决赛', '半决赛'),
                           (r'grand final|总决赛', '总决赛'),
                           (r'\bfinal\b|决赛', '决赛')):
        if re.search(pattern, name, re.I):
            prefix = '淘汰赛 · ' if re.search(r'playoff|knockout|淘汰赛', combined, re.I) else ''
            return prefix+label
    if title:
        return title
    if re.search(r'playoff|knockout|淘汰赛', tournament, re.I):
        return '淘汰赛'
    return name if name and not re.search(r'\bvs\.?\b', name, re.I) else ''
