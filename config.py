"""Validation shared by configuration imports and the Web API.

Only editable settings are returned. Exported runtime state and unknown keys are
ignored, so existing configuration backups can still be imported safely.
"""

from copy import deepcopy
import ipaddress
import re
from urllib.parse import urlsplit

from .official_extension import DEFAULT_FEATURES


class ConfigError(ValueError):
    """A setting or request value supplied by the user is invalid."""


DEFAULT_SCHEDULE_DAYS = 7
MAX_SCHEDULE_DAYS = 7


INTEGER_RANGES = {
    "remind_minutes": (1, 120),
    "web_panel_port": (1, 65535),
    "fetch_interval_min": (1, 1440),
    "fetch_ahead_days": (1, MAX_SCHEDULE_DAYS),
    "daily_push_days": (1, MAX_SCHEDULE_DAYS),
    "tournament_announce_hours": (1, 168),
}
BOOLEAN_KEYS = {
    "reschedule_notify", "web_panel_enabled", "notify_all_followed",
    "daily_push_enabled", "tournament_announce_enabled", "image_push_enabled",
    "test_mode_enabled", "official_extension_enabled", "official_rich_replies",
    "push_enabled", "upcoming_enabled", "finished_enabled",
    "major_stage_notify_enabled",
}
STRING_KEYS = {"pandascore_token", "web_panel_token", "test_target_id", "push_platform_id"}
STRING_LIST_KEYS = {"blacklist_teams", "blacklist_leagues"}


