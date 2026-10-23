'use strict';
const $ = id => document.getElementById(id);
const headers = {'X-Whale-Setup':'whale-setup-v1','Content-Type':'application/json'};
let timer = null, qrVersion = '', qrObject = '', friendLoaded = false, polling = false, setupCompleted = false;
let featureState = null;
const resourceFields = [
  ['cpu_threads','合成与审查 CPU 线程数',1,8,1],['gpu_memory_fraction','日常朗读显存比例（软限制）',0.1,0.6,0.05],
  ['min_free_vram_mib','加载前最低可用显存（MiB）',4096,16384,512],
  ['idle_seconds','空闲退出声音工作进程（秒）',10,120,5],['cache_max_mib','声音磁盘缓存上限（MiB）',128,2048,128],
  ['cache_ttl_hours','声音磁盘缓存保留时间（小时）',1,168,1],
  ['singing_gpu_memory_fraction','清唱合成显存比例（软限制）',0.4,0.75,0.05],
  ['conversion_gpu_memory_fraction','清唱换声显存比例（软限制）',0.3,0.65,0.05],
  ['singing_min_free_vram_mib','清唱前最低可用显存（MiB）',6144,16384,512]
];
function renderFeatures(state, preset) {
  featureState = state;
  const hardware = state.hardware;
  const gpu = hardware.gpus.map(g=>`${g.name} ${(g.total_mib/1024).toFixed(1)} GiB（当前可用 ${(g.free_mib/1024).toFixed(1)} GiB）`).join('；') || '未检测到可用 NVIDIA 显卡';
  $('hardware-summary').textContent = `${hardware.cpu_name} · ${(hardware.ram_total_mib/1024).toFixed(1)} GiB 内存 · ${gpu}`;
  const recommended = preset || state.recommended;
  const settings = !preset && state.saved.settings ? state.saved.settings : recommended.settings;
  const selected = new Set(!preset && state.saved.selected ? state.saved.selected : recommended.selected);
  $('feature-summary').textContent = `共 ${state.catalog.length} 类功能 · 已选 ${selected.size} 项，展开调整与查看缺项`;
  $('performance-mode').value = !preset && state.saved.mode ? state.saved.mode : recommended.mode;
  $('hardware-advice').replaceChildren();
  for (const text of recommended.notes) { const li=document.createElement('li');li.textContent=text;$('hardware-advice').append(li); }
  $('resource-fields').replaceChildren();
  for (const [key,label,min,max,step] of resourceFields) {
    const wrapper=document.createElement('label');wrapper.textContent=label;
    const input=document.createElement('input');input.type='number';input.min=String(min);input.max=String(key.endsWith('min_free_vram_mib')?Math.max(max,...hardware.gpus.map(g=>g.total_mib)):max);input.step=String(step);input.value=String(settings[key]??recommended.settings[key]);input.dataset.resource=key;
    wrapper.append(input);$('resource-fields').append(wrapper);
  }
  $('feature-catalog').replaceChildren();
  for (const item of state.catalog) {
    const ready = state.readiness[item.id];
    const label=document.createElement('label');label.className='feature-item';
    const checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.dataset.feature=item.id;checkbox.checked=selected.has(item.id);label.append(checkbox,document.createTextNode(item.name));
    checkbox.addEventListener('change',()=>{$('feature-summary').textContent=`共 ${state.catalog.length} 类功能 · 已选 ${document.querySelectorAll('[data-feature]:checked').length} 项，展开调整与查看缺项`;});
    const badge=document.createElement('span');badge.className='feature-status'+(ready.ready?'':' pending');badge.textContent=ready.ready?'已具备配置基础':'待配置';label.append(badge);
    const detail=document.createElement('span');detail.className='feature-detail';detail.textContent=ready.detail;label.append(detail);
    const cost=document.createElement('span');cost.className='feature-cost';cost.textContent=item.cost;label.append(cost);
    const link=document.createElement('a');link.textContent='打开功能设置';link.href=item.page.startsWith('http')?item.page:'http://127.0.0.1:18766'+item.page;link.target='_blank';link.rel='noopener noreferrer';
    link.addEventListener('click',event=>{if(!setupCompleted){event.preventDefault();$('profile-result').textContent='先接通这份安装的基础聊天，再打开功能设置。';}});
    label.append(link);$('feature-catalog').append(label);
  }
}
function renderUpdate(state) {
  if (!state) return;
  $('update-status').textContent = `当前 v${state.current} · ${state.check_message}`;
  $('update-progress').textContent = state.progress.message || '';
  $('apply-update').hidden = !state.available;
  $('apply-update').disabled = !state.can_upgrade || ['preparing','waiting_for_tasks','switching','validating','rolling_back'].includes(state.progress.phase);
  $('release-link').hidden = !state.release;
  if (state.release) $('release-link').href = state.release.release_url;
}
async function request(url, method='GET', body) {
  const response = await fetch(url, {method, headers, credentials:'same-origin', cache:'no-store',
    body:body===undefined?undefined:JSON.stringify(body)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || '本机程序暂未就绪');
  return result;
}
function message(text, error=false) {
  $('connection').textContent = text;
  $('connection').classList.toggle('error', error);
}
async function loadFriends() {
  const data = await request('/friends');
  const selected = $('administrator').value;
  $('administrator').replaceChildren(new Option('请选择你的账号',''));
  for (const friend of data.friends) {
    $('administrator').add(new Option(`${friend.name}（${friend.qq}）`,friend.qq));
  }
  $('administrator').value = selected;
  friendLoaded = true;
}
async function poll() {
  if (polling) return;
  polling = true;
  try {
    const state = await request('/state');
    setupCompleted = state.completed;
    $('test-note').hidden = !state.test_instance;
    renderUpdate(state.updates);
    message(state.completed?'配置已经保存。':state.message, state.qq_runtime==='port_conflict');
    $('qq-status').textContent = state.message;
    if (state.key_saved) $('key-result').textContent = 'Key 已保存在本机，不会回显。';
    $('owner-section').hidden = !state.logged_in || state.completed;
    $('completed').hidden = !state.completed;
    if (state.completed) $('key-section').hidden = true;
    if (state.logged_in && !friendLoaded && !state.completed) await loadFriends();
    if (state.qr_ready && !state.logged_in && qrVersion!==state.qr_version) {
      const response = await fetch('/qr', {headers,credentials:'same-origin',cache:'no-store'});
      if (response.ok) {
        if (qrObject) URL.revokeObjectURL(qrObject);
        qrObject = URL.createObjectURL(await response.blob());
        $('qr').src = qrObject; $('qr').hidden = false; $('qr-placeholder').hidden = true;
        qrVersion = state.qr_version;
      }
    }
    if (state.logged_in) {
      $('qr').hidden = true; $('qr-placeholder').hidden = false; $('qr-placeholder').textContent = 'QQ 已登录 ✓';
    }
  } catch (error) { message(error.message,true); }
  finally { polling = false; }
}
$('toggle-key').addEventListener('click', () => {
  const visible = $('api-key').type === 'password';
  $('api-key').type = visible?'text':'password';
  $('toggle-key').textContent = visible?'隐藏':'显示';
  $('toggle-key').setAttribute('aria-pressed',String(visible));
});
$('key-form').addEventListener('submit', async event => {
  event.preventDefault(); $('save-key').disabled = true;
  $('key-result').textContent = '正在向 DeepSeek 官方验证…';
  try {
    await request('/key','POST',{api_key:$('api-key').value});
    $('api-key').value = ''; $('api-key').type = 'password';
    $('toggle-key').textContent = '显示'; $('toggle-key').setAttribute('aria-pressed','false');
    $('key-result').textContent = '验证通过，Key 已保存在本机。';
  } catch (error) { $('key-result').textContent = error.message; }
  finally { $('save-key').disabled = false; }
});
$('load-friends').addEventListener('click', async () => {
  try { await loadFriends(); } catch (error) { message(error.message,true); }
});
$('refresh-qq').addEventListener('click', poll);
$('check-update').addEventListener('click', async () => {
  $('check-update').disabled = true;
  try { renderUpdate(await request('/updates/check','POST',{})); }
  catch (error) { $('update-progress').textContent = error.message; }
  finally { $('check-update').disabled = false; }
});
$('apply-update').addEventListener('click', async () => {
  $('apply-update').disabled = true;
  try { renderUpdate(await request('/updates/apply','POST',{})); }
  catch (error) { $('update-progress').textContent = error.message; $('apply-update').disabled = false; }
});
$('load-preset').addEventListener('click', async () => {
  try {const preset=await request('/features/preview','POST',{mode:$('performance-mode').value});renderFeatures(featureState,preset);$('profile-result').textContent='已载入预设，点击保存才写入本机。';}
  catch(error){$('profile-result').textContent=error.message;}
});
$('refresh-hardware').addEventListener('click', async () => {
  $('refresh-hardware').disabled=true;
  try {renderFeatures(await request('/features/state'));$('profile-result').textContent='已重新读取已保存的配置。硬件状态最多缓存 60 秒。';}
  catch(error){$('profile-result').textContent=error.message;}
  finally{$('refresh-hardware').disabled=false;}
});
$('save-profile').addEventListener('click', async () => {
  $('save-profile').disabled=true;
  try {
    const selected=Array.from(document.querySelectorAll('[data-feature]:checked'),input=>input.dataset.feature);
    const overrides=Object.fromEntries(Array.from(document.querySelectorAll('[data-resource]'),input=>[input.dataset.resource,Number(input.value)]));
    const result=await request('/features/profile','POST',{mode:$('performance-mode').value,selected,overrides});
    $('profile-result').textContent=result.message;
  }catch(error){$('profile-result').textContent=error.message;}
  finally{$('save-profile').disabled=false;}
});
$('finish').addEventListener('click', async () => {
  if (!$('administrator').value) { $('finish-result').textContent = '请先选择你自己的管理账号。'; return; }
  $('finish').disabled = true;
  try {
    await request('/finish','POST',{administrator:$('administrator').value});
    $('finish-result').textContent = '已保存，正在启动聊天服务…';
    await poll();
  } catch (error) { $('finish-result').textContent = error.message; }
  finally { $('finish').disabled = false; }
});
async function connect() {
  const token = location.hash.slice(1);
  history.replaceState(null,'',location.pathname);
  try {
    if (token) {
      try {await request('/session','POST',{token});}
      catch (_) {await request('/state');} // An existing authenticated window can survive an old launch link.
    }
    await poll(); timer = setInterval(poll,3500);
    try {renderFeatures(await request('/features/state'));} catch(error){$('hardware-summary').textContent=error.message;}
  } catch (error) { message(error.message,true); }
}
window.addEventListener('pagehide', () => { if (timer) clearInterval(timer); if (qrObject) URL.revokeObjectURL(qrObject); });
connect();
