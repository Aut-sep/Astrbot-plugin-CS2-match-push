export const BUILTIN_PRESETS = [
  {
    id: "builtin-big-event",
    name: "大赛观众",
    description: "提醒你观看高水平赛事，了解开赛时间与比分。",
    summary: "S 级 · 赛前 10 分钟 · 赛后与改期 · Major 阶段和赛事开幕预告",
    rules: {
      filter_mode: "tiers", min_tiers: ["s"], followed_teams: [],
      upcoming_enabled: true, remind_minutes: 10, finished_enabled: true,
      reschedule_notify: true, major_stage_notify_enabled: true,
      tournament_announce_enabled: true, tournament_announce_hours: 2,
      daily_push_enabled: false, daily_push_times: ["08:00"], daily_push_days: 1,
      image_push_enabled: true,
    },
  },
  {
    id: "builtin-favorite-team",
    name: "主队粉丝",
    description: "只关注你选择的战队，不受赛事等级限制。",
    summary: "仅关注战队 · 赛前 10 分钟 · 赛后与改期 · Major 阶段和赛事开幕预告",
    rules: {
      filter_mode: "followed", min_tiers: [],
      upcoming_enabled: true, remind_minutes: 10, finished_enabled: true,
      reschedule_notify: true, major_stage_notify_enabled: true,
      tournament_announce_enabled: true, tournament_announce_hours: 2,
      daily_push_enabled: false, daily_push_times: ["08:00"], daily_push_days: 1,
      image_push_enabled: true,
    },
  },
  {
    id: "builtin-event-digest",
    name: "赛事资讯群",
    description: "用每日赛程、比分和赛事开幕预告分享赛事动态。",
    summary: "S/A 级 · 每日 08:00 赛程 · Major 阶段和赛事开幕预告",
    rules: {
      filter_mode: "tiers", min_tiers: ["s", "a"], followed_teams: [],
      upcoming_enabled: false, remind_minutes: 10, finished_enabled: true,
      reschedule_notify: false, major_stage_notify_enabled: true,
      tournament_announce_enabled: true, tournament_announce_hours: 2,
      daily_push_enabled: true, daily_push_times: ["08:00"], daily_push_days: 1,
      image_push_enabled: true,
    },
  },
];

export function presetRules(preset, current, { builtin = false } = {}) {
  const result = structuredClone(current);
  for (const [key, value] of Object.entries(preset.rules)) result[key] = structuredClone(value);
  // User choices such as blocked teams and leagues survive applying a preset.
  if (builtin) {
    for (const key of ["blacklist_teams", "blacklist_leagues", "push_enabled"]) {
      if (Object.hasOwn(current, key)) result[key] = structuredClone(current[key]);
    }
  }
  if (builtin && preset.rules.filter_mode === "followed") {
    result.followed_teams = structuredClone(current.followed_teams || []);
  }
  return result;
}

export function applyPresetToGroup(groupRules, groupId, preset, current, options = {}) {
  const updated = structuredClone(groupRules || {});
  updated[groupId] = presetRules(preset, current, options);
  return updated;
}

export function newPresetId() {
  return `custom-${globalThis.crypto?.randomUUID?.().replaceAll("-", "") || `${Date.now()}-${Math.random().toString(36).slice(2)}`}`;
}
