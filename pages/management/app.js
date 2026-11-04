import { createSetupGuide } from './setup.js';
import { createGroupRuleEditor } from './group-rules.js';
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const gameLabel = "CS2";
const bridge = window.AstrBotPluginView || window.AstrBotPluginPage;
const state = {
  config: {},
  subscriptionsDraft: null,
  saving: false,
  matches: [],
  teams: [],
  searchResultsReady: false,
  ready: false,
  active: "overview",
  dirty: new Set(),
  pending: new Set(),
  tokenConfigured: false,
  teamLogos: {},
  teamLogoDarkNames: new Set(),
  popularTeams: [],
  popularLoaded: false,
  popularLoading: false,
  popularAttempted: false,
};
const secretKeys = new Set(["pandascore_token", "web_panel_token"]);
const escaped = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (character) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        character
      ],
  );
const splitLines = (value) => [
  ...new Set(
    value
      .split(/[\n,，]/)
      .map((item) => item.trim())
      .filter(Boolean),
  ),
];
let sessionToken = "";
let pollTimer;
let refreshTimer;
let searchSequence = 0;
let setupGuide;
let groupRuleEditor;
let pushViewInitialized = false;

// Sandboxed AstrBot views can throw even when accessing the storage property.
// Remembering a tab is optional; storage failures must not block the panel.
function readSession(key) {
  try {
    return window.sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeSession(key, value) {
  try {
    window.sessionStorage.setItem(key, value);
  } catch {
    // Keep the current page state when browser storage is unavailable.
  }
}

async function api(path, method = "GET", body, query = {}) {
  let result;
  if (bridge) {
    result =
      method === "GET"
        ? await bridge.apiGet("panel", { path, ...query })
        : await bridge.apiPost("panel", { path, method, body: body ?? {} });
  } else {
    const url = new URL(path, location.origin);
    Object.entries(query).forEach(([key, value]) =>
      url.searchParams.set(key, value),
    );
    const headers = { Accept: "application/json" };
    if (sessionToken) headers.Authorization = `Bearer ${sessionToken}`;
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const response = await fetch(url, {
      method,
      headers,
      credentials: "same-origin",
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    try {
      result = await response.json();
    } catch {
      throw new Error("服务器未返回有效数据，请检查插件是否已启动。");
    }
    if (response.status === 401)
      throw new Error("登录已失效，请重新打开独立面板并输入管理令牌。");
    if (!response.ok)
      throw new Error(result.msg || result.message || "请求失败，请稍后重试。");
  }
  if (!result || result.ok === false)
    throw new Error(result?.msg || result?.message || "操作未完成。");
  return result;
}

function toast(message, error = false) {
  const element = document.createElement("div");
  element.className = `toast${error ? " error" : ""}`;
  element.textContent = message;
  $("#toasts").append(element);
  setTimeout(() => element.remove(), error ? 9000 : 5000);
}

async function run(button, operation) {
  if (button?.disabled || state.pending.has(button)) return;
  if (button) {
    state.pending.add(button);
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
  }
  try {
    await operation();
  } catch (error) {
    toast(error.message || "操作失败", true);
  } finally {
    if (button) {
      state.pending.delete(button);
      button.disabled = button.dataset.keepDisabled === "true";
      button.removeAttribute("aria-busy");
    }
    updateDirtyIndicators();
  }
}

function dialog(
  title,
  description,
  { confirm = "确定", danger = false, fields = "" } = {},
) {
  return new Promise((resolve) => {
    const element = $("#action-dialog");
    if (element.open) {
      resolve(null);
      return;
    }
    $("#dialog-title").textContent = title;
    $("#dialog-description").textContent = description;
    $("#dialog-fields").innerHTML = fields;
    $("#dialog-confirm").textContent = confirm;
    $("#dialog-confirm").className = `button ${danger ? "danger" : "primary"}`;
    element.returnValue = "";
    element.addEventListener(
      "close",
      () => {
        const values = Object.fromEntries(new FormData($("#dialog-form")));
        resolve(element.returnValue === "confirm" ? values : null);
        $("#dialog-fields").replaceChildren();
      },
      { once: true },
    );
    element.showModal();
  });
}

function subscriptionValues() {
  return state.subscriptionsDraft || state.config;
}

function editSubscriptions(edit) {
  if (!state.ready || state.saving) return;
  state.subscriptionsDraft ||= structuredClone({
    push_groups: state.config.push_groups || [],
    followed_teams: state.config.followed_teams || [],
    group_rules: state.config.group_rules || {},
    rule_presets: state.config.rule_presets || [],
  });
  edit(state.subscriptionsDraft);
  renderSubscriptions();
  syncTeamFollowedState();
  checkDirty("subscriptions");
  checkDirty("push");
}

function updateDirtyIndicators() {
  $("#save-all-bar").hidden = !state.dirty.size;
  $("#save-all").disabled = state.saving;
  $("#discard-all").disabled = state.saving;
  for (const section of ["subscriptions", "push", "advanced"]) {
    const dirty = state.dirty.has(section);
    $(`#tab-${section} .tab-dirty`).hidden = !dirty;
  }
}

function formValues(section) {
  if (section === "subscriptions") {
    const draft = subscriptionValues();
    return {
      push_groups: structuredClone(draft.push_groups || []),
      followed_teams: structuredClone(draft.followed_teams || []),
    };
  }
  const form = $(`#${section}-form`);
  const values = {};
  const tiers = $$('[data-key="min_tiers"]:checked', form).map(
    (input) => input.value,
  );
  $$("[data-key]", form).forEach((input) => {
    if (input.disabled) return;
    const key = input.dataset.key;
    if (key === "min_tiers") {
      values[key] = tiers;
      return;
    }
    if (key === "official_features" || key === "official_platform_ids") {
      values[key] = $$(`[data-key="${key}"]:checked`, form).map((item) => item.value);
      return;
    }
    if (secretKeys.has(key)) {
      if ($(`[data-clear="${key}"]`, form)?.checked) values[key] = "";
      else if (input.value.trim()) values[key] = input.value.trim();
      return;
    }
    if (input.multiple) values[key] = [...input.selectedOptions].map((option) => option.value);
    else if (input.type === "checkbox") values[key] = input.checked;
    else if (input.type === "number") values[key] = Number(input.value);
    else if (input.dataset.type === "lines")
      values[key] = splitLines(input.value).map((value) => value.toLowerCase());
    else if (input.dataset.type === "times")
      values[key] = splitLines(input.value).sort();
    else values[key] = input.value.trim();
  });
  if (section === "push") {
    values.custom_streams = $$(".stream-row").map((row) => ({
      name: $(".stream-name", row).value.trim(),
      url: $(".stream-url", row).value.trim(),
    }));
  }
  if (section === "push") {
    values.group_rules = structuredClone(subscriptionValues().group_rules || {});
    values.rule_presets = structuredClone(subscriptionValues().rule_presets || []);
  }
  return values;
}

function checkDirty(section) {
  if (!state.ready) return;
  const values = formValues(section);
  const dirty = Object.entries(values).some(
    ([key, value]) =>
      JSON.stringify(value) !== JSON.stringify(state.config[key]),
  );
  if (dirty) state.dirty.add(section);
  else state.dirty.delete(section);
  updateDirtyIndicators();
}

function fillForm(section) {
  if (section === "subscriptions") return;
  const form = $(`#${section}-form`);
  $$("[data-key]", form).forEach((input) => {
    const key = input.dataset.key;
    const value = state.config[key];
    if (secretKeys.has(key)) {
      input.value = "";
      input.type = "password";
      return;
    }
    if (key === "min_tiers" || key === "official_features" || key === "official_platform_ids")
      input.checked = (value || []).includes(input.value);
    else if (input.multiple)
      [...input.options].forEach((option) => { option.selected = (value || []).includes(option.value); });
    else if (input.type === "checkbox") input.checked = !!value;
    else if (input.dataset.type === "lines")
      input.value = (value || []).join("\n");
    else if (input.dataset.type === "times")
      input.value = (value || []).join(", ");
    else input.value = value ?? "";
  });
  $$("[data-clear]", form).forEach((input) => {
    input.checked = false;
  });
  $$("[data-reveal]", form).forEach((button) => {
    button.textContent = "显示";
    button.setAttribute("aria-pressed", "false");
  });
  if (section === "push") renderStreams(state.config.custom_streams || []);
}

async function loadConfig() {
  const result = await api("/api/config");
  state.config = result.data;
  renderOfficialPlatforms(result.official_platforms || [], result.official_platforms_note || "");
  state.teamLogos = result.team_logos || {};
  state.teamLogoDarkNames = new Set(result.team_logo_dark_names || []);
  state.tokenConfigured = !!result.token_configured;
  $("#pandascore-secret-hint").textContent = state.tokenConfigured
    ? "已配置。留空保留原 Token；输入新值可替换。"
    : "尚未配置，请输入 PandaScore Token。";
  $("#web-secret-hint").textContent =
    result.web_token_configured === false
      ? "尚未配置管理令牌。"
      : "管理令牌不在页面回显。留空保留原令牌。";
  $("#setup-notice").hidden = state.tokenConfigured;
  for (const section of ["subscriptions", "push", "advanced"])
    if (!state.dirty.has(section)) fillForm(section);
  renderSubscriptions();
  syncTeamFollowedState();
  renderStats();
  renderPushSummary();
  updateDirtyIndicators();
  setupGuide?.sync();
  if (!pushViewInitialized) {
    pushViewInitialized = true;
    const stored = readSession("cs2.pushView");
    activatePushView(stored === "groups" ? "groups" : "defaults");
  }
}

function renderOfficialPlatforms(platforms, note) {
  const list = $("#official-platform-list");
  const selected = state.dirty.has("advanced")
    ? $$('[data-key="official_platform_ids"]:checked', list).map((input) => input.value)
    : (state.config.official_platform_ids || []);
  const available = new Map((platforms || [])
    .filter((platform) => typeof platform?.id === "string" && platform.id)
    .map((platform) => [platform.id, platform]));
  const entries = [...available.values()];
  for (const id of selected) {
    if (!available.has(id)) entries.push({ id, name: "当前未连接的官方机器人" });
  }
  list.innerHTML = entries.length
    ? entries.map((platform) => {
      const offline = !available.has(platform.id);
      const label = offline
        ? `${platform.name} · ${platform.id}（当前不可用，仍保留）`
        : `${platform.name || "QQ 官方机器人"} · ${platform.id}`;
      return `<label class="inline-check"><input type="checkbox" data-key="official_platform_ids" value="${escaped(platform.id)}"><span>${escaped(label)}</span></label>`;
    }).join("")
    : '<p class="muted">未发现可选的 QQ 官方机器人实例。</p>';
  $("#official-platforms-note").textContent = note || "只显示 AstrBot 已配置的 QQ 官方实例；不会读取或展示登录凭据。";
  $$('[data-key="official_platform_ids"]', list).forEach((input) => {
    input.checked = selected.includes(input.value);
  });
}

async function saveSection() {
  if (state.saving || !state.dirty.size) return;
  const invalidRuleInput = $$("input", $("#group-rules-editor")).find((input) => !input.checkValidity());
  if (invalidRuleInput) {
    activateTab("push");
    activatePushView("groups");
    invalidRuleInput.reportValidity();
    return;
  }
  groupRuleEditor.validate();
  const sections = [...state.dirty];
  const values = {};
  for (const section of sections) {
    const form = section === "subscriptions" ? null : $(`#${section}-form`);
    if (form && !form.checkValidity()) {
      activateTab(section);
      if (section === "push") {
        const invalid = form.querySelector(":invalid");
        const view = invalid?.closest("#push-view-groups") ? "groups" : "defaults";
        activatePushView(view);
      }
      form.reportValidity();
      return;
    }
    Object.assign(values, Object.fromEntries(Object.entries(formValues(section)).filter(
      ([key, value]) => JSON.stringify(value) !== JSON.stringify(state.config[key]),
    )));
  }
  if (values.daily_push_enabled && !(values.daily_push_times || state.config.daily_push_times)?.length)
    throw new Error("每日推送开启时，请至少填写一个推送时间。");
  if (values.daily_push_times?.some(time => !/^([01]\d|2[0-3]):[0-5]\d$/.test(time)))
    throw new Error("推送时间请使用 HH:MM，例如 08:00。");
  const merged = { ...state.config, ...values };
  if (merged.test_mode_enabled && !/^[1-9][0-9]*$/.test(merged.test_target_id))
    throw new Error("启用测试模式时，请填写纯数字 QQ 号或群号。");
  state.saving = true;
  const inputs = $$("input,select,textarea,button", $("#app"));
  const disabledStates = inputs.map(input => input.disabled);
  inputs.forEach(input => { input.disabled = true; });
  try {
    await api("/api/config", "PATCH", values);
    Object.assign(state.config, values);
    state.subscriptionsDraft = null;
    state.dirty.clear();
    sections.forEach(fillForm);
    renderSubscriptions();
    syncTeamFollowedState();
    renderStats();
    renderPushSummary();
    try { await loadConfig(); }
    catch { toast("配置已保存，页面刷新失败，可稍后重新打开页面。", true); }
    toast("全部配置已保存并生效。");
  } finally {
    state.saving = false;
    inputs.forEach((input, index) => { input.disabled = disabledStates[index]; });
    updateDirtyIndicators();
  }
}

function renderPushSummary() {
  const cfg = state.config;
  $("#push-target-summary").textContent = `${cfg.push_groups?.length || 0} 个接收群`;
  $("#push-format-summary").textContent = cfg.image_push_enabled ? "图片 + 战队标识" : "文字 + 直播链接";
  $("#push-rule-summary").textContent = `默认赛前 ${cfg.remind_minutes || 10} 分钟 · ${cfg.daily_push_enabled ? "默认日报已开启" : "默认日报已关闭"}`;
  const missing = [];
  if (!state.tokenConfigured) missing.push("尚未配置数据源 Token，请在高级维护中填写");
  if (!cfg.push_groups?.length) missing.push("尚未配置接收群，请在订阅管理中添加");
  $("#push-readiness").hidden = !missing.length;
  $("#push-readiness").textContent = missing.join("；") + (missing.length ? "。" : "");
}

let previewSequence = 0;
async function previewTool(kind, overrides = {}) {
  if (!state.ready) throw new Error("页面正在连接，请稍后重试。");
  if (state.saving || state.dirty.size) throw new Error("请先保存或放弃未保存的修改，再查询预览。");
  const days = overrides.days ?? Number($(kind === "results" ? "#tool-results-days" : "#tool-days").value);
  if ((kind === "schedule" || kind === "results") && (!Number.isInteger(days) || days < 1 || days > (kind === "results" ? 30 : 3)))
    throw new Error("查询天数超出范围：赛程 1～3 天，赛果 1～30 天。");
  const matchId = $("#tool-match-id").value.trim();
  if (kind === "results" && matchId && !/^[1-9][0-9]*$/.test(matchId))
    throw new Error("比赛 ID 应为正整数。");
  activateTab("tools");
  const sequence = ++previewSequence;
  const panel = $("#tool-preview");
  const titles = { schedule: "赛程预览", bracket: "晋级对阵图", results: "最近赛果" };
  $("#tool-preview-title").textContent = `${gameLabel} · ${titles[kind] || "查询预览"}`;
  $("#tool-preview-error").hidden = true;
  $("#tool-preview-image").hidden = true;
  $("#tool-preview-image").removeAttribute("src");
  $("#tool-preview-text").textContent = "正在查询，请稍候…";
  $("#tool-preview-note").textContent = "使用已保存设置查询，结果仅在网页展示。";
  panel.setAttribute("aria-busy", "true");
  try {
    const result = await api("/api/tools/preview", "GET", undefined, {
      kind, days, stage: $("#tool-stage").value, match_id: matchId, group_id: $('#tool-rule-group').value,
    });
    if (sequence !== previewSequence) return;
    $("#tool-preview-text").textContent = result.text || "暂无可展示内容。";
    $("#tool-preview-note").textContent = result.note || "查询完成，结果仅在网页展示。";
    if (result.image) {
      $("#tool-preview-image").src = `data:image/png;base64,${result.image}`;
      $("#tool-preview-image").hidden = false;
    }
  } catch (error) {
    if (sequence !== previewSequence) return;
    $("#tool-preview-error").textContent = error.message || "查询失败，请重试。";
    $("#tool-preview-error").hidden = false;
    $("#tool-preview-text").textContent = "未获取到结果，可重新查询。";
  } finally {
    if (sequence === previewSequence) panel.setAttribute("aria-busy", "false");
  }
}

function renderStats() {
  $("#stat-matches").textContent = state.matches.length;
  $("#stat-teams").textContent = (state.config.followed_teams || []).length;
  $("#stat-groups").textContent = (state.config.push_groups || []).length;
  $("#stat-remind").innerHTML =
    `${escaped(state.config.remind_minutes ?? 10)}<span class="unit">分钟</span>`;
}

const teamName = (match, index) =>
  match[index === 0 ? "_t1" : "_t2"] ||
  match.opponents?.[index]?.opponent?.name ||
  "TBD";

function teamAvatar(team, name = team?.name || "TBD") {
  const candidates = [
    ...state.teams,
    ...state.popularTeams,
    ...state.matches.flatMap((match) =>
      (match.opponents || []).map((item) => item.opponent).filter(Boolean),
    ),
  ];
  const known = candidates.find(
    (item) =>
      item.image_url &&
      ((team?.id && item.id === team.id) || item.name === name),
  );
  const key = String(name).toLowerCase().replace(/[^a-z0-9]/g, "");
  const official = state.teamLogos[key];
  const fallback = String(team?.image_url || known?.image_url || "");
  const url = official || fallback;
  const valid = !!official || /^https?:\/\//i.test(url);
  const initials = String(name).slice(0, 2).toUpperCase();
  return `<span class="team-avatar${valid ? " has-logo" : ""}${official && state.teamLogoDarkNames.has(key) ? " on-dark" : ""}" aria-hidden="true"><span>${escaped(initials)}</span>${valid ? `<img src="${escaped(url)}"${official && /^https?:\/\//i.test(fallback) ? ` data-fallback="${escaped(fallback)}"` : ""} alt="" loading="lazy" referrerpolicy="no-referrer">` : ""}</span>`;
}

function isTeamFollowed(candidate) {
  return (subscriptionValues().followed_teams || []).some((item) =>
    typeof item === "string"
      ? item.toLowerCase() === String(candidate.name || "").toLowerCase()
      : item.id
        ? String(item.id) === String(candidate.id)
        : String(item.name || "").toLowerCase() ===
          String(candidate.name || "").toLowerCase(),
  );
}

function syncTeamFollowedState() {
  state.teams = state.teams.map((team) => ({
    ...team,
    followed: isTeamFollowed(team),
  }));
  state.popularTeams = state.popularTeams.map((team) => ({
    ...team,
    followed: isTeamFollowed(team),
  }));
  renderTeamSearchResults();
  renderPopularTeams();
}

function teamMatchNote(team) {
  if (team.match_kind === "fuzzy" && team.matched_alias)
    return `你可能想找“${escaped(team.matched_alias)}”`;
  if (team.matched_alias)
    return `通过“${escaped(team.matched_alias)}”找到`;
  const explanations = {
    exact: "名称完全匹配",
    alias: "常用名称匹配",
    fuzzy: "名称相近",
    prefix: "名称开头匹配",
    contains: "名称相关匹配",
  };
  const kind = String(team.match_kind || "").toLowerCase();
  return explanations[kind] || "相关战队";
}

function renderTeamSearchResults() {
  const container = $("#team-search-results");
  if (container.hidden || !state.searchResultsReady) return;
  container.innerHTML = `<div class="search-results"><div class="search-caption">搜索结果 · ${state.teams.length} 支战队</div>${state.teams.length ? state.teams.map((team, index) => `<div class="item-row"><div class="item-name">${teamAvatar(team)}<div><strong>${escaped(team.name)}${team.is_main ? '<span class="team-main-badge">主队</span>' : ""}</strong><small>${teamMatchNote(team)}</small></div></div><button class="button ${team.followed ? "ghost" : "primary"}" type="button" data-team-follow="${index}" data-team-source="search" ${team.followed ? "disabled" : ""}>${team.followed ? "已关注" : "关注"}</button></div>`).join("") : '<div class="empty compact">没有找到战队，试试其他名称。</div>'}</div>`;
}

function renderPopularTeams() {
  const container = $("#popular-teams");
  if (!container) return;
  if (state.popularLoading) {
    container.innerHTML = '<div class="empty compact">正在加载常看战队…</div>';
    return;
  }
  if (!state.popularLoaded) {
    container.innerHTML = state.popularAttempted
      ? '<div class="empty compact">常看战队暂时无法加载。<button class="button ghost" type="button" data-popular-retry>重试</button></div>'
      : '<div class="empty compact">打开订阅管理后加载常看战队…</div>';
    return;
  }
  container.innerHTML = state.popularTeams.length
    ? state.popularTeams
        .map(
          (team, index) => {
            const displayName = team.display_name || team.name;
            return `<div class="popular-tile"><div class="popular-team">${teamAvatar(team, displayName)}<strong title="${escaped(displayName)}">${escaped(displayName)}</strong></div><button class="button ${team.followed ? "ghost" : "primary"}" type="button" data-team-follow="${index}" data-team-source="popular" ${team.followed ? "disabled" : ""}>${team.followed ? "已关注" : "关注"}</button></div>`;
          },
        )
        .join("")
    : '<div class="empty compact">暂时没有可选战队。</div>';
  $("#popular-count").textContent = `${state.popularTeams.length} 支`;
}

async function loadPopularTeams() {
  if (state.popularLoading || state.popularLoaded) return;
  state.popularAttempted = true;
  state.popularLoading = true;
  renderPopularTeams();
  try {
    const result = await api("/api/teams/popular");
    state.popularTeams = result.teams || [];
    state.popularLoaded = true;
    syncTeamFollowedState();
  } catch {
    state.popularLoaded = false;
    renderPopularTeams();
  } finally {
    state.popularLoading = false;
    renderPopularTeams();
  }
}

function matchTeam(match, index) {
  const team = match.opponents?.[index]?.opponent || {};
  const name = teamName(match, index);
  return `<span class="match-team">${teamAvatar(team, name)}<span>${escaped(name)}</span></span>`;
}
function isFollowed(match) {
  const ids = match.opponents?.map((opponent) => opponent.opponent?.id) || [];
  const names = [teamName(match, 0), teamName(match, 1)].map((name) =>
    name.toLowerCase(),
  );
  return (state.config.followed_teams || []).some((team) =>
    typeof team === "string"
      ? names.some((name) => name.includes(team.toLowerCase()))
      : team.id
        ? ids.includes(team.id)
        : names.includes(String(team.name || "").toLowerCase()),
  );
}

function matchCategory(match) {
  if (match._task_status === "finished" || match.status === "finished")
    return "finished";
  if (
    ["polling_result", "waiting_result"].includes(match._task_status) ||
    match.status === "running"
  )
    return "live";
  return "upcoming";
}

function dateText(iso) {
  const date = new Date(iso);
  if (!iso || Number.isNaN(date.getTime())) return ["时间待定", ""];
  return [
    new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai",
      month: "2-digit",
      day: "2-digit",
      weekday: "short",
    }).format(date),
    new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(date),
  ];
}

function renderMatches() {
  const query = $("#match-search").value.toLowerCase().trim();
  const category = $("#match-status").value;
  const matches = state.matches.filter((match) => {
    const text =
      `${teamName(match, 0)} ${teamName(match, 1)} ${match.league?.name || ""} ${match.tournament?.name || ""}`.toLowerCase();
    return (
      (!query || text.includes(query)) &&
      (category === "all" || matchCategory(match) === category) &&
      (!$("#only-followed").checked || isFollowed(match))
    );
  });
  $("#match-count").textContent =
    `${matches.length} 场比赛${matches.length === state.matches.length ? "" : ` / 共 ${state.matches.length} 场`}`;
  if (!matches.length) {
    $("#match-list").innerHTML =
      `<div class="empty"><strong>${state.matches.length ? "没有匹配的比赛" : "暂无比赛安排"}</strong><p>${state.matches.length ? "试试其他关键词或筛选条件。" : state.tokenConfigured ? "可获取最新赛程，或调整订阅中的赛事筛选。" : "配置数据源后，在这里查看比赛安排。"}</p></div>`;
    return;
  }
  $("#match-list").innerHTML = matches
    .map((match) => {
      const mid = Number(match.id);
      const [date, time] = dateText(match.scheduled_at || match.begin_at);
      const kind = matchCategory(match);
      const status =
        kind === "finished"
          ? "已完成"
          : kind === "live"
            ? "等待赛果"
            : "待开赛";
      const tier = String(match.tournament?.tier || "unranked").toUpperCase();
      const custom = match._custom_remind != null;
      const minutes = Number(
        match._remind_min ?? state.config.remind_minutes ?? 10,
      );
      const reminders = match._group_reminders;
      const detail = reminders?.map(item => `群 ${item.group_id}：提前 ${item.minutes} 分钟${item.sent ? '（已提醒）' : ''}`).join('；') || '';
      const distinct = [...new Set((reminders || []).map(item => item.minutes))];
      const reminderLabel = custom ? `提前 ${minutes} 分钟 · 单场覆盖` : reminders == null ? `默认提前 ${minutes} 分钟` : !reminders.length ? '未开启赛前提醒' : distinct.length === 1 ? `${reminders.length} 群提前 ${distinct[0]} 分钟` : `按群提醒 · ${Math.min(...distinct)}～${Math.max(...distinct)} 分钟`;
      return `<article class="match-row"><div class="match-time"><strong>${escaped(date)}</strong><small>${escaped(time)}${time ? " 北京时间" : ""}</small></div><div class="match-body"><div class="match-title">${matchTeam(match, 0)}<span class="versus">vs</span>${matchTeam(match, 1)}${isFollowed(match) ? '<span class="follow-indicator">已关注</span>' : ""}</div><div class="match-subtitle"><span>${escaped(match.league?.name || "未知赛事")}</span><span>·</span><span>${escaped(tier === "UNRANKED" ? "未定级" : `${tier} 级`)}</span><span>·</span><span>BO${escaped(match.number_of_games || 1)}</span>${kind === "upcoming" ? `<span>·</span><span title="${escaped(detail)}">${escaped(reminderLabel)}</span>` : ""}</div>${match._task_status === "polling_result" ? `<div class="match-note">正在查询赛果${match._attempt != null ? ` · 已查询 ${escaped(match._attempt)} 次` : ""}</div>` : ""}${[teamName(match, 0), teamName(match, 1)].includes("TBD") ? '<div class="match-note">参赛队伍待确认，确认后自动调整提醒。</div>' : ""}</div><div class="match-side"><span class="badge ${kind === "finished" ? "green" : kind === "live" ? "blue" : ""}">${status}</span><details class="match-actions"><summary aria-label="比赛操作">⋯</summary><div>${kind === "upcoming" ? `<button type="button" data-match-edit="${mid}">调整提醒时间</button>` : ""}<button type="button" data-match-rebuild="${mid}">重建比赛任务</button></div></details></div></article>`;
    })
    .join("");
}

async function loadMatches({ quiet = false } = {}) {
  try {
    const result = await api("/api/matches");
    state.matches = result.matches || [];
    renderStats();
    renderMatches();
    $("#matches-updated").textContent =
      `北京时间 · 页面更新于 ${new Date().toLocaleTimeString("zh-CN", { hour12: false })}`;
    $("#connection-dot").className = "status-dot connected";
    $("#connection-label").textContent = "已连接";
  } catch (error) {
    $("#connection-dot").className = "status-dot failed";
    $("#connection-label").textContent = "连接异常";
    if (!quiet) {
      $("#match-list").innerHTML =
        '<div class="empty"><strong>赛程加载失败</strong><p>请检查连接后重试获取赛程。</p></div>';
      throw error;
    }
  }
}

function customizedGroupCount() {
  const draft = subscriptionValues();
  const rules = draft.group_rules || {};
  return (draft.push_groups || []).filter(
    (gid) => Object.keys(rules[gid] || {}).length > 0,
  ).length;
}

function renderPushGroupsCoverage() {
  const total = (subscriptionValues().push_groups || []).length;
  const customized = customizedGroupCount();
  const countBadge = $("#push-view-groups-count");
  countBadge.textContent = customized;
  countBadge.hidden = customized === 0;
  $("#push-defaults-override-notice").hidden = customized === 0;
  if (customized > 0)
    $("#push-defaults-override-text").textContent =
      `${customized} 个群有独立规则，它们改过的字段不受这里影响。`;
  $("#push-groups-coverage-notice").textContent = customized === 0
    ? "所有群都在沿用默认规则。选择一个群开始覆盖。"
    : `已自定义 ${customized} / ${total} 个群`;
}

function renderSubscriptions() {
  const groups = subscriptionValues().push_groups || [];
  const teams = subscriptionValues().followed_teams || [];
  $("#group-count").textContent = groups.length;
  $("#team-count").textContent = teams.length;
  $("#group-list").innerHTML = groups.length
    ? groups
        .map(
          (gid) =>
            `<div class="item-row"><div class="item-name"><span class="item-avatar" aria-hidden="true">群</span><div><strong>${escaped(gid)}</strong><small>${escaped(groupRuleEditor?.summary(gid) || '继承默认规则')}</small></div></div><button type="button" class="button" data-group-edit="${escaped(gid)}">编辑规则</button><button type="button" class="button ghost" data-group-remove="${escaped(gid)}" aria-label="移除群组 ${escaped(gid)}">移除</button></div>`,
        )
        .join("")
    : '<div class="empty compact">还没有推送群组，添加后接收比赛消息。</div>';
  $("#team-list").innerHTML = teams.length
    ? teams
        .map((team, index) => {
          const name = typeof team === "string" ? team : team.name;
          return `<div class="item-row"><div class="item-name">${teamAvatar(typeof team === "object" ? team : {}, name)}<div><strong>${escaped(name)}</strong><small>${typeof team === "object" && team.id ? `ID ${escaped(team.id)}` : "按名称关注"}</small></div></div><button type="button" class="button ghost" data-team-remove="${index}" aria-label="取消关注 ${escaped(name)}">取消关注</button></div>`;
        })
        .join("")
    : '<div class="empty compact">还没有关注战队，搜索战队开始关注。</div>';
  groupRuleEditor?.render();
  renderPushGroupsCoverage();
  renderMatches();
}

function renderStreams(streams) {
  $("#stream-list").innerHTML = streams
    .map(
      (stream) =>
        `<div class="stream-row"><input class="stream-name" placeholder="直播名称" aria-label="直播名称" value="${escaped(stream.name)}"><input class="stream-url" type="url" placeholder="https://…" aria-label="直播链接" value="${escaped(stream.url)}" pattern="https?://.+" required><button type="button" class="button ghost" data-stream-remove aria-label="移除直播链接">×</button></div>`,
    )
    .join("");
}

async function loadLogs() {
  const result = await api("/api/logs");
  $("#logs").innerHTML = result.logs?.length
    ? result.logs
        .slice()
        .reverse()
        .map(
          (log) =>
            `<div class="log-row" data-level="${escaped(log.level)}"><time>${escaped(log.time)}</time><span class="log-level">${escaped(log.level)}</span><span>${escaped(log.msg)}</span></div>`,
        )
        .join("")
    : '<div class="empty compact">暂无运行日志。</div>';
}

function activateTab(name, { focus = false } = {}) {
  state.active = name;
  $$("[data-tab]").forEach((button) => {
    const active = button.dataset.tab === name;
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
    if (active && focus) button.focus();
  });
  $$('.app > [role="tabpanel"]').forEach((section) => {
    section.hidden = section.id !== name;
  });
  if (name === "advanced" && state.ready) run($("#refresh-logs"), loadLogs);
  if (name === "subscriptions" && state.ready) loadPopularTeams();
}

function activatePushView(view, { focus = false } = {}) {
  if (view !== "defaults" && view !== "groups") return;
  $$("[data-push-view]").forEach((button) => {
    const active = button.dataset.pushView === view;
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
    if (active && focus) button.focus();
  });
  $("#push-view-defaults").hidden = view !== "defaults";
  $("#push-view-groups").hidden = view !== "groups";
  writeSession("cs2.pushView", view);
}

function installEvents() {
  $$("[data-preview]").forEach(button => button.addEventListener("click", () => run(button, () => previewTool(button.dataset.preview))));
  $("#preview-daily").addEventListener("click", () => {
    activateTab("tools");
    $("#tool-days").value = Number(state.config.daily_push_days || 1);
    $("#tool-days").focus();
  });
  $("#save-all").addEventListener("click", event => run(event.currentTarget, saveSection));
  $("#discard-all").addEventListener("click", () => {
    const sections = [...state.dirty];
    state.subscriptionsDraft = null;
    state.dirty.clear();
    sections.forEach(fillForm);
    renderSubscriptions();
    syncTeamFollowedState();
    updateDirtyIndicators();
  });
  document.addEventListener(
    "error",
    (event) => {
      if (event.target.matches?.(".team-avatar img")) {
        if (event.target.dataset.fallback) {
          const fallback = event.target.dataset.fallback;
          delete event.target.dataset.fallback;
          event.target.closest(".team-avatar").classList.remove("on-dark");
          event.target.src = fallback;
          return;
        }
        event.target.closest(".team-avatar").classList.remove("has-logo", "on-dark");
        event.target.remove();
      }
    },
    true,
  );
  $$("[data-tab]").forEach((button) =>
    button.addEventListener("click", () => activateTab(button.dataset.tab)),
  );
  $(".tabs").addEventListener("keydown", (event) => {
    const tabs = $$("[data-tab]");
    const index = tabs.findIndex((tab) => tab === document.activeElement);
    if (
      index < 0 ||
      !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)
    )
      return;
    event.preventDefault();
    const next =
      event.key === "Home"
        ? 0
        : event.key === "End"
          ? tabs.length - 1
          : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) %
            tabs.length;
    activateTab(tabs[next].dataset.tab, { focus: true });
  });
  $$("[data-push-view]").forEach((button) =>
    button.addEventListener("click", () => activatePushView(button.dataset.pushView)),
  );
  $(".push-view-switch").addEventListener("keydown", (event) => {
    const views = $$("[data-push-view]");
    const index = views.findIndex((view) => view === document.activeElement);
    if (
      index < 0 ||
      !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)
    )
      return;
    event.preventDefault();
    const next =
      event.key === "Home"
        ? 0
        : event.key === "End"
          ? views.length - 1
          : (index + (event.key === "ArrowRight" ? 1 : -1) + views.length) %
            views.length;
    activatePushView(views[next].dataset.pushView, { focus: true });
  });
  $$("form[data-section]").forEach((form) => {
    form.addEventListener("input", () => checkDirty(form.dataset.section));
    form.addEventListener("change", () => checkDirty(form.dataset.section));
    form.addEventListener("invalid", (event) => {
      let parent = event.target.parentElement;
      while (parent && parent !== form) {
        if (parent.tagName === "DETAILS") parent.open = true;
        parent = parent.parentElement;
      }
    }, true);
    form.addEventListener("submit", (event) => event.preventDefault());
  });
  $$("[data-reveal]").forEach((button) =>
    button.addEventListener("click", () => {
      const input = $(`[data-key="${button.dataset.reveal}"]`);
      const reveal = input.type === "password";
      input.type = reveal ? "text" : "password";
      button.textContent = reveal ? "隐藏" : "显示";
      button.setAttribute("aria-pressed", String(reveal));
    }),
  );
  ["match-search", "match-status", "only-followed"].forEach((id) =>
    $(`#${id}`).addEventListener("input", renderMatches),
  );
  $("#refresh-matches").addEventListener("click", (event) =>
    run(event.currentTarget, async () => {
      await api("/api/refresh", "POST", {});
      toast("已开始获取赛程，页面将自动更新。");
      clearTimeout(refreshTimer);
      refreshTimer = setTimeout(() => loadMatches({ quiet: true }), 3000);
    }),
  );
  $("#group-form").addEventListener("submit", (event) => {
    event.preventDefault();
    run($("button", event.currentTarget), async () => {
      const gid = $("#group-id").value.trim();
      if (!/^[1-9][0-9]*$/.test(gid))
        throw new Error("请输入纯数字 QQ 群号。");
      editSubscriptions(draft => {
        if (!draft.push_groups.includes(gid)) draft.push_groups.push(gid);
      });
      $("#group-id").value = "";
    });
  });
  $("#team-search-form").addEventListener("submit", (event) => {
    event.preventDefault();
    run($("button", event.currentTarget), async () => {
      const q = $("#team-query").value.trim();
      if (!q) throw new Error("请输入战队名称或简称。");
      const sequence = ++searchSequence;
      state.searchResultsReady = false;
      const container = $("#team-search-results");
      container.hidden = false;
      container.innerHTML = '<div class="empty compact">正在搜索战队…</div>';
      try {
        const result = await api("/api/teams/search", "GET", undefined, { q });
        if (sequence !== searchSequence) return;
        state.teams = result.teams || [];
        state.searchResultsReady = true;
        syncTeamFollowedState();
      } catch (error) {
        if (sequence !== searchSequence) return;
        container.innerHTML =
          '<div class="empty compact">搜索失败，请检查 Token 或稍后重试。</div>';
        throw error;
      }
    });
  });
  $("#team-query").addEventListener("input", () => {
    searchSequence += 1;
    state.searchResultsReady = false;
    const results = $("#team-search-results");
    if (!results.hidden)
      results.innerHTML = '<div class="empty compact">关键词已更改，点击搜索查看结果。</div>';
  });
  $("#popular-teams").addEventListener("click", (event) => {
    if (event.target.closest("[data-popular-retry]")) loadPopularTeams();
  });
  document.addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    if (button.hasAttribute("data-navigate")) activateTab(button.dataset.navigate, { focus: true });
    if (button.hasAttribute("data-push-view-goto")) activatePushView(button.dataset.pushViewGoto);
    if (button.hasAttribute("data-group-edit")) {
      activateTab("push");
      activatePushView("groups");
      groupRuleEditor.select(button.dataset.groupEdit);
    }
    if (button.hasAttribute("data-group-remove"))
      editSubscriptions(draft => {
        draft.push_groups = draft.push_groups.filter(gid => String(gid) !== button.dataset.groupRemove);
        delete draft.group_rules[button.dataset.groupRemove];
      });
    if (button.hasAttribute("data-team-remove"))
      editSubscriptions(draft => { draft.followed_teams.splice(Number(button.dataset.teamRemove), 1); });
    if (button.hasAttribute("data-team-follow")) {
      const teams = button.dataset.teamSource === "popular" ? state.popularTeams : state.teams;
      const team = teams[Number(button.dataset.teamFollow)];
      if (!team?.id) return toast("无法确认这支战队的资料，请重新搜索后再试。", true);
      if (!isTeamFollowed(team)) editSubscriptions(draft => {
        draft.followed_teams.push({ id: team.id, name: team.name, slug: team.slug || "", image_url: team.image_url || "" });
      });
    }
    if (button.hasAttribute("data-stream-remove")) {
      button.closest(".stream-row").remove();
      checkDirty("push");
    }
    if (button.hasAttribute("data-match-edit"))
      run(button, () => editMatch(Number(button.dataset.matchEdit)));
    if (button.hasAttribute("data-match-rebuild"))
      run(button, async () => {
        button.closest("details").open = false;
        if (
          !(await dialog(
            "重建比赛任务",
            "重新安排这场比赛的提醒，并停止当前赛果轮询。",
            { confirm: "重建任务" },
          ))
        )
          return;
        await api(
          `/api/matches/${button.dataset.matchRebuild}/rebuild`,
          "POST",
          {},
        );
        await loadMatches();
        toast("比赛任务已重建。");
      });
  });
  document.addEventListener("click", (event) => {
    $$(".match-actions[open]").forEach((menu) => {
      if (!menu.contains(event.target)) menu.open = false;
    });
  });
  $("#add-stream").addEventListener("click", () => {
    const streams = formValues("push").custom_streams;
    renderStreams([...streams, { name: "", url: "" }]);
    checkDirty("push");
    $(".stream-row:last-child .stream-name").focus();
  });
  $("#push-now").addEventListener("click", (event) =>
    run(event.currentTarget, async () => {
      if (state.dirty.size || state.saving)
        throw new Error("请先保存全部修改，再立即推送。");
      if (!state.config.push_groups?.length)
        throw new Error("请先在订阅管理中添加推送群组。");
      const gid = $('#push-rule-group').value;
      const days = Number($('#push-now-days').value);
      if (!Number.isInteger(days) || days < 1 || days > 3) throw new Error('推送天数应为 1～3 的整数。');
      if (
        !(await dialog(
          "立即推送赛程",
          `将未来 ${days} 天的赛程发送到${gid ? '群 ' + gid : '全部启用的接收群'}，各群按本群规则筛选。`,
          { confirm: "确认推送" },
        ))
      )
        return;
      const result = await api("/api/push_now", "POST", { days, group_id: gid });
      toast(
        result.skipped
          ? "当前没有符合条件的比赛，无需推送。"
          : "赛程推送已安排。",
      );
    }),
  );
  $("#send-test").addEventListener("click", (event) =>
    run(event.currentTarget, async () => {
      if (state.dirty.size || state.saving)
        throw new Error("请先保存全部修改，再发送测试消息。");
      if (!state.config.test_mode_enabled || !state.config.test_target_id)
        throw new Error("请启用测试模式并保存目标号码。");
      const action = $("#test-action").value;
      const days = ["日报", "全部"].includes(action) ? Number($("#test-days").value) : 1;
      if (!Number.isInteger(days) || days < 1 || days > 3) throw new Error("测试日报天数应为 1～3 的整数。");
      if (
        !(await dialog(
          "发送测试消息",
          `向${state.config.test_target_type === "group" ? "群" : "QQ"} ${state.config.test_target_id} 发送「${action}」测试消息。`,
          { confirm: "发送测试" },
        ))
      )
        return;
      const result = await api("/api/test", "POST", { action, days, group_id: $("#test-rule-group").value });
      toast(result.msg || "测试消息已发送。");
      await loadLogs();
    }),
  );
  $("#refresh-logs").addEventListener("click", (event) =>
    run(event.currentTarget, loadLogs),
  );
  $("#export-config").addEventListener("click", (event) =>
    run(event.currentTarget, exportConfig),
  );
  $("#import-config").addEventListener("click", () =>
    $("#import-file").click(),
  );
  $("#import-file").addEventListener("change", (event) =>
    run($("#import-config"), () => importConfig(event.target)),
  );
  $("#rebuild-tasks").addEventListener("click", (event) =>
    run(event.currentTarget, async () => {
      if (
        !(await dialog(
          "重建全部比赛任务",
          "使用当前已保存的设置重新安排全部比赛任务。已发送记录会保留。",
          { confirm: "重建任务" },
        ))
      )
        return;
      await api("/api/tasks/rebuild", "POST", {});
      await loadMatches();
      toast("比赛任务已重建。");
    }),
  );
  $("#clear-notified").addEventListener("click", (event) =>
    run(event.currentTarget, async () => {
      if (
        !(await dialog(
          "重置通知记录",
          "这会清除比赛通知的去重状态，已经发送过的比赛可能再次推送。",
          { confirm: "确认重置", danger: true },
        ))
      )
        return;
      await api("/api/notified/clear", "POST", {});
      await loadMatches();
      toast("通知记录已重置。");
    }),
  );
  window.addEventListener("beforeunload", (event) => {
    if (state.dirty.size) {
      event.preventDefault();
      event.returnValue = "";
    }
  });
}

