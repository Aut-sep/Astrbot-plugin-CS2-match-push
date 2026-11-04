// Group drafts share the page's existing Save/Discard transaction.
export const RULE_FIELDS = [
  ['push_enabled', '自动推送总开关', 'bool', true],
  ['filter_mode', '比赛筛选方式', 'mode', 'tiers_or_followed'],
  ['min_tiers', '赛事级别', 'tiers', ['s', 'a']],
  ['followed_teams', '关注战队', 'teams', []],
  ['blacklist_teams', '战队黑名单关键词', 'lines', []],
  ['blacklist_leagues', '联赛黑名单关键词', 'lines', []],
  ['upcoming_enabled', '赛前提醒 / 已开赛补报', 'bool', true],
  ['remind_minutes', '赛前提前提醒（分钟）', 'number', 10, 1, 120],
  ['finished_enabled', '赛后结果', 'bool', true],
  ['reschedule_notify', '改期通知', 'bool', true],
  ['tournament_announce_enabled', '赛事开幕通知', 'bool', true],
  ['tournament_announce_hours', '开幕提前提醒（小时）', 'number', 2, 1, 168],
  ['daily_push_enabled', '定时赛程日报', 'bool', false],
  ['daily_push_times', '日报时间（北京时间）', 'times', ['08:00']],
  ['daily_push_days', '日报包含天数', 'number', 1, 1, 30],
  ['image_push_enabled', '图片消息', 'bool', true],
];
const own = (object, key) => Object.prototype.hasOwnProperty.call(object, key);
const RULE_SECTIONS = [
  ['scope', '赛事范围', ['filter_mode', 'min_tiers', 'followed_teams', 'blacklist_teams', 'blacklist_leagues']],
  ['notices', '比赛通知', ['upcoming_enabled', 'remind_minutes', 'finished_enabled', 'reschedule_notify', 'tournament_announce_enabled', 'tournament_announce_hours']],
  ['daily', '赛程日报', ['daily_push_enabled', 'daily_push_times', 'daily_push_days']],
  ['style', '消息样式', ['image_push_enabled']],
];
export function effectiveRules(config, group) {
  const values = Object.fromEntries(RULE_FIELDS.map(([key, , , fallback]) => [key, config[key] ?? structuredClone(fallback)]));
  values.filter_mode = config.filter_mode || (config.notify_all_followed === false ? 'tiers' : 'tiers_or_followed');
  return { ...structuredClone(values), ...structuredClone(config.group_rules?.[group] || {}) };
}

