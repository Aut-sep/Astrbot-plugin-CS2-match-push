"""Small, temporary query hints; no network, tool routing or persona mutation."""

import re
import unicodedata
from datetime import timedelta

from .config import DEFAULT_SCHEDULE_DAYS
from .formatters import CST, now_utc
from .official_extension import denial_reason
from .team_search import TeamSearch

_CATALOG = TeamSearch(None)
_ALIASES = {
    unicodedata.normalize("NFKC", value).casefold()
    for canonical, entry in _CATALOG.entries.items()
    for value in [canonical, entry.get("name", ""), *entry.get("aliases", [])]
    if value and len(value) >= 2
}
_TEAM = re.compile(
    "|".join(
        r"(?<![a-z0-9_])" + re.escape(alias) + r"(?![a-z0-9_])"
        for alias in sorted(_ALIASES, key=lambda alias: (-len(alias), alias))
    )
    or r"(?!)",
    re.I,
)
_CS = re.compile(
    r"(?<![a-z0-9_])(?:cs(?:\s*2|go)?|counter[ -]strike|major)(?![a-z0-9_])|反恐精英", re.I
)
_OTHER_GAME = re.compile(
    r"足球|篮球|欧冠|世界杯|网球|乒乓球|羽毛球|英雄联盟|刀塔|无畏契约|王者荣耀|"
    r"(?<![a-z0-9_])(?:nba|dota\s*2|lol|valorant|soccer|football)(?![a-z0-9_])",
    re.I,
)
_WEB_ONLY = re.compile(
    r"(?:只|仅).{0,8}(?:网页|搜索|联网)|(?:不要|别|不用|禁止).{0,6}(?:插件|cs2工具)|"
    r"(?:使用|通过|用)\s*(?:tavily|网页搜索|网络搜索)|"
    r"\b(?:only use|use|using|via)\s+(?:tavily|web\s+search)",
    re.I,
)
_NO_WEB = re.compile(
    r"(?:不要|别|不用|禁止)\s*(?:再)?\s*(?:使用|通过|用)?\s*(?:tavily|网页搜索|网络搜索)(?:工具)?|"
    r"\b(?:do not|don't) use (?:tavily|web search)\b"
)
_NEWS = re.compile(r"新闻|转会|阵容|八卦|处罚|采访|预测|赔率|战术|分析|\b(?:news|transfer|roster|odds|prediction)\b")
_PREDICTION = re.compile(r"比分预测|预测[^，。；？！]{0,12}(?:比分|谁赢)|谁会赢|胜率预测|\b(?:score prediction|predict the score)\b")
_NO_TOOLS = re.compile(r"(?:不要|别|不用|禁止)\s*(?:再|调用|使用|用|任何|这些|所有){0,3}工具|\b(?:no tools|without tools|do not use tools)\b")
_FOLLOWED = re.compile(r"关注(?:了|的)?\s*(?:哪些|什么|谁|列表)|(?:哪些|什么).{0,8}(?:关注|订阅)|关注列表|订阅列表|\bfollowed teams\b")
_RESULT = re.compile(
    r"赛果|比分|比赛结果|谁赢|赢了吗|输了吗|赢没赢|赢了没|打[得的]怎么样|战绩|(?:赢|输)了|"
    r"上一?场|最近一场|(?:上次|最近).{0,8}交手|交手记录|历史交手|交手过吗|"
    r"\b(?:score|results?|who won|did .{1,40} win|last match)\b"
)
_SCHEDULE = re.compile(
    r"赛程|开赛|几点|打谁|谁打谁|哪天打|哪天交手|下一场|下次交手|什么时候.{0,8}(?:打|比赛|开始|交手)|"
    r"(?:比赛|赛事).{0,6}(?:时间|安排)|\b(?:schedule|fixtures?|upcoming|next (?:match|game)|when does .{1,40} play)\b"
)
_ASK_MATCH = re.compile(r"(?:有|有没有).{0,12}(?:比赛|赛事)|有(?:吗|么)|有打吗|打不打|\b(?:any matches|play today|play tomorrow)\b")
_DATE_OFFSETS = {
    "前天": -2, "昨天": -1, "昨晚": -1, "今日": 0, "今天": 0, "今晚": 0, "现在": 0,
    "明天": 1, "明晚": 1, "后天": 2, "yesterday": -1, "today": 0, "tonight": 0, "tomorrow": 1,
}
_DATE = re.compile(r"前天|昨天|昨晚|今日|今天|今晚|现在|明天|明晚|后天|\b(?:yesterday|today|tonight|tomorrow)\b")
_RECENT_MATCH = re.compile(r"(?:未来|最近|近期|接下来).{0,16}比赛")
_NEXT_MATCH = re.compile(r"下一场|下次交手|\bnext (?:match|game)\b")
_LAST_MATCH = re.compile(r"上一?场|最近一场|上次交手|最近一次交手|\blast match\b")
_BRACKET = re.compile(r"对阵图|对阵|晋级|淘汰赛|瑞士轮|下一阶段|当前阶段|第[一二三123]阶段|\b(?:bracket|swiss|playoffs|next stage|stage [123])\b")
_MAJOR = re.compile(r"(?<![a-z0-9_])major(?![a-z0-9_])")
_FOLLOW_UP = re.compile(
    r"(?:那|再看)?\s*(?:前天|昨天|昨晚|今天|今晚|明天|明晚|后天|下一场|上一?场|下一阶段|瑞士轮|淘汰赛)"
    r"\s*(?:呢|怎么样|有吗|有比赛吗|打谁|几点打|比分呢)?[？?。！!\s]*"
)
_STAGES = (
    (r"下一阶段|\bnext stage\b", "next"),
    (r"第[一1]阶段|阶段[一1]|\bstage 1\b", "stage1"),
    (r"第[二2]阶段|阶段[二2]|\bstage 2\b", "stage2"),
    (r"第[三3]阶段|阶段[三3]|\bstage 3\b", "stage3"),
    (r"淘汰赛|\bplayoffs\b", "knockout"),
    (r"瑞士轮|\bswiss\b", "swiss"),
    (r"全部阶段|整届|\bfull bracket\b", "all"),
)
MARKER = "[CS2 插件查询建议]"
FEATURES = {
    "get_cs2_schedule": "schedule",
    "get_cs2_match_result": "results",
    "get_cs2_followed_teams": "followed",
    "get_cs2_major_bracket": "bracket",
}