async function editMatch(mid) {
  const match = state.matches.find((item) => Number(item.id) === mid);
  if (!match) return;
  $$(".match-actions[open]").forEach((menu) => {
    menu.open = false;
  });
  const minutes = Number(
    match._remind_min ?? state.config.remind_minutes ?? 10,
  );
  const result = await dialog(
    "调整赛前提醒",
    `${teamName(match, 0)} vs ${teamName(match, 1)}\n仅调整这一场比赛。`,
    {
      confirm: "保存提醒",
      fields: `<label class="field">提前提醒（分钟）<input type="number" name="minutes" value="${minutes}" min="1" max="120" required></label><label class="inline-check" style="margin-top:14px"><input type="checkbox" name="useGlobal">恢复各群的提醒设置（默认 ${escaped(state.config.remind_minutes)} 分钟）</label>`,
    },
  );
  if (!result) return;
  await api(`/api/matches/${mid}`, "PATCH", {
    remind_minutes: result.useGlobal ? null : Number(result.minutes),
  });
  await loadMatches();
  toast("比赛提醒已更新。");
}

async function exportConfig() {
  if (bridge) {
    await bridge.download("export", {}, "cs2-config.json");
  } else {
    const result = await api("/api/config/export");
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(result.data, null, 2)], {
        type: "application/json",
      }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = "cs2-config.json";
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  toast("配置已导出。");
}

