"""Reconcile explicit host edits without replaying stale settings over the panel."""
import hashlib
import json

from .config import validate_config


KEYS = ('pandascore_token', 'fetch_interval_min', 'fetch_ahead_days',
        'web_panel_host', 'web_panel_port', 'web_panel_enabled',
        'image_push_enabled', 'push_platform_id')


def sync_host_config(store, config, *, first_run=False):
    if config is None:
        return
    supplied = {key: config.get(key) for key in KEYS if config.get(key) is not None}
    if 'fetch_ahead_days' not in supplied and config.get('fetch_ahead_hours') is not None:
        supplied['fetch_ahead_hours'] = config.get('fetch_ahead_hours')
    # Clamp legacy host values before applying the stricter current schema.
    if 'fetch_ahead_days' in supplied:
        supplied['fetch_ahead_days'] = max(1, min(int(supplied['fetch_ahead_days']), 3))
    if 'fetch_ahead_hours' in supplied:
        supplied['fetch_ahead_hours'] = max(1, min(int(supplied['fetch_ahead_hours']), 72))
    supplied = validate_config(supplied)
    fingerprints = {key: hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
                    for key, value in supplied.items()}
    previous = store.get('_host_config_fingerprints', {})
    changes = {}
    for key, value in supplied.items():
        if first_run or (key in previous and fingerprints[key] != previous[key]):
            changes[key] = value
        elif key not in previous:
            # First upgrade: retain panel values when the host still has defaults.
            if value != store._DEFAULTS[key]:
                changes[key] = value
    if changes:
        store.import_config(changes)
    store.set('_host_config_fingerprints', fingerprints)
