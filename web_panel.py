"""
web_panel.py — Web 管理面板
包含单页 HTML 面板字符串常量，以及基于 aiohttp 的轻量 WebPanel 服务器类。

访问地址：http://<host>:<port>/
"""

import asyncio
import hmac
import ipaddress
import json
from datetime import datetime, timezone, timedelta

from aiohttp import web

from astrbot.api import logger

CST = timezone(timedelta(hours=8))


WEB_PANEL_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CS2 推送插件管理面板</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Noto+Sans+SC:wght@400;500;700&display=swap" rel="stylesheet">
<style>
  :root {
    --bg: #0d0f14;
    --bg2: #13161e;
    --bg3: #1a1e2a;
    --border: rgba(255,255,255,0.07);
    --border2: rgba(255,255,255,0.13);
    --text: #e8eaf2;
    --text2: #8b90a8;
    --text3: #555a72;
    --accent: #e8973a;
    --accent2: #f0c070;
    --accent-dim: rgba(232,151,58,0.12);
    --green: #4fd69c;
    --green-dim: rgba(79,214,156,0.12);
    --red: #f16868;
    --red-dim: rgba(241,104,104,0.12);
    --blue: #5ba4f5;
    --blue-dim: rgba(91,164,245,0.12);
    --purple: #a78bfa;
    --purple-dim: rgba(167,139,250,0.12);
    --radius: 8px;
    --radius-lg: 12px;
    --font-mono: 'JetBrains Mono', monospace;
    --font-sans: 'Noto Sans SC', sans-serif;
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: var(--font-sans);
    font-size: 14px;
    line-height: 1.6;
    min-height: 100vh;
  }

  /* ── 侧边栏 ── */
  .layout { display: flex; min-height: 100vh; }

  .sidebar {
    width: 220px;
    background: var(--bg2);
    border-right: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    position: fixed;
    top: 0; left: 0; bottom: 0;
    z-index: 100;
  }

  .sidebar-logo {
    padding: 20px 20px 16px;
    border-bottom: 1px solid var(--border);
  }

  .sidebar-logo .logo-icon {
    font-size: 24px;
    margin-bottom: 6px;
  }

  .sidebar-logo h1 {
    font-family: var(--font-mono);
    font-size: 13px;
    font-weight: 700;
    color: var(--accent);
    letter-spacing: 0.05em;
    line-height: 1.3;
  }

  .sidebar-logo p {
    font-size: 11px;
    color: var(--text3);
    font-family: var(--font-mono);
  }

  .nav { flex: 1; padding: 12px 0; overflow-y: auto; }

  .nav-section {
    padding: 6px 16px 4px;
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--text3);
    font-family: var(--font-mono);
  }

  .nav-item {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 9px 20px;
    cursor: pointer;
    border-radius: 0;
    transition: all 0.15s;
    color: var(--text2);
    font-size: 13px;
    border-left: 2px solid transparent;
    user-select: none;
  }

  .nav-item:hover { background: var(--bg3); color: var(--text); }

  .nav-item.active {
    background: var(--accent-dim);
    color: var(--accent);
    border-left-color: var(--accent);
    font-weight: 500;
  }

  .nav-icon { width: 16px; text-align: center; font-size: 14px; }

  .sidebar-footer {
    padding: 12px 16px;
    border-top: 1px solid var(--border);
  }

  .status-dot {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 11px;
    color: var(--text3);
    font-family: var(--font-mono);
  }

  .dot {
    width: 7px; height: 7px;
    border-radius: 50%;
    background: var(--green);
    box-shadow: 0 0 6px var(--green);
    animation: pulse 2s infinite;
  }

  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
  }

  /* ── 主内容 ── */
  .main { margin-left: 220px; flex: 1; }

  .topbar {
    background: var(--bg2);
    border-bottom: 1px solid var(--border);
    padding: 14px 28px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    position: sticky;
    top: 0;
    z-index: 50;
  }

  .topbar-title {
    font-family: var(--font-mono);
    font-size: 13px;
    font-weight: 700;
    color: var(--text);
  }

  .topbar-actions { display: flex; gap: 8px; }

  .page { display: none; padding: 28px; }
  .page.active { display: block; }

  /* ── 统计卡片 ── */
  .stats-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
    gap: 12px;
    margin-bottom: 24px;
  }

  .stat-card {
    background: var(--bg2);
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    padding: 16px 18px;
    transition: border-color 0.15s;
  }

  .stat-card:hover { border-color: var(--border2); }

  .stat-label {
    font-size: 11px;
    color: var(--text3);
    font-family: var(--font-mono);
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-bottom: 8px;
  }

  .stat-value {
    font-size: 26px;
    font-weight: 700;
    font-family: var(--font-mono);
    line-height: 1;
  }

  .stat-sub {
    font-size: 11px;
    color: var(--text3);
    margin-top: 4px;
  }

  .c-accent { color: var(--accent); }
  .c-green  { color: var(--green); }
  .c-blue   { color: var(--blue); }
  .c-purple { color: var(--purple); }
  .c-red    { color: var(--red); }

  /* ── Section ── */
  .section {
    background: var(--bg2);
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    margin-bottom: 16px;
    overflow: hidden;
  }

  .section-header {
    padding: 14px 20px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  .section-title {
    font-size: 13px;
    font-weight: 500;
    color: var(--text);
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .section-body { padding: 20px; }

  /* ── 表单元素 ── */
  .form-row {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 14px;
  }

  .form-row:last-child { margin-bottom: 0; }

  label {
    font-size: 12px;
    color: var(--text2);
    min-width: 120px;
    font-family: var(--font-mono);
  }

  input[type="text"],
  input[type="number"],
  input[type="password"],
  select {
    background: var(--bg3);
    border: 1px solid var(--border2);
    border-radius: var(--radius);
    color: var(--text);
    font-family: var(--font-mono);
    font-size: 13px;
    padding: 8px 12px;
    transition: border-color 0.15s;
    outline: none;
    width: 100%;
  }

  input[type="text"]:focus,
  input[type="number"]:focus,
  input[type="password"]:focus,
  select:focus {
    border-color: var(--accent);
    box-shadow: 0 0 0 2px var(--accent-dim);
  }

  input[type="range"] {
    -webkit-appearance: none;
    appearance: none;
    height: 4px;
    border-radius: 2px;
    background: var(--bg3);
    outline: none;
    cursor: pointer;
    flex: 1;
  }

  input[type="range"]::-webkit-slider-thumb {
    -webkit-appearance: none;
    width: 16px; height: 16px;
    border-radius: 50%;
    background: var(--accent);
    cursor: pointer;
    transition: transform 0.15s;
  }

  input[type="range"]::-webkit-slider-thumb:hover { transform: scale(1.2); }

  .range-val {
    font-family: var(--font-mono);
    font-size: 13px;
    color: var(--accent);
    min-width: 48px;
    text-align: right;
  }

  /* ── 开关 ── */
  .toggle {
    position: relative;
    width: 40px; height: 22px;
    flex-shrink: 0;
    cursor: pointer;
  }

  .toggle input { opacity: 0; width: 0; height: 0; position: absolute; }

  .toggle-track {
    position: absolute;
    inset: 0;
    background: var(--bg3);
    border: 1px solid var(--border2);
    border-radius: 11px;
    transition: all 0.2s;
  }

  .toggle-thumb {
    position: absolute;
    top: 3px; left: 3px;
    width: 14px; height: 14px;
    background: var(--text3);
    border-radius: 50%;
    transition: all 0.2s;
  }

  .toggle input:checked ~ .toggle-track {
    background: var(--accent-dim);
    border-color: var(--accent);
  }

  .toggle input:checked ~ .toggle-thumb {
    left: 21px;
    background: var(--accent);
  }

  /* ── 按钮 ── */
  .btn {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 8px 16px;
    border-radius: var(--radius);
    font-size: 12px;
    font-family: var(--font-mono);
    font-weight: 500;
    cursor: pointer;
    transition: all 0.15s;
    border: 1px solid var(--border2);
    background: var(--bg3);
    color: var(--text2);
    white-space: nowrap;
  }

  .btn:hover { border-color: var(--border2); background: var(--bg2); color: var(--text); }
  .btn:active { transform: scale(0.97); }

  .btn-primary {
    background: var(--accent);
    border-color: var(--accent);
    color: #000;
    font-weight: 700;
  }

  .btn-primary:hover { background: var(--accent2); border-color: var(--accent2); color: #000; }

  .btn-danger { border-color: var(--red); color: var(--red); }
  .btn-danger:hover { background: var(--red-dim); }

  .btn-success { border-color: var(--green); color: var(--green); }
  .btn-success:hover { background: var(--green-dim); }

  .btn-sm { padding: 5px 10px; font-size: 11px; }

  /* ── 标签 ── */
  .tag-list {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    min-height: 32px;
    align-items: flex-start;
  }

  .tag {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 10px;
    background: var(--bg3);
    border: 1px solid var(--border2);
    border-radius: 20px;
    font-size: 12px;
    font-family: var(--font-mono);
    color: var(--text2);
  }

  .tag.tag-accent { border-color: var(--accent); color: var(--accent); background: var(--accent-dim); }
  .tag.tag-green  { border-color: var(--green); color: var(--green); background: var(--green-dim); }
  .tag.tag-blue   { border-color: var(--blue); color: var(--blue); background: var(--blue-dim); }
  .tag.tag-red    { border-color: var(--red); color: var(--red); background: var(--red-dim); }

  .tag-del {
    cursor: pointer;
    opacity: 0.5;
    transition: opacity 0.15s;
    font-size: 14px;
    line-height: 1;
  }

  .tag-del:hover { opacity: 1; }

  /* ── 比赛卡片 ── */
  .match-card {
    background: var(--bg3);
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    padding: 14px 16px;
    margin-bottom: 10px;
    transition: border-color 0.15s;
  }

  .match-card:hover { border-color: var(--border2); }

  .match-card.starred { border-color: var(--accent); }

  .match-card.match-finished { opacity: 0.55; }

  .match-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 8px;
  }

  .match-teams {
    font-family: var(--font-mono);
    font-size: 14px;
    font-weight: 700;
    color: var(--text);
  }

  .match-meta {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }

  .badge {
    font-family: var(--font-mono);
    font-size: 10px;
    padding: 2px 7px;
    border-radius: 4px;
    font-weight: 700;
    letter-spacing: 0.05em;
    text-transform: uppercase;
  }

  .badge-s   { background: #7c3aed22; color: #a78bfa; border: 1px solid #a78bfa44; }
  .badge-a   { background: #1d4ed822; color: #5ba4f5; border: 1px solid #5ba4f544; }
  .badge-b   { background: #16653422; color: #4fd69c; border: 1px solid #4fd69c44; }
  .badge-c   { background: #78350f22; color: #e8973a; border: 1px solid #e8973a44; }
  .badge-other { background: var(--bg2); color: var(--text3); border: 1px solid var(--border); }

  .match-time {
    font-family: var(--font-mono);
    font-size: 12px;
    color: var(--text3);
  }

  .match-league {
    font-size: 12px;
    color: var(--text2);
    margin-top: 4px;
  }

  .match-actions {
    border-top: 1px solid var(--border);
    padding-top: 8px;
    margin-top: 8px;
  }

  .match-actions .btn {
    font-size: 11px;
    padding: 4px 10px;
  }

  /* ── Tier 选择器 ── */
  .tier-grid {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }

  .tier-btn {
    padding: 8px 16px;
    border-radius: var(--radius);
    font-family: var(--font-mono);
    font-size: 13px;
    font-weight: 700;
    cursor: pointer;
    border: 1px solid var(--border2);
    background: var(--bg3);
    color: var(--text3);
    transition: all 0.15s;
    user-select: none;
  }

  .tier-btn.active-s { background: #7c3aed22; color: #a78bfa; border-color: #a78bfa; }
  .tier-btn.active-a { background: #1d4ed822; color: #5ba4f5; border-color: #5ba4f5; }
  .tier-btn.active-b { background: #16653422; color: #4fd69c; border-color: #4fd69c; }
  .tier-btn.active-c { background: #78350f22; color: #e8973a; border-color: #e8973a; }
  .tier-btn.active-d { background: rgba(100,100,100,0.15); color: #888; border-color: #888; }
  .tier-btn.active-unranked { background: rgba(100,100,100,0.1); color: #666; border-color: #666; }

  /* ── 自定义直播流 ── */
  .stream-row {
    display: flex;
    gap: 8px;
    margin-bottom: 8px;
    align-items: center;
  }

  /* ── 通知 Toast ── */
  #toast-container {
    position: fixed;
    bottom: 20px;
    right: 20px;
    z-index: 9999;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .toast {
    background: var(--bg2);
    border: 1px solid var(--border2);
    border-radius: var(--radius);
    padding: 12px 16px;
    font-size: 13px;
    font-family: var(--font-mono);
    display: flex;
    align-items: center;
    gap: 8px;
    animation: slide-in 0.2s ease;
    max-width: 320px;
  }

  @keyframes slide-in {
    from { transform: translateX(100%); opacity: 0; }
    to   { transform: translateX(0);   opacity: 1; }
  }

  .toast.success { border-color: var(--green); color: var(--green); }
  .toast.error   { border-color: var(--red);   color: var(--red); }
  .toast.info    { border-color: var(--blue);   color: var(--blue); }

  /* ── 日志 ── */
  .log-panel {
    background: var(--bg);
    border-radius: var(--radius);
    padding: 14px;
    font-family: var(--font-mono);
    font-size: 12px;
    color: var(--text2);
    max-height: 320px;
    overflow-y: auto;
    line-height: 1.8;
    border: 1px solid var(--border);
  }

  .log-line { margin: 0; }
  .log-time { color: var(--text3); }
  .log-info   { color: var(--blue); }
  .log-warn   { color: var(--accent); }
  .log-error  { color: var(--red); }
  .log-ok     { color: var(--green); }

  /* ── Divider ── */
  .divider {
    height: 1px;
    background: var(--border);
    margin: 20px 0;
  }

  /* ── 输入行 ── */
  .input-add-row {
    display: flex;
    gap: 8px;
    margin-top: 12px;
  }

  /* ── 帮助文字 ── */
  .hint {
    font-size: 11px;
    color: var(--text3);
    margin-top: 4px;
    font-family: var(--font-mono);
  }

  /* ── Loading ── */
  .loading {
    display: inline-block;
    width: 14px; height: 14px;
    border: 2px solid var(--border2);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: spin 0.6s linear infinite;
    vertical-align: middle;
  }

  @keyframes spin { to { transform: rotate(360deg); } }

  /* ── 响应式 ── */
  @media (max-width: 768px) {
    .sidebar { width: 0; overflow: hidden; }
    .main { margin-left: 0; }
    .stats-grid { grid-template-columns: repeat(2, 1fr); }
  }

  /* ── 空状态 ── */
  .empty {
    text-align: center;
    padding: 32px;
    color: var(--text3);
    font-family: var(--font-mono);
    font-size: 13px;
  }

  .empty-icon { font-size: 32px; margin-bottom: 8px; }

  /* 分组 grid */
  .two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  @media (max-width: 960px) { .two-col { grid-template-columns: 1fr; } }
</style>
</head>
<body>
<div class="layout">

<!-- ── 侧边栏 ── -->
<nav class="sidebar">
  <div class="sidebar-logo">
    <div class="logo-icon">🎮</div>
    <h1>CS2 PUSH<br>MANAGER</h1>
    <p>v4.1.0</p>
  </div>
  <div class="nav">
    <div class="nav-section">概览</div>
    <div class="nav-item active" data-page="dashboard" onclick="goPage('dashboard',this)">
      <span class="nav-icon">◈</span> 控制台
    </div>
    <div class="nav-item" data-page="matches" onclick="goPage('matches',this)">
      <span class="nav-icon">⚔</span> 赛程列表
    </div>

    <div class="nav-section" style="margin-top:8px;">推送配置</div>
    <div class="nav-item" data-page="groups" onclick="goPage('groups',this)">
      <span class="nav-icon">💬</span> 推送群组
    </div>
    <div class="nav-item" data-page="teams" onclick="goPage('teams',this)">
      <span class="nav-icon">⭐</span> 关注战队
    </div>
    <div class="nav-item" data-page="tiers" onclick="goPage('tiers',this)">
      <span class="nav-icon">🏅</span> 赛事等级
    </div>
    <div class="nav-item" data-page="filters" onclick="goPage('filters',this)">
      <span class="nav-icon">🚫</span> 屏蔽过滤
    </div>

    <div class="nav-section" style="margin-top:8px;">高级</div>
    <div class="nav-item" data-page="daily" onclick="goPage('daily',this)">
      <span class="nav-icon">🗓</span> 每日推送
    </div>
    <div class="nav-item" data-page="timing" onclick="goPage('timing',this)">
      <span class="nav-icon">⏱</span> 时间设置
    </div>
    <div class="nav-item" data-page="streams" onclick="goPage('streams',this)">
      <span class="nav-icon">📡</span> 自定义直播
    </div>
    <div class="nav-item" data-page="advanced" onclick="goPage('advanced',this)">
      <span class="nav-icon">⚙</span> 高级设置
    </div>
  </div>
  <div class="sidebar-footer">
    <div class="status-dot">
      <span class="dot"></span>
      <span id="status-label">运行中</span>
    </div>
  </div>
</nav>

<!-- ── 主区域 ── -->
<div class="main">
  <div class="topbar">
    <div class="topbar-title" id="page-title">控制台</div>
    <div class="topbar-actions">
      <button class="btn btn-sm" onclick="refreshAll()">↺ 刷新数据</button>
      <button class="btn btn-sm btn-primary" onclick="saveAll()">✓ 保存配置</button>
    </div>
  </div>

  <!-- ════ 控制台 ════ -->
  <div id="page-dashboard" class="page active">
    <div class="stats-grid" id="stats-grid">
      <div class="stat-card">
        <div class="stat-label">已安排比赛</div>
        <div class="stat-value c-accent" id="st-matches">–</div>
        <div class="stat-sub">未来 48h</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">推送群数</div>
        <div class="stat-value c-blue" id="st-groups">–</div>
        <div class="stat-sub">活跃推送</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">关注战队</div>
        <div class="stat-value c-green" id="st-teams">–</div>
        <div class="stat-sub">全级别推送</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">赛前提醒</div>
        <div class="stat-value c-purple" id="st-remind">–</div>
        <div class="stat-sub">分钟</div>
      </div>
    </div>

    <div class="two-col">
      <div class="section">
        <div class="section-header">
          <div class="section-title">🔔 推送设置快览</div>
        </div>
        <div class="section-body">
          <div class="form-row">
            <label>赛前提醒时间</label>
            <input type="range" id="q-remind" min="1" max="120" step="1" value="10"
                   oninput="document.getElementById('q-remind-val').textContent=this.value+'min'">
            <span class="range-val" id="q-remind-val">10min</span>
          </div>
          <div class="form-row">
            <label>延迟变更通知</label>
            <label class="toggle">
              <input type="checkbox" id="q-reschedule">
              <span class="toggle-track"></span>
              <span class="toggle-thumb"></span>
            </label>
            <span style="font-size:12px;color:var(--text3)">比赛时间变更时推送提醒</span>
          </div>
          <div class="form-row">
            <label>关注队忽略Tier</label>
            <label class="toggle">
              <input type="checkbox" id="q-notify-all" checked>
              <span class="toggle-track"></span>
              <span class="toggle-thumb"></span>
            </label>
            <span style="font-size:12px;color:var(--text3)">关注的战队所有赛事都推送</span>
          </div>
          <div style="margin-top:16px;">
            <button class="btn btn-primary" onclick="quickSave()" style="width:100%">保存快速设置</button>
          </div>
        </div>
      </div>

      <div class="section">
        <div class="section-header">
          <div class="section-title">📅 即将开始</div>
          <button class="btn btn-sm" onclick="loadMatches()">刷新</button>
        </div>
        <div class="section-body" id="upcoming-preview" style="max-height:280px;overflow-y:auto;">
          <div class="empty"><div class="empty-icon">⏳</div>加载中…</div>
        </div>
      </div>
    </div>

    <div class="section">
      <div class="section-header">
        <div class="section-title">📋 运行日志</div>
        <button class="btn btn-sm" onclick="loadLogs()">刷新</button>
      </div>
      <div class="section-body">
        <div class="log-panel" id="log-panel">
          <div style="color:var(--text3)">正在加载日志…</div>
        </div>
      </div>
    </div>
  </div>

  <!-- ════ 赛程列表 ════ -->
  <div id="page-matches" class="page">
    <div class="section">
      <div class="section-header">
        <div class="section-title">⚔ 未来赛程</div>
        <div style="display:flex;gap:8px;align-items:center;">
          <input type="text" id="match-search" placeholder="搜索战队/联赛…"
                 style="width:180px;" oninput="filterMatches()">
          <button class="btn btn-sm" onclick="loadMatches()">↺ 刷新</button>
          <button class="btn btn-sm btn-primary" onclick="forceRefresh()">立即拉取</button>
        </div>
      </div>
      <div class="section-body" id="matches-list">
        <div class="empty"><div class="empty-icon">📭</div>加载中…</div>
      </div>
    </div>
  </div>

  <!-- ════ 推送群组 ════ -->
  <div id="page-groups" class="page">
    <div class="section">
      <div class="section-header">
        <div class="section-title">💬 推送群组管理</div>
      </div>
      <div class="section-body">
        <p style="font-size:12px;color:var(--text3);margin-bottom:16px;">所有推送消息（赛前提醒、赛果、时间变更）将发送到以下群号</p>
        <div class="tag-list" id="groups-list"></div>
        <div class="input-add-row">
          <input type="text" id="new-group" placeholder="输入群号，如 123456789" style="max-width:220px;"
                 onkeydown="if(event.key==='Enter')addGroup()">
          <button class="btn btn-success" onclick="addGroup()">+ 添加群</button>
        </div>
      </div>
    </div>
  </div>

  <!-- ════ 关注战队 ════ -->
  <div id="page-teams" class="page">
    <div class="section">
      <div class="section-header">
        <div class="section-title">⭐ 已关注战队</div>
        <button class="btn btn-sm" onclick="loadConfig()">↺ 刷新</button>
      </div>
      <div class="section-body">
        <p style="font-size:12px;color:var(--text3);margin-bottom:12px;">
          关注的战队无论赛事等级如何都会推送（可通过"高级设置"关闭此行为）
        </p>
        <div id="teams-list" style="min-height:32px;"></div>
      </div>
    </div>

    <div class="section">
      <div class="section-header">
        <div class="section-title">🔍 搜索并关注战队</div>
      </div>
      <div class="section-body">
        <p style="font-size:12px;color:var(--text3);margin-bottom:14px;">
          通过 PandaScore 搜索精确战队，避免同名/相似名称导致误关注（如 Falcons vs Falcons Force）
        </p>
        <div style="display:flex;gap:8px;margin-bottom:12px;">
          <input type="text" id="team-search-input" placeholder="输入战队名，至少2个字符…"
                 style="flex:1;" oninput="onTeamSearchInput()"
                 onkeydown="if(event.key==='Enter')searchTeams()">
          <button class="btn btn-primary" onclick="searchTeams()">搜索</button>
        </div>
        <div id="team-search-status" style="font-size:12px;color:var(--text3);margin-bottom:8px;"></div>
        <div id="team-search-results"></div>
      </div>
    </div>
  </div>

  <!-- ════ 赛事等级 ════ -->
  <div id="page-tiers" class="page">
    <div class="section">
      <div class="section-header">
        <div class="section-title">🏅 赛事等级筛选</div>
      </div>
      <div class="section-body">
        <p style="font-size:12px;color:var(--text3);margin-bottom:16px;">
          点击选择要推送的赛事等级。S 为顶级赛事（Major），A 为高级联赛，B/C/D 为次级赛事
        </p>
        <div class="tier-grid" id="tier-grid">
          <div class="tier-btn" data-tier="s" onclick="toggleTier('s',this)">S 级</div>
          <div class="tier-btn" data-tier="a" onclick="toggleTier('a',this)">A 级</div>
          <div class="tier-btn" data-tier="b" onclick="toggleTier('b',this)">B 级</div>
          <div class="tier-btn" data-tier="c" onclick="toggleTier('c',this)">C 级</div>
          <div class="tier-btn" data-tier="d" onclick="toggleTier('d',this)">D 级</div>
          <div class="tier-btn" data-tier="unranked" onclick="toggleTier('unranked',this)">未分级</div>
        </div>
        <div class="hint" style="margin-top:12px;">当前选中：<span id="tier-summary" style="color:var(--accent)">S、A</span></div>
        <div class="divider"></div>
        <button class="btn btn-primary" onclick="saveTiers()" style="width:100%">保存等级设置</button>
      </div>
    </div>
  </div>

  <!-- ════ 屏蔽过滤 ════ -->
  <div id="page-filters" class="page">
    <div class="two-col">
      <div class="section">
        <div class="section-header">
          <div class="section-title">🚫 屏蔽战队</div>
        </div>
        <div class="section-body">
          <p style="font-size:12px;color:var(--text3);margin-bottom:12px;">屏蔽后，含该战队的比赛不会推送</p>
          <div class="tag-list" id="blacklist-teams-list"></div>
          <div class="input-add-row">
            <input type="text" id="new-bl-team" placeholder="战队名…" style="flex:1;"
                   onkeydown="if(event.key==='Enter')addBlacklistTeam()">
            <button class="btn btn-danger btn-sm" onclick="addBlacklistTeam()">+ 屏蔽</button>
          </div>
        </div>
      </div>
      <div class="section">
        <div class="section-header">
          <div class="section-title">🚫 屏蔽联赛</div>
        </div>
        <div class="section-body">
          <p style="font-size:12px;color:var(--text3);margin-bottom:12px;">屏蔽后，该联赛的比赛不会推送</p>
          <div class="tag-list" id="blacklist-leagues-list"></div>
          <div class="input-add-row">
            <input type="text" id="new-bl-league" placeholder="联赛名…" style="flex:1;"
                   onkeydown="if(event.key==='Enter')addBlacklistLeague()">
            <button class="btn btn-danger btn-sm" onclick="addBlacklistLeague()">+ 屏蔽</button>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- ════ 每日推送 ════ -->
  <div id="page-daily" class="page">
    <div class="section">
      <div class="section-header">
        <div class="section-title">🗓 每日定时赛程推送</div>
      </div>
      <div class="section-body">
        <div class="form-row">
          <label>启用每日推送</label>
          <label class="toggle">
            <input type="checkbox" id="daily-enabled">
            <span class="toggle-track"></span>
            <span class="toggle-thumb"></span>
          </label>
          <span style="font-size:12px;color:var(--text3)">每天按指定时间自动推送赛程摘要到所有群</span>
        </div>

        <div class="divider"></div>

        <p style="font-size:12px;color:var(--text2);margin-bottom:12px;">推送时间（CST 北京时间，可添加多个）</p>
        <div class="tag-list" id="daily-times-list" style="margin-bottom:12px;"></div>
        <div class="input-add-row">
          <input type="time" id="new-daily-time" style="width:140px;">
          <button class="btn btn-success" onclick="addDailyTime()">+ 添加时间</button>
        </div>
        <div class="hint">推荐设置早上 08:00 和/或 下午 20:00</div>

        <div class="divider"></div>

        <div class="form-row">
          <label>推送天数范围</label>
          <div class="tier-grid" id="days-grid">
            <div class="tier-btn" data-days="1" onclick="selectDays(1,this)">仅今天</div>
            <div class="tier-btn" data-days="2" onclick="selectDays(2,this)">今天+明天</div>
            <div class="tier-btn" data-days="3" onclick="selectDays(3,this)">3 天</div>
          </div>
        </div>
        <div class="hint">推送几天内的赛程摘要</div>

        <div class="divider"></div>

        <div style="display:flex;gap:8px;flex-wrap:wrap;">
          <button class="btn btn-primary" onclick="saveDailyPush()" style="flex:1;">💾 保存每日推送设置</button>
          <button class="btn btn-success" onclick="pushNow(1)" style="flex:1;">📤 立即推送今日赛程</button>
          <button class="btn btn-success" onclick="pushNow(2)" style="flex:1;">📤 立即推送两日赛程</button>
        </div>

        <div class="divider"></div>

        <div class="section-header" style="padding:0;border:none;margin-bottom:12px;">
          <div class="section-title" style="font-size:12px;">📋 推送内容预览</div>
          <button class="btn btn-sm" onclick="previewDaily()">刷新预览</button>
        </div>
        <div id="daily-preview" style="background:var(--bg);border:1px solid var(--border);border-radius:var(--radius);padding:14px;font-family:var(--font-mono);font-size:12px;color:var(--text2);white-space:pre-wrap;min-height:80px;line-height:1.8;">
          点击"刷新预览"查看推送效果
        </div>
      </div>
    </div>
  </div>

  <!-- ════ 时间设置 ════ -->
  <div id="page-timing" class="page">
    <div class="section">
      <div class="section-header">
        <div class="section-title">⏱ 时间参数设置</div>
      </div>
      <div class="section-body">
        <div class="form-row">
          <label>赛前提醒（分钟）</label>
          <input type="range" id="remind-range" min="1" max="120" step="1" value="10"
                 oninput="document.getElementById('remind-val').textContent=this.value">
          <span class="range-val" id="remind-val" style="min-width:32px;">10</span>
          <span style="font-size:12px;color:var(--text3)">分钟</span>
        </div>
        <div class="hint">开赛前多少分钟推送提醒</div>
        <div class="divider"></div>
        <div class="form-row">
          <label>日程刷新间隔</label>
          <input type="range" id="fetch-interval" min="1" max="120" step="1" value="10"
                 oninput="document.getElementById('fi-val').textContent=this.value">
          <span class="range-val" id="fi-val" style="min-width:32px;">10</span>
          <span style="font-size:12px;color:var(--text3)">分钟</span>
        </div>
        <div class="hint">自动从 PandaScore 拉取最新日程的频率（1~120 分钟）</div>
        <div class="divider"></div>
        <div class="form-row">
          <label>提前获取范围</label>
          <input type="range" id="fetch-ahead" min="1" max="30" step="1" value="2"
                 oninput="document.getElementById('fa-val').textContent=this.value">
          <span class="range-val" id="fa-val" style="min-width:32px;">2</span>
          <span style="font-size:12px;color:var(--text3)">天</span>
        </div>
        <div class="hint">获取未来多少天内的赛程（1~30 天）</div>
        <div class="divider"></div>
        <button class="btn btn-primary" onclick="saveTiming()" style="width:100%">保存时间设置</button>
      </div>
    </div>
  </div>

  <!-- ════ 自定义直播 ════ -->
  <div id="page-streams" class="page">
    <div class="section">
      <div class="section-header">
        <div class="section-title">📡 自定义直播源</div>
      </div>
      <div class="section-body">
        <p style="font-size:12px;color:var(--text3);margin-bottom:16px;">
          赛前提醒消息中追加自定义直播链接（默认已包含斗鱼/B站固定链接）
        </p>
        <div id="streams-list"></div>
        <div class="input-add-row" style="flex-wrap:wrap;gap:8px;">
          <input type="text" id="new-stream-name" placeholder="平台名，如 虎牙" style="width:130px;">
          <input type="text" id="new-stream-url"  placeholder="直播链接 https://…"  style="flex:1;min-width:200px;">
          <button class="btn btn-success" onclick="addStream()">+ 添加</button>
        </div>
        <div class="divider"></div>
        <button class="btn btn-primary" onclick="saveStreams()" style="width:100%">保存直播设置</button>
      </div>
    </div>
  </div>

  <!-- ════ 高级设置 ════ -->
  <div id="page-advanced" class="page">
    <div class="section">
      <div class="section-header">
        <div class="section-title">⚙ 高级设置</div>
      </div>
      <div class="section-body">
        <div class="form-row">
          <label>PandaScore Token</label>
          <input type="password" id="adv-token" placeholder="Bearer Token（留空保持不变）">
        </div>
        <div class="form-row">
          <label>Web 面板监听地址</label>
          <input id="adv-web-host" placeholder="127.0.0.1">
          <span style="font-size:12px;color:var(--text3)">默认仅本机可访问；如需局域网访问可改为 0.0.0.0</span>
        </div>
        <div class="hint" style="margin-bottom:16px;">Token 保存后立即生效；监听地址和端口修改后需重启插件生效</div>

        <div class="form-row">
          <label>关注队忽略Tier</label>
          <label class="toggle">
            <input type="checkbox" id="adv-notify-all" checked>
            <span class="toggle-track"></span>
            <span class="toggle-thumb"></span>
          </label>
          <span style="font-size:12px;color:var(--text3)">开启后，关注战队的所有赛事都推送，不受等级限制</span>
        </div>

        <div class="form-row">
          <label>时间变更通知</label>
          <label class="toggle">
            <input type="checkbox" id="adv-reschedule" checked>
            <span class="toggle-track"></span>
            <span class="toggle-thumb"></span>
          </label>
          <span style="font-size:12px;color:var(--text3)">比赛时间变更时向群推送通知</span>
        </div>

        <div class="form-row">
          <label>图片推送模式</label>
          <label class="toggle">
            <input type="checkbox" id="adv-image-push" checked>
            <span class="toggle-track"></span>
            <span class="toggle-thumb"></span>
          </label>
          <span style="font-size:12px;color:var(--text3)">开启后，赛前提醒和赛后结果将使用图片版式；关闭则使用纯文字</span>
        </div>

        <div class="divider"></div>
        <p style="font-size:12px;color:var(--text2);margin-bottom:12px;">🎉 赛事开幕通知</p>

        <div class="form-row">
          <label>开启赛事开幕推送</label>
          <label class="toggle">
            <input type="checkbox" id="adv-tour-announce" checked>
            <span class="toggle-track"></span>
            <span class="toggle-thumb"></span>
          </label>
          <span style="font-size:12px;color:var(--text3)">新赛事（锦标赛）开始前提前推送开幕通知</span>
        </div>

        <div class="form-row">
          <label>提前推送时间（小时）</label>
          <input type="number" id="adv-tour-hours" min="1" max="24" value="2"
                 style="width:90px;">
          <span style="font-size:12px;color:var(--text3)">开赛前多少小时推送（1~24）</span>
        </div>
        <div class="hint" style="margin-bottom:16px;">
          推送范围：达到配置等级的赛事，以及关注战队参赛的任意等级赛事
        </div>

        <div class="divider"></div>
        <p style="font-size:12px;color:var(--text2);margin-bottom:12px;">测试模式</p>

        <div class="form-row">
          <label>开启测试模式</label>
          <label class="toggle">
            <input type="checkbox" id="adv-test-mode">
            <span class="toggle-track"></span>
            <span class="toggle-thumb"></span>
          </label>
          <span style="font-size:12px;color:var(--text3)">开启后可发送带 [测试] 前缀的模拟推送</span>
        </div>

        <div class="form-row">
          <label>测试目标类型</label>
          <select id="adv-test-target-type">
            <option value="private">私聊 QQ</option>
            <option value="group">群聊</option>
          </select>
          <span style="font-size:12px;color:var(--text3)">测试消息只发到这里，不走正式推送群</span>
        </div>

        <div class="form-row">
          <label>测试目标号码</label>
          <input id="adv-test-target-id" placeholder="输入 QQ 号或群号">
          <span style="font-size:12px;color:var(--text3)">留空则无法发送测试消息</span>
        </div>

        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px;">
          <button class="btn" onclick="saveTestConfig()">保存测试配置</button>
        </div>

        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px;">
          <button class="btn" onclick="sendTestPush('赛前')">测试赛前</button>
          <button class="btn" onclick="sendTestPush('赛果')">测试赛果</button>
          <button class="btn" onclick="sendTestPush('变更')">测试变更</button>
          <button class="btn" onclick="sendTestPush('开幕')">测试开幕</button>
          <button class="btn" onclick="sendTestPush('日报')">测试日报</button>
          <button class="btn btn-success" onclick="sendTestPush('全部')">全部测试</button>
        </div>

        <div class="divider"></div>
        <button class="btn btn-primary" onclick="saveAdvanced()" style="width:100%">保存高级设置</button>

        <div class="divider"></div>
        <p style="font-size:12px;color:var(--text2);margin-bottom:12px;">数据管理</p>
        <div style="display:flex;gap:8px;flex-wrap:wrap;">
          <button class="btn" onclick="exportConfig()">⬇ 导出配置 JSON</button>
          <button class="btn" onclick="document.getElementById('import-file').click()">⬆ 导入配置</button>
          <input type="file" id="import-file" accept=".json" style="display:none" onchange="importConfig(event)">
          <button class="btn btn-danger" onclick="clearNotified()">🗑 清除通知记录</button>
          <button class="btn btn-danger" onclick="if(confirm('确认重建所有比赛提醒任务？'))rebuildTasks()">♻ 重建提醒任务</button>
        </div>
      </div>
    </div>
  </div>

</div><!-- /main -->
</div><!-- /layout -->

<div id="toast-container"></div>

<script>
// ══════════════════════════════════════════
// 状态
// ══════════════════════════════════════════
let STATE = {
  config: {},
  matches: [],
  tiers: ["s","a"],
  streams: [],
  blacklistTeams: [],
  blacklistLeagues: [],
};

// ══════════════════════════════════════════
// API 工具
// ══════════════════════════════════════════
async function api(path, method='GET', body=null) {
  const opts = { method, headers: {'Content-Type':'application/json'} };
  const token = localStorage.getItem('cs_panel_token') || '';
  if (token) opts.headers.Authorization = 'Bearer ' + token;
  if (body) opts.body = JSON.stringify(body);
  const r = await fetch('/api' + path, opts);
  if (r.status === 401) {
    const token = prompt('请输入 Web 面板管理令牌');
    if (token) {
      localStorage.setItem('cs_panel_token', token.trim());
      return api(path, method, body);
    }
  }
  return r.json();
}

function toast(msg, type='info') {
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  const icon = type==='success'?'✓':type==='error'?'✗':'ℹ';
  el.innerHTML = `<span>${icon}</span><span>${msg}</span>`;
  document.getElementById('toast-container').appendChild(el);
  setTimeout(() => el.remove(), 3000);
}

// ══════════════════════════════════════════
// 页面导航
// ══════════════════════════════════════════
const PAGE_TITLES = {
  dashboard:'控制台', matches:'赛程列表', groups:'推送群组',
  teams:'关注战队', tiers:'赛事等级', filters:'屏蔽过滤',
  daily:'每日推送', timing:'时间设置', streams:'自定义直播', advanced:'高级设置',
};

function goPage(name, el) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.getElementById('page-'+name).classList.add('active');
  el.classList.add('active');
  document.getElementById('page-title').textContent = PAGE_TITLES[name] || name;
  if (name==='matches') loadMatches();
}

// ══════════════════════════════════════════
// 初始化 & 刷新
// ══════════════════════════════════════════
async function refreshAll() {
  await loadConfig();
  await loadMatches();
  await loadLogs();
  toast('数据已刷新', 'success');
}

async function loadConfig() {
  const d = await api('/config');
  if (!d.ok) { toast('加载配置失败', 'error'); return; }
  STATE.config = d.data;
  applyConfig(d.data);
}

function applyConfig(c) {
  // 快速设置
  const rm = c.remind_minutes || 10;
  setSlider('q-remind', rm); document.getElementById('q-remind-val').textContent = rm+'min';
  setCheck('q-reschedule', c.reschedule_notify);
  setCheck('q-notify-all', c.notify_all_followed !== false);

  // 时间设置
  setSlider('remind-range', rm); document.getElementById('remind-val').textContent = rm;
  setSlider('fetch-interval', c.fetch_interval_min||10); document.getElementById('fi-val').textContent = c.fetch_interval_min||10;
  setSlider('fetch-ahead', c.fetch_ahead_days||2); document.getElementById('fa-val').textContent = c.fetch_ahead_days||2;

  // 高级
  setCheck('adv-notify-all', c.notify_all_followed !== false);
  setCheck('adv-reschedule', c.reschedule_notify);
  applyAdvancedConfig(c);

  // 群组
  renderTags('groups-list', c.push_groups||[], 'tag-blue', removeGroup);

  // 战队（新格式 [{id, name, slug}]，兼容旧格式字符串）
  renderFollowedTeams(c.followed_teams || []);

  // Tier
  STATE.tiers = c.min_tiers || ["s","a"];
  renderTiers();

  // 直播
  STATE.streams = c.custom_streams || [];
  renderStreams();

  // 屏蔽
  STATE.blacklistTeams = c.blacklist_teams || [];
  STATE.blacklistLeagues = c.blacklist_leagues || [];
  renderTags('blacklist-teams-list', STATE.blacklistTeams, 'tag-red', removeBlTeam);
  renderTags('blacklist-leagues-list', STATE.blacklistLeagues, 'tag-red', removeBlLeague);

  // Stats
  document.getElementById('st-groups').textContent  = (c.push_groups||[]).length;
  document.getElementById('st-teams').textContent   = (c.followed_teams||[]).length;
  document.getElementById('st-remind').textContent  = rm;

  // 每日推送
  applyDailyConfig(c);
}

function setSlider(id, val) {
  const el = document.getElementById(id);
  if (el) el.value = val;
}

function setCheck(id, val) {
  const el = document.getElementById(id);
  if (el) el.checked = !!val;
}

// ══════════════════════════════════════════
// 赛程
// ══════════════════════════════════════════
async function loadMatches() {
  const d = await api('/matches');
  if (!d.ok) { document.getElementById('st-matches').textContent='?'; return; }
  STATE.matches = d.matches || [];
  document.getElementById('st-matches').textContent = STATE.matches.length;
  renderMatchList(STATE.matches);
  renderUpcomingPreview(STATE.matches.slice(0,4));
}

function filterMatches() {
  const q = document.getElementById('match-search').value.toLowerCase();
  const filtered = q ? STATE.matches.filter(m => {
    const t1 = teamName(m,0).toLowerCase();
    const t2 = teamName(m,1).toLowerCase();
    const lg = ((m.league||{}).name||'').toLowerCase();
    return t1.includes(q)||t2.includes(q)||lg.includes(q);
  }) : STATE.matches;
  renderMatchList(filtered);
}

function teamName(m, i) {
  try { return m.opponents[i].opponent.name; } catch(e) { return 'TBD'; }
}

function followedTerms(list) {
  return (list || [])
    .map(item => typeof item === 'object' ? (item?.name || '') : String(item))
    .map(name => name.trim().toLowerCase())
    .filter(Boolean);
}

function fmtTime(iso) {
  if (!iso) return '未知';
  try {
    const d = new Date(iso);
    const cst = new Date(d.getTime() + 8*3600*1000);
    const mo = String(cst.getUTCMonth()+1).padStart(2,'0');
    const day = String(cst.getUTCDate()).padStart(2,'0');
    const h  = String(cst.getUTCHours()).padStart(2,'0');
    const mn = String(cst.getUTCMinutes()).padStart(2,'0');
    return `${mo}-${day} ${h}:${mn}`;
  } catch(e) { return iso; }
}

function tierBadge(m) {
  const tier = ((m.tournament||{}).tier||'other').toLowerCase();
  const map = {s:'badge-s',a:'badge-a',b:'badge-b',c:'badge-c'};
  const cls = map[tier] || 'badge-other';
  return `<span class="badge ${cls}">${tier.toUpperCase()}</span>`;
}

function statusBadge(s) {
  const map = {
    waiting_remind:  ['⏰', 'badge-a',     '等待提醒'],
    waiting_result:  ['🔴', 'badge-s',     '比赛中'],
    polling_result:  ['🔍', 'badge-s',     '查询结果中'],
    scheduled:       ['📅', 'badge-other', '已安排'],
    finished:        ['✅', 'badge-b',     '已结束'],
  };
  const [icon, cls, label] = map[s] || ['❓', 'badge-other', s];
  return `<span class="badge ${cls}">${icon} ${label}</span>`;
}

function fmtCountdown(sec) {
  if (sec == null) return '';
  if (sec <= 0) return '<span style="color:var(--red)">已开赛</span>';
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  if (h > 24) return `${Math.floor(h/24)}天后`;
  if (h > 0)  return `${h}h ${m}m 后`;
  return `${m}分钟后`;
}

function renderMatchCard(m) {
  const t1 = m._t1 || teamName(m,0);
  const t2 = m._t2 || teamName(m,1);
  const followed = followedTerms(STATE.config.followed_teams);
  const starred  = followed.some(f => t1.toLowerCase().includes(f)||t2.toLowerCase().includes(f));
  const league   = (m.league||{}).name||'未知联赛';
  const sched    = m.scheduled_at||m.begin_at;
  const bo       = m.number_of_games||1;
  const status   = m._task_status || 'scheduled';
  const remindMin= m._remind_min ?? (STATE.config.remind_minutes||10);
  const isCustom = m._custom_remind != null;
  const countdown= m._countdown_sec;
  const mid      = m.id;
  const isPolling= status === 'polling_result';
  const isFinished = status === 'finished';

  const t1HasTbd = t1 === 'TBD';
  const t2HasTbd = t2 === 'TBD';
  const tbdWarn  = (t1HasTbd || t2HasTbd)
    ? `<div style="font-size:11px;color:var(--accent);margin-top:4px;">⚠️ 队伍待确认（TBD），确认后将自动重建提醒</div>`
    : '';

  // 结果查询进度行
  let pollInfo = '';
  if (isPolling && m._attempt != null) {
    const dlSec = m._deadline_sec;
    const dlStr = dlSec != null
      ? `截止还剩 ${Math.floor(dlSec/3600)}h${Math.floor((dlSec%3600)/60)}m`
      : '';
    pollInfo = `<div style="font-size:11px;color:var(--blue);margin-top:4px;">
      🔍 已查询 ${m._attempt} 次&nbsp;&nbsp;${dlStr}
    </div>`;
  }

  // 操作按钮：结束的比赛只保留重建，正在查询结果的不显示编辑提醒
  const actions = isFinished
    ? `<button class="btn btn-sm" onclick="rebuildMatch(${mid})">♻️ 重建任务</button>`
    : isPolling
    ? `<button class="btn btn-sm" onclick="rebuildMatch(${mid})">♻️ 重建任务</button>`
    : `<button class="btn btn-sm" onclick="openEditRemind(${mid},${remindMin},${isCustom})">✏️ 编辑提醒</button>
       <button class="btn btn-sm" onclick="rebuildMatch(${mid})">♻️ 重建任务</button>`;

  return `
<div class="match-card ${starred?'starred':''} ${isFinished?'match-finished':''}" id="mc-${mid}">
  <div class="match-header">
    <div class="match-teams">${starred?'⭐ ':''}
      <span ${t1HasTbd?'style="color:var(--text3);font-style:italic"':''}>${t1}</span>
      <span style="color:var(--text3)"> vs </span>
      <span ${t2HasTbd?'style="color:var(--text3);font-style:italic"':''}>${t2}</span>
    </div>
    <div class="match-meta">${tierBadge(m)}${statusBadge(status)}<span class="badge badge-other">BO${bo}</span></div>
  </div>
  <div class="match-league">🏆 ${league}</div>
  <div class="match-time" style="display:flex;justify-content:space-between;align-items:center;">
    <span>🕒 ${fmtTime(sched)} &nbsp;<span style="color:var(--text3);font-size:11px;">${fmtCountdown(countdown)}</span></span>
    ${!isPolling && !isFinished ? `<span style="font-size:11px;color:${isCustom?'var(--accent)':'var(--text3)'}">
      ⏰ ${remindMin}分钟前${isCustom?' (自定义)':''}
    </span>` : ''}
  </div>
  ${tbdWarn}
  ${pollInfo}
  <div class="match-actions" style="margin-top:8px;display:flex;gap:6px;">
    ${actions}
  </div>
</div>`;
}

function renderMatchList(matches) {
  const el = document.getElementById('matches-list');
  if (!matches.length) {
    el.innerHTML = '<div class="empty"><div class="empty-icon">📭</div>没有符合条件的比赛</div>';
    return;
  }
  // 按状态排序：等待提醒 > 等待结果 > 已安排 > 已结束
  const order = {waiting_remind:0, waiting_result:1, polling_result:2, scheduled:3, finished:4};
  const sorted = [...matches].sort((a,b) => {
    const od = (order[a._task_status]??9) - (order[b._task_status]??9);
    return od !== 0 ? od : (a._countdown_sec??99999) - (b._countdown_sec??99999);
  });
  el.innerHTML = sorted.map(renderMatchCard).join('');
}

function renderUpcomingPreview(matches) {
  const el = document.getElementById('upcoming-preview');
  if (!matches.length) {
    el.innerHTML = '<div class="empty"><div class="empty-icon">📭</div>暂无即将开始的比赛</div>';
    return;
  }
  const upcoming = matches.filter(m => m._task_status !== 'finished').slice(0,5);
  el.innerHTML = upcoming.map(renderMatchCard).join('');
}

// ══════════════════════════════════════════
// 编辑提醒弹窗
// ══════════════════════════════════════════
let _editMid = null;

function openEditRemind(mid, currentMin, isCustom) {
  _editMid = mid;
  document.getElementById('edit-mid-label').textContent = `比赛 #${mid}`;
  document.getElementById('edit-remind-input').value = currentMin;
  document.getElementById('edit-remind-custom').checked = isCustom;
  document.getElementById('edit-modal').style.display = 'flex';
}

function closeEditModal() {
  document.getElementById('edit-modal').style.display = 'none';
  _editMid = null;
}

async function saveEditRemind() {
  if (!_editMid) return;
  const useCustom = document.getElementById('edit-remind-custom').checked;
  const minutes   = parseInt(document.getElementById('edit-remind-input').value);
  const body      = { remind_minutes: useCustom ? minutes : null };
  const d         = await api(`/matches/${_editMid}`, 'PATCH', body);
  toast(d.ok ? (d.msg||'已保存') : (d.msg||'保存失败'), d.ok?'success':'error');
  if (d.ok) { closeEditModal(); await loadMatches(); }
}

async function rebuildMatch(mid) {
  const d = await api(`/matches/${mid}/rebuild`, 'POST');
  toast(d.ok ? '已重建任务' : '操作失败', d.ok?'success':'error');
  if (d.ok) setTimeout(loadMatches, 500);
}

async function forceRefresh() {
  const d = await api('/refresh', 'POST');
  toast(d.ok ? '正在后台刷新日程…' : '刷新失败', d.ok?'info':'error');
  setTimeout(loadMatches, 3000);
}

// ══════════════════════════════════════════
// Tier
// ══════════════════════════════════════════
function renderTiers() {
  document.querySelectorAll('.tier-btn').forEach(btn => {
    const t = btn.dataset.tier;
    btn.className = 'tier-btn' + (STATE.tiers.includes(t) ? ' active-'+t : '');
  });
  document.getElementById('tier-summary').textContent =
    STATE.tiers.length ? STATE.tiers.map(t=>t.toUpperCase()).join('、') : '（无）';
}

function toggleTier(t, btn) {
  if (STATE.tiers.includes(t)) {
    STATE.tiers = STATE.tiers.filter(x=>x!==t);
  } else {
    STATE.tiers.push(t);
  }
  renderTiers();
}

async function saveTiers() {
  const d = await api('/config', 'PATCH', {min_tiers: STATE.tiers});
  toast(d.ok ? 'Tier 设置已保存' : '保存失败', d.ok?'success':'error');
}

// ══════════════════════════════════════════
// Tags 通用渲染
// ══════════════════════════════════════════
function renderTags(containerId, items, cls, onRemove) {
  const el = document.getElementById(containerId);
  if (!items.length) {
    el.innerHTML = '<span style="font-size:12px;color:var(--text3);">（空）</span>';
    return;
  }
  el.innerHTML = items.map((item,i) =>
    `<span class="tag ${cls}">${item}<span class="tag-del" onclick="(${onRemove.toString()})('${item.replace(/'/g,"\\'")}')">×</span></span>`
  ).join('');
}

// ══════════════════════════════════════════
// 群组
// ══════════════════════════════════════════
async function addGroup() {
  const inp = document.getElementById('new-group');
  const gid = inp.value.trim();
  if (!/^\d+$/.test(gid)) { toast('请输入纯数字群号', 'error'); return; }
  const d = await api('/groups', 'POST', {gid});
  if (d.ok) { inp.value=''; await loadConfig(); toast('已添加群 '+gid, 'success'); }
  else toast(d.msg||'添加失败', 'error');
}

async function removeGroup(gid) {
  const d = await api('/groups/'+gid, 'DELETE');
  if (d.ok) { await loadConfig(); toast('已移除群 '+gid, 'success'); }
  else toast('移除失败', 'error');
}

// ══════════════════════════════════════════
// 战队关注（精确 ID 匹配）
// ══════════════════════════════════════════

function renderFollowedTeams(teams) {
  const el = document.getElementById('teams-list');
  if (!teams.length) {
    el.innerHTML = '<div style="font-size:12px;color:var(--text3);padding:8px 0;">还没有关注任何战队，在下方搜索添加</div>';
    return;
  }
  el.innerHTML = teams.map(t => {
    const name  = typeof t === 'string' ? t : (t.name || '?');
    const id    = typeof t === 'object' && t.id ? t.id : null;
    const isOld = !id;
    return `<div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--border);">
      <span style="font-family:var(--font-mono);font-size:13px;color:var(--text);flex:1;">
        ⭐ ${name}${isOld ? ' <span style="color:var(--text3);font-size:10px;">(旧版)</span>' : ''}
      </span>
      ${id ? `<span style="font-size:11px;color:var(--text3);font-family:var(--font-mono);">ID:${id}</span>` : ''}
      <button class="btn btn-danger btn-sm unfollow-btn"
              data-id="${id || ''}"
              data-name="${name.replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;')}">
        取消关注
      </button>
    </div>`;
  }).join('');

  // 绑定事件（避免 onclick 内嵌参数的特殊字符问题）
  el.querySelectorAll('.unfollow-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const id   = parseInt(btn.dataset.id) || null;
      const name = btn.dataset.name || '';
      removeTeam(id, name);
    });
  });
}

async function removeTeam(teamId, displayName) {
  const body = teamId ? {id: teamId} : {name: displayName};
  const d = await api('/teams/unfollow', 'POST', body);
  if (d.ok) {
    await loadConfig();
    toast('已取消关注 ' + (displayName || teamId), 'success');
    const resEl = document.getElementById('team-search-results');
    if (resEl && resEl.innerHTML) searchTeams();
  } else {
    toast(d.msg || '操作失败', 'error');
  }
}

async function followTeam(id, name, slug) {
  const d = await api('/teams', 'POST', {id, name, slug});
  if (d.ok) {
    await loadConfig();
    toast('已关注 ' + name, 'success');
    const resEl = document.getElementById('team-search-results');
    if (resEl && resEl.innerHTML) await searchTeams();
  } else {
    toast(d.msg || '操作失败', 'error');
  }
}

// 搜索节流
let _searchTimer = null;
function onTeamSearchInput() {
  clearTimeout(_searchTimer);
  const q = document.getElementById('team-search-input').value.trim();
  if (q.length < 2) {
    document.getElementById('team-search-results').innerHTML = '';
    document.getElementById('team-search-status').textContent = '';
    return;
  }
  _searchTimer = setTimeout(searchTeams, 400);
}

async function searchTeams() {
  const q = document.getElementById('team-search-input').value.trim();
  if (q.length < 2) { toast('请输入至少2个字符', 'info'); return; }
  const statusEl = document.getElementById('team-search-status');
  const resultsEl = document.getElementById('team-search-results');
  statusEl.textContent = '搜索中…';
  resultsEl.innerHTML = '';
  const d = await api('/teams/search?q=' + encodeURIComponent(q));
  if (!d.ok) { statusEl.textContent = d.msg || '搜索失败'; return; }
  const teams = d.teams || [];
  statusEl.textContent = teams.length ? `找到 ${teams.length} 支战队：` : '未找到匹配战队';
  if (!teams.length) return;

  resultsEl.innerHTML = teams.map(t => `
<div style="display:flex;align-items:center;gap:10px;padding:10px 0;border-bottom:1px solid var(--border);">
  ${t.image_url
    ? `<img src="${t.image_url}" style="width:28px;height:28px;border-radius:4px;object-fit:contain;background:var(--bg3);" onerror="this.style.display='none'">`
    : `<div style="width:28px;height:28px;border-radius:4px;background:var(--bg3);display:flex;align-items:center;justify-content:center;font-size:14px;">🎮</div>`}
  <div style="flex:1;">
    <div style="font-family:var(--font-mono);font-size:13px;color:var(--text);font-weight:600;">${t.name}</div>
    <div style="font-size:11px;color:var(--text3);">ID: ${t.id}${t.slug ? ' · ' + t.slug : ''}</div>
  </div>
  ${t.followed
    ? `<span style="font-size:11px;color:var(--green);font-family:var(--font-mono);">✅ 已关注</span>
       <button class="btn btn-danger btn-sm search-unfollow-btn"
               data-id="${t.id}"
               data-name="${(t.name||'').replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;')}">取消</button>`
    : `<button class="btn btn-success btn-sm search-follow-btn"
               data-id="${t.id}"
               data-name="${(t.name||'').replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;')}"
               data-slug="${(t.slug||'').replace(/"/g,'&quot;')}">+ 关注</button>`}
</div>`).join('');

  // 绑定搜索结果里的按钮事件
  resultsEl.querySelectorAll('.search-follow-btn').forEach(btn => {
    btn.addEventListener('click', () => followTeam(parseInt(btn.dataset.id), btn.dataset.name, btn.dataset.slug));
  });
  resultsEl.querySelectorAll('.search-unfollow-btn').forEach(btn => {
    btn.addEventListener('click', () => removeTeam(parseInt(btn.dataset.id), btn.dataset.name));
  });
}

async function followTeam(id, name, slug) {
  const d = await api('/teams', 'POST', {id, name, slug});
  if (d.ok) {
    toast('已关注 ' + name, 'success');
    await loadConfig();
    // 刷新搜索结果里的状态
    await searchTeams();
  } else {
    toast(d.msg || '操作失败', 'error');
  }
}

// ══════════════════════════════════════════
// 黑名单
// ══════════════════════════════════════════
async function addBlacklistTeam() {
  const inp = document.getElementById('new-bl-team');
  const name = inp.value.trim();
  if (!name) return;
  STATE.blacklistTeams.push(name.toLowerCase());
  inp.value='';
  renderTags('blacklist-teams-list', STATE.blacklistTeams, 'tag-red', removeBlTeam);
  await api('/config', 'PATCH', {blacklist_teams: STATE.blacklistTeams});
  toast('已屏蔽战队 '+name, 'success');
}

function removeBlTeam(name) {
  STATE.blacklistTeams = STATE.blacklistTeams.filter(x=>x!==name);
  renderTags('blacklist-teams-list', STATE.blacklistTeams, 'tag-red', removeBlTeam);
  api('/config', 'PATCH', {blacklist_teams: STATE.blacklistTeams});
  toast('已取消屏蔽 '+name, 'success');
}

async function addBlacklistLeague() {
  const inp = document.getElementById('new-bl-league');
  const name = inp.value.trim();
  if (!name) return;
  STATE.blacklistLeagues.push(name.toLowerCase());
  inp.value='';
  renderTags('blacklist-leagues-list', STATE.blacklistLeagues, 'tag-red', removeBlLeague);
  await api('/config', 'PATCH', {blacklist_leagues: STATE.blacklistLeagues});
  toast('已屏蔽联赛 '+name, 'success');
}

function removeBlLeague(name) {
  STATE.blacklistLeagues = STATE.blacklistLeagues.filter(x=>x!==name);
  renderTags('blacklist-leagues-list', STATE.blacklistLeagues, 'tag-red', removeBlLeague);
  api('/config', 'PATCH', {blacklist_leagues: STATE.blacklistLeagues});
  toast('已取消屏蔽联赛 '+name, 'success');
}

// ══════════════════════════════════════════
// 时间设置
// ══════════════════════════════════════════
async function saveTiming() {
  const body = {
    remind_minutes:    parseInt(document.getElementById('remind-range').value),
    fetch_interval_min: parseInt(document.getElementById('fetch-interval').value),
    fetch_ahead_days:   parseInt(document.getElementById('fetch-ahead').value),
  };
  const d = await api('/config', 'PATCH', body);
  toast(d.ok?'时间设置已保存':'保存失败', d.ok?'success':'error');
  if (d.ok) await loadConfig();
}

// ══════════════════════════════════════════
// 直播流
// ══════════════════════════════════════════
function renderStreams() {
  const el = document.getElementById('streams-list');
  if (!STATE.streams.length) {
    el.innerHTML = '<div style="font-size:12px;color:var(--text3);margin-bottom:8px;">（无自定义直播源）</div>';
    return;
  }
  el.innerHTML = STATE.streams.map((s,i) => `
<div class="stream-row">
  <input type="text" value="${s.name||''}" placeholder="平台名"
         style="width:120px;" onchange="STATE.streams[${i}].name=this.value">
  <input type="text" value="${s.url||''}" placeholder="链接"
         style="flex:1;" onchange="STATE.streams[${i}].url=this.value">
  <button class="btn btn-danger btn-sm" onclick="removeStream(${i})">删除</button>
</div>`).join('');
}

function addStream() {
  const name = document.getElementById('new-stream-name').value.trim();
  const url  = document.getElementById('new-stream-url').value.trim();
  if (!url) { toast('请输入直播链接', 'error'); return; }
  STATE.streams.push({name: name||'自定义', url});
  document.getElementById('new-stream-name').value = '';
  document.getElementById('new-stream-url').value  = '';
  renderStreams();
}

function removeStream(i) {
  STATE.streams.splice(i, 1);
  renderStreams();
}

async function saveStreams() {
  const d = await api('/config', 'PATCH', {custom_streams: STATE.streams});
  toast(d.ok?'直播设置已保存':'保存失败', d.ok?'success':'error');
}

// ══════════════════════════════════════════
// 高级设置
// ══════════════════════════════════════════
function applyAdvancedConfig(c) {
  setCheck('adv-notify-all',   c.notify_all_followed   ?? true);
  setCheck('adv-reschedule',   c.reschedule_notify     ?? true);
  setCheck('adv-image-push',   c.image_push_enabled    ?? true);
  setCheck('adv-tour-announce',c.tournament_announce_enabled ?? true);
  setCheck('adv-test-mode',    c.test_mode_enabled     ?? false);
  const webHostEl = document.getElementById('adv-web-host');
  if (webHostEl) webHostEl.value = c.web_panel_host ?? '127.0.0.1';
  const targetTypeEl = document.getElementById('adv-test-target-type');
  if (targetTypeEl) targetTypeEl.value = c.test_target_type ?? 'private';
  const targetIdEl = document.getElementById('adv-test-target-id');
  if (targetIdEl) targetIdEl.value = c.test_target_id ?? '';
  const hoursEl = document.getElementById('adv-tour-hours');
  if (hoursEl) hoursEl.value = c.tournament_announce_hours ?? 2;
}

/* async function sendTestPush(action) {
  const d = await api('/test', 'POST', {action});
  if (d.ok && d.msg) {
    toast(d.msg, 'success');
    return;
  }
  toast(d.msg || '测试消息发送失败', d.ok ? 'success' : 'error');
}

function getAdvancedConfigBody() {
  return {
    notify_all_followed:         document.getElementById('adv-notify-all').checked,
    reschedule_notify:           document.getElementById('adv-reschedule').checked,
    tournament_announce_enabled: document.getElementById('adv-tour-announce').checked,
    tournament_announce_hours:   parseInt(document.getElementById('adv-tour-hours').value) || 2,
    test_mode_enabled:           document.getElementById('adv-test-mode').checked,
    test_target_type:            document.getElementById('adv-test-target-type').value,
    test_target_id:              document.getElementById('adv-test-target-id').value.trim(),
  };
}

async function saveAdvanced(silent = false) {
  const body = {
    ...getAdvancedConfigBody(),
  };
  const token = document.getElementById('adv-token').value.trim();
  if (token) body.pandascore_token = token;
  const d = await api('/config', 'PATCH', body);
  if (!silent) toast(d.ok ? '高级设置已保存' : '保存失败', d.ok ? 'success' : 'error');
  if (d.ok) document.getElementById('adv-token').value = '';
  return d;
}

async function saveTestConfig() {
  const targetId = document.getElementById('adv-test-target-id').value.trim();
  if (!targetId) {
    toast('请先输入测试 QQ 或群号', 'error');
    return;
  }
  if (!/^\d+$/.test(targetId)) {
    toast('测试目标号码只能填写数字', 'error');
    return;
  }
  const d = await saveAdvanced(true);
  toast(d.ok ? '测试配置已保存' : '测试配置保存失败', d.ok ? 'success' : 'error');
}

async function sendTestPush(action) {
  const targetId = document.getElementById('adv-test-target-id').value.trim();
  if (!targetId) {
    toast('请先输入并保存测试 QQ 或群号', 'error');
    return;
  }
  if (!/^\d+$/.test(targetId)) {
    toast('测试目标号码只能填写数字', 'error');
    return;
  }
  const saved = await saveAdvanced(true);
  if (!saved.ok) {
    toast(saved.msg || '测试配置保存失败', 'error');
    return;
  }
  const d = await api('/test', 'POST', {action});
  if (d.ok && d.msg) {
    toast(d.msg, 'success');
    return;
  }
  toast(d.msg || '测试消息发送失败', d.ok ? 'success' : 'error');
}

*/
function getAdvancedConfigBodySafe() {
  return {
    web_panel_host: document.getElementById('adv-web-host').value.trim() || '127.0.0.1',
    notify_all_followed: document.getElementById('adv-notify-all').checked,
    reschedule_notify: document.getElementById('adv-reschedule').checked,
    image_push_enabled: document.getElementById('adv-image-push').checked,
    tournament_announce_enabled: document.getElementById('adv-tour-announce').checked,
    tournament_announce_hours: parseInt(document.getElementById('adv-tour-hours').value) || 2,
    test_mode_enabled: document.getElementById('adv-test-mode').checked,
    test_target_type: document.getElementById('adv-test-target-type').value,
    test_target_id: document.getElementById('adv-test-target-id').value.trim(),
  };
}

async function saveAdvanced(silent = false) {
  const body = { ...getAdvancedConfigBodySafe() };
  const token = document.getElementById('adv-token').value.trim();
  if (token) body.pandascore_token = token;
  const d = await api('/config', 'PATCH', body);
  if (!silent) {
    const msg = d.ok
      ? (d.restart_required ? '\u9ad8\u7ea7\u8bbe\u7f6e\u5df2\u4fdd\u5b58\uff0cWeb \u9762\u677f\u5730\u5740/\u7aef\u53e3\u53d8\u66f4\u9700\u91cd\u542f\u63d2\u4ef6' : '\u9ad8\u7ea7\u8bbe\u7f6e\u5df2\u4fdd\u5b58')
      : '\u4fdd\u5b58\u5931\u8d25';
    toast(msg, d.ok ? 'success' : 'error');
  }
  if (d.ok) document.getElementById('adv-token').value = '';
  return d;
}

async function saveTestConfig() {
  const targetId = document.getElementById('adv-test-target-id').value.trim();
  if (!targetId) {
    toast('\u8bf7\u5148\u8f93\u5165\u6d4b\u8bd5 QQ \u6216\u7fa4\u53f7', 'error');
    return;
  }
  if (!/^\d+$/.test(targetId)) {
    toast('\u6d4b\u8bd5\u76ee\u6807\u53f7\u7801\u53ea\u80fd\u586b\u5199\u6570\u5b57', 'error');
    return;
  }
  const d = await saveAdvanced(true);
  toast(d.ok ? '\u6d4b\u8bd5\u914d\u7f6e\u5df2\u4fdd\u5b58' : '\u6d4b\u8bd5\u914d\u7f6e\u4fdd\u5b58\u5931\u8d25', d.ok ? 'success' : 'error');
}

async function sendTestPush(action) {
  const targetId = document.getElementById('adv-test-target-id').value.trim();
  if (!targetId) {
    toast('\u8bf7\u5148\u8f93\u5165\u5e76\u4fdd\u5b58\u6d4b\u8bd5 QQ \u6216\u7fa4\u53f7', 'error');
    return;
  }
  if (!/^\d+$/.test(targetId)) {
    toast('\u6d4b\u8bd5\u76ee\u6807\u53f7\u7801\u53ea\u80fd\u586b\u5199\u6570\u5b57', 'error');
    return;
  }
  const saved = await saveAdvanced(true);
  if (!saved.ok) {
    toast(saved.msg || '\u6d4b\u8bd5\u914d\u7f6e\u4fdd\u5b58\u5931\u8d25', 'error');
    return;
  }
  const d = await api('/test', 'POST', {action});
  if (d.ok && d.msg) {
    toast(d.msg, 'success');
    return;
  }
  toast(d.msg || '\u6d4b\u8bd5\u6d88\u606f\u53d1\u9001\u5931\u8d25', d.ok ? 'success' : 'error');
}

// ══════════════════════════════════════════
// 每日推送
// ══════════════════════════════════════════
let DAILY_TIMES = [];
let DAILY_DAYS  = 1;

function applyDailyConfig(c) {
  setCheck('daily-enabled', c.daily_push_enabled);
  DAILY_TIMES = c.daily_push_times || ['08:00'];
  DAILY_DAYS  = c.daily_push_days  || 1;
  renderDailyTimes();
  renderDaysGrid();
}

function renderDailyTimes() {
  const el = document.getElementById('daily-times-list');
  if (!DAILY_TIMES.length) {
    el.innerHTML = '<span style="font-size:12px;color:var(--text3);">（未设置推送时间）</span>';
    return;
  }
  el.innerHTML = DAILY_TIMES.map(t =>
    `<span class="tag tag-green">${t}<span class="tag-del" onclick="removeDailyTime('${t}')">×</span></span>`
  ).join('');
}

function addDailyTime() {
  const val = document.getElementById('new-daily-time').value;
  if (!val) { toast('请选择时间', 'error'); return; }
  const hhmm = val.slice(0,5);
  if (DAILY_TIMES.includes(hhmm)) { toast('该时间已存在', 'info'); return; }
  DAILY_TIMES.push(hhmm);
  DAILY_TIMES.sort();
  renderDailyTimes();
}

function removeDailyTime(t) {
  DAILY_TIMES = DAILY_TIMES.filter(x => x !== t);
  renderDailyTimes();
}

function renderDaysGrid() {
  document.querySelectorAll('#days-grid .tier-btn').forEach(btn => {
    const d = parseInt(btn.dataset.days);
    btn.className = 'tier-btn' + (d === DAILY_DAYS ? ' active-a' : '');
  });
}

function selectDays(n, btn) {
  DAILY_DAYS = n;
  renderDaysGrid();
}

async function saveDailyPush() {
  const body = {
    daily_push_enabled: document.getElementById('daily-enabled').checked,
    daily_push_times:   DAILY_TIMES,
    daily_push_days:    DAILY_DAYS,
  };
  const d = await api('/config', 'PATCH', body);
  toast(d.ok ? '每日推送设置已保存' : '保存失败', d.ok?'success':'error');
}

async function pushNow(days) {
  const d = await api('/push_now', 'POST', {days});
  if (d.ok && d.skipped) {
    toast('当前没有可推送的已安排比赛，已跳过日报推送', 'info');
    return;
  }
  toast(d.ok ? `已推送 ${days} 天赛程到所有群` : '推送失败', d.ok?'success':'error');
}

async function previewDaily() {
  const days = DAILY_DAYS || 1;
  const el   = document.getElementById('daily-preview');
  el.textContent = '加载中…';
  const cfg  = await api('/config');
  const mResp = await api('/matches');
  if (!mResp.ok) { el.textContent = '获取比赛数据失败'; return; }

  const matches  = mResp.matches || [];
  const followed = followedTerms((cfg.data || {}).followed_teams);
  const nowCst   = new Date(Date.now() + 8*3600*1000);
  const todayStr = nowCst.toISOString().slice(0,10);

  const buckets = {};
  for (const m of matches) {
    const iso = m.scheduled_at || m.begin_at;
    if (!iso) continue;
    const dt  = new Date(iso);
    const dtCst = new Date(dt.getTime() + 8*3600*1000);
    const dateStr = dtCst.toISOString().slice(0,10);
    const delta = Math.floor((new Date(dateStr) - new Date(todayStr)) / 86400000);
    if (delta < 0 || delta >= days) continue;
    const label = delta===0?'今天':delta===1?'明天':dtCst.toISOString().slice(5,10).replace('-','-');
    if (!buckets[label]) buckets[label] = [];
    const hh = String(dtCst.getUTCHours()).padStart(2,'0');
    const mm = String(dtCst.getUTCMinutes()).padStart(2,'0');
    const t1 = m._t1 || m.opponents?.[0]?.opponent?.name||'TBD';
    const t2 = m._t2 || m.opponents?.[1]?.opponent?.name||'TBD';
    const star = followed.some(f=>t1.toLowerCase().includes(f)||t2.toLowerCase().includes(f)) ? '⭐' : '  ';
    const lg = (m.league||{}).name||'?';
    const bo = m.number_of_games||1;
    buckets[label].push(`  ${star} ${hh}:${mm}  ${t1} vs ${t2}  BO${bo} · ${lg}`);
  }

  if (!Object.keys(buckets).length) {
    el.textContent = `📭 未来 ${days} 天内没有符合条件的 CS2 比赛`;
    return;
  }

  const now8 = new Date(Date.now()+8*3600*1000);
  const mo = String(now8.getUTCMonth()+1).padStart(2,'0');
  const dy = String(now8.getUTCDate()).padStart(2,'0');
  const hh = String(now8.getUTCHours()).padStart(2,'0');
  const mn = String(now8.getUTCMinutes()).padStart(2,'0');
  let text = `📅 【CS2 赛程日报】${mo}月${dy}日 ${hh}:${mn} 播报\n`;
  for (const [label, lines] of Object.entries(buckets)) {
    text += `\n🗓 ${label}（共 ${lines.length} 场）\n` + lines.join('\n');
  }
  el.textContent = text;
}

// ══════════════════════════════════════════
// 快速保存 & 全量保存
// ══════════════════════════════════════════
async function quickSave() {
  const body = {
    remind_minutes:      parseInt(document.getElementById('q-remind').value),
    reschedule_notify:   document.getElementById('q-reschedule').checked,
    notify_all_followed: document.getElementById('q-notify-all').checked,
  };
  const d = await api('/config', 'PATCH', body);
  if (!d.ok) toast('快速设置保存失败', 'error');
  return d.ok;
}

async function saveAll() {
  // 收集所有页面当前状态，一次性全量保存
  const body = {
    // 快速设置
    remind_minutes:      parseInt(document.getElementById('q-remind').value),
    reschedule_notify:   document.getElementById('q-reschedule').checked,
    notify_all_followed: document.getElementById('q-notify-all').checked,
    // 时间设置
    fetch_interval_min:  parseInt(document.getElementById('fetch-interval').value),
    fetch_ahead_days:    parseInt(document.getElementById('fetch-ahead').value),
    // 每日推送
    daily_push_enabled:  document.getElementById('daily-enabled').checked,
    daily_push_times:    DAILY_TIMES,
    daily_push_days:     DAILY_DAYS,
    // 战队/群组/Tier（从 STATE 读，始终最新）
    min_tiers:           STATE.tiers,
    push_groups:         STATE.config.push_groups  || [],
    // 注：followed_teams 通过搜索关注接口单独管理，saveAll 不覆盖
    custom_streams:      STATE.streams,
    blacklist_teams:     STATE.blacklistTeams,
    blacklist_leagues:   STATE.blacklistLeagues,
    // 高级
    web_panel_host:               document.getElementById('adv-web-host').value.trim() || '127.0.0.1',
    image_push_enabled:          document.getElementById('adv-image-push').checked,
    tournament_announce_enabled: document.getElementById('adv-tour-announce').checked,
    tournament_announce_hours:   parseInt(document.getElementById('adv-tour-hours').value) || 2,
  };
  const d = await api('/config', 'PATCH', body);
  const msg = d.ok
    ? (d.restart_required ? '全部配置已保存，Web 面板地址/端口变更需重启插件' : '全部配置已保存')
    : '保存失败';
  toast(msg, d.ok ? 'success' : 'error');
  if (d.ok) await loadConfig();
}

// ══════════════════════════════════════════
// 导出 / 导入 / 清除
// ══════════════════════════════════════════
async function exportConfig() {
  const d = await api('/config/export');
  const blob = new Blob([JSON.stringify(d.data, null, 2)], {type:'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'cs2_plugin_config.json';
  a.click();
  toast('配置已导出', 'success');
}

async function importConfig(e) {
  const file = e.target.files[0];
  if (!file) return;
  const text = await file.text();
  try {
    const cfg = JSON.parse(text);
    const d = await api('/config/import', 'POST', cfg);
    toast(d.ok?'配置已导入':'导入失败', d.ok?'success':'error');
    if (d.ok) await loadConfig();
  } catch(err) { toast('JSON 解析失败', 'error'); }
  e.target.value='';
}

async function clearNotified() {
  if (!confirm('确认清除所有通知记录？（不影响配置）')) return;
  const d = await api('/notified/clear', 'POST');
  toast(d.ok?'已清除通知记录':'清除失败', d.ok?'success':'error');
}

async function rebuildTasks() {
  const d = await api('/tasks/rebuild', 'POST');
  toast(d.ok?'已重建提醒任务':'操作失败', d.ok?'success':'error');
}

// ══════════════════════════════════════════
// 日志
// ══════════════════════════════════════════
async function loadLogs() {
  const d = await api('/logs');
  const el = document.getElementById('log-panel');
  if (!d.ok || !d.logs.length) {
    el.innerHTML = '<div style="color:var(--text3)">暂无日志记录</div>';
    return;
  }
  el.innerHTML = d.logs.map(l => {
    const cls = l.level==='ERROR'?'log-error':l.level==='WARN'?'log-warn':l.level==='OK'?'log-ok':'log-info';
    return `<p class="log-line"><span class="log-time">[${l.time}]</span> <span class="${cls}">[${l.level}]</span> ${l.msg}</p>`;
  }).join('');
  el.scrollTop = el.scrollHeight;
}

// ══════════════════════════════════════════
// 启动
// ══════════════════════════════════════════
(async () => {
  await loadConfig();
  await loadMatches();
  await loadLogs();
  // 每 30 秒自动刷新赛程列表，保持倒计时和状态实时更新
  setInterval(async () => {
    if (document.getElementById('page-matches').classList.contains('active') ||
        document.getElementById('page-dashboard').classList.contains('active')) {
      await loadMatches();
    }
  }, 30000);
})();
</script>
<!-- ════ 编辑提醒弹窗 ════ -->
<div id="edit-modal" style="
  display:none; position:fixed; inset:0; z-index:999;
  background:rgba(0,0,0,0.6); align-items:center; justify-content:center;">
  <div style="
    background:var(--bg2); border:1px solid var(--border2);
    border-radius:var(--radius-lg); padding:28px; width:360px; max-width:90vw;">
    <div style="font-family:var(--font-mono);font-weight:700;font-size:14px;color:var(--text);margin-bottom:16px;">
      ✏️ 编辑赛前提醒 &nbsp;<span id="edit-mid-label" style="color:var(--text3);font-size:12px;"></span>
    </div>

    <div style="margin-bottom:14px;">
      <label style="font-size:12px;color:var(--text2);font-family:var(--font-mono);display:flex;align-items:center;gap:8px;margin-bottom:10px;">
        <input type="checkbox" id="edit-remind-custom">
        使用自定义提醒时间（不勾选则恢复全局设置）
      </label>
      <div style="display:flex;align-items:center;gap:10px;">
        <input type="number" id="edit-remind-input" min="1" max="120" value="10"
          style="width:90px;" placeholder="分钟">
        <span style="font-size:12px;color:var(--text3)">分钟前提醒（1~120）</span>
      </div>
    </div>

    <div style="font-size:11px;color:var(--text3);font-family:var(--font-mono);margin-bottom:18px;line-height:1.6;">
      修改后将立即重建该场比赛的提醒任务，若比赛时间已过提醒点则本次不再发送提醒。
    </div>

    <div style="display:flex;gap:8px;justify-content:flex-end;">
      <button class="btn" onclick="closeEditModal()">取消</button>
      <button class="btn btn-primary" onclick="saveEditRemind()">保存并重建</button>
    </div>
  </div>
</div>

</body>
</html>
"""


WEB_PANEL_LOGIN_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CS2 推送插件管理面板</title>
<style>
  * { box-sizing: border-box; }
  body {
    margin: 0;
    min-height: 100vh;
    display: grid;
    place-items: center;
    background: #0d0f14;
    color: #e8eaf2;
    font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  }
  .box {
    width: min(420px, calc(100vw - 32px));
    border: 1px solid rgba(255,255,255,.13);
    border-radius: 8px;
    background: #13161e;
    padding: 24px;
  }
  h1 { margin: 0 0 14px; font-size: 18px; }
  p { margin: 0 0 18px; color: #8b90a8; font-size: 13px; line-height: 1.6; }
  input {
    width: 100%;
    padding: 10px 12px;
    border-radius: 8px;
    border: 1px solid rgba(255,255,255,.13);
    background: #1a1e2a;
    color: #e8eaf2;
    outline: none;
  }
  button {
    margin-top: 12px;
    width: 100%;
    padding: 10px 14px;
    border-radius: 8px;
    border: 1px solid #e8973a;
    background: #e8973a;
    color: #000;
    font-weight: 700;
    cursor: pointer;
  }
  .err { min-height: 18px; margin-top: 10px; color: #f16868; font-size: 12px; }
</style>
</head>
<body>
  <form class="box" onsubmit="login(event)">
    <h1>CS2 推送插件管理面板</h1>
    <p>请输入管理令牌。令牌可通过机器人指令 <code>~cs面板</code> 获取。</p>
    <input id="token" type="password" autocomplete="current-password" autofocus>
    <button type="submit">进入面板</button>
    <div class="err" id="err"></div>
  </form>
<script>
function login(e) {
  e.preventDefault();
  const token = document.getElementById('token').value.trim();
  if (!token) {
    document.getElementById('err').textContent = '请输入令牌';
    return;
  }
  localStorage.setItem('cs_panel_token', token);
  location.href = '/?token=' + encodeURIComponent(token);
}
</script>
</body>
</html>
"""


# ─────────────────────────────────────────
# Web 面板服务器
# ─────────────────────────────────────────

class WebPanel:
    def __init__(self, plugin: "CSMatchPlugin"):
        self.plugin = plugin
        self.app    = web.Application(middlewares=[self._auth_middleware])
        self._runner = None
        self._site   = None
        self._logs: list = []  # 内存日志缓冲
        self._setup_routes()

    @web.middleware
    async def _auth_middleware(self, req, handler):
        path = req.path
        if path == "/" or path.startswith("/api/"):
            token = self._request_token(req)
            token_ok = self._token_matches(token)
            if token_ok or self._is_local_request(req):
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
        return bool(token and expected and hmac.compare_digest(token, expected))

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

    def _cors(self, resp):
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp

    async def index(self, req):
        html = WEB_PANEL_HTML
        token = req.rel_url.query.get("token", "").strip()
        if self._token_matches(token):
            bootstrap = (
                "<script>\n"
                f"localStorage.setItem('cs_panel_token', {json.dumps(token)});\n"
                "history.replaceState(null, '', location.pathname);\n"
            )
            html = html.replace("<script>", bootstrap, 1)
        return web.Response(text=html, content_type="text/html", charset="utf-8")

    async def get_config(self, req):
        data = self.plugin.store.export_all()
        return self._json({"ok": True, "data": data})

    async def patch_config(self, req):
        try:
            body = await req.json()
        except Exception:
            return self._json({"ok": False, "msg": "invalid json"}, 400)
        self.plugin.store.import_config(body)
        await self.plugin.reload_runtime_config()
        # 同步运行时配置
        if "remind_minutes" in body:
            self.plugin.store.set_remind_minutes(int(body["remind_minutes"]))
        if "reschedule_notify" in body:
            self.plugin.store.set_reschedule_notify(bool(body["reschedule_notify"]))
        if any(k in body for k in (
            "pandascore_token", "fetch_ahead_days", "fetch_ahead_hours",
        )):
            self.plugin._create_background_task(self.plugin._fetch_and_schedule(), "web_patch_config")
        restart_required = any(k in body for k in ("web_panel_host", "web_panel_port", "web_panel_enabled"))
        return self._json({"ok": True, "restart_required": restart_required})

    async def export_config(self, req):
        data = self.plugin.store.export_all()
        return self._json({"ok": True, "data": data})

    async def import_config(self, req):
        try:
            body = await req.json()
        except Exception:
            return self._json({"ok": False, "msg": "invalid json"}, 400)
        self.plugin.store.import_config(body)
        await self.plugin.reload_runtime_config()
        self.plugin._create_background_task(self.plugin._fetch_and_schedule(), "web_import_config")
        restart_required = any(k in body for k in ("web_panel_host", "web_panel_port", "web_panel_enabled"))
        return self._json({"ok": True, "restart_required": restart_required})

    async def get_matches(self, req):
        from datetime import timezone as _tz
        now    = datetime.now(_tz.utc)
        plugin = self.plugin
        result = []
        seen   = set()

        def _build_entry(m, status, remind_min, custom_rem, countdown,
                         attempt=None, deadline_sec=None):
            snap  = plugin.store.get_match_snapshot(m["id"]) or {}
            entry = dict(m)
            entry["_task_status"]   = status
            entry["_remind_min"]    = remind_min
            entry["_custom_remind"] = custom_rem
            entry["_countdown_sec"] = countdown
            entry["_t1"]            = snap.get("t1") or "TBD"
            entry["_t2"]            = snap.get("t2") or "TBD"
            entry["_attempt"]       = attempt       # 已查询结果次数
            entry["_deadline_sec"]  = deadline_sec  # 距截止还剩秒数
            return entry

        def _countdown(sched_iso):
            if not sched_iso:
                return None
            try:
                dt = datetime.fromisoformat(sched_iso.replace("Z", "+00:00"))
                return int((dt - now).total_seconds())
            except Exception:
                return None

        # ── 1. 已安排但未开赛的比赛（_scheduled 列表）──────────────────────
        for m in plugin._scheduled:
            mid        = m["id"]
            seen.add(mid)
            snap       = plugin.store.get_match_snapshot(mid) or {}
            sched_iso  = snap.get("sched") or m.get("scheduled_at") or m.get("begin_at")
            custom_rem = plugin.store.get_custom_remind(mid)
            remind_min = custom_rem if custom_rem is not None else plugin.store.get_remind_minutes()
            countdown  = _countdown(sched_iso)

            task = plugin._match_tasks.get(mid)
            if plugin.store.is_finished_notified(mid):
                status = "finished"
            elif task and not task.done():
                status = "waiting_remind" if not plugin.store.is_upcoming_notified(mid) else "waiting_result"
            else:
                status = "scheduled"

            result.append(_build_entry(m, status, remind_min, custom_rem, countdown))

        # ── 2. 正在轮询结果的比赛（已离开 _scheduled，但任务仍运行）──────────
        for mid, task in list(plugin._result_tasks.items()):
            if mid in seen:
                continue
            seen.add(mid)
            meta       = plugin._result_meta.get(mid, {})
            match      = meta.get("match", {})
            attempt    = meta.get("attempt", 0)
            snap       = plugin.store.get_match_snapshot(mid) or {}
            sched_iso  = snap.get("sched") or match.get("scheduled_at") or match.get("begin_at")
            custom_rem = plugin.store.get_custom_remind(mid)
            remind_min = custom_rem if custom_rem is not None else plugin.store.get_remind_minutes()
            countdown  = _countdown(sched_iso)

            # 截止时间剩余秒数
            deadline_sec = None
            if meta.get("deadline"):
                try:
                    dl = datetime.fromisoformat(meta["deadline"].replace("Z", "+00:00"))
                    deadline_sec = max(0, int((dl - now).total_seconds()))
                except Exception:
                    pass

            if plugin.store.is_finished_notified(mid):
                status = "finished"
            elif task and not task.done():
                status = "polling_result"
            else:
                status = "finished"

            result.append(_build_entry(match, status, remind_min, custom_rem,
                                       countdown, attempt, deadline_sec))

        return self._json({"ok": True, "matches": result})

    async def refresh(self, req):
        self.plugin._create_background_task(self.plugin._fetch_and_schedule(), "web_refresh")
        return self._json({"ok": True})

    async def push_now(self, req):
        """立即推送赛程到所有群"""
        try:
            body = await req.json()
        except Exception:
            body = {}
        days = int(body.get("days", 1))
        if not self.plugin._has_daily_matches_to_push(days):
            return self._json({"ok": True, "skipped": True, "msg": "no matches to push"})
        self.plugin._create_background_task(self.plugin._do_instant_push(days), "web_push_now")
        return self._json({"ok": True})

    async def send_test(self, req):
        try:
            body = await req.json()
        except Exception:
            body = {}
        action = str(body.get("action", "")).strip() or "赛前"
        if not self.plugin._is_test_mode_enabled():
            return self._json({"ok": False, "msg": "测试模式未开启"}, 400)
        if not self.plugin._get_test_target():
            return self._json({"ok": False, "msg": "请先配置测试 QQ 或群号"}, 400)
        valid = {"赛前", "赛果", "变更", "开幕", "日报", "全部"}
        if action not in valid:
            return self._json({"ok": False, "msg": "未知测试动作"}, 400)
        msg = await self.plugin._run_test_push(action, 1)
        return self._json({"ok": True, "msg": msg})

    async def add_group(self, req):
        body = await req.json()
        gid  = str(body.get("gid", "")).strip()
        if not gid.isdigit():
            return self._json({"ok": False, "msg": "Invalid group id"}, 400)
        added = self.plugin.store.add_group(gid)
        return self._json({"ok": True, "added": added})

    async def remove_group(self, req):
        gid = req.match_info["gid"]
        self.plugin.store.remove_group(gid)
        return self._json({"ok": True})

    async def add_team(self, req):
        """通过精确 ID 关注战队"""
        try:
            body = await req.json()
        except Exception:
            return self._json({"ok": False, "msg": "invalid json"}, 400)
        team_id = body.get("id")
        name    = (body.get("name") or "").strip()
        slug    = (body.get("slug") or "").strip()
        if not team_id or not name:
            return self._json({"ok": False, "msg": "id and name required"}, 400)
        added = self.plugin.store.follow_team(int(team_id), name, slug)
        if added:
            self.plugin._create_background_task(self.plugin._fetch_and_schedule(), "web_add_team")
        return self._json({"ok": True, "added": added})

    async def remove_team(self, req):
        """取消关注战队 — 支持 POST /teams/unfollow {id} 和旧版 DELETE /teams/{name}"""
        team_id = None

        # 优先从 POST body 读 id
        if req.method == "POST":
            try:
                body    = await req.json()
                team_id = int(body.get("id", 0)) or None
            except Exception:
                pass

        # 兼容 DELETE /api/teams/{name}：从 URL 参数取名字再找 id
        if team_id is None:
            import urllib.parse
            name   = urllib.parse.unquote(req.match_info.get("name", ""))
            teams  = self.plugin.store.get_followed_teams()
            matched = [t for t in teams if isinstance(t, dict) and
                       t.get("name", "").lower() == name.lower()]
            if matched:
                team_id = matched[0].get("id")

        if not team_id:
            return self._json({"ok": False, "msg": "team id not found"}, 400)

        self.plugin.store.unfollow_team(team_id)
        self.plugin._create_background_task(self.plugin._fetch_and_schedule(), "web_remove_team")
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
        return self._json({"ok": True, "logs": self._logs[-100:]})

    async def clear_notified(self, req):
        self.plugin.store._data["notified_upcoming"] = []
        self.plugin.store._data["notified_finished"] = []
        self.plugin.store.save()
        return self._json({"ok": True})

    async def rebuild_tasks(self, req):
        for t in self.plugin._match_tasks.values():
            t.cancel()
        self.plugin._match_tasks.clear()
        self.plugin._scheduled_mids.clear()
        self.plugin._create_background_task(self.plugin._fetch_and_schedule(), "web_rebuild_tasks")
        return self._json({"ok": True})

    async def patch_match(self, req):
        """修改单场比赛的自定义提醒时间"""
        try:
            mid = int(req.match_info.get("mid", ""))
        except ValueError:
            return self._json({"ok": False, "msg": "invalid match id"}, 400)
        try:
            body = await req.json()
        except Exception:
            return self._json({"ok": False, "msg": "invalid json"}, 400)

        plugin = self.plugin

        if "remind_minutes" not in body:
            return self._json({"ok": False, "msg": "no supported fields"}, 400)

        val = body["remind_minutes"]
        if val is None:
            plugin.store.del_custom_remind(mid)
            msg = f"已恢复比赛 {mid} 为全局提醒时间"
        else:
            minutes = int(val)
            if not 1 <= minutes <= 120:
                return self._json({"ok": False, "msg": "remind_minutes must be 1~120"}, 400)
            plugin.store.set_custom_remind(mid, minutes)
            msg = f"已设置比赛 {mid} 自定义提醒：{minutes} 分钟"

        # 立即重建该场任务使新时间生效
        task = plugin._match_tasks.get(mid)
        if task and not task.done():
            task.cancel()
        plugin.store.clear_upcoming_notified(mid)
        plugin._scheduled_mids.discard(mid)
        plugin._match_tasks.pop(mid, None)

        match = next((m for m in plugin._scheduled if m["id"] == mid), None)
        if match:
            remind_min = plugin.store.get_custom_remind(mid)
            if remind_min is None:
                remind_min = plugin.store.get_remind_minutes()
            t = asyncio.create_task(plugin._schedule_match(match, remind_min))
            plugin._match_tasks[mid] = t
            plugin._scheduled_mids.add(mid)

        self.push_log("OK", msg)
        return self._json({"ok": True, "msg": msg})

    async def rebuild_match(self, req):
        """手动重建单场比赛的提醒任务（同时取消正在进行的结果轮询）"""
        try:
            mid = int(req.match_info.get("mid", ""))
        except ValueError:
            return self._json({"ok": False, "msg": "invalid match id"}, 400)

        plugin = self.plugin

        # 先从 _scheduled 或 _result_meta 里找到比赛数据
        match = next((m for m in plugin._scheduled if m["id"] == mid), None)
        if match is None:
            meta  = plugin._result_meta.get(mid, {})
            match = meta.get("match")
        if not match:
            return self._json({"ok": False, "msg": "match not found"}, 404)

        # 取消所有相关任务
        for tasks_dict in (plugin._match_tasks, plugin._result_tasks):
            task = tasks_dict.get(mid)
            if task and not task.done():
                task.cancel()
        plugin.store.clear_upcoming_notified(mid)
        plugin._scheduled_mids.discard(mid)
        plugin._match_tasks.pop(mid, None)
        plugin._result_tasks.pop(mid, None)
        plugin._result_meta.pop(mid, None)

        remind_min = plugin.store.get_custom_remind(mid)
        if remind_min is None:
            remind_min = plugin.store.get_remind_minutes()

        t = asyncio.create_task(plugin._schedule_match(match, remind_min))
        plugin._match_tasks[mid] = t
        plugin._scheduled_mids.add(mid)
        self.push_log("OK", f"已手动重建比赛 {mid} 任务（{remind_min} 分钟）")
        return self._json({"ok": True})

    def push_log(self, level: str, msg: str):
        now = datetime.now(CST).strftime("%H:%M:%S")
        self._logs.append({"time": now, "level": level, "msg": msg})
        if len(self._logs) > 500:
            self._logs = self._logs[-500:]

    async def start(self, host: str, port: int):
        self._runner = web.AppRunner(self.app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host, port)
        await self._site.start()
        logger.info(f"[CS] Web 管理面板已启动：http://{host}:{port}")
        self.push_log("OK", f"Web 管理面板已启动，监听 {host}:{port}")

    async def stop(self):
        if self._runner:
            await self._runner.cleanup()