export function createGroupRuleEditor({ getConfig, getDraft, getDefaults, edit, api, run, dialog, escape: esc, onSelect }) {
  const host = document.querySelector('#group-rules-editor');
  let selected = '';
  let activeSection = 'scope';
  let searchVersion = 0;
  let searchResults = [];
  const hasOwn = (key) => own(getDraft().group_rules?.[selected] || {}, key);
  const merged = () => ({ ...getDefaults(), group_rules: getDraft().group_rules || {} });
  function set(key, value, inherit = false) {
    const gid = selected;
    edit(draft => {
      const overrides = draft.group_rules[gid] ||= {};
      if (inherit) delete overrides[key];
      else overrides[key] = structuredClone(value);
      if (!Object.keys(overrides).length) delete draft.group_rules[gid];
    });
  }
  function summary(gid, config = merged()) {
    const rules = effectiveRules(config, gid);
    const independent = Object.keys(config.group_rules?.[gid] || {}).length;
    const mode = { tiers: '按级别', followed: '只看关注', tiers_or_followed: '级别或关注' }[rules.filter_mode];
    return `${rules.push_enabled ? '开启' : '暂停'} · ${independent ? '含独立设置' : '继承默认'} · ${mode} · 赛前${rules.upcoming_enabled ? rules.remind_minutes + '分钟' : '关闭'}`;
  }
  function valueEditor(field, value, custom) {
    const [key, label, type, , min, max] = field;
    const disabled = custom ? '' : 'disabled';
    if (type === 'bool') return `<select data-rule-value="${key}" aria-label="${label}" ${disabled}><option value="true" ${value ? 'selected' : ''}>开启</option><option value="false" ${!value ? 'selected' : ''}>关闭</option></select>`;
    if (type === 'number') return `<input type="number" data-rule-value="${key}" aria-label="${label}" min="${min}" max="${max}" step="1" value="${Number(value)}" required ${disabled}>`;
    if (type === 'mode') return `<select data-rule-value="${key}" aria-label="${label}" ${disabled}>${[['tiers','按赛事级别'],['followed','只看关注战队'],['tiers_or_followed','赛事级别或关注战队']].map(([v,l]) => `<option value="${v}" ${v === value ? 'selected' : ''}>${l}</option>`).join('')}</select>`;
    if (type === 'tiers') return `<fieldset class="tiers rule-tiers" aria-label="${label}" ${disabled}>${['s','a','b','c','d','unranked'].map(tier => `<label><input type="checkbox" data-rule-tier="${tier}" ${(value || []).includes(tier) ? 'checked' : ''}>${tier === 'unranked' ? '未评级' : tier.toUpperCase()}</label>`).join('')}</fieldset>`;
    if (type === 'teams') {
      const list = (value || []).map((team,index) => `<div class="rule-team"><span>${esc(typeof team === 'string' ? team : team.name)}</span>${custom ? `<button type="button" class="button ghost" data-rule-team-remove="${index}" aria-label="取消关注 ${esc(typeof team === 'string' ? team : team.name)}">×</button>` : ''}</div>`).join('') || '<small>没有关注战队</small>';
      return `<div class="rule-team-list">${list}</div>${custom ? '<div class="add-row"><input id="rule-team-query" type="search" placeholder="输入战队名、简称或中文外号" aria-label="搜索本群关注战队"><button type="button" class="button" data-rule-search>搜索</button></div><div id="rule-team-results" aria-live="polite"></div>' : ''}`;
    }
    return `<input type="text" data-rule-value="${key}" aria-label="${label}" value="${esc((value || []).join(', '))}" placeholder="${type === 'times' ? '08:00, 18:00' : '多个关键词用逗号分隔；留空表示不限制'}" ${disabled}>`;
  }
  function refreshTargetSelectors() {
    const groups = getConfig().push_groups || [];
    for (const [id, label] of [['tool-rule-group','默认规则'],['test-rule-group','默认规则'],['push-rule-group','全部启用的群（各用本群规则）']]) {
      const element = document.getElementById(id);
      if (!element) continue;
      const value = element.value;
      element.innerHTML = `<option value="">${label}</option>` + groups.map(gid => `<option value="${esc(gid)}">群 ${esc(gid)}</option>`).join('');
      element.value = groups.includes(value) ? value : '';
    }
  }
  function renderField(field, rules) {
    const [key, label, type] = field;
    const custom = hasOwn(key);
    return `<div class="rule-field${type === 'teams' ? ' rule-field-wide' : ''}${custom ? ' is-custom' : ''}" role="group" aria-labelledby="rule-label-${key}">
      <div class="rule-field-heading"><span id="rule-label-${key}">${label}</span>
        <label class="rule-override"><input type="checkbox" data-rule-inherit="${key}" aria-label="${label}使用本群独立设置" ${custom ? 'checked' : ''}>独立设置</label></div>
      <div class="rule-field-value">${valueEditor(field, rules[key], custom)}</div>
    </div>`;
  }
  function render() {
    const groups = getDraft().push_groups || [];
    if (!groups.includes(selected)) selected = '';
    const rules = effectiveRules(merged(), selected);
    const count = Object.keys(getDraft().group_rules?.[selected] || {}).length;
    const section = RULE_SECTIONS.find(([key]) => key === activeSection);
    const description = selected
      ? (count ? `${count} 项独立设置，其余沿用默认。` : '全部沿用默认，可按需调整。')
      : (groups.length ? '选择接收群，只调整需要不同的项目。' : '先在上方添加接收群，再设置独立规则。');
    host.innerHTML = `<div class="card-header rule-header"><div><h3>群推送规则</h3><p>${description}</p></div>
      <label class="field">选择接收群<select id="rule-group"><option value="">选择一个群开始设置</option>${groups.map(gid => `<option value="${esc(gid)}" ${selected === gid ? 'selected' : ''}>群 ${esc(gid)}</option>`).join('')}</select></label></div>
      ${!selected ? '' : `<div class="rule-content">
        <div class="rule-master">${renderField(RULE_FIELDS[0], rules)}</div>
        <div class="rule-tabs" role="tablist" aria-label="群规则分类">${RULE_SECTIONS.map(([key, title]) => `<button type="button" role="tab" id="rule-tab-${key}" aria-controls="rule-fields" aria-selected="${key === activeSection}" tabindex="${key === activeSection ? 0 : -1}" data-rule-section="${key}">${title}</button>`).join('')}</div>
        <div id="rule-fields" class="rule-fields" role="tabpanel" aria-labelledby="rule-tab-${activeSection}">${section[2].map(key => renderField(RULE_FIELDS.find(field => field[0] === key), rules)).join('')}</div>
        <div class="rule-footer"><span>勾选「独立设置」后修改，取消勾选恢复继承。</span><details class="rule-copy-menu"><summary>复制与恢复</summary><div class="rule-copy"><label class="field">复制规则来源<select id="rule-copy-from"><option value="">默认规则</option>${groups.filter(gid => gid !== selected).map(gid => `<option value="${esc(gid)}">群 ${esc(gid)}</option>`).join('')}</select></label><button type="button" class="button" data-rule-copy>复制完整规则</button><button type="button" class="button ghost" data-rule-reset>全部恢复继承</button></div></details></div>
      </div>`}`;
    refreshTargetSelectors();
  }
  function showSearch() {
    const results = host.querySelector('#rule-team-results');
    if (!results) return;
    const followed = effectiveRules(merged(), selected).followed_teams;
    results.innerHTML = searchResults.length ? searchResults.map((team,index) => {
      const added = followed.some(item => typeof item === 'object' && String(item.id) === String(team.id));
      return `<div class="rule-team"><span>${esc(team.name)} · ID ${esc(team.id)}</span><button type="button" class="button" data-rule-team-add="${index}" ${added ? 'disabled' : ''}>${added ? '已关注' : '关注'}</button></div>`;
    }).join('') : '<small>未找到战队，请尝试全名或其他简称。</small>';
  }
  host.addEventListener('change', event => {
    const element = event.target;
    if (element.id === 'rule-group') { selected = element.value; searchVersion++; searchResults = []; render(); onSelect?.(selected); return; }
    if (element.dataset.ruleInherit) {
      const key = element.dataset.ruleInherit;
      set(key, effectiveRules(merged(), selected)[key], !element.checked);
    } else if (element.dataset.ruleValue) {
      const key = element.dataset.ruleValue;
      const field = RULE_FIELDS.find(item => item[0] === key);
      let value = element.value;
      if (field[2] === 'bool') value = value === 'true';
      else if (field[2] === 'number') {
        if (!element.checkValidity()) { element.reportValidity(); return; }
        value = Number(value);
      } else if (['lines','times'].includes(field[2])) {
        value = [...new Set(value.split(/[,，\n]/).map(v => v.trim()).filter(Boolean))];
        if (field[2] === 'times' && value.some(v => !/^([01]\d|2[0-3]):[0-5]\d$/.test(v))) {
          element.setCustomValidity('请使用 HH:MM，例如 08:00, 18:00'); element.reportValidity(); return;
        }
        element.setCustomValidity('');
      }
      set(key, value);
    } else if (element.dataset.ruleTier) {
      set('min_tiers', [...host.querySelectorAll('[data-rule-tier]:checked')].map(input => input.dataset.ruleTier));
    }
  });
  host.addEventListener('input', event => { event.target.setCustomValidity?.(''); if (event.target.id === 'rule-team-query') searchVersion++; });
  host.addEventListener('keydown', event => {
    const tab = event.target.closest('[data-rule-section]');
    if (!tab || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const index = RULE_SECTIONS.findIndex(([key]) => key === activeSection);
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? RULE_SECTIONS.length - 1
      : (index + (event.key === 'ArrowRight' ? 1 : -1) + RULE_SECTIONS.length) % RULE_SECTIONS.length;
    activeSection = RULE_SECTIONS[next][0]; render();
    host.querySelector(`[data-rule-section="${activeSection}"]`).focus();
  });
  host.addEventListener('click', event => {
    const button = event.target.closest('button');
    if (!button) return;
    if (button.dataset.ruleSection) {
      activeSection = button.dataset.ruleSection; render();
      host.querySelector(`[data-rule-section="${activeSection}"]`).focus(); return;
    }
    run(button, async () => {
      if (button.hasAttribute('data-rule-reset')) {
        if (await dialog('恢复默认规则', `清除群 ${selected} 的所有独立设置，改为逐项继承默认规则。保存后生效。`))
          edit(draft => { delete draft.group_rules[selected]; });
      } else if (button.hasAttribute('data-rule-copy')) {
        const source = host.querySelector('#rule-copy-from').value;
        const values = effectiveRules(merged(), source);
        edit(draft => { draft.group_rules[selected] = structuredClone(values); });
      } else if (button.hasAttribute('data-rule-team-remove')) {
        const teams = structuredClone(effectiveRules(merged(), selected).followed_teams);
        teams.splice(Number(button.dataset.ruleTeamRemove), 1); set('followed_teams', teams);
      } else if (button.hasAttribute('data-rule-search')) {
        const query = host.querySelector('#rule-team-query').value.trim();
        if (!query) throw new Error('请输入战队名称或简称。');
        const version = ++searchVersion, group = selected;
        const result = await api('/api/teams/search','GET',undefined,{q:query});
        if (version !== searchVersion || group !== selected) return;
        searchResults = result.teams || []; showSearch();
      } else if (button.hasAttribute('data-rule-team-add')) {
        const team = searchResults[Number(button.dataset.ruleTeamAdd)];
        if (!team?.id) throw new Error('无法确认战队 ID，请重新搜索。');
        const teams = structuredClone(effectiveRules(merged(), selected).followed_teams);
        if (!teams.some(item => typeof item === 'object' && String(item.id) === String(team.id))) {
          teams.push({id:team.id,name:team.name,slug:team.slug || '',image_url:team.image_url || ''});
          set('followed_teams', teams);
        }
      }
    });
  });
  return { render, summary, savedEffective: gid => effectiveRules(getConfig(), gid),
    select(gid) { selected = gid; searchVersion++; render(); host.scrollIntoView({block:'start',behavior:'smooth'}); },
    validate() {
      const invalid = [...host.querySelectorAll('input')].find(input => !input.checkValidity());
      if (invalid) { invalid.reportValidity(); throw new Error('请检查群规则输入值。'); }
    },
  };
}
