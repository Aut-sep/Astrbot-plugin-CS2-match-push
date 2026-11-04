import { BUILTIN_PRESETS, getDefaultPreset, newPresetId, presetRules } from './presets.js';

// Group drafts share the page's existing Save/Discard transaction.
export const RULE_FIELDS = [
  ['push_enabled', '本群赛事推送', 'bool', true],
  ['filter_mode', '比赛筛选方式', 'mode', 'tiers_or_followed'],
  ['min_tiers', '赛事级别', 'tiers', ['s', 'a']],
  ['followed_teams', '关注战队', 'teams', []],
  ['blacklist_teams', '战队黑名单关键词', 'lines', []],
  ['blacklist_leagues', '联赛黑名单关键词', 'lines', []],
  ['upcoming_enabled', '赛前提醒 / 已开赛补报', 'bool', true],
  ['remind_minutes', '赛前提前提醒（分钟）', 'number', 10, 1, 120],
  ['finished_enabled', '赛后结果', 'bool', true],
  ['reschedule_notify', '改期通知', 'bool', true],
  ['major_stage_notify_enabled', 'Major 阶段提醒', 'bool', true],
  ['tournament_announce_enabled', '赛事开幕通知', 'bool', true],
  ['tournament_announce_hours', '开幕提前提醒（小时）', 'number', 2, 1, 168],
  ['daily_push_enabled', '定时赛程日报', 'bool', false],
  ['daily_push_times', '日报时间（北京时间）', 'times', ['08:00']],
  ['daily_push_days', '日报包含天数', 'number', 1, 1, 30],
  ['image_push_enabled', '图片消息', 'bool', true],
];
const RULE_SECTIONS = [
  ['scope', '赛事范围', ['filter_mode', 'min_tiers', 'followed_teams', 'blacklist_teams', 'blacklist_leagues']],
  ['notices', '比赛通知', ['upcoming_enabled', 'remind_minutes', 'finished_enabled', 'reschedule_notify', 'major_stage_notify_enabled', 'tournament_announce_enabled', 'tournament_announce_hours']],
  ['daily', '赛程日报', ['daily_push_enabled', 'daily_push_times', 'daily_push_days']],
  ['style', '消息样式', ['image_push_enabled']],
];
export function effectiveRules(config, group) {
  const values = Object.fromEntries(RULE_FIELDS.map(([key, , , fallback]) => [key, config[key] ?? structuredClone(fallback)]));
  values.filter_mode = config.filter_mode || (config.notify_all_followed === false ? 'tiers' : 'tiers_or_followed');
  return { ...structuredClone(values), ...structuredClone(config.group_rules?.[group] || {}) };
}

