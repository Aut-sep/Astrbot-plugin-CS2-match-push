"""
web_panel.py — Web 管理面板
与内嵌 pages/management/ 共用页面，提供基于 aiohttp 的轻量 WebPanel 服务器。

访问地址：http://<host>:<port>/
"""

import asyncio
from collections import deque
import hmac
import json
import secrets
from datetime import datetime, timezone, timedelta
from pathlib import Path

from aiohttp import web
from yarl import URL

from astrbot.api import logger

from .config import (
    ConfigError, MAX_SCHEDULE_DAYS, integer, message_target_id, migrate_imported_schedule_windows, string, validate_config,
)
from .client import PandaScoreClient
from .delivery import DeliveryError
from .group_rules import groups_for
from .panel_previews import require_fresh_schedule, timed_preview

CST = timezone(timedelta(hours=8))


# Resolve resources from the plugin location, independent of AstrBot's cwd.
PANEL_DIR = Path(__file__).parent / "pages" / "management"



WEB_PANEL_LOGIN_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>CS2 比赛推送 · 登录</title>
<style>
:root{color-scheme:dark;--bg:#13171c;--grid:rgb(231 226 214/3.5%);--surface:#1b2027;--field:#11151a;--text:#e7e2d6;--muted:#949a9f;--border:#3d4651;--accent:#f0a531;--ink:#1a1206;--danger:#ff7a70;font-family:"HarmonyOS Sans SC","MiSans","PingFang SC","Microsoft YaHei UI","Microsoft YaHei",system-ui,sans-serif}
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background-color:var(--bg);background-image:linear-gradient(var(--grid) 1px,transparent 1px),linear-gradient(90deg,var(--grid) 1px,transparent 1px);background-size:32px 32px;color:var(--text)}
form{width:min(420px,calc(100vw - 32px));padding:30px 32px;background:var(--surface);border:1px solid var(--border);border-top:3px solid var(--accent);border-radius:2px}h1{font-size:22px;margin:0 0 10px}p{color:var(--muted);font-size:14px;line-height:1.7;margin:0 0 22px}label{display:block;font-size:13px;font-weight:600;margin-bottom:8px}input{width:100%;padding:11px 12px;border:1px solid var(--border);border-radius:2px;background:var(--field);color:var(--text);font:inherit}input:focus{border-color:var(--accent)}input:focus-visible,button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}button{width:100%;padding:12px;margin-top:20px;border:0;background:var(--accent);color:var(--ink);font-family:inherit;font-size:15px;font-weight:700;cursor:pointer;clip-path:polygon(0 0,calc(100% - 10px) 0,100% 10px,100% 100%,10px 100%,0 calc(100% - 10px))}button:hover{background:#ffb84d}.error{color:var(--danger);font-size:13px;min-height:20px;margin-top:12px}.note{margin:18px 0 0;font-size:12px}code{font-family:"Cascadia Mono",Consolas,monospace;padding:1px 5px;background:var(--field);border:1px solid var(--border)}
</style>
</head>
<body>
<form method="post" action="/login">
<h1>CS2 比赛推送</h1><p>使用管理令牌进入独立面板。</p>
<label for="token">管理令牌</label><input id="token" name="token" type="password" autocomplete="off" required autofocus>
<button type="submit">进入管理面板</button><div class="error" role="alert">{error}</div>
<p class="note">管理员私聊机器人发送 <code>/cs面板</code> 获取令牌。</p>
</form>
</body></html>"""

PANEL_PAGE_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
PANEL_SESSION_COOKIE = "cs_panel_session"


# ─────────────────────────────────────────
# Web 面板服务器
# ─────────────────────────────────────────

class WebPanel:
    def __init__(self, plugin: "CSMatchPlugin"):
        self.plugin = plugin
        self.app = web.Application(middlewares=[self._auth_middleware, self._input_middleware])
        self._runner = None
        self._site   = None
        self._bind_host = None
        self._bind_port = None
        self._reconfigure_lock = asyncio.Lock()
        self._config_lock = asyncio.Lock()
        session_key = plugin.store.get("_web_panel_session_key")
        if not isinstance(session_key, str) or not session_key:
            session_key = secrets.token_hex(32)
            plugin.store.set("_web_panel_session_key", session_key)
        self._session_key = session_key.encode()
        self._logs = deque(maxlen=500)
        self._setup_routes()

    @web.middleware
    async def _auth_middleware(self, req, handler):
        if not req.path.startswith("/api/"):
            return await handler(req)

        explicit_token = self._explicit_token(req)
        expected = self._panel_token()
        if explicit_token is not None:
            if not self._token_matches(explicit_token, expected):
                return self._json({"ok": False, "msg": "unauthorized"}, 401)
        else:
            if not self._session_matches(req, expected):
                return self._json({"ok": False, "msg": "unauthorized"}, 401)
            if req.method not in {"GET", "HEAD", "OPTIONS"} and not self._cookie_write_allowed(req):
                return self._json({"ok": False, "msg": "cross-origin request denied"}, 403)
        return await handler(req)

    @web.middleware
    async def _input_middleware(self, req, handler):
        try:
            return await handler(req)
        except ConfigError as exc:
            return self._json({"ok": False, "msg": str(exc)}, 400)

    async def _body(self, req, *, allow_empty=False) -> dict:
        if allow_empty and not req.can_read_body:
            return {}
        try:
            body = await req.json()
        except (ValueError, UnicodeDecodeError) as exc:
            raise ConfigError("invalid json") from exc
        if not isinstance(body, dict):
            raise ConfigError("request body must be a JSON object")
        return body

    def _explicit_token(self, req) -> str | None:
        auth = req.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        query_token = req.rel_url.query.get("token", "")
        if query_token:
            return query_token.strip()
        return None

    def _panel_token(self) -> str:
        return str(self.plugin.store.get("web_panel_token") or "").strip()

    @staticmethod
    def _token_matches(token: str, expected: str) -> bool:
        return bool(token and expected and hmac.compare_digest(token.encode(), expected.encode()))

    def _session_value(self, token: str) -> str:
        return hmac.digest(self._session_key, token.encode(), "sha256").hex()

    def _session_matches(self, req, expected: str) -> bool:
        cookie = req.cookies.get(PANEL_SESSION_COOKIE, "")
        return bool(expected and self._token_matches(cookie, self._session_value(expected)))

    def _set_session_cookie(self, response, token):
        response.set_cookie(PANEL_SESSION_COOKIE, self._session_value(token), httponly=True,
                            samesite="Lax", max_age=30 * 24 * 3600)
        response.del_cookie("cs_panel_token")

    @staticmethod
    def _cookie_write_allowed(req) -> bool:
        # The browser reports its actual origin relationship even behind a TLS proxy.
        site = req.headers.get("Sec-Fetch-Site")
        if site in {"same-origin", "same-site", "cross-site"}:
            return site == "same-origin"
        origin = req.headers.get("Origin")
        if not origin:
            return True
        try:
            return URL(origin).origin() == URL(f"{req.scheme}://{req.host}").origin()
        except ValueError:
            return False

    def _setup_routes(self):
        a = self.app.router
        a.add_get("/",                          self.index)
        a.add_post("/login",                     self.login)
        a.add_get("/app.js",                    self.asset)
        a.add_get("/setup.js",                  self.asset)
        a.add_get("/group-rules.js",            self.asset)
        a.add_get("/presets.js",                self.asset)
        a.add_get("/style.css",                 self.asset)
        a.add_get("/api/config",                self.get_config)
        a.add_patch("/api/config",              self.patch_config)
        a.add_get("/api/config/export",         self.export_config)
        a.add_post("/api/config/import",        self.import_config)
        a.add_post("/api/setup/check",          self.check_setup)
        a.add_get("/api/matches",               self.get_matches)
        a.add_get("/api/tools/preview",         self.preview_tool)
        a.add_post("/api/refresh",              self.refresh)
        a.add_post("/api/push_now",             self.push_now)
        a.add_post("/api/test",                 self.send_test)
        a.add_post("/api/groups",               self.add_group)
        a.add_delete("/api/groups/{gid}",       self.remove_group)
        a.add_get("/api/teams/search",          self.search_teams)   # 必须在 {name} 之前
        a.add_get("/api/teams/popular",         self.popular_teams)
        a.add_post("/api/teams",                self.add_team)
        a.add_post("/api/teams/unfollow",       self.remove_team)
        a.add_delete("/api/teams/{name}",       self.remove_team)    # 保留兼容
        a.add_get("/api/logs",                  self.get_logs)
        a.add_post("/api/notified/clear",       self.clear_notified)
        a.add_post("/api/tasks/rebuild",        self.rebuild_tasks)
        # 单场比赛：自定义提醒时间 & 手动重建
        a.add_patch("/api/matches/{mid}",       self.patch_match)
        a.add_post("/api/matches/{mid}/rebuild",self.rebuild_match)

    def _json(self, data: dict, status=200):
        return web.Response(
            text=json.dumps(data, ensure_ascii=False),
            content_type="application/json",
            status=status
        )

    async def index(self, req):
        expected = self._panel_token()
        if "token" in req.rel_url.query:
            token = req.rel_url.query["token"].strip()
            valid = self._token_matches(token, expected)
            params = [(key, value) for key, value in req.rel_url.query.items() if key != "token"]
            if not valid:
                params.append(("login", "invalid"))
            response = web.Response(status=303, headers={**PANEL_PAGE_HEADERS,
                                                        "Location": str(req.rel_url.with_query(params))})
            if valid:
                self._set_session_cookie(response, expected)
            return response

        explicit_token = self._explicit_token(req)
        bearer_valid = self._token_matches(explicit_token or "", expected)
        legacy_valid = self._token_matches(req.cookies.get("cs_panel_token", ""), expected)
        if not bearer_valid and not legacy_valid and not self._session_matches(req, expected):
            return self._login_page(invalid=req.rel_url.query.get("login") == "invalid")
        html = (PANEL_DIR / "index.html").read_text(encoding="utf-8")
        response = web.Response(text=html, content_type="text/html", charset="utf-8", headers=PANEL_PAGE_HEADERS)
        if bearer_valid or legacy_valid:
            self._set_session_cookie(response, expected)
        return response

    async def login(self, req):
        if not self._cookie_write_allowed(req):
            return self._login_page(invalid=True, status=403)
        body = await req.post()
        token = body.get("token", "")
        expected = self._panel_token()
        if not isinstance(token, str) or not self._token_matches(token.strip(), expected):
            return self._login_page(invalid=True, status=401)
        response = web.Response(status=303, headers={**PANEL_PAGE_HEADERS, "Location": "/"})
        self._set_session_cookie(response, expected)
        return response

    @staticmethod
    def _login_page(*, invalid=False, status=200):
        return web.Response(
            text=WEB_PANEL_LOGIN_HTML.replace("{error}", "令牌无效，请重试。" if invalid else ""),
            content_type="text/html", charset="utf-8", status=status,
            headers={**PANEL_PAGE_HEADERS, "Referrer-Policy": "same-origin"},
        )

    async def asset(self, req):
        # Both paths are fixed routes, never an arbitrary filesystem path.
        return web.FileResponse(PANEL_DIR / req.path.lstrip("/"))

    async def get_config(self, req):
        data = self.plugin.store.export_all()
        official_platforms, official_platforms_note = await self._official_platforms()
        return self._json({"ok": True, "data": data,
                           "token_configured": bool(self.plugin.store.get("pandascore_token")),
                           "team_logos": getattr(getattr(self.plugin, "team_logos", None), "web_images", {}),
                           "team_logo_dark_names": getattr(getattr(self.plugin, "team_logos", None), "web_dark_names", []),
                           "web_token_configured": bool(self.plugin.store.get("web_panel_token")),
                           "embedded_available": bool(getattr(self.plugin, "embedded_available", False)),
                           "official_platforms": official_platforms,
                           "official_platforms_note": official_platforms_note})

    async def _official_platforms(self):
        """Expose IDs and display names for configured QQ official instances only."""
        try:
            from .delivery import _get_platforms

            platforms = await _get_platforms(self.plugin.context)
            official = []
            for platform in platforms:
                meta = platform.meta()
                if getattr(meta, "name", None) not in {"qq_official", "qq_official_webhook"}:
                    continue
                platform_id = getattr(meta, "id", None)
                name = getattr(meta, "name", None)
                if isinstance(platform_id, str) and platform_id:
                    official.append({"id": platform_id, "name": name or "QQ 官方机器人"})
            if official:
                return official, "只显示 AstrBot 已配置的 QQ 官方实例；不会读取或展示登录凭据。"
            return [], "当前没有已连接的 QQ 官方机器人实例；请先在 AstrBot 配置官方机器人。"
        except Exception:
            return [], "暂时无法读取 AstrBot 官方机器人实例，请稍后刷新；已保存的实例选择会保留。"

    async def preview_tool(self, req):
        result = await timed_preview(self.plugin, req.rel_url.query)
        return self._json({"ok": True, **result})

    async def patch_config(self, req):
        return await self._apply_config(req, imported=False)

    async def export_config(self, req):
        data = self.plugin.store.export_all()
        return self._json({"ok": True, "data": data})

    async def import_config(self, req):
        return await self._apply_config(req, imported=True)

    async def _apply_config(self, req, *, imported: bool):
        raw = await self._body(req)
        body = validate_config(migrate_imported_schedule_windows(raw) if imported else raw)
        push_id = body.get("push_platform_id")
        if push_id:
            from .delivery import _get_platforms
            try:
                platforms = await _get_platforms(self.plugin.context)
            except DeliveryError:
                platforms = []
            for platform in platforms:
                meta = platform.meta()
                if getattr(meta, "id", None) == push_id and getattr(meta, "name", None) != "aiocqhttp":
                    raise ConfigError("赛事主动推送请选择普通 OneBot 机器人，官方实例请在官方扩展区域选择。")
        async with self._config_lock:
            token_regenerated = body.get("web_panel_token") == ""
            if token_regenerated:
                body["web_panel_token"] = secrets.token_urlsafe(24)
            previous = {key: self.plugin.store.get(key) for key in body}
            changed = {key for key, value in body.items() if previous[key] != value}
            web_changed = changed & {"web_panel_host", "web_panel_port", "web_panel_enabled"}
            self.plugin.store.import_config(body)
            web_reconfigured = False
            if web_changed:
                try:
                    web_reconfigured = await self.plugin.apply_web_panel_config()
                except Exception as exc:
                    # A failed bind leaves the previous listener intact. Keep the
                    # saved settings consistent with it before reporting failure.
                    self.plugin.store.import_config(previous)
                    logger.error(f"[CS] Web 面板配置应用失败: {exc}")
                    return self._json({"ok": False, "msg": f"web panel reconfigure failed: {exc}"}, 500)
            runtime_changed = changed - {"rule_presets", "web_panel_token"}
            if runtime_changed:
                await self.plugin.reload_runtime_config()
            filters = {
                "min_tiers", "blacklist_teams", "blacklist_leagues",
                "notify_all_followed", "followed_teams", "filter_mode", "group_rules", "push_groups", "push_enabled", "upcoming_enabled", "finished_enabled",
            }
            if changed & (filters | {"remind_minutes", "custom_remind_minutes"}):
                self.plugin.rebuild_all_match_tasks()
            elif (imported and runtime_changed) or changed & {"pandascore_token", "fetch_ahead_days"}:
                self.plugin.schedule_refresh("web_import_config" if imported else "web_patch_config")
            if changed & (filters | {"tournament_announce_enabled", "tournament_announce_hours"}):
                self.plugin.rebuild_tournament_tasks()
        response = self._json({"ok": True, "restart_required": False, "web_reconfigured": web_reconfigured,
                               "web_token_regenerated": token_regenerated})
        if "web_panel_token" in changed:
            self._set_session_cookie(response, body["web_panel_token"])
        return response

    async def get_matches(self, req):
        schedule_state = self.plugin.get_panel_schedule_state()
        return self._json({
            "ok": True,
            "matches": self.plugin.get_panel_matches(),
            **schedule_state,
        })

    async def check_setup(self, req):
        """Check candidate/saved PandaScore credentials without changing config."""
        body = await self._body(req, allow_empty=True)
        candidate = body.get("pandascore_token")
        if candidate is not None and not isinstance(candidate, str):
            raise ConfigError("pandascore_token 必须是字符串")
        token = candidate.strip() if isinstance(candidate, str) and candidate.strip() else ""
        source = {"ok": False, "message": "请填写 PandaScore Token，或先在插件配置中保存 Token。"}
        if not token:
            token = str(self.plugin.store.get("pandascore_token") or "").strip()

        if token:
            client = PandaScoreClient(token)
            try:
                matches = await asyncio.wait_for(
                    client.get_upcoming_matches(per_page=1), timeout=15,
                )
                if matches is None:
                    source = {
                        "ok": False,
                        "message": "PandaScore 验证失败，请检查 Token 是否有效及服务器网络是否可访问。",
                    }
                else:
                    source = {"ok": True, "message": "PandaScore 连接正常。"}
            except asyncio.TimeoutError:
                source = {
                    "ok": False,
                    "message": "PandaScore 请求超时，请检查服务器网络后重试。",
                }
            except Exception:
                # Do not expose exception text: request/client errors can include
                # URLs or other details, and candidate tokens must stay private.
                source = {
                    "ok": False,
                    "message": "PandaScore 检查失败，请检查 Token 和服务器网络后重试。",
                }
            finally:
                await client.close()

        groups = self.plugin.store.get("push_groups")
        group_count = len(groups) if isinstance(groups, (list, tuple)) else 0
        group_ok = group_count > 0
        group_status = {
            "ok": group_ok,
            "count": group_count,
            "message": "已配置推送群。" if group_ok else "尚未配置推送群，请添加接收比赛通知的群。",
        }
        platform_status = {
            "ok": None,
            "message": "请在 AstrBot 确认机器人已连接，并且已加入接收群。",
        }
        return self._json({
            "ok": True,
            "source": source,
            "groups": group_status,
            "platform": platform_status,
        })

    async def refresh(self, req):
        self.plugin.schedule_refresh("web_refresh")
        return self._json({"ok": True})

    async def push_now(self, req):
        """Queue a daily schedule for the selected active group or all active groups."""
        body = await self._body(req, allow_empty=True)
        days = integer(body.get("days", 1), "days", 1, MAX_SCHEDULE_DAYS)
        gid = string(body.get('group_id', ''), 'group_id')
        if gid and gid not in self.plugin.store.get_groups():
            raise ConfigError('请选择已配置的接收群')
        groups = groups_for(self.plugin.store)
        if gid and gid not in groups:
            raise ConfigError("该群已暂停推送。")
        if not groups:
            raise ConfigError("没有启用推送的接收群。")
        require_fresh_schedule(self.plugin)
        if days > self.plugin._fetch_ahead:
            raise ConfigError(f"当前仅获取未来 {self.plugin._fetch_ahead} 天的赛程，请缩短推送天数。")
        if not self.plugin.has_daily_matches_to_push(days, group_id=gid or None):
            return self._json({"ok": True, "skipped": True, "msg": "no matches to push"})
        self.plugin.schedule_instant_push(days, "web_push_now", group_id=gid or None)
        return self._json({"ok": True})

    async def send_test(self, req):
        body = await self._body(req, allow_empty=True)
        action = string(body.get("action", ""), "action") or "赛前"
        days = integer(body.get("days", 1), "days", 1, MAX_SCHEDULE_DAYS)
        if not self.plugin.is_test_mode_enabled():
            return self._json({"ok": False, "msg": "测试模式未开启"}, 400)
        if not self.plugin.has_test_target():
            return self._json({"ok": False, "msg": "请先配置测试 QQ 或群号"}, 400)
        valid = {"赛前", "赛果", "变更", "开幕", "日报", "全部"}
        if action not in valid:
            return self._json({"ok": False, "msg": "未知测试动作"}, 400)
        gid = string(body.get('group_id', ''), 'group_id')
        if gid and gid not in self.plugin.store.get_groups():
            raise ConfigError('请选择已配置的接收群')
        from .group_rules import Rules, scope
        try:
            with scope(Rules(self.plugin.store, gid or None)):
                msg = await self.plugin.run_test_push(action, days)
        except DeliveryError as exc:
            return self._json({"ok": False, "msg": str(exc)}, 502)
        return self._json({"ok": True, "msg": msg})

    async def add_group(self, req):
        body = await self._body(req)
        gid = message_target_id(body.get("gid"), "group id")
        added = self.plugin.store.add_group(gid)
        return self._json({"ok": True, "added": added})

    async def remove_group(self, req):
        gid = message_target_id(req.match_info["gid"], "group id")
        self.plugin.store.remove_group(gid)
        return self._json({"ok": True})

    async def add_team(self, req):
        """通过精确 ID 关注战队"""
        body = await self._body(req)
        team_id = integer(body.get("id"), "team id", 1)
        name = string(body.get("name"), "team name", nonempty=True)
        slug = string("" if body.get("slug") is None else body["slug"], "team slug")
        image_url = string(body.get("image_url") or "", "team image URL")
        if image_url and not image_url.startswith(("https://", "http://")):
            raise ConfigError("team image URL must use http:// or https://")
        added = self.plugin.store.follow_team(team_id, name, slug, image_url)
        if added:
            self.plugin.schedule_refresh("web_add_team")
        return self._json({"ok": True, "added": added})

    async def remove_team(self, req):
        """取消关注战队 — 支持 POST /teams/unfollow {id} 和旧版 DELETE /teams/{name}"""
        team_id = None
        name = req.match_info.get("name", "")
        if req.method == "POST":
            body = await self._body(req)
            if body.get("id") is not None:
                team_id = integer(body["id"], "team id", 1)
            else:
                name = string(body.get("name"), "team name", nonempty=True)

        # aiohttp has already decoded the route parameter. Do not unquote twice.
        if team_id is None:
            name = string(name, "team name", nonempty=True)
            teams = self.plugin.store.get_followed_teams()
            matched = [t for t in teams if isinstance(t, dict) and
                       t.get("name", "").lower() == name.lower()]
            if matched:
                team_id = matched[0].get("id")
        if not team_id:
            if not self.plugin.store.unfollow_team_by_name(name):
                return self._json({"ok": False, "msg": "team id not found"}, 400)
        else:
            self.plugin.store.unfollow_team(team_id)
        self.plugin.schedule_refresh("web_remove_team")
        return self._json({"ok": True})

    async def search_teams(self, req):
        """搜索战队，返回精确列表供前端展示"""
        q = req.rel_url.query.get("q", "").strip()
        if not q or len(q) > 80:
            return self._json({"ok": False, "msg": "请输入 1–80 个字符的战队名称或别称"}, 400)
        results = await self.plugin.client.search_teams(q, per_page=15)
        teams = [
            {"id": t.get("id"), "name": t.get("name"), "slug": t.get("slug"),
             "image_url": t.get("image_url"), "is_main": bool(t.get("is_main")),
             "matched_alias": t.get("matched_alias", ""), "match_kind": t.get("match_kind", "")}
            for t in results if t.get("id") and t.get("name")
        ]
        # 标记已关注
        followed_ids = self.plugin.store.get_followed_team_ids()
        for t in teams:
            t["followed"] = t["id"] in followed_ids
        return self._json({"ok": True, "teams": teams})

    async def popular_teams(self, req):
        result = self.plugin.client.team_search.popular()
        followed = self.plugin.store.get_followed_team_ids()
        for team in result['teams']:
            team['followed'] = team['id'] in followed
        return self._json({'ok': True, **result})

    async def get_logs(self, req):
        return self._json({"ok": True, "logs": list(self._logs)[-100:]})

    async def clear_notified(self, req):
        self.plugin.clear_match_notifications()
        return self._json({"ok": True})

    async def rebuild_tasks(self, req):
        self.plugin.rebuild_all_match_tasks()
        return self._json({"ok": True})

    async def patch_match(self, req):
        """修改单场比赛的自定义提醒时间"""
        mid = integer(req.match_info.get("mid", ""), "match id", 1)
        body = await self._body(req)

        if "remind_minutes" not in body:
            return self._json({"ok": False, "msg": "no supported fields"}, 400)

        val = body["remind_minutes"]
        minutes = None if val is None else integer(val, "remind_minutes", 1, 120)

        msg = self.plugin.update_match_remind(mid, minutes)
        return self._json({"ok": True, "msg": msg})

    async def rebuild_match(self, req):
        """手动重建单场比赛的提醒任务（同时取消正在进行的结果轮询）"""
        mid = integer(req.match_info.get("mid", ""), "match id", 1)

        msg = self.plugin.rebuild_match_task(mid)
        if msg is None:
            return self._json({"ok": False, "msg": "match not found"}, 404)
        return self._json({"ok": True, "msg": msg})

    def push_log(self, level: str, msg: str):
        now = datetime.now(CST).strftime("%H:%M:%S")
        self._logs.append({"time": now, "level": level, "msg": msg})

    async def start(self, host: str, port: int):
        await self.reconfigure(True, host, port)

    async def stop(self):
        # Runner cleanup belongs to final plugin shutdown. Reconfiguring a site
        # must not run Application shutdown/cleanup hooks on an active app.
        async with self._reconfigure_lock:
            runner = self._runner
            self._runner = None
            self._site = None
            self._bind_host = None
            self._bind_port = None
            if runner:
                await runner.cleanup()

    async def reconfigure(self, enabled: bool, host: str, port: int) -> bool:
        async with self._reconfigure_lock:
            if not enabled:
                if self._site:
                    await self._site.stop()
                    self._site = None
                    self._bind_host = None
                    self._bind_port = None
                    logger.info("[CS] Web 管理面板已关闭")
                    self.push_log("OK", "Web 管理面板已关闭")
                    return True
                return False

            if self._site and self._bind_host == host and self._bind_port == port:
                return False

            if self._runner is None:
                runner = web.AppRunner(self.app)
                await runner.setup()
                self._runner = runner

            new_site = web.TCPSite(self._runner, host, port)
            try:
                await new_site.start()
            except Exception:
                # A failed start can register a site with the runner. Remove it
                # while preserving the previous live site and application.
                await new_site.stop()
                raise

            old_site = self._site
            self._site = new_site
            self._bind_host = host
            self._bind_port = port
            if old_site:
                # Site.stop only stops accepting connections. The config request
                # that arrived through this site can finish sending its response.
                await old_site.stop()

            logger.info(f"[CS] Web 管理面板已切换监听：http://{host}:{port}")
            self.push_log("OK", f"Web 管理面板监听 {host}:{port}")
            return True
