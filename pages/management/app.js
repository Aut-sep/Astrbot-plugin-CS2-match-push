const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const bridge = window.AstrBotPluginView || window.AstrBotPluginPage;
const state = {
  config: {},
  matches: [],
  teams: [],
  ready: false,
  active: "overview",
  dirty: new Set(),
  pending: new Set(),
  tokenConfigured: false,
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

function updateDirtyIndicators() {
  $$("form[data-section]").forEach((form) => {
    const section = form.dataset.section;
    const dirty = state.dirty.has(section);
    $(".save-bar", form).classList.toggle("dirty", dirty);
    $(".save-state", form).textContent = dirty
      ? "有未保存的修改"
      : "设置已保存";
    $('.save-bar button[type="submit"]', form).disabled =
      !dirty || state.pending.has($('.save-bar button[type="submit"]', form));
    $("[data-discard]", form).disabled = !dirty;
    $(`#tab-${section} .tab-dirty`).hidden = !dirty;
  });
}

function formValues(section) {
  const form = $(`#${section}-form`);
  const values = {};
  const tiers = $$('[data-key="min_tiers"]:checked', form).map(
    (input) => input.value,
  );
  $$("[data-key]", form).forEach((input) => {
    const key = input.dataset.key;
    if (key === "min_tiers") {
      values[key] = tiers;
      return;
    }
    if (secretKeys.has(key)) {
      if ($(`[data-clear="${key}"]`, form)?.checked) values[key] = "";
      else if (input.value.trim()) values[key] = input.value.trim();
      return;
    }
    if (input.type === "checkbox") values[key] = input.checked;
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
  const form = $(`#${section}-form`);
  $$("[data-key]", form).forEach((input) => {
    const key = input.dataset.key;
    const value = state.config[key];
    if (secretKeys.has(key)) {
      input.value = "";
      input.type = "password";
      return;
    }
    if (key === "min_tiers")
      input.checked = (value || []).includes(input.value);
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
  renderStats();
  updateDirtyIndicators();
}

async function saveSection(section, button) {
  const form = $(`#${section}-form`);
  if (!form.reportValidity()) return;
  const values = formValues(section);
  if (values.daily_push_enabled && !values.daily_push_times.length)
    throw new Error("每日推送开启时，请至少填写一个推送时间。");
  if (
    values.daily_push_times?.some(
      (time) => !/^([01]\d|2[0-3]):[0-5]\d$/.test(time),
    )
  )
    throw new Error("推送时间请使用 HH:MM，例如 08:00。");
  if (values.test_mode_enabled && !/^\d+$/.test(values.test_target_id))
    throw new Error("启用测试模式时，请填写有效的目标号码。");
  const inputs = $$("input,select,textarea,button", form);
  const disabledStates = inputs.map((input) => input.disabled);
  inputs.forEach((input) => {
    input.disabled = true;
  });
  try {
    await api("/api/config", "PATCH", values);
    state.dirty.delete(section);
    await loadConfig();
    toast("设置已保存并生效。");
  } finally {
    inputs.forEach((input, index) => {
      input.disabled = disabledStates[index];
    });
    updateDirtyIndicators();
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
    ...state.matches.flatMap((match) =>
      (match.opponents || []).map((item) => item.opponent).filter(Boolean),
    ),
  ];
  const known = candidates.find(
    (item) =>
      item.image_url &&
      ((team?.id && item.id === team.id) || item.name === name),
  );
  const url = String(team?.image_url || known?.image_url || "");
  const valid = /^https?:\/\//i.test(url);
  const initials = String(name).slice(0, 2).toUpperCase();
  return `<span class="team-avatar${valid ? " has-logo" : ""}" aria-hidden="true"><span>${escaped(initials)}</span>${valid ? `<img src="${escaped(url)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : ""}</span>`;
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
      return `<article class="match-row"><div class="match-time"><strong>${escaped(date)}</strong><small>${escaped(time)}${time ? " 北京时间" : ""}</small></div><div class="match-body"><div class="match-title">${matchTeam(match, 0)}<span class="versus">vs</span>${matchTeam(match, 1)}${isFollowed(match) ? '<span class="follow-indicator">已关注</span>' : ""}</div><div class="match-subtitle"><span>${escaped(match.league?.name || "未知赛事")}</span><span>·</span><span>${escaped(tier === "UNRANKED" ? "未定级" : `${tier} 级`)}</span><span>·</span><span>BO${escaped(match.number_of_games || 1)}</span>${kind === "upcoming" ? `<span>·</span><span>提前 ${minutes} 分钟${custom ? " · 自定义" : ""}</span>` : ""}</div>${match._task_status === "polling_result" ? `<div class="match-note">正在查询赛果${match._attempt != null ? ` · 已查询 ${escaped(match._attempt)} 次` : ""}</div>` : ""}${[teamName(match, 0), teamName(match, 1)].includes("TBD") ? '<div class="match-note">参赛队伍待确认，确认后自动调整提醒。</div>' : ""}</div><div class="match-side"><span class="badge ${kind === "finished" ? "green" : kind === "live" ? "blue" : ""}">${status}</span><details class="match-actions"><summary aria-label="比赛操作">⋯</summary><div>${kind === "upcoming" ? `<button type="button" data-match-edit="${mid}">调整提醒时间</button>` : ""}<button type="button" data-match-rebuild="${mid}">重建比赛任务</button></div></details></div></article>`;
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

function renderSubscriptions() {
  const groups = state.config.push_groups || [];
  const teams = state.config.followed_teams || [];
  $("#group-count").textContent = groups.length;
  $("#team-count").textContent = teams.length;
  $("#group-list").innerHTML = groups.length
    ? groups
        .map(
          (gid) =>
            `<div class="item-row"><div class="item-name"><span class="item-avatar" aria-hidden="true">群</span><strong>${escaped(gid)}</strong></div><button type="button" class="button ghost" data-group-remove="${escaped(gid)}" aria-label="移除群组 ${escaped(gid)}">移除</button></div>`,
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
  $$('[role="tabpanel"]').forEach((section) => {
    section.hidden = section.id !== name;
  });
  if (name === "advanced" && state.ready) run($("#refresh-logs"), loadLogs);
}

function installEvents() {
  document.addEventListener(
    "error",
    (event) => {
      if (event.target.matches?.(".team-avatar img")) {
        event.target.closest(".team-avatar").classList.remove("has-logo");
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
  $$("form[data-section]").forEach((form) => {
    form.addEventListener("input", () => checkDirty(form.dataset.section));
    form.addEventListener("change", () => checkDirty(form.dataset.section));
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const button = $('[type="submit"]', form);
      run(button, () => saveSection(form.dataset.section, button));
    });
  });
  $$("[data-discard]").forEach((button) =>
    button.addEventListener("click", () => {
      const section = button.dataset.discard;
      state.dirty.delete(section);
      fillForm(section);
      updateDirtyIndicators();
    }),
  );
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
      if (!/^\d+$/.test(gid) || BigInt(gid) < 1n)
        throw new Error("请输入有效的 QQ 群号。");
      const result = await api("/api/groups", "POST", { gid });
      $("#group-id").value = "";
      await loadConfig();
      toast(result.added ? "群组已添加。" : "该群组已在推送列表中。");
    });
  });
  $("#team-search-form").addEventListener("submit", (event) => {
    event.preventDefault();
    run($("button", event.currentTarget), async () => {
      const q = $("#team-query").value.trim();
      if (q.length < 2) throw new Error("请至少输入两个字符。");
      const sequence = ++searchSequence;
      const container = $("#team-search-results");
      container.hidden = false;
      container.innerHTML = '<div class="empty compact">正在搜索战队…</div>';
      try {
        const result = await api("/api/teams/search", "GET", undefined, { q });
        if (sequence !== searchSequence) return;
        state.teams = result.teams || [];
        container.innerHTML = `<div class="search-results"><div class="search-caption">搜索结果 · ${state.teams.length} 支战队</div>${state.teams.length ? state.teams.map((team, index) => `<div class="item-row"><div class="item-name">${teamAvatar(team)}<div><strong>${escaped(team.name)}</strong><small>ID ${escaped(team.id)}</small></div></div><button class="button ${team.followed ? "ghost" : "primary"}" type="button" data-team-follow="${index}" ${team.followed ? "disabled" : ""}>${team.followed ? "已关注" : "关注"}</button></div>`).join("") : '<div class="empty compact">没有找到战队，试试其他名称。</div>'}</div>`;
      } catch (error) {
        container.innerHTML =
          '<div class="empty compact">搜索失败，请检查 Token 或稍后重试。</div>';
        throw error;
      }
    });
  });
  document.addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    if (button.hasAttribute("data-group-remove"))
      run(button, async () => {
        if (
          !(await dialog(
            "移除推送群组",
            `确定不再向群 ${button.dataset.groupRemove} 推送比赛消息？`,
            { confirm: "移除" },
          ))
        )
          return;
        await api(
          `/api/groups/${encodeURIComponent(button.dataset.groupRemove)}`,
          "DELETE",
        );
        await loadConfig();
        toast("群组已移除。");
      });
    if (button.hasAttribute("data-team-remove"))
      run(button, async () => {
        const team =
          state.config.followed_teams[Number(button.dataset.teamRemove)];
        const name = typeof team === "string" ? team : team.name;
        if (
          !(await dialog("取消战队关注", `确定取消关注 ${name}？`, {
            confirm: "取消关注",
          }))
        )
          return;
        await api(
          "/api/teams/unfollow",
          "POST",
          typeof team === "object" && team.id ? { id: team.id } : { name },
        );
        await loadConfig();
        $("#team-search-results").hidden = true;
        toast("已取消关注。");
      });
    if (button.hasAttribute("data-team-follow"))
      run(button, async () => {
        const team = state.teams[Number(button.dataset.teamFollow)];
        await api("/api/teams", "POST", {
          id: team.id,
          name: team.name,
          slug: team.slug || "",
          image_url: team.image_url || "",
        });
        await loadConfig();
        button.textContent = "已关注";
        button.removeAttribute("data-team-follow");
        button.dataset.keepDisabled = "true";
        button.className = "button ghost";
        toast(`已关注 ${team.name}。`);
      });
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
      if (state.dirty.has("push"))
        throw new Error("请先保存推送设置，再立即推送。");
      if (!state.config.push_groups?.length)
        throw new Error("请先在订阅管理中添加推送群组。");
      const days = Number(state.config.daily_push_days || 1);
      if (
        !(await dialog(
          "立即推送赛程",
          `将未来 ${days} 天的赛程发送到全部 ${state.config.push_groups.length} 个推送群组。`,
          { confirm: "确认推送" },
        ))
      )
        return;
      const result = await api("/api/push_now", "POST", { days });
      toast(
        result.skipped
          ? "当前没有符合条件的比赛，无需推送。"
          : "赛程推送已安排。",
      );
    }),
  );
  $("#send-test").addEventListener("click", (event) =>
    run(event.currentTarget, async () => {
      if (state.dirty.has("advanced"))
        throw new Error("请先保存连接设置，再发送测试消息。");
      if (!state.config.test_mode_enabled || !state.config.test_target_id)
        throw new Error("请启用测试模式并保存目标号码。");
      const action = $("#test-action").value;
      if (
        !(await dialog(
          "发送测试消息",
          `向${state.config.test_target_type === "group" ? "群" : "QQ"} ${state.config.test_target_id} 发送「${action}」测试消息。`,
          { confirm: "发送测试" },
        ))
      )
        return;
      const result = await api("/api/test", "POST", { action });
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
      fields: `<label class="field">提前提醒（分钟）<input type="number" name="minutes" value="${minutes}" min="1" max="120" required></label><label class="inline-check" style="margin-top:14px"><input type="checkbox" name="useGlobal">使用全局提醒设置（${escaped(state.config.remind_minutes)} 分钟）</label>`,
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

async function initialize() {
  installEvents();
  try {
    if (bridge) {
      const context = await bridge.ready();
      const applyContext = (value) => {
        const next = value || bridge.getContext?.();
        if (next && typeof next.isDark === "boolean")
          document.documentElement.dataset.theme = next.isDark
            ? "dark"
            : "light";
        document.title =
          bridge.t?.("views.management.title", "CS2 比赛推送") ||
          "CS2 比赛推送";
      };
      applyContext(context);
      const unsubscribe = bridge.onContext(applyContext);
      window.addEventListener("pagehide", unsubscribe, { once: true });
      $("#host-mode").textContent = "AstrBot 内嵌管理视图";
    } else {
      const media = window.matchMedia("(prefers-color-scheme: dark)");
      const applyTheme = () => {
        document.documentElement.dataset.theme = media.matches
          ? "dark"
          : "light";
      };
      applyTheme();
      media.addEventListener("change", applyTheme);
      try {
        sessionToken =
          new URLSearchParams(location.search).get("token") ||
          sessionStorage.getItem("cs_panel_token") ||
          "";
        if (sessionToken)
          sessionStorage.setItem("cs_panel_token", sessionToken);
      } catch {
        /* HttpOnly session cookies remain usable if browser storage is unavailable. */
      }
      if (new URLSearchParams(location.search).has("token"))
        history.replaceState(null, "", location.pathname);
      $("#host-mode").textContent = "独立 Web 管理面板";
    }
    await loadConfig();
    state.ready = true;
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

initialize();