export function createGroupRuleEditor({ getConfig, getDraft, getDefaults, edit, editDefaults, api, run, dialog, escape: esc, onSelect, toast = () => {} }) {
  const host = document.querySelector('#group-rules-editor');
  const DEFAULT = 'builtin-default';
  let selected = DEFAULT;
  let lastDraft = getDraft();
  let pendingPreset = null;
  let presetTeams = [];
  const presetGroupsRequiringTeams = new Set();
  let searchVersion = 0;
  let searchResults = [];
  const merged = () => ({ ...getDefaults(), group_rules: getDraft().group_rules || {} });
  const editingRules = () => effectiveRules(merged(), selected === DEFAULT ? undefined : selected);
  const defaultPreset = () => getDefaultPreset(templateRules(effectiveRules(getDefaults())));
  function assignRules(values) {
    if (selected === DEFAULT) editDefaults(templateRules(values));
    else edit(draft => { draft.group_rules[selected] = templateRules(values); });
  }
  function set(key, value) {
    assignRules({ ...editingRules(), [key]: structuredClone(value) });
  }
  function summary(gid, config = merged()) {
    const rules = effectiveRules(config, gid);
    const mode = { tiers: '按级别', followed: '只看关注', tiers_or_followed: '级别或关注' }[rules.filter_mode];
    return `${rules.push_enabled ? '开启' : '暂停'} · ${mode} · 赛前${rules.upcoming_enabled ? rules.remind_minutes + '分钟' : '关闭'}`;
  }
  function valueEditor(field, value, custom) {
    const [key, label, type, , min, max] = field;
    const disabled = custom ? '' : 'disabled';
    if (type === 'bool') return `<select data-rule-value="${key}" aria-label="${label}" ${disabled}><option value="true" ${value ? 'selected' : ''}>开启</option><option value="false" ${!value ? 'selected' : ''}>${key === 'push_enabled' ? '暂停' : '关闭'}</option></select>`;
    if (type === 'number') return `<input type="number" data-rule-value="${key}" aria-label="${label}" min="${min}" max="${max}" step="1" value="${Number(value)}" required ${disabled}>`;
    if (type === 'mode') return `<select data-rule-value="${key}" aria-label="${label}" ${disabled}>${[['tiers','按赛事级别'],['followed','只看关注战队'],['tiers_or_followed','赛事级别或关注战队']].map(([v,l]) => `<option value="${v}" ${v === value ? 'selected' : ''}>${l}</option>`).join('')}</select>`;
    if (type === 'tiers') return `<fieldset class="tiers rule-tiers" aria-label="${label}" ${disabled}>${['s','a','b','c','d','unranked'].map(tier => `<label><input type="checkbox" data-rule-tier="${tier}" ${(value || []).includes(tier) ? 'checked' : ''}>${tier === 'unranked' ? '未评级' : tier.toUpperCase()}</label>`).join('')}</fieldset>`;
    if (type === 'teams') {
      const list = (value || []).map((team,index) => `<div class="rule-team"><span>${esc(typeof team === 'string' ? team : team.name)}</span>${custom ? `<button type="button" class="button ghost" data-rule-team-remove="${index}" aria-label="取消关注 ${esc(typeof team === 'string' ? team : team.name)}">×</button>` : ''}</div>`).join('') || '<small>没有关注战队</small>';
      return `<div class="rule-team-list">${list}</div>${custom ? '<div class="add-row"><input id="rule-team-query" type="search" placeholder="输入战队名、简称或中文外号" aria-label="搜索关注战队"><button type="button" class="button" data-rule-search>搜索</button></div><div id="rule-team-results" aria-live="polite"></div>' : ''}`;
    }
    return `<input type="text" data-rule-value="${key}" aria-label="${label}" value="${esc((value || []).join(', '))}" placeholder="${type === 'times' ? '08:00, 18:00' : '多个关键词用逗号分隔；留空表示不限制'}" ${disabled}>`;
  }
  function refreshTargetSelectors() {
    const groups = getConfig().push_groups || [];
    for (const [id, label] of [['tool-rule-group','默认配置'],['test-rule-group','默认配置'],['push-rule-group','全部启用的群（各用本群规则）']]) {
      const element = document.getElementById(id);
      if (!element) continue;
      const value = element.value;
      element.innerHTML = `<option value="">${label}</option>` + groups.map(gid => `<option value="${esc(gid)}">群 ${esc(gid)}</option>`).join('');
      element.value = groups.includes(value) ? value : '';
    }
  }
  function renderField(field, rules) {
    const [key, label, type] = field;
    return `<div class="rule-field${type === 'teams' ? ' rule-field-wide' : ''}" role="group" aria-labelledby="rule-label-${key}">
      <div class="rule-field-heading"><span id="rule-label-${key}">${label}</span>
        </div>
      <div class="rule-field-value">${valueEditor(field, rules[key], true)}</div>
    </div>`;
  }
  function templateRules(rules) {
    return Object.fromEntries(RULE_FIELDS.map(([key]) => [key, structuredClone(rules[key])]));
  }
  function renderPresets(rules) {
    const custom = getDraft().rule_presets || [];
    const baseline = defaultPreset();
    const card = (preset, builtin) => `<article class="preset-card${builtin ? ' preset-card-builtin' : ''}">
      <div class="preset-card-copy"><div class="preset-card-title"><strong>${esc(preset.name)}</strong>${builtin ? '<span class="settings-tag">内置</span>' : '<span class="settings-tag">自定义</span>'}</div>
        <p>${esc(preset.description || preset.summary || '按已保存的推送规则配置。')}</p><small>${esc(preset.summary || '自定义推送规则')}</small></div>
      <div class="preset-card-actions"><button type="button" class="button primary" data-preset-action="${preset.id === DEFAULT && selected === DEFAULT ? 'edit-default' : 'apply'}" data-preset-id="${esc(preset.id)}">${preset.id === DEFAULT && selected === DEFAULT ? '编辑配置' : selected === DEFAULT ? '使用配置' : '应用到此群'}</button>${preset.id === DEFAULT && selected !== DEFAULT ? '<button type="button" class="button ghost" data-preset-action="edit-default">编辑</button>' : ''}
      ${builtin ? '' : `<details class="preset-more"><summary>管理</summary><div class="preset-more-actions">
        <button type="button" class="button ghost" data-preset-action="update" data-preset-id="${esc(preset.id)}">用当前群更新</button>
        <button type="button" class="button ghost" data-preset-action="rename" data-preset-id="${esc(preset.id)}">重命名</button>
        <button type="button" class="button ghost" data-preset-action="duplicate" data-preset-id="${esc(preset.id)}">另存副本</button>
        <button type="button" class="button danger" data-preset-action="delete" data-preset-id="${esc(preset.id)}">删除</button>
      </div></details>`}</div></article>`;
    const teamPicker = pendingPreset ? `<div class="preset-team-picker"><div><strong>选择关注战队</strong><p>至少选择一支，稍后仍可在规则中增删。</p></div><div class="preset-team-options">${presetTeams.map(team => `<label class="inline-check"><input type="checkbox" data-preset-team value="${esc(team.id)}"><span>${esc(team.name)}</span></label>`).join('')}</div><div class="button-group"><button type="button" class="button primary" data-preset-action="apply-selected">应用预设</button><button type="button" class="button ghost" data-preset-action="cancel-select">取消</button></div></div>` : '';
    return `<section class="preset-library" aria-labelledby="preset-title"><div class="card-header preset-library-header"><h3 id="preset-title">推送配置</h3>
      <button type="button" class="button" data-preset-action="save-current">另存为我的配置</button></div>
      <div class="preset-grid">${card(baseline, true)}${BUILTIN_PRESETS.map(p => card(p, true)).join('')}${custom.map(p => card(p, false)).join('')}</div>${teamPicker}
    </section>`;
  }
  function render() {
    // The page returns the saved config object after Save or Discard. Drop
    // transient fan-preset validation when that draft transaction ends.
    if (getDraft() !== lastDraft && getDraft() === getConfig()) {
      presetGroupsRequiringTeams.clear();
      pendingPreset = null;
      presetTeams = [];
    }
    lastDraft = getDraft();
    const groups = getDraft().push_groups || [];
    if (selected !== DEFAULT && !groups.includes(selected)) selected = DEFAULT;
    const rules = editingRules();
    host.innerHTML = `<div class="card-header rule-header"><h3>${selected === DEFAULT ? '默认配置' : '群 ' + esc(selected)}</h3>
      <label class="field">接收群<select id="rule-group"><option value="${DEFAULT}" ${selected === DEFAULT ? 'selected' : ''}>默认配置</option>${groups.map(gid => `<option value="${esc(gid)}" ${selected === gid ? 'selected' : ''}>群 ${esc(gid)}</option>`).join('')}</select></label></div>${renderPresets(rules)}
      <div class="rule-content">
        <label class="switch-row rule-master"><strong>${selected === DEFAULT ? '赛事推送' : '本群赛事推送'}</strong><input type="checkbox" role="switch" data-rule-value="push_enabled" aria-label="${selected === DEFAULT ? '赛事推送' : '本群赛事推送'}" ${rules.push_enabled ? 'checked' : ''}></label>
        ${RULE_SECTIONS.map(([key, title, fields]) => `<section class="rule-config-section" aria-labelledby="rule-section-${key}"><h3 id="rule-section-${key}" class="rule-section-title">${title}</h3><div class="rule-fields">${fields.map(field => renderField(RULE_FIELDS.find(item => item[0] === field), rules)).join('')}</div></section>`).join('')}
      </div>`;
    refreshTargetSelectors();
  }
  function showSearch() {
    const results = host.querySelector('#rule-team-results');
    if (!results) return;
    const followed = editingRules().followed_teams;
    results.innerHTML = searchResults.length ? searchResults.map((team,index) => {
      const added = followed.some(item => typeof item === 'object' && String(item.id) === String(team.id));
      return `<div class="rule-team"><span>${esc(team.name)} · ID ${esc(team.id)}</span><button type="button" class="button" data-rule-team-add="${index}" ${added ? 'disabled' : ''}>${added ? '已关注' : '关注'}</button></div>`;
    }).join('') : '<small>未找到战队，请尝试全名或其他简称。</small>';
  }
  host.addEventListener('change', event => {
    const element = event.target;
    if (element.id === 'rule-group') { selected = element.value; pendingPreset = null; presetTeams = []; searchVersion++; searchResults = []; render(); onSelect?.(selected); return; }
    if (element.dataset.ruleValue) {
      const key = element.dataset.ruleValue;
      const field = RULE_FIELDS.find(item => item[0] === key);
      let value = element.value;
      if (field[2] === 'bool') value = element.type === 'checkbox' ? element.checked : value === 'true';
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
      if (key === 'push_enabled') host.querySelector('[data-rule-value="push_enabled"]')?.focus();
    } else if (element.dataset.ruleTier) {
      set('min_tiers', [...host.querySelectorAll('[data-rule-tier]:checked')].map(input => input.dataset.ruleTier));
    }
  });
  host.addEventListener('input', event => { event.target.setCustomValidity?.(''); if (event.target.id === 'rule-team-query') searchVersion++; });
  host.addEventListener('click', event => {
    const button = event.target.closest('button');
    if (!button) return;
    if (button.dataset.presetAction) {
      run(button, async () => {
        const action = button.dataset.presetAction;
        const id = button.dataset.presetId;
        const presets = getDraft().rule_presets || [];
        const preset = presets.find(item => item.id === id);
        if (action === 'edit-default') { selected = DEFAULT; pendingPreset = null; presetTeams = []; searchVersion++; render(); host.querySelector('.rule-content')?.scrollIntoView?.({block:'start',behavior:'smooth'}); return; }
        if (action === 'apply') {
          if (!selected) throw new Error('请先选择要应用预设的接收群。');
          const builtin = BUILTIN_PRESETS.find(item => item.id === id);
          const source = id === DEFAULT ? defaultPreset() : builtin || preset;
          if (!source) throw new Error('找不到此预设，请刷新页面后重试。');
          const group = selected;
          const current = editingRules();
          const values = presetRules(source, current, { builtin: !!builtin });
          if (source.rules.filter_mode === 'followed' && !values.followed_teams.length) {
            const result = await api('/api/teams/popular');
            if (selected !== group) return;
            presetTeams = result.teams || [];
            if (!presetTeams.length) throw new Error('未能读取常看战队，请先在订阅管理中搜索并关注一支战队。');
            pendingPreset = { id, group, builtin: !!builtin };
            render();
            return;
          }
          assignRules(values);
          if (source.rules.filter_mode === 'followed') presetGroupsRequiringTeams.add(group);
          toast(group === DEFAULT ? '默认配置已更新，保存后生效。' : `「${source.name}」已应用到群 ${group}，保存后生效。`);
          return;
        }
        if (action === 'apply-selected') {
          if (!pendingPreset || !selected || pendingPreset.group !== selected) throw new Error('请重新选择主队粉丝配置。');
          const ids = new Set([...host.querySelectorAll('[data-preset-team]:checked')].map(input => String(input.value)));
          if (!ids.size) throw new Error('请至少选择一支关注战队。');
          const source = (pendingPreset.id === DEFAULT ? defaultPreset() : BUILTIN_PRESETS.find(item => item.id === pendingPreset.id)) || (getDraft().rule_presets || []).find(item => item.id === pendingPreset.id);
          if (!source) throw new Error('找不到此预设，请刷新页面后重试。');
          const current = editingRules();
          const values = presetRules(source, current, { builtin: pendingPreset.builtin });
          values.followed_teams = presetTeams.filter(team => ids.has(String(team.id))).map(team => ({ id: team.id, name: team.name, slug: team.slug || '' }));
          assignRules(values);
          presetGroupsRequiringTeams.add(selected);
          const group = selected;
          pendingPreset = null; presetTeams = [];
          toast(group === DEFAULT ? '默认配置已更新，保存后生效。' : `「${source.name}」已应用到群 ${group}，保存后生效。`);
          return;
        }
        if (action === 'cancel-select') { pendingPreset = null; presetTeams = []; render(); return; }
        const current = selected ? editingRules() : null;
        if (action === 'save-current' || action === 'duplicate') {
          const sourceRules = action === 'duplicate' ? preset?.rules : current && templateRules(current);
          if (!sourceRules) throw new Error(action === 'duplicate' ? '找不到此预设。' : '请先选择接收群。');
          if (sourceRules.filter_mode === 'followed' && !sourceRules.followed_teams?.length) throw new Error('主队粉丝规则至少需要关注一支战队。');
          const value = await dialog(action === 'duplicate' ? '另存为预设' : '保存自定义配置', '预设只保存推送规则，不含群号和账号信息。', {
            confirm: '保存预设', fields: '<label class="field">预设名称<input name="name" maxlength="40" minlength="1" required placeholder="例如：周末观赛"></label>',
          });
          if (!value) return;
          const name = String(value.name || '').trim();
          if (!name || [...name].length > 40) throw new Error('预设名称请填写 1～40 个字符。');
          if (presets.some(item => item.name.trim().toLocaleLowerCase() === name.toLocaleLowerCase())) throw new Error('已有同名预设，请换一个名称。');
          if (presets.length >= 50) throw new Error('最多保存 50 个自定义预设，请先删除不需要的预设。');
          edit(draft => { draft.rule_presets = [...(draft.rule_presets || []), { id: newPresetId(), name, rules: structuredClone(sourceRules) }]; });
        } else if (action === 'update') {
          if (!selected || !current) throw new Error('请先选择要读取规则的接收群。');
          if (current.filter_mode === 'followed' && !current.followed_teams.length) throw new Error('主队粉丝规则至少需要关注一支战队。');
          edit(draft => { draft.rule_presets = draft.rule_presets.map(item => item.id === id ? { ...item, rules: templateRules(current) } : item); });
        } else if (action === 'rename') {
          const value = await dialog('重命名配置', '名称仅用于识别此预设。', {
            confirm: '保存名称', fields: `<label class="field">预设名称<input name="name" maxlength="40" minlength="1" required value="${esc(preset?.name || '')}"></label>`,
          });
          if (!value) return;
          const name = String(value.name || '').trim();
          if (!name || [...name].length > 40) throw new Error('预设名称请填写 1～40 个字符。');
          if ((getDraft().rule_presets || []).some(item => item.id !== id && item.name.trim().toLocaleLowerCase() === name.toLocaleLowerCase())) throw new Error('已有同名预设，请换一个名称。');
          edit(draft => { draft.rule_presets = draft.rule_presets.map(item => item.id === id ? { ...item, name } : item); });
        } else if (action === 'delete') {
          if (await dialog('删除自定义配置', `删除「${preset?.name || ''}」？已应用到群组的规则不会改变。`, { confirm: '删除预设', danger: true }))
            edit(draft => { draft.rule_presets = draft.rule_presets.filter(item => item.id !== id); });
        }
      });
      return;
    }
    run(button, async () => {
      if (button.hasAttribute('data-rule-team-remove')) {
        const teams = structuredClone(editingRules().followed_teams);
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
        const teams = structuredClone(editingRules().followed_teams);
        if (!teams.some(item => typeof item === 'object' && String(item.id) === String(team.id))) {
          teams.push({id:team.id,name:team.name,slug:team.slug || '',image_url:team.image_url || ''});
          set('followed_teams', teams);
        }
      }
    });
  });
  return { render, summary, savedEffective: gid => effectiveRules(getConfig(), gid),
    select(gid) { selected = gid; pendingPreset = null; presetTeams = []; searchVersion++; render(); host.scrollIntoView({block:'start',behavior:'smooth'}); },
    validate() {
      const invalid = [...host.querySelectorAll('input')].find(input => !input.checkValidity());
      if (invalid) { invalid.reportValidity(); throw new Error('请检查群规则输入值。'); }
      const draft = getDraft();
      for (const gid of presetGroupsRequiringTeams) {
        if (!draft.push_groups.includes(gid)) { presetGroupsRequiringTeams.delete(gid); continue; }
        const rules = effectiveRules(merged(), gid);
        if (rules.filter_mode === 'followed' && !rules.followed_teams.length) {
          selected = gid; render();
          throw new Error(`群 ${gid} 使用了主队粉丝配置，请至少选择一支关注战队后再保存。`);
        }
      }
    },
  };
}
