'use strict';
(() => {
  if (window.whaleUpdateUI) return;
  window.whaleUpdateUI = true;
  const $ = id => document.getElementById(id);
  const fullPage = Boolean($('check-version'));
  const active = ['preparing','waiting_for_tasks','switching','validating','rolling_back'];
  async function call(path, method='GET') {
    const response = await fetch(path,{method,credentials:'same-origin',cache:'no-store',headers:{'X-Local-Client':'whale-local-ui-v1','Content-Type':'application/json'},body:method==='POST'?'{}':undefined});
    const state = await response.json();
    if (!response.ok) throw new Error(state.detail || '暂时无法读取升级状态');
    return state;
  }
  function render(state) {
    if (fullPage) {
      $('update-title').textContent = `当前版本 v${state.current}`;
      $('update-state').textContent = state.check_message;
      $('update-detail').textContent = state.progress.message || '';
      $('upgrade').hidden = !state.available;
      $('upgrade').disabled = !state.can_upgrade || active.includes(state.progress.phase);
      $('release-notes').hidden = !state.release;
      if (state.release) $('release-notes').href = state.release.release_url;
      $('update-owner-note').textContent = state.needs_recovery?'上次升级已中断，请先恢复本机备份。':state.can_upgrade?'':'此安装尚未由新版安装器接管，仅提供新版提醒；不会覆盖现有个人实例。';
    } else if (state.available) {
      let banner = $('whale-update-notice');
      if (!banner) { banner=document.createElement('p');banner.id='whale-update-notice';banner.setAttribute('role','status');document.body.prepend(banner); }
      banner.replaceChildren();
      const link=document.createElement('a');link.href='/updates';link.textContent=`鲸娅有新版 ${state.release.tag}，点击查看与升级`;banner.append(link);
    }
  }
  async function refresh() { try {render(await call('/updates/state'));} catch (_) {} }
  if (fullPage) {
    $('check-version').addEventListener('click',async()=>{ $('check-version').disabled=true;try {render(await call('/updates/check','POST'));}catch(error){$('update-detail').textContent=error.message;}finally{$('check-version').disabled=false;} });
    $('upgrade').addEventListener('click',async()=>{ $('upgrade').disabled=true;try {render(await call('/updates/apply','POST'));}catch(error){$('update-detail').textContent=error.message;$('upgrade').disabled=false;} });
  }
  setTimeout(refresh,1500);
  const timer=setInterval(refresh,fullPage?5000:60000);
  window.addEventListener('pagehide',()=>clearInterval(timer),{once:true});
})();
