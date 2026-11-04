"""Small, temporary query hints; no network, tool routing or persona mutation."""

import re
import unicodedata

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
        for alias in sorted(_ALIASES, key=len, reverse=True)
    )
    or r"(?!)",
    re.I,
)
_CS = re.compile(
    r"(?<![a-z0-9_])(?:cs\s*2|csgo|counter[ -]strike|major)(?![a-z0-9_])|反恐精英", re.I
)
_OTHER_GAME = re.compile(
    r"足球|篮球|欧冠|世界杯|网球|乒乓球|羽毛球|英雄联盟|刀塔|无畏契约|王者荣耀|(?<![a-z0-9_])(?:nba|dota\s*2|lol|valorant)(?![a-z0-9_])",
    re.I,
)
_WEB_ONLY = re.compile(
    r"(?:只|仅).{0,8}(?:网页|搜索|联网)|(?:不要|别|不用|禁止).{0,6}(?:插件|cs2工具)|(?:使用|通过|用)\s*(?:tavily|网页搜索|网络搜索)|(?:only|use|using|via)\s+(?:tavily|web\s+search)",
    re.I,
)
_NEWS = re.compile(r"新闻|转会|阵容|八卦|处罚|采访|预测|赔率|战术|分析")
_RESULT = re.compile(
    r"赛果|比分|比赛结果|谁赢|赢了吗|输了吗|赢没赢|赢了没|打得怎么样|打的怎么样|战绩|(?:赢|输)了"
)
_SCHEDULE = re.compile(
    r"赛程|开赛|几点|打谁|什么时候.{0,8}(?:打|比赛|开始)|(?:比赛|赛事).{0,6}(?:时间|安排)"
)
_ASK_MATCH = re.compile(r"(?:有|有没有).{0,12}比赛")
_RECENT_MATCH = re.compile(r"(?:今天|今晚|明天|后天|未来|最近|近期|接下来).{0,16}比赛")
_BRACKET = re.compile(r"对阵|晋级|淘汰赛|瑞士轮|下一阶段|当前阶段|第[一二三123]阶段")
MARKER = "[CS2 插件查询建议]"
FEATURES = {
    "get_cs2_schedule": "schedule",
    "get_cs2_match_result": "results",
    "get_cs2_followed_teams": "followed",
    "get_cs2_major_bracket": "bracket",
}


def query_tools(text):
    """Conservative current-message matching, not intent classification by an LLM."""
    if not isinstance(text, str) or not text or len(text) > 4096:
        return []
    text = unicodedata.normalize("NFKC", text).casefold()
    if _OTHER_GAME.search(text) or _WEB_ONLY.search(text):
        return []
    if re.search(
        r"(?:不要|别|不用|禁止)(?:再|调用|使用|用|任何|这些|所有){0,3}工具", text
    ):
        return []
    if not (_CS.search(text) or _TEAM.search(text)):
        return []
    tools = []
    if "关注" in text and re.search(r"战队|队伍|哪些|列表|关注谁", text):
        tools.append("get_cs2_followed_teams")
    if "major" in text and _BRACKET.search(text):
        tools.append("get_cs2_major_bracket")
    if _RESULT.search(text):
        tools.append("get_cs2_match_result")
    if _SCHEDULE.search(text) or (
        not _NEWS.search(text)
        and (_RECENT_MATCH.search(text) or _ASK_MATCH.search(text))
    ):
        tools.append("get_cs2_schedule")
    return tools


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
    requested = query_tools(get_text() if callable(get_text) else "")
    offered = {
        tool.name
        for tool in getattr(getattr(req, "func_tool", None), "tools", [])
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
    today = now_utc().astimezone(CST).date().isoformat()
    hint = (
        f"{MARKER} 当前消息匹配 CS2 结构化赛事查询，本轮可用工具：{', '.join(tools)}。"
        "在不违反用户明确指定的数据来源、人格要求或管理员限制时，"
        "此类赛程/赛果/关注/Major 对阵查询优先使用上列插件工具，再按真实结果回答；"
        "不要仅用网页新闻摘要代替赛程或比分。新闻、转会等插件不覆盖的信息仍可网页搜索。"
        f"北京时间今天是 {today}（Asia/Shanghai）。"
        "未指定日期的‘最近有没有比赛’按未来7个日历天查询；今晚、明天等明确日期按相应日期且 days=1，"
        "过去的赛果问题按用户实际日期查询，不能查询未来赛程代替赛果。"
        "依据返回的 status/source/fetched_at/is_cached 说明来源和读取状态；"
        "失败不能说没有比赛，不推测未知比分，若另用搜索须说明来源已切换。"
        "这只是本轮查询建议，不更改人格、工具开关或其他消息的处理方式。"
    )
    # AstrBot's temporary content is sent with this request but omitted from
    # persisted conversation history, so later unrelated questions stay clean.
    from astrbot.core.agent.message import TextPart

    parts.append(TextPart(text=hint).mark_as_temp())