def _message(text):
    return unicodedata.normalize("NFKC", text).casefold().strip() if isinstance(text, str) and len(text) <= 4096 else ""


def _blocked(text):
    return _OTHER_GAME.search(text) or _NO_TOOLS.search(text) or _WEB_ONLY.search(_NO_WEB.sub("", text))


def query_tools(text, previous=""):
    """Match explicit CS2 intent or a short follow-up to a recognized question."""
    text, previous = _message(text), _message(previous)
    if not text or _blocked(text):
        return []
    direct = _CS.search(text) or _TEAM.search(text)
    previous_tools = query_tools(previous) if not direct and _FOLLOW_UP.fullmatch(text) else []
    follow_up = any(name != "get_cs2_followed_teams" for name in previous_tools)
    if not direct and not follow_up:
        return []
    dates = {_DATE_OFFSETS[match[0]] for match in _DATE.finditer(text)}
    past = bool(dates) and max(dates) < 0
    match_question = _ASK_MATCH.search(text) or _RECENT_MATCH.search(text) or follow_up or (
        dates and "比赛" in text
    )
    tools = []
    if _FOLLOWED.search(text):
        tools.append("get_cs2_followed_teams")
    major = _MAJOR.search(text) or (follow_up and _MAJOR.search(previous))
    bracket = major and (_BRACKET.search(text) or _SCHEDULE.search(text))
    bracket = bracket or (_CS.search(text) and any(re.search(pattern, text) for pattern, _ in _STAGES))
    if bracket:
        tools.append("get_cs2_major_bracket")
    result = _RESULT.search(text) and not _PREDICTION.search(text)
    if result or (past and (match_question or _SCHEDULE.search(text)) and not _NEWS.search(text)):
        tools.append("get_cs2_match_result")
    if not bracket and not past and (
        _SCHEDULE.search(text) or (match_question and not _NEWS.search(text) and not result)
    ):
        tools.append("get_cs2_schedule")
    return tools


