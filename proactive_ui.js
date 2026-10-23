'use strict';
const el=id=>document.getElementById(id);
let authenticated=false,busy=false,dirty=false,currentState=null;
const labels={draft:'待你审核',sent:'已发送',sending:'发送中，请勿重复点击',uncertain:'结果待核对，不会重发',failed:'本次未发出',dismissed:'已放弃',stale:'已失效',replaced:'已换新话题',skipped:'这次先不打扰'};
const fmt=at=>at?new Date(at*1000).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}):'暂无';
async function api(path,body){const r=await fetch('/qq/proactive'+path,{method:body===undefined?'GET':'POST',headers:{Authorization:'Bearer '+el('token').value,'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});const data=await r.json();if(!r.ok)throw Error(typeof data.detail==='string'?data.detail:'输入格式不正确。');return data;}
function setBusy(value){busy=value;for(const id of ['preview','regenerate','refresh','enabled','mode','newsEnabled','biliEnabled','resume'])el(id).disabled=value||!authenticated;document.querySelectorAll('.draft button').forEach(b=>{b.disabled=value||b.dataset.blocked==='yes';});}
function node(tag,text,parent,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;if(parent)parent.append(n);return n;}
function render(s){currentState=s;dirty=false;el('enabled').checked=s.enabled;el('mode').value=s.mode;el('newsEnabled').checked=s.news_enabled;el('modeBadge').textContent=!s.enabled?'主动聊天已暂停':s.automatic_send?'自动发送 · DeepSeek 自己选题':'只预览 · 由你点发送';el('topicPolicy').textContent=s.topic_policy;el('worker').textContent=s.service_running?'每分钟检查正在运行':'定时检查未启动';el('eligibility').textContent=s.eligibility.allowed?(s.automatic_send?'当前允许自动聊天，由 DeepSeek 决定是否开口和本轮话题。':'当前条件允许：审核候选后，可以手动发送。'):s.eligibility.reasons.join(' ');el('activity').textContent='上次聊天：'+fmt(s.eligibility.last_activity)+'；已空闲 '+Math.floor(s.eligibility.idle_minutes)+' 分钟。今天主动发送尝试：'+s.eligibility.daily_attempts+' / 1。';el('resume').hidden=!s.eligibility.contact_opt_out;const news=s.news_status||{};el('newsStatus').textContent=!s.news_enabled?'当天资讯未启用，仍可自由聊日常和兴趣。':'当天资讯池：'+(news.cached_today_items||0)+' 条，来源：中新网、IT之家。'+(news.fetched_at?'最近更新 '+fmt(news.fetched_at)+'。':'选题时按需读取。')+'她可以选用，也可以聊别的；读取失败时不编造热点。';
  el('biliEnabled').checked=s.bilibili_enabled;const bili=s.bilibili_status||{};el('biliStatus').textContent=s.bilibili_enabled?'B站话题已加入候选；真实读过 '+(bili.reviewed_videos||0)+' 条，今天自主阅读 '+(bili.automatic_reads_today||0)+' / 1 轮。由她决定是否分享，仍与普通主动聊天共用每天1次。':'B站未加入主动话题；可在B站页面设置。';
  const container=el('drafts');container.replaceChildren();if(!s.drafts.length)container.textContent='还没有候选。可以先点“准备一条候选”看看。';
 for(const d of s.drafts){const card=node('article',undefined,container,'draft');node('span',d.state==='draft'?(d.automatic_candidate?'定时选题 · 满足条件时自动发':'人工预览 · 由你点发送'):labels[d.state]||d.state,card,'badge');node('h3',d.topic||'本次话题',card);node('p',d.reason||'暂无话题说明。',card,'reason');node('p',fmt(d.at)+' · '+(d.sources.length?'参考 '+d.sources.length+' 条已有聊天':'自由选题，结合人格和已认可记忆')+' · '+(d.state==='draft'?'候选保留至 '+fmt(d.expires):'历史记录'),card,'note');
  for(const source of d.news_sources||[]){const line=node('p',undefined,card,'note'),link=node('a',source.publisher+' · '+source.title,line);link.href=source.url;link.target='_blank';link.rel='noreferrer noopener';}
   for(const source of d.video_sources||[]){const line=node('p',undefined,card,'note'),link=node('a','B站 · '+source.title,line);link.href=source.url;link.target='_blank';link.rel='noreferrer noopener';}
  if(d.state!=='draft'){if(d.message)node('p',d.message,card);continue;}
  const label=node('label','开场内容',card),text=node('textarea',undefined,label);text.value=d.message;text.maxLength=160;text.rows=3;text.setAttribute('aria-label','开场内容');
  const save=node('button','保存改写',card,'secondary');save.disabled=true;save.dataset.blocked='yes';
  const send=node('button','发送给 管理员',card);send.disabled=!d.can_send;send.dataset.blocked=d.can_send?'no':'yes';
  const discard=node('button','放弃这条',card,'secondary');
  const hint=node('p',d.automatic_candidate&&s.automatic_send?'定时选题可自动发出；保存改写会改成人工预览。':d.can_send?'仅点击“发送给 管理员”才会发出。':'当前仅能预览：'+s.eligibility.reasons.join(' '),card,'note');
  text.oninput=()=>{dirty=text.value.trim()!==d.message;save.disabled=!dirty||busy;save.dataset.blocked=dirty?'no':'yes';send.disabled=dirty||!d.can_send||busy;send.dataset.blocked=dirty||!d.can_send?'yes':'no';hint.textContent=dirty?'改写尚未保存，先保存再发送。':d.can_send?'仅点击“发送给 管理员”才会发出。':'当前不符合发送条件。';};
  save.onclick=()=>action(async()=>{await api('/drafts/'+d.id+'/edit',{message:text.value});el('notice').textContent='改写已保存，未发送到 QQ。';});
  discard.onclick=()=>action(async()=>{await api('/drafts/'+d.id+'/dismiss',{});el('notice').textContent='已放弃这条候选。';});
  send.onclick=()=>action(async()=>{const r=await api('/send',{draft_id:d.id,approved_text:text.value});el('notice').textContent=r.receipt_received?'QQ 已返回消息编号，已提交给 管理员。'+(r.memory_saved?'':'本地记忆写入失败，请查看接入记录。'):'请核对发送记录。';});
 }
 el('deliveries').textContent=s.deliveries.map(d=>fmt(d.at)+' · '+(d.mode==='automatic'?'DeepSeek 自主选题 · 自动发送':'人工确认发送')+' · '+(labels[d.status]||d.status)+(d.error?' · '+d.error:'')).join('\n')||'尚未主动发送过。';
}
async function refresh(){if(!authenticated)return;render(await api('/state'));}
async function action(fn){if(busy)return;setBusy(true);try{await fn();await refresh();}catch(e){el('notice').textContent=e.message;try{await refresh();}catch{}}finally{setBusy(false);}}
el('connect').onclick=async()=>{try{const s=await api('/state');authenticated=true;render(s);setBusy(false);el('status').textContent='已连接。接收人固定为 管理员；自动模式下由 DeepSeek 自己选题，预览模式由你点发送。';}catch(e){el('status').textContent=e.message;}};
el('file').onchange=async()=>{try{const c=JSON.parse(await el('file').files[0].text());el('token').value=c.api_token||'';el('connect').click();}catch{el('status').textContent='配置文件无法读取。';}};
for(const id of ['enabled','mode','newsEnabled','biliEnabled'])el(id).onchange=()=>action(async()=>{await api('/config',{enabled:el('enabled').checked,mode:el('mode').value,news_enabled:el('newsEnabled').checked,bilibili_enabled:el('biliEnabled').checked});el('notice').textContent=!el('enabled').checked?'主动聊天已暂停。':el('mode').value==='automatic'?'已启用自动发送。满足条件后，她会自己选题并发给 管理员。':'已切换为只预览，需要你点击才发送。';});
el('resume').onclick=()=>action(async()=>{await api('/contact/resume',{});el('notice').textContent='已恢复候选；其他发送条件仍然有效。';});
for(const [id,regenerate] of [['preview',false],['regenerate',true]])el(id).onclick=()=>action(async()=>{el('notice').textContent='正在结合聊天记忆准备候选……';const r=await api('/preview',{regenerate});el('notice').textContent=r.draft?.state==='draft'?'候选已准备，未发送到 QQ。':r.draft?.reason||'本次没有合适的开场，先不打扰。';});
el('refresh').onclick=()=>action(async()=>{el('notice').textContent='状态已刷新。';});
setInterval(async()=>{if(!authenticated||busy||dirty||document.activeElement?.tagName==='TEXTAREA')return;try{await refresh();setBusy(false);}catch(e){el('status').textContent=e.message;}},15000);