async function importConfig(input) {
  const file = input.files?.[0];
  input.value = "";
  if (!file) return;
  if (file.size > 1024 * 1024)
    throw new Error("配置文件过大，请选择 1 MB 以内的 JSON 文件。");
  let data;
  try {
    data = JSON.parse(await file.text());
  } catch {
    throw new Error("文件不是有效的 JSON 配置。");
  }
  if (data?.ok === true && data.data) data = data.data;
  if (!data || typeof data !== "object" || Array.isArray(data))
    throw new Error("配置文件的内容应为 JSON 对象。");
  if (
    !(await dialog(
      "导入配置",
      `导入 ${file.name} 的配置将覆盖文件中包含的设置。${state.dirty.size ? "\n页面中未保存的修改将被放弃。" : ""}`,
      { confirm: "确认导入" },
    ))
  )
    return;
  await api("/api/config/import", "POST", data);
  state.dirty.clear();
  await loadConfig();
  await loadMatches();
  toast("配置已导入并生效。");
}

// 主题：手动选择优先并记住；否则内嵌时跟随 AstrBot，独立访问默认深色。
const THEME_KEY = "cs_panel_theme";
function storedTheme() {
  try {
    const value = localStorage.getItem(THEME_KEY);
    return value === "dark" || value === "light" ? value : "";
  } catch {
    return "";
  }
}
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const next = theme === "dark" ? "浅色" : "深色";
  const toggle = $("#theme-toggle");
  if (!toggle) return;
  toggle.setAttribute("aria-label", `切换为${next}主题`);
  toggle.title = `切换为${next}主题`;
}
function installThemeToggle() {
  applyTheme(storedTheme() || "dark");
  $("#theme-toggle")?.addEventListener("click", () => {
    const theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    applyTheme(theme);
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch {
      /* 存储不可用时仅本次页面生效。 */
    }
  });
}

