"""Validation shared by configuration imports and the Web API.

Only editable settings are returned. Exported runtime state and unknown keys are
ignored, so existing configuration backups can still be imported safely.
"""

import ipaddress
import re
from urllib.parse import urlsplit


class ConfigError(ValueError):
    """A setting or request value supplied by the user is invalid."""


INTEGER_RANGES = {
    "remind_minutes": (1, 120),
    "web_panel_port": (1, 65535),
    "fetch_interval_min": (1, 1440),
    "fetch_ahead_days": (1, 30),
    "daily_push_days": (1, 30),
    "tournament_announce_hours": (1, 168),
}
BOOLEAN_KEYS = {
    "reschedule_notify", "web_panel_enabled", "notify_all_followed",
    "daily_push_enabled", "tournament_announce_enabled", "image_push_enabled",
    "test_mode_enabled",
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


def string(value, name: str, *, nonempty: bool = False) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{name} must be a string")
    value = value.strip()
    if nonempty and not value:
        raise ConfigError(f"{name} is required")
    return value


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
    for key, bounds in INTEGER_RANGES.items():
        if key in cfg:
            result[key] = integer(cfg[key], key, *bounds)
    if "fetch_ahead_days" not in cfg and "fetch_ahead_hours" in cfg:
        hours = integer(cfg["fetch_ahead_hours"], "fetch_ahead_hours", 1, 720)
        result["fetch_ahead_days"] = (hours + 23) // 24
    for key in BOOLEAN_KEYS:
        if key in cfg:
            if not isinstance(cfg[key], bool):
                raise ConfigError(f"{key} must be a boolean")
            result[key] = cfg[key]
    for key in STRING_KEYS:
        if key in cfg:
            result[key] = string(cfg[key], key)
    if result.get("test_target_id") and not result["test_target_id"].isascii():
        raise ConfigError("test_target_id must contain only digits")
    if result.get("test_target_id") and not result["test_target_id"].isdigit():
        raise ConfigError("test_target_id must contain only digits")
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
    if "min_tiers" in cfg:
        tiers = _strings(cfg["min_tiers"], "min_tiers", lowercase=True)
        if any(t not in {"s", "a", "b", "c", "d", "unranked"} for t in tiers):
            raise ConfigError("min_tiers contains an unknown tier")
        result["min_tiers"] = tiers
    if "push_groups" in cfg:
        groups = []
        for gid in _list(cfg["push_groups"], "push_groups"):
            gid = str(integer(gid, "group id", 1))
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
