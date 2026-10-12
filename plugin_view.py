"""AstrBot Dashboard bridge for the shared management panel.

Only the panel's explicit operations are exposed; Dashboard authenticates these
requests before invoking the handler. No panel token crosses the iframe bridge.
"""

import json
import re
from types import SimpleNamespace

from astrbot.api import logger

from .config import ConfigError

PLUGIN_NAME = "astrbot_plugin_cs_match"

ROUTES = (
    ("GET", r"/api/config", "get_config"),
    ("PATCH", r"/api/config", "patch_config"),
    ("GET", r"/api/config/export", "export_config"),
    ("POST", r"/api/config/import", "import_config"),
    ("GET", r"/api/matches", "get_matches"),
    ("POST", r"/api/refresh", "refresh"),
    ("POST", r"/api/push_now", "push_now"),
    ("POST", r"/api/test", "send_test"),
    ("POST", r"/api/groups", "add_group"),
    ("DELETE", r"/api/groups/(?P<gid>[0-9]+)", "remove_group"),
    ("GET", r"/api/teams/search", "search_teams"),
    ("POST", r"/api/teams", "add_team"),
    ("POST", r"/api/teams/unfollow", "remove_team"),
    ("GET", r"/api/logs", "get_logs"),
    ("POST", r"/api/notified/clear", "clear_notified"),
    ("POST", r"/api/tasks/rebuild", "rebuild_tasks"),
    ("PATCH", r"/api/matches/(?P<mid>[0-9]+)", "patch_match"),
    ("POST", r"/api/matches/(?P<mid>[0-9]+)/rebuild", "rebuild_match"),
)


class PanelRequest:
    """Minimal request data used by the framework-independent panel operations."""

    def __init__(self, method, query, body, params):
        self.method = method
        self.rel_url = SimpleNamespace(query=query)
        self.match_info = params
        self.can_read_body = body is not None
        self._body = body

    async def json(self):
        return self._body


class PluginView:
    def __init__(self, plugin):
        self.plugin = plugin
        self._registrations = []

    def register(self):
        context = self.plugin.context
        if not callable(getattr(context, "register_web_api", None)):
            return False
        try:
            from astrbot.api.web import json_response, request
        except ImportError:
            logger.info("[CS] 当前 AstrBot 不支持内嵌管理视图，可使用独立面板")
            return False
        self._json_response = json_response
        self._request = request
        for endpoint, handler, methods, desc in (
            ("panel", self.dispatch, ["GET", "POST"], "CS2 管理面板"),
            ("export", self.export, ["GET"], "导出 CS2 配置（不含令牌）"),
        ):
            route = f"/{PLUGIN_NAME}/{endpoint}"
            context.register_web_api(route, handler, methods, desc)
            self._registrations.append((route, handler))
        return True

    def unregister(self):
        # Context has no unregister API. Remove only this instance's callbacks;
        # reloading another instance may already have replaced the same route.
        registry = getattr(self.plugin.context, "registered_web_apis", None)
        if isinstance(registry, list):
            registry[:] = [entry for entry in registry if not any(
                entry[0] == route and entry[1] == handler
                for route, handler in self._registrations
            )]
        self._registrations.clear()

    async def dispatch(self):
        req = self._request
        try:
            if req.method == "GET":
                path = req.query.get("path", "")
                method = "GET"
                query = dict(req.query)
                body = None
            else:
                envelope = await req.json(default=None)
                if not isinstance(envelope, dict):
                    raise ConfigError("request body must be a JSON object")
                path = envelope.get("path", "")
                method = envelope.get("method", "POST")
                query = {}
                body = envelope.get("body")
            if not isinstance(path, str) or not isinstance(method, str):
                raise ConfigError("invalid panel operation")
            for allowed_method, pattern, handler_name in ROUTES:
                match = re.fullmatch(pattern, path)
                if method == allowed_method and match:
                    # A GET envelope cannot invoke writes. The explicit method
                    # and path pair is checked before any business code runs.
                    result = await getattr(self.plugin.panel, handler_name)(
                        PanelRequest(method, query, body, match.groupdict())
                    )
                    payload = json.loads(result.text)
                    if result.status >= 400:
                        return self._json_response(
                            {"status": "error", "message": payload.get("msg", "操作失败")},
                            status_code=result.status,
                        )
                    # Older Dashboard bridges unwrap every `data` field. A
                    # standard AstrBot envelope preserves the panel's own
                    # nested config data and readiness flags on both versions.
                    return self._json_response({"status": "ok", "data": payload})
            return self._json_response({"ok": False, "msg": "unsupported panel operation"}, status_code=404)
        except ConfigError as exc:
            return self._json_response({"ok": False, "msg": str(exc)}, status_code=400)
        except Exception:
            logger.exception("[CS] 内嵌面板请求失败")
            return self._json_response({"ok": False, "msg": "操作失败，请查看运行日志"}, status_code=500)

    async def export(self):
        return self._json_response(
            self.plugin.store.export_all(),
            headers={"Content-Disposition": 'attachment; filename="cs2-config.json"'},
        )