def integer(value, name: str, minimum: int, maximum: int | None = None) -> int:
    """Accept integers and legacy integer strings, never booleans or floats."""
    if isinstance(value, bool) or not (
        isinstance(value, int)
        or isinstance(value, str) and re.fullmatch(r"[0-9]+", value.strip())
    ):
        raise ConfigError(f"{name} must be an integer")
    try:
        number = int(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a valid integer") from exc
    if number < minimum or maximum is not None and number > maximum:
        limit = f"{minimum}~{maximum}" if maximum is not None else f">= {minimum}"
        raise ConfigError(f"{name} must be {limit}")
    return number


def cap_legacy_schedule_window(value, key: str) -> int:
    maximum = MAX_SCHEDULE_DAYS * 24 if key == "fetch_ahead_hours" else INTEGER_RANGES[key][1]
    return min(integer(value, key, 1), maximum)


def migrate_imported_schedule_windows(cfg):
    """Cap old backup ranges before validating the rest of the configuration."""
    if not isinstance(cfg, dict):
        return cfg
    cfg = deepcopy(cfg)
    fetch_key = "fetch_ahead_days" if "fetch_ahead_days" in cfg else "fetch_ahead_hours"
    for key in (fetch_key, "daily_push_days"):
        if key in cfg:
            cfg[key] = cap_legacy_schedule_window(cfg[key], key)

    groups = cfg.get("group_rules")
    presets = cfg.get("rule_presets")
    rules = list(groups.values()) if isinstance(groups, dict) else []
    if isinstance(presets, list):
        rules.extend(preset.get("rules") for preset in presets if isinstance(preset, dict))
    for rule in rules:
        if isinstance(rule, dict) and "daily_push_days" in rule:
            rule["daily_push_days"] = cap_legacy_schedule_window(rule["daily_push_days"], "daily_push_days")
    return cfg


def string(value, name: str, *, nonempty: bool = False) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{name} must be a string")
    value = value.strip()
    if nonempty and not value:
        raise ConfigError(f"{name} is required")
    return value


def message_target_id(value, name: str = "target id") -> str:
    """Active notifications target ordinary QQ numbers, never official OpenIDs."""
    return str(integer(value, name, 1))


def _list(value, name: str) -> list:
    if not isinstance(value, list):
        raise ConfigError(f"{name} must be a list")
    return value


def _strings(value, name: str, *, lowercase: bool = False) -> list[str]:
    result = []
    for item in _list(value, name):
        item = string(item, name, nonempty=True)
        item = item.lower() if lowercase else item
        if item not in result:
            result.append(item)
    return result


def _host(value) -> str:
    host = string(value, "web_panel_host", nonempty=True)
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    labels = host.rstrip(".").split(".")
    if len(host) > 253 or not all(
        re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", label)
        for label in labels
    ):
        raise ConfigError("web_panel_host must be an IP address or hostname")
    return host


def validate_config(cfg: dict) -> dict:
    if not isinstance(cfg, dict):
        raise ConfigError("configuration must be a JSON object")
    result = {}
    if "rule_presets" in cfg:
        from .group_rules import RULE_KEYS
        presets = _list(cfg["rule_presets"], "rule_presets")
        if len(presets) > 50:
            raise ConfigError("最多保存 50 个自定义预设")
        ids, names, normalized = set(), set(), []
        for preset in presets:
            if not isinstance(preset, dict) or set(preset) != {"id", "name", "rules"}:
                raise ConfigError("预设需要 id、name 和 rules")
            identity = string(preset["id"], "preset id", nonempty=True)
            name = string(preset["name"], "preset name", nonempty=True)
            if (not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", identity)
                    or identity.startswith("builtin-")):
                raise ConfigError("预设 ID 无效")
            if len(name) > 40:
                raise ConfigError("预设名称不能超过 40 个字符")
            if identity in ids or name.casefold() in names:
                raise ConfigError("预设 ID 和名称不能重复")
            rules = preset["rules"]
            if not isinstance(rules, dict) or set(rules) != RULE_KEYS:
                raise ConfigError("预设必须包含完整的群推送规则，不能包含账号或接收群信息")
            values = validate_config(rules)
            if values["filter_mode"] == "followed" and not values["followed_teams"]:
                raise ConfigError("只看关注战队的预设至少需要选择一支战队")
            ids.add(identity)
            names.add(name.casefold())
            normalized.append({"id": identity, "name": name, "rules": values})
        result["rule_presets"] = normalized
    if "filter_mode" in cfg:
        mode = string(cfg["filter_mode"], "filter_mode")
        if mode not in {"tiers", "followed", "tiers_or_followed"}:
            raise ConfigError("filter_mode must be tiers, followed or tiers_or_followed")
        result["filter_mode"] = mode
    if "group_rules" in cfg:
        from .group_rules import RULE_KEYS
        if not isinstance(cfg["group_rules"], dict):
            raise ConfigError("group_rules must be an object")
        rules = {}
        for identity, overrides in cfg["group_rules"].items():
            gid = message_target_id(identity, "group id")
            if not isinstance(overrides, dict) or set(overrides) - RULE_KEYS:
                raise ConfigError("group rule contains invalid fields")
            # Missing keys inherit; null/false/[] must never be confused.
            if any(value is None for value in overrides.values()):
                raise ConfigError("remove a group rule key to inherit; null is not a value")
            rules[gid] = validate_config(overrides)
        result["group_rules"] = rules
    for key, bounds in INTEGER_RANGES.items():
        if key in cfg:
            result[key] = integer(cfg[key], key, *bounds)
    if "fetch_ahead_days" not in cfg and "fetch_ahead_hours" in cfg:
        hours = integer(cfg["fetch_ahead_hours"], "fetch_ahead_hours", 1, MAX_SCHEDULE_DAYS * 24)
        result["fetch_ahead_days"] = (hours + 23) // 24
    for key in BOOLEAN_KEYS:
        if key in cfg:
            if not isinstance(cfg[key], bool):
                raise ConfigError(f"{key} must be a boolean")
            result[key] = cfg[key]
    for key in STRING_KEYS:
        if key in cfg:
            result[key] = string(cfg[key], key)
    if result.get("test_target_id"):
        result["test_target_id"] = message_target_id(result["test_target_id"], "test_target_id")
    if "web_panel_host" in cfg:
        result["web_panel_host"] = _host(cfg["web_panel_host"])
    if "test_target_type" in cfg:
        value = string(cfg["test_target_type"], "test_target_type")
        if value not in {"private", "group"}:
            raise ConfigError("test_target_type must be private or group")
        result["test_target_type"] = value
    for key in STRING_LIST_KEYS:
        if key in cfg:
            result[key] = _strings(cfg[key], key, lowercase=True)
    if "official_platform_ids" in cfg:
        result["official_platform_ids"] = _strings(cfg["official_platform_ids"], "official_platform_ids")
    if "official_features" in cfg:
        features = _strings(cfg["official_features"], "official_features")
        if any(feature not in DEFAULT_FEATURES for feature in features):
            raise ConfigError("official_features contains an unknown feature")
        result["official_features"] = features
    if "min_tiers" in cfg:
        tiers = _strings(cfg["min_tiers"], "min_tiers", lowercase=True)
        if any(t not in {"s", "a", "b", "c", "d", "unranked"} for t in tiers):
            raise ConfigError("min_tiers contains an unknown tier")
        result["min_tiers"] = tiers
    if "push_groups" in cfg:
        groups = []
        for gid in _list(cfg["push_groups"], "push_groups"):
            gid = message_target_id(gid, "group id")
            if gid not in groups:
                groups.append(gid)
        result["push_groups"] = groups
    if "daily_push_times" in cfg:
        times = _strings(cfg["daily_push_times"], "daily_push_times")
        if any(not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", t) for t in times):
            raise ConfigError("daily_push_times must use HH:MM (00:00~23:59)")
        result["daily_push_times"] = sorted(times)
    if "custom_streams" in cfg:
        streams = []
        for stream in _list(cfg["custom_streams"], "custom_streams"):
            if not isinstance(stream, dict):
                raise ConfigError("custom_streams entries must be objects")
            url = string(stream.get("url"), "stream url", nonempty=True)
            try:
                parsed = urlsplit(url)
                valid_url = parsed.scheme in {"http", "https"} and bool(parsed.hostname)
            except ValueError:
                valid_url = False
            if not valid_url:
                raise ConfigError("stream url must use http:// or https://")
            streams.append({
                "name": string(stream.get("name", ""), "stream name"),
                "url": url,
            })
        result["custom_streams"] = streams
    if "followed_teams" in cfg:
        teams = []
        for team in _list(cfg["followed_teams"], "followed_teams"):
            if isinstance(team, str):
                teams.append(string(team, "team name", nonempty=True))
            elif isinstance(team, dict):
                name = string(team.get("name"), "team name", nonempty=True)
                if team.get("id") is None:
                    # Old exports represent name-only subscriptions with a null ID.
                    teams.append(name)
                else:
                    teams.append({
                        "id": integer(team.get("id"), "team id", 1),
                        "name": name,
                        "slug": string(team.get("slug", ""), "team slug"),
                        **({"image_url": string(team["image_url"], "team image URL")}
                           if team.get("image_url") else {}),
                    })
            else:
                raise ConfigError("followed_teams entries must be names or objects")
        result["followed_teams"] = teams
    if "custom_remind_minutes" in cfg:
        reminders = cfg["custom_remind_minutes"]
        if not isinstance(reminders, dict):
            raise ConfigError("custom_remind_minutes must be an object")
        result["custom_remind_minutes"] = {
            str(integer(mid, "match id", 1)): integer(minutes, "remind_minutes", 1, 120)
            for mid, minutes in reminders.items()
        }
    return result
