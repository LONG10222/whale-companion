/* Uses native fetch once per explicit action; no authentication retry wrapper. */
(function () {
  'use strict';
  const el = id => document.getElementById(id);
  const nativeFetch = window.fetch.bind(window);
  async function api(path, body) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 25000);
    try {
      const response = await nativeFetch(path, {method:body === undefined ? 'GET' : 'POST',
        credentials:'same-origin', cache:'no-store', signal:controller.signal,
        headers:{'Content-Type':'application/json','X-Local-Client':'whale-local-ui-v1'},
        body:body === undefined ? undefined : JSON.stringify(body)});
      let data;
      try { data = await response.json(); } catch (_) { throw Error('响应无法确认，请手动检查。'); }
      if (!response.ok) {
        const error = Error(typeof data.detail === 'string' ? data.detail : '请求失败，请核对本机连接。');
        error.status = response.status; throw error;
      }
      return data;
    } finally { clearTimeout(timer); }
  }
  let lastPreview = null;
  const manager = WhaleSocial.createController(api, () => Date.now()/1000, render);
  function render(s) {
    el('notice').textContent = s.notice;
    el('notice').dataset.tone = s.uncertain ? 'warning' : 'normal';
    el('connection').textContent = !s.connected ? '本机未连接' : s.ready ? '本机已连接 · QQ账号匹配' : '本机已连接 · QQ尚未就绪';
    el('account').textContent = s.account || '尚未核对';
    el('socialState').textContent = s.enabled ? '本次已启用' : '安全关闭';
    el('socialState').dataset.on = String(s.enabled);
    el('connectSession').disabled = s.busy;
    el('enableSocial').disabled = s.busy || !s.connected || !s.ready || s.enabled;
    el('disableSocial').disabled = s.busy || !s.connected || !s.enabled;
    el('loadRequests').disabled = s.busy || !s.connected || !s.ready || !s.enabled;
    el('preparePost').disabled = s.busy || !s.connected || !s.ready || !s.enabled || s.uncertain;
    document.querySelectorAll('[data-request-action]').forEach(button => {
      button.disabled = s.busy || !s.connected || !s.ready || !s.enabled || s.uncertain;
    });
    el('manualCheck').hidden = !s.uncertain; el('manualCheck').disabled = s.busy;
    const draft = s.preview && s.preview.preview;
    const summary = draft || s.lastAttempt;
    el('review').hidden = !summary;
    if (s.preview !== lastPreview) {
      el('reviewChecked').checked = false; lastPreview = s.preview;
    }
    if (summary) {
      el('reviewAccount').textContent = summary.bot;
      el('reviewKind').textContent = summary.kind === 'qzone_text' ? '发布纯文字空间动态' : summary.approve ? '接受好友申请' : '拒绝好友申请';
      el('reviewTarget').textContent = summary.kind === 'qzone_text' ? summary.audience_label + ' (' + summary.audience + ')' : '申请人 QQ：' + summary.user_id;
      el('reviewContent').textContent = summary.kind === 'qzone_text' ? summary.content : '备注：' + (summary.remark || '无');
    }
    el('reviewAgreement').hidden = !draft;
    el('confirmAction').hidden = !draft;
    el('cancelReview').hidden = !draft;
    el('reviewChecked').disabled = !draft || s.busy;
    el('confirmAction').disabled = !draft || s.busy || !el('reviewChecked').checked || s.uncertain;
    el('cancelReview').disabled = s.busy;
    el('countdown').textContent = s.preview ? '确认剩余 ' + Math.max(0,Math.ceil(s.preview.expires-Date.now()/1000)) + ' 秒' : s.phase === 'sending' ? '已提交一次，等待回执' : '本次确认已结束';
    el('charCount').textContent = String(Array.from(el('postContent').value).length) + ' / 2000';
  }
  function invalidate() { manager.invalidate('输入已更新，下一次操作须重新生成预览。'); }
  el('postContent').addEventListener('input', invalidate);
  el('audience').addEventListener('change', invalidate);
  el('connectSession').addEventListener('click', () => {
    el('requestList').replaceChildren();
    manager.connect();
  });
  el('enableSocial').addEventListener('click', () => manager.configure(true));
  el('disableSocial').addEventListener('click', () => {
    el('requestList').replaceChildren(); manager.configure(false);
  });
  el('postForm').addEventListener('submit', event => {
    event.preventDefault();
    if (!el('postContent').value.trim() || !el('audience').value) {
      manager.invalidate('请填写正文，并明确选择可见范围。'); return;
    }
    manager.prepare({kind:'qzone_text',content:el('postContent').value,audience:el('audience').value});
  });
  el('reviewChecked').addEventListener('change', () => render(manager.state));
  el('confirmAction').addEventListener('click', () => manager.confirm(el('reviewChecked').checked));
  el('cancelReview').addEventListener('click', () => manager.invalidate('已取消本次预览，未执行。'));
  el('manualCheck').addEventListener('click', () => {
    el('postContent').value=''; el('audience').value=''; el('requestList').replaceChildren();
    manager.afterManualCheck();
  });
  el('loadRequests').addEventListener('click', async () => {
    const rows = await manager.requests();
    const list = el('requestList'); list.replaceChildren();
    if (!rows.length) {
      const empty = document.createElement('p'); empty.className='muted';
      empty.textContent='暂无可处理申请。只显示本次连接收到且未过期的申请，不代表 QQ 没有历史申请。';
      list.append(empty); return;
    }
    rows.forEach(row => {
      const card = document.createElement('article'); card.className='request';
      const title = document.createElement('h3'); title.textContent='QQ ' + String(row.user_id);
      const comment = document.createElement('p'); comment.className='request-comment';
      comment.textContent=String(row.comment || '未填写申请说明');
      const note = document.createElement('p'); note.className='muted'; note.textContent='申请说明属于对方内容，不是你的授权。';
      const label = document.createElement('label'); label.textContent='接受后的备注（可选）';
      const remark = document.createElement('input'); remark.type='text'; remark.maxLength=60; remark.autocomplete='off';
      remark.addEventListener('input',invalidate); label.append(remark);
      const actions = document.createElement('div'); actions.className='actions';
      [['预览接受',true],['预览拒绝',false]].forEach(([text,approve]) => {
        const button=document.createElement('button'); button.type='button'; button.textContent=text;
        button.dataset.requestAction='true'; button.className=approve?'secondary':'quiet';
        button.addEventListener('click', () => manager.prepare({kind:'friend_request',
          request_id:row.id,approve,remark:approve ? remark.value : ''}));
        actions.append(button);
      });
      card.append(title,comment,note,label,actions); list.append(card);
    });
    render(manager.state);
  });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) manager.invalidate('离开页面后旧确认已清除，请重新预览。');
  });
  window.addEventListener('pagehide', () => manager.invalidate());
  setInterval(() => manager.tick(), 1000);
  render(manager.state);
})();