async function initialize() {
  document.title = `${gameLabel} 比赛推送`;
  $(".identity h1").textContent = `${gameLabel} 比赛推送`;
  installThemeToggle();
  installEvents();
  setupGuide = createSetupGuide({ api, getState: () => state, loadConfig, activateTab, toast });
  try {
    if (bridge) {
      const context = await bridge.ready();
      const applyContext = (value) => {
        const next = value || bridge.getContext?.();
        if (!storedTheme() && next && typeof next.isDark === "boolean")
          applyTheme(next.isDark ? "dark" : "light");
        document.title = `${gameLabel} 比赛推送`;
      };
      applyContext(context);
      const unsubscribe = bridge.onContext(applyContext);
      window.addEventListener("pagehide", unsubscribe, { once: true });
      $("#host-mode").textContent = "AstrBot 内嵌管理视图";
    } else {
      try {
        sessionToken =
          new URLSearchParams(location.search).get("token") ||
          readSession("cs_panel_token") ||
          "";
        if (sessionToken)
          writeSession("cs_panel_token", sessionToken);
      } catch {
        /* HttpOnly session cookies remain usable if browser storage is unavailable. */
      }
      if (new URLSearchParams(location.search).has("token"))
        { const u = new URL(location.href); u.searchParams.delete("token"); history.replaceState(null, "", u.pathname + u.search); }
      $("#host-mode").textContent = "独立 Web 管理面板";
    }
    await loadConfig();
    state.ready = true;
    setupGuide.startIfNeeded();
    if (state.active === "subscriptions") loadPopularTeams();
    await loadMatches();
    $("#app").setAttribute("aria-busy", "false");
    pollTimer = setInterval(() => {
      if (!document.hidden && state.ready) {
        loadMatches({ quiet: true });
        if (state.active === "advanced") loadLogs().catch(() => {});
      }
    }, 30000);
  } catch (error) {
    $("#page-error").textContent =
      `${error.message || "页面初始化失败"} 请刷新页面重试。`;
    $("#page-error").hidden = false;
    $("#connection-dot").className = "status-dot failed";
    $("#connection-label").textContent = "连接失败";
    $("#app").setAttribute("aria-busy", "false");
  }
  window.addEventListener(
    "pagehide",
    () => {
      clearInterval(pollTimer);
      clearTimeout(refreshTimer);
    },
    { once: true },
  );
}

groupRuleEditor = createGroupRuleEditor({
  getConfig: () => state.config, getDraft: subscriptionValues,
  getDefaults: () => state.ready ? { ...state.config, ...formValues('subscriptions'), ...formValues('push') } : state.config,
    edit: editSubscriptions, api, run, dialog, escape: escaped, toast,
});
initialize();
