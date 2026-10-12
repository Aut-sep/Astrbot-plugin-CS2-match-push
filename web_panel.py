"""
web_panel.py — Web 管理面板
与内嵌 pages/management/ 共用页面，提供基于 aiohttp 的轻量 WebPanel 服务器。

访问地址：http://<host>:<port>/
"""

import asyncio
from collections import deque
import hmac
import ipaddress
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

from aiohttp import web
from yarl import URL

from astrbot.api import logger

from .config import ConfigError, integer, string, validate_config
from .delivery import DeliveryError

CST = timezone(timedelta(hours=8))


# Resolve resources from the plugin location, independent of AstrBot's cwd.
PANEL_DIR = Path(__file__).parent / "pages" / "management"



WEB_PANEL_LOGIN_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>CS2 比赛推送 · 登录</title>
<style>
:root{color-scheme:light dark;--bg:#f7f8fa;--surface:#fff;--text:#212121;--muted:#667085;--border:#e2e5e9;--primary:#1976d2}
@media(prefers-color-scheme:dark){:root{--bg:#1a1a1a;--surface:#242424;--text:#eee;--muted:#aaa;--border:#3b3b3b;--primary:#64b5f6}}
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--text);font-family:system-ui,-apple-system,"Segoe UI",sans-serif}
form{width:min(420px,calc(100vw - 32px));padding:32px;background:var(--surface);border:1px solid var(--border);border-radius:20px}h1{font-size:22px;margin:0 0 12px}p{color:var(--muted);font-size:14px;line-height:1.7;margin:0 0 24px}label{display:block;font-size:13px;margin-bottom:8px}input{width:100%;padding:12px;border:1px solid var(--border);border-radius:8px;background:var(--surface);color:var(--text);font:inherit}input:focus-visible,button:focus-visible{outline:3px solid var(--primary);outline-offset:3px}button{width:100%;padding:12px;margin-top:20px;border:0;border-radius:8px;background:var(--primary);color:#fff;font:600 14px system-ui;cursor:pointer}.error{color:#d32f2f;font-size:13px;min-height:20px;margin-top:12px}.note{margin:20px 0 0;font-size:12px}
</style>
</head>
<body>
<form id="login-form">
<h1>CS2 比赛推送</h1><p>使用管理令牌进入独立面板。</p>
<label for="token">管理令牌</label><input id="token" type="password" autocomplete="off" required autofocus>
<button type="submit">进入管理面板</button><div class="error" id="error" role="alert"></div>
<p class="note">管理员私聊机器人发送 <code>/cs面板</code> 获取令牌。也可直接从 AstrBot 插件详情打开内嵌页面。</p>
</form>
<script>
document.getElementById('login-form').addEventListener('submit', event => {
 event.preventDefault();
 const token = document.getElementById('token').value.trim();
 if (!token) { document.getElementById('error').textContent = '请输入管理令牌'; return; }
 location.href = '/?token=' + encodeURIComponent(token);
});
</script>
</body></html>"""


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
        self._logs = deque(maxlen=500)
        self._setup_routes()

    @web.middleware
    async def _auth_middleware(self, req, handler):
        path = req.path
        if path == "/" or path.startswith("/api/"):
            token = self._request_token(req)
            token_ok = self._token_matches(token)
            if token_ok or self._is_local_request(req):
                if req.method not in {"GET", "HEAD", "OPTIONS"}:
                    origin = req.headers.get("Origin")
                    if origin and not self._same_origin(req, origin):
                        # Browser cookies are ambient credentials, including
                        # across localhost ports. Cross-origin writes need an
                        # explicitly supplied token rather than a session cookie.
                        auth = req.headers.get("Authorization", "")
                        explicit_token = auth[7:].strip() if auth.lower().startswith("bearer ") else req.rel_url.query.get("token", "")
                        if not self._token_matches(explicit_token):
                            return self._json({"ok": False, "msg": "cross-origin request denied"}, 403)
                resp = await handler(req)
                if token_ok:
                    resp.set_cookie(
                        "cs_panel_token",
                        token,
                        httponly=True,
                        samesite="Lax",
                        max_age=30 * 24 * 3600,
                    )
                return resp
            if path == "/":
                return web.Response(text=WEB_PANEL_LOGIN_HTML, content_type="text/html", charset="utf-8")
            return self._json({"ok": False, "msg": "unauthorized"}, 401)
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

    def _request_token(self, req) -> str:
        auth = req.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        query_token = req.rel_url.query.get("token", "")
        if query_token:
            return query_token.strip()
        return (req.cookies.get("cs_panel_token") or "").strip()

    def _token_matches(self, token: str) -> bool:
        expected = str(self.plugin.store.get("web_panel_token") or "").strip()
        return bool(token and expected and hmac.compare_digest(token.encode(), expected.encode()))

    def _same_origin(self, req, origin: str) -> bool:
        try:
            return URL(origin).origin() == URL(f"{req.scheme}://{req.host}").origin()
        except ValueError:
            return False

    def _is_local_request(self, req) -> bool:
        remote = req.remote or ""
        if remote in {"localhost", "127.0.0.1", "::1"}:
            return True
        try:
            return ipaddress.ip_address(remote).is_loopback
        except ValueError:
            return False

    def _setup_routes(self):
        a = self.app.router
        a.add_get("/",                          self.index)
        a.add_get("/app.js",                    self.asset)
        a.add_get("/style.css",                 self.asset)
        a.add_get("/api/config",                self.get_config)
        a.add_patch("/api/config",              self.patch_config)
        a.add_get("/api/config/export",         self.export_config)
        a.add_post("/api/config/import",        self.import_config)
        a.add_get("/api/matches",               self.get_matches)
        a.add_post("/api/refresh",              self.refresh)
        a.add_post("/api/push_now",             self.push_now)
        a.add_post("/api/test",                 self.send_test)
        a.add_post("/api/groups",               self.add_group)
        a.add_delete("/api/groups/{gid}",       self.remove_group)
        a.add_get("/api/teams/search",          self.search_teams)   # 必须在 {name} 之前
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
        html = (PANEL_DIR / "index.html").read_text(encoding="utf-8")
        token = req.rel_url.query.get("token", "").strip()
        if self._token_matches(token):
            bootstrap = (
                "<script>\n"
                "history.replaceState(null, '', location.pathname);\n"
                "</script>\n"
            )
            html = html.replace("</head>", bootstrap + "</head>", 1)
        return web.Response(text=html, content_type="text/html", charset="utf-8")

    async def asset(self, req):
        # Both paths are fixed routes, never an arbitrary filesystem path.
        return web.FileResponse(PANEL_DIR / req.path.lstrip("/"))

    async def get_config(self, req):
        data = self.plugin.store.export_all()
        return self._json({"ok": True, "data": data,
                           "token_configured": bool(self.plugin.store.get("pandascore_token")),
                           "web_token_configured": bool(self.plugin.store.get("web_panel_token")),
                           "embedded_available": bool(getattr(self.plugin, "embedded_available", False))})

    async def patch_config(self, req):
        return await self._apply_config(req, imported=False)

    async def export_config(self, req):
        data = self.plugin.store.export_all()
        return self._json({"ok": True, "data": data})

    async def import_config(self, req):
        return await self._apply_config(req, imported=True)

    async def _apply_config(self, req, *, imported: bool):
        body = validate_config(await self._body(req))
        async with self._config_lock:
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
            if changed:
                await self.plugin.reload_runtime_config()
            filters = {
                "min_tiers", "blacklist_teams", "blacklist_leagues",
                "notify_all_followed", "followed_teams",
            }
            if changed & (filters | {"remind_minutes", "custom_remind_minutes"}):
                self.plugin.rebuild_all_match_tasks()
            elif imported or changed & {"pandascore_token", "fetch_ahead_days"}:
                self.plugin.schedule_refresh("web_import_config" if imported else "web_patch_config")
            if changed & (filters | {"tournament_announce_enabled", "tournament_announce_hours"}):
                self.plugin.rebuild_tournament_tasks()
        return self._json({"ok": True, "restart_required": False, "web_reconfigured": web_reconfigured})

    async def get_matches(self, req):
        return self._json({"ok": True, "matches": self.plugin.get_panel_matches()})

    async def refresh(self, req):
        self.plugin.schedule_refresh("web_refresh")
        return self._json({"ok": True})

    async def push_now(self, req):
        """立即推送赛程到所有群"""
        body = await self._body(req, allow_empty=True)
        days = integer(body.get("days", 1), "days", 1, 30)
        if not self.plugin.has_daily_matches_to_push(days):
            return self._json({"ok": True, "skipped": True, "msg": "no matches to push"})
        self.plugin.schedule_instant_push(days, "web_push_now")
        return self._json({"ok": True})

    async def send_test(self, req):
        body = await self._body(req, allow_empty=True)
        action = string(body.get("action", ""), "action") or "赛前"
        if not self.plugin.is_test_mode_enabled():
            return self._json({"ok": False, "msg": "测试模式未开启"}, 400)
        if not self.plugin.has_test_target():
            return self._json({"ok": False, "msg": "请先配置测试 QQ 或群号"}, 400)
        valid = {"赛前", "赛果", "变更", "开幕", "日报", "全部"}
        if action not in valid:
            return self._json({"ok": False, "msg": "未知测试动作"}, 400)
        try:
            msg = await self.plugin.run_test_push(action, 1)
        except DeliveryError as exc:
            return self._json({"ok": False, "msg": str(exc)}, 502)
        return self._json({"ok": True, "msg": msg})

    async def add_group(self, req):
        body = await self._body(req)
        gid = str(integer(body.get("gid"), "group id", 1))
        added = self.plugin.store.add_group(gid)
        return self._json({"ok": True, "added": added})

    async def remove_group(self, req):
        gid = str(integer(req.match_info["gid"], "group id", 1))
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
        if len(q) < 2:
            return self._json({"ok": False, "msg": "query too short"}, 400)
        results = await self.plugin.client.search_teams(q, per_page=15)
        teams = [
            {"id": t.get("id"), "name": t.get("name"), "slug": t.get("slug"),
             "image_url": t.get("image_url")}
            for t in results if t.get("id") and t.get("name")
        ]
        # 标记已关注
        followed_ids = self.plugin.store.get_followed_team_ids()
        for t in teams:
            t["followed"] = t["id"] in followed_ids
        return self._json({"ok": True, "teams": teams})

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