def _previous_question(req):
    for message in reversed(getattr(req, "contexts", None) or []):
        if isinstance(message, dict) and message.get("role") == "user":
            return _message(message.get("content"))
    return ""


def _tool_hint(name, text, today):
    if name == "get_cs2_major_bracket":
        stage = next((stage for pattern, stage in _STAGES if re.search(pattern, text)), "current")
        return f'{name}(stage="{stage}")'
    if name == "get_cs2_followed_teams":
        return f"{name}()"
    args = []
    if not (_CS.search(text) or _TEAM.search(text)) and _FOLLOW_UP.fullmatch(text):
        args.append("战队沿用上一问")
    dates = {_DATE_OFFSETS[match[0]] for match in _DATE.finditer(text)}
    if len(dates) == 1:
        start = today + timedelta(days=dates.pop())
        args.extend((f'start_date="{start.isoformat()}"', "days=1"))
    elif not dates and name == "get_cs2_match_result" and (
        _LAST_MATCH.search(text) or re.search(r"最近.{0,8}交手|交手记录|历史交手|交手过吗", text)
    ):
        args.append("days=30")
    if (name == "get_cs2_schedule" and _NEXT_MATCH.search(text)
            or name == "get_cs2_match_result" and _LAST_MATCH.search(text)):
        args.append("limit=1")
    return f"{name}({', '.join(args)})" if args else name


def apply_guidance(plugin, event, req):
    """Only advise about active tools already offered to this specific request."""
    if not getattr(plugin, "_ai_query_guidance_enabled", True):
        return
    parts = getattr(req, "extra_user_content_parts", None)
    if not isinstance(parts, list):
        return
    if any(
        MARKER
        in str(
            part.get("text", "")
            if isinstance(part, dict)
            else getattr(part, "text", "")
        )
        for part in parts
    ):
        return
    get_text = getattr(event, "get_message_str", None)
    text = _message(get_text() if callable(get_text) else "")
    previous = _previous_question(req) if _FOLLOW_UP.fullmatch(text) else ""
    requested = query_tools(text, previous)
    offered = {
        tool.name
        for tool in (getattr(getattr(req, "func_tool", None), "tools", None) or [])
        if getattr(tool, "active", True)
    }
    tools = [
        name
        for name in requested
        if name in offered
        and denial_reason(plugin, event, FEATURES[name]) is None
        and (name == "get_cs2_followed_teams" or bool(plugin._token))
    ]
    if not tools:
        return
    today = now_utc().astimezone(CST).date()
    hints = "；".join(_tool_hint(name, text, today) for name in tools)
    match_args = ("明确问两队交手时分别传 team 和 opponent。"
                  if any(FEATURES[name] in {"schedule", "results"} for name in tools) else "")
    hint = (
        f"{MARKER} 本轮优先调用：{hints}。按用户问题补全参数。{match_args}"
        f"北京时间 {today.isoformat()}（Asia/Shanghai），近期赛程默认 {DEFAULT_SCHEDULE_DAYS} 个日历天。"
        "若同问多个日期，分别使用各自日期；今晚按开赛时间筛选。"
        "依据工具状态简洁回答：失败与空结果分别说明，比分未确认时如实提示。"
    )
    # AstrBot's temporary content is sent with this request but omitted from
    # persisted conversation history, so later unrelated questions stay clean.
    from astrbot.core.agent.message import TextPart

    parts.append(TextPart(text=hint).mark_as_temp())
