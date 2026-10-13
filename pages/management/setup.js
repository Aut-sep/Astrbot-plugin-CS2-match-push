/** Guided setup uses the same authenticated operations as the management forms. */
export function createSetupGuide({ api, getState, loadConfig, activateTab, toast }) {
  const $ = (selector) => document.querySelector(selector);
  let step = 0, busy = false, verified = false, checks = null;
  const titles = ['连接数据源', '添加接收群', '选择推送方式', '检查并完成'];
  const setError = (message = '') => {
    $('#setup-error').textContent = message;
    $('#setup-error').hidden = !message;
  };
  function sync() {
    const state = getState();
    const ready = state.tokenConfigured && state.config.push_groups?.length;
    $('#setup-summary').textContent = ready ? '基础配置已填写，可随时重新检查或调整。' : '先完成 2 项必填配置，再按需选择提醒和日报。';
    $('#setup-open').disabled = !state.ready;
    $('#setup-token-hint').textContent = state.tokenConfigured ? '已保存 Token。留空使用当前 Token，填写新值可替换。' : '还没有 Token？按下面步骤获取后粘贴到这里。';
    $('#setup-groups-status').textContent = state.config.push_groups?.length ? `已保存 ${state.config.push_groups.length} 个接收群；可继续添加。` : '尚未添加接收群。';
  }
  function showStep(next) {
    step = next;
    setError();
    document.querySelectorAll('[data-setup-step]').forEach(el => {
      el.hidden = Number(el.dataset.setupStep) !== step;
    });
    document.querySelectorAll('[data-setup-progress]').forEach(el => {
      el.classList.toggle('current', Number(el.dataset.setupProgress) === step);
      el.setAttribute('aria-current', Number(el.dataset.setupProgress) === step ? 'step' : 'false');
    });
    $('#setup-step-title').textContent = `${step + 1} / 4 · ${titles[step]}`;
    $('#setup-step-title').focus({ preventScroll: true });
  }
  function open() {
    if (getState().dirty.size) return toast('请先保存或放弃页面中尚未保存的修改，再开始引导。', true);
    const config = getState().config;
    $('#setup-token').value = '';
    $('#setup-group').value = '';
    $('#setup-bot-confirm').checked = false;
    for (const key of ['remind_minutes', 'image_push_enabled', 'daily_push_enabled', 'daily_push_days', 'push_platform_id']) {
      const el = document.querySelector(`[data-setup-key="${key}"]`);
      if (el.type === 'checkbox') el.checked = !!config[key];
      else el.value = config[key] ?? '';
    }
    $('#setup-daily-times').value = (config.daily_push_times || ['08:00']).join(', ');
    document.querySelectorAll('[data-setup-tier]').forEach(el => el.checked = (config.min_tiers || []).includes(el.value));
    verified = false;
    checks = null;
    $('#setup-wizard').hidden = false;
    $('#setup-open').hidden = true;
    sync();
    showStep(0);
  }
  function close() {
    if (busy) return;
    $('#setup-wizard').hidden = true;
    $('#setup-open').hidden = false;
    $('#setup-token').value = '';
    setError();
    $('#setup-open').focus({ preventScroll: true });
  }
  async function action(button, operation) {
    if (busy) return;
    busy = true;
    setError();
    const controls = [...$('#setup-guide').querySelectorAll('button,input,select,textarea')];
    const before = controls.map(el => el.disabled);
    controls.forEach(el => el.disabled = true);
    button.setAttribute('aria-busy', 'true');
    const oldLabel = button.textContent;
    button.textContent = '正在处理…';
    try { await operation(); } catch (error) { setError(error.message || '操作失败，请稍后重试。'); }
    finally {
      busy = false;
      controls.forEach((el, i) => el.disabled = before[i]);
      button.textContent = oldLabel;
      button.removeAttribute('aria-busy');
      sync();
    }
  }
  function showChecks(result) {
    checks = result;
    const list = $('#setup-checks');
    list.replaceChildren();
    for (const [label, check] of [['数据源', result.source], ['接收群', result.groups], ['QQ 机器人', result.platform]]) {
      const row = document.createElement('li');
      row.className = check?.ok === true ? 'setup-check passed' : 'setup-check';
      const title = document.createElement('strong');
      title.textContent = `${check?.ok === true ? '✓' : '待确认'} ${label}`;
      const detail = document.createElement('span');
      detail.textContent = check?.message || '请检查该项配置。';
      row.append(title, detail);
      list.append(row);
    }
  }
  async function goToCheck() {
    showStep(3);
    $('#setup-checks').textContent = '正在检查已保存的配置…';
    const result = await api('/api/setup/check', 'POST', {});
    showChecks(result);
  }
  $('#setup-open').addEventListener('click', open);
  $('#setup-close').addEventListener('click', close);
  $('#setup-token').addEventListener('input', () => verified = false);
  document.querySelectorAll('[data-setup-back]').forEach(button => button.addEventListener('click', () => showStep(step - 1)));
  $('#setup-source-form').addEventListener('submit', event => {
    event.preventDefault();
    action($('#setup-source-save'), async () => {
      const token = $('#setup-token').value.trim();
      if (!token && !getState().tokenConfigured) throw new Error('请先填写 PandaScore Token。');
      const result = await api('/api/setup/check', 'POST', token ? { pandascore_token: token } : {});
      if (!result.source?.ok) throw new Error(result.source?.message || '数据源验证失败，请检查 Token。');
      if (token) await api('/api/config', 'PATCH', { pandascore_token: token });
      $('#setup-token').value = '';
      await loadConfig();
      verified = true;
      showStep(1);
    });
  });
  $('#setup-group-form').addEventListener('submit', event => {
    event.preventDefault();
    action($('#setup-group-next'), async () => {
      const gid = $('#setup-group').value.trim();
      if (gid) {
        if (!/^\d+$/.test(gid) || BigInt(gid) < 1n) throw new Error('请填写 QQ 群号，不是群名称或机器人 QQ 号。');
        await api('/api/groups', 'POST', { gid });
        $('#setup-group').value = '';
        await loadConfig();
      }
      if (!getState().config.push_groups?.length) throw new Error('请至少添加一个接收群。');
      showStep(2);
    });
  });
  $('#setup-options-form').addEventListener('submit', event => {
    event.preventDefault();
    action($('#setup-options-save'), async () => {
      if (!event.currentTarget.reportValidity()) return;
      const values = {};
      document.querySelectorAll('[data-setup-key]').forEach(el => {
        values[el.dataset.setupKey] = el.type === 'checkbox' ? el.checked : el.type === 'number' ? Number(el.value) : el.value.trim();
      });
      values.min_tiers = [...document.querySelectorAll('[data-setup-tier]:checked')].map(el => el.value);
      values.daily_push_times = [...new Set($('#setup-daily-times').value.split(/[,，\s]+/).filter(Boolean))];
      if (values.daily_push_enabled && !values.daily_push_times.length) throw new Error('开启日报时，请填写至少一个推送时间，例如 08:00。');
      if (values.daily_push_times.some(time => !/^([01]\d|2[0-3]):[0-5]\d$/.test(time))) throw new Error('日报时间请使用 HH:MM，例如 08:00、18:00。');
      const changed = Object.fromEntries(Object.entries(values).filter(([key, value]) => JSON.stringify(value) !== JSON.stringify(getState().config[key])));
      if (Object.keys(changed).length) await api('/api/config', 'PATCH', changed);
      await loadConfig();
      await goToCheck();
    });
  });
  $('#setup-options-skip').addEventListener('click', event => action(event.currentTarget, goToCheck));
  $('#setup-recheck').addEventListener('click', event => action(event.currentTarget, goToCheck));
  $('#setup-finish').addEventListener('click', event => action(event.currentTarget, async () => {
    if (!verified || !checks?.source?.ok || !checks?.groups?.ok) throw new Error('请完成数据源和接收群配置，并通过检查后继续。');
    if (!$('#setup-bot-confirm').checked) throw new Error('请先确认 QQ 机器人在线，并且已经加入接收群。');
    if (checks.platform?.ok === false) throw new Error(checks.platform.message || '请先连接 QQ 机器人，再重新检查。');
    await api('/api/refresh', 'POST', {});
    $('#setup-wizard').hidden = true;
    $('#setup-open').hidden = false;
    $('#setup-token').value = '';
    activateTab('overview');
    toast('基础配置完成，正在获取最新赛程。暂无比赛时不会发送空日报。');
  }));
  return { sync, startIfNeeded() { sync(); if (!getState().tokenConfigured || !getState().config.push_groups?.length) open(); } };
}
