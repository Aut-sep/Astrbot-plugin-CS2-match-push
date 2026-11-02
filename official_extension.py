"""Capabilities for optional, reply-only official QQ query accounts."""

OFFICIAL_PLATFORMS = frozenset({'qq_official', 'qq_official_webhook'})
FEATURE_LABELS = {
    'help': '菜单与帮助',
    'schedule': '赛程查询',
    'bracket': 'Major 对阵图',
    'results': '最近赛果',
    'player': '今日选手',
    'followed': '关注战队',
    'status': '查询状态',
}
DEFAULT_FEATURES = tuple(FEATURE_LABELS)


def feature_enabled(plugin, feature):
    return bool(plugin.store.get('official_extension_enabled', False)
                and feature in plugin.store.get('official_features', DEFAULT_FEATURES))


def denial_reason(plugin, event, feature=None):
    """Return a refusal before any official command can change shared state."""
    if event.get_platform_name() not in OFFICIAL_PLATFORMS:
        return None
    if feature is None:
        return '官方端仅提供查询；推送和管理操作请使用普通机器人或管理面板。'
    if not plugin.store.get('official_extension_enabled', False):
        return '官方机器人扩展尚未启用，请由管理员在 CS 管理面板中配置。'
    if event.get_platform_id() not in plugin.store.get('official_platform_ids', []):
        return '这个官方机器人尚未被选为 CS 查询入口。'
    if not feature_enabled(plugin, feature):
        return f'管理员尚未开放“{FEATURE_LABELS.get(feature, feature)}”功能。'
    return None
