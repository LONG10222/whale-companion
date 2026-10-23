'use strict';
(() => {
  const el = id => document.getElementById(id);
  const fetchOnce = window.fetch.bind(window);
  let pending = null, busy = false, submitted = false, revision = 0, expires = 0;
  const invalidate = () => { revision++; pending = null; el('agree').checked = false; el('agree').disabled = true; el('connect').disabled = true; };
  const api = async (path, body) => {
    const response = await fetchOnce(path, {method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Local-Client':'whale-local-ui-v1'},body:JSON.stringify(body)});
    if (!response.ok) throw Error('请求失败或会话已过期；不会自动续期或重试。');
    return response.json();
  };
  el('saveKey').onclick = async () => {
    if (busy || !el('saveAgree').checked || !el('key').value) return;
    busy = true; invalidate(); el('saveKey').disabled = true;
    try {
      await api('/local/session', {});
      await api('/agent/config', {api_key:el('key').value});
      el('keyNotice').textContent = '已保存到本机，未向服务商验证；不会自动调用。';
    } catch (_) { el('keyNotice').textContent = '保存未确认，请人工检查本机配置；不会自动重试。'; }
    finally { el('key').value = ''; el('saveAgree').checked = false; el('saveKey').disabled = false; busy = false; }
  };
  el('inspect').onclick = async () => {
    if (busy || submitted) return;
    invalidate(); const version = revision; busy = true; el('inspect').disabled = true;
    try {
      await api('/local/session', {});
      const result = await api('/deployment/prepare', {});
      if (version !== revision) return;
      pending = result; expires = Date.now() + result.expires_in * 1000;
      el('preview').textContent = '机器人 QQ：' + result.preview.bot_id + '\n允许私聊：' + result.preview.allowed_private_ids.join(', ') + '\n将连接 QQ 并恢复已有登录；回复仍暂停。';
      el('agree').disabled = false; el('notice').textContent = '请核对，确认有效期 120 秒。';
    } catch (_) { el('notice').textContent = '未完成核对，请重新连接本机。没有提交 QQ 连接。'; }
    finally { busy = false; el('inspect').disabled = submitted; }
  };
  el('agree').onchange = () => { el('connect').disabled = !pending || !el('agree').checked || busy || submitted; };
  el('connect').onclick = async () => {
    if (!pending || !el('agree').checked || busy || submitted || Date.now() >= expires) return;
    const body = {nonce:pending.nonce,preview:pending.preview,confirmed:true};
    submitted = true; busy = true; invalidate(); el('inspect').disabled = true;
    try {
      const result = await api('/deployment/connect', body);
      el('notice').textContent = result.message; el('manage').hidden = false;
    } catch (_) { el('notice').textContent = '连接结果不确定。请人工核对 QQ 客户端，不要重复点击或重启来重试。'; }
    finally { busy = false; }
  };
  document.addEventListener('visibilitychange', () => { if (document.hidden) invalidate(); });
  window.addEventListener('pagehide', () => { invalidate(); el('key').value = ''; el('saveAgree').checked = false; });
  setInterval(() => { if (pending && Date.now() >= expires) { invalidate(); el('notice').textContent = '确认已过期，请重新核对。'; } }, 500);
})();
