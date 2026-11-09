"""Reconcile explicit host edits without replaying stale settings over the panel."""
import hashlib
import json

from astrbot.api import logger

from .config import ConfigError, cap_legacy_schedule_window, validate_config


KEYS = ('pandascore_token', 'fetch_interval_min', 'fetch_ahead_days',
        'web_panel_host', 'web_panel_port', 'web_panel_enabled',
        'image_push_enabled', 'push_platform_id')


def sync_host_config(store, config, *, first_run=False):
    if config is None:
        return
    supplied = {key: config.get(key) for key in KEYS if config.get(key) is not None}
    if 'fetch_ahead_days' not in supplied and config.get('fetch_ahead_hours') is not None:
        supplied['fetch_ahead_hours'] = config.get('fetch_ahead_hours')
    validated = {}
    for key, value in supplied.items():
        try:
            if key in ('fetch_ahead_days', 'fetch_ahead_hours'):
                value = cap_legacy_schedule_window(value, key)
            validated.update(validate_config({key: value}))
        except ConfigError:
            logger.warning(f'[CS] AstrBot 配置项 {key} 无效，保留已保存的设置。')

    previous = store.get('_host_config_fingerprints', {})
    if not isinstance(previous, dict):
        previous = {}
    fingerprints = previous.copy()
    changes = {}
    for key, value in validated.items():
        fingerprint = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
        fingerprints[key] = fingerprint
        if first_run or (key in previous and fingerprint != previous[key]):
            changes[key] = value
        elif key not in previous and key == 'pandascore_token' and value and not store.get(key):
            # Older installs could keep their only credential in AstrBot.
            changes[key] = value
    for key in KEYS:
        fingerprints.setdefault(key, None)
    if changes:
        store.import_config(changes)
    if fingerprints != previous:
        store.set('_host_config_fingerprints', fingerprints)
