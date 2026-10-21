'use strict';
const el=id=>document.getElementById(id);
let authenticated=false,busy=false,dirty=false,currentState=null;
const labels={draft:'待你审核',sent:'已发送',sending:'发送中，请勿重复点击',uncertain:'结果待核对，不会重发',failed:'本次未发出',dismissed:'已放弃',stale:'已失效',replaced:'已换新话题',skipped:'这次先不打扰'};
const fmt=at=>at?new Date(at*1000).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}):'暂无';
async function api(path,body){const r=await fetch('/qq/proactive'+path,{method:body===undefined?'GET':'POST',headers:{Authorization:'Bearer '+el('token').value,'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});const data=await r.json();if(!r.ok)throw Error(typeof data.detail==='string'?data.detail:'输入格式不正确。');return data;}
function setBusy(value){busy=value;for(const id of ['preview','regenerate','refresh','enabled','resume'])el(id).disabled=value||!authenticated;document.querySelectorAll('.draft button').forEach(b=>{b.disabled=value||b.dataset.blocked==='yes';});}
function node(tag,text,parent,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;if(parent)parent.append(n);return n;}
function render(s){currentState=s;dirty=false;el('enabled').checked=s.enabled;el('worker').textContent=s.service_running?'候选检查正在运行':'候选检查未启动';el('eligibility').textContent=s.eligibility.allowed?'当前条件允许：审核候选后，可以手动发送。':s.eligibility.reasons.join(' ');el('activity').textContent='上次聊天：'+fmt(s.eligibility.last_activity)+'；已空闲 '+Math.floor(s.eligibility.idle_minutes)+' 分钟。今天主动发送尝试：'+s.eligibility.daily_attempts+' / 1。';el('resume').hidden=!s.eligibility.contact_opt_out;
 const container=el('drafts');container.replaceChildren();if(!s.drafts.length)container.textContent='还没有候选。可以先点“准备一条候选”看看。';
 for(const d of s.drafts){const card=node('article',undefined,container,'draft');node('span',labels[d.state]||d.state,card,'badge');node('h3',d.topic||'本次话题',card);node('p',d.reason||'暂无话题说明。',card,'reason');node('p',fmt(d.at)+' · '+(d.sources.length?'参考 '+d.sources.length+' 条已有聊天':'结合人格和已认可记忆')+' · '+(d.state==='draft'?'候选保留至 '+fmt(d.expires):'历史记录'),card,'note');
  if(d.state!=='draft'){if(d.message)node('p',d.message,card);continue;}
  const label=node('label','开场内容',card),text=node('textarea',undefined,label);text.value=d.message;text.maxLength=160;text.rows=3;text.setAttribute('aria-label','开场内容');
  const save=node('button','保存改写',card,'secondary');save.disabled=true;save.dataset.blocked='yes';
  const send=node('button','发送给 owner',card);send.disabled=!d.can_send;send.dataset.blocked=d.can_send?'no':'yes';
  const discard=node('button','放弃这条',card,'secondary');
  const hint=node('p',d.can_send?'仅点击“发送给 owner”才会发出。':'当前仅能预览：'+s.eligibility.reasons.join(' '),card,'note');
  text.oninput=()=>{dirty=text.value.trim()!==d.message;save.disabled=!dirty||busy;save.dataset.blocked=dirty?'no':'yes';send.disabled=dirty||!d.can_send||busy;send.dataset.blocked=dirty||!d.can_send?'yes':'no';hint.textContent=dirty?'改写尚未保存，先保存再发送。':d.can_send?'仅点击“发送给 owner”才会发出。':'当前不符合发送条件。';};
  save.onclick=()=>action(async()=>{await api('/drafts/'+d.id+'/edit',{message:text.value});el('notice').textContent='改写已保存，未发送到 QQ。';});
  discard.onclick=()=>action(async()=>{await api('/drafts/'+d.id+'/dismiss',{});el('notice').textContent='已放弃这条候选。';});
  send.onclick=()=>action(async()=>{const r=await api('/send',{draft_id:d.id,approved_text:text.value});el('notice').textContent=r.receipt_received?'QQ 已返回消息编号，已提交给 owner。'+(r.memory_saved?'':'本地记忆写入失败，请查看接入记录。'):'请核对发送记录。';});
 }
 el('deliveries').textContent=s.deliveries.map(d=>fmt(d.at)+' · '+(labels[d.status]||d.status)+(d.error?' · '+d.error:'')).join('\n')||'尚未主动发送过。';
}
async function refresh(){if(!authenticated)return;render(await api('/state'));}
async function action(fn){if(busy)return;setBusy(true);try{await fn();await refresh();}catch(e){el('notice').textContent=e.message;try{await refresh();}catch{}}finally{setBusy(false);}}
el('connect').onclick=async()=>{try{const s=await api('/state');authenticated=true;render(s);setBusy(false);el('status').textContent='已连接。当前仅为 owner 准备候选，由你决定是否发送。';}catch(e){el('status').textContent=e.message;}};
el('file').onchange=async()=>{try{const c=JSON.parse(await el('file').files[0].text());el('token').value=c.api_token||'';el('connect').click();}catch{el('status').textContent='配置文件无法读取。';}};
el('enabled').onchange=()=>action(async()=>{await api('/config',{enabled:el('enabled').checked});el('notice').textContent=el('enabled').checked?'定时准备已启用，仍需你点击才发送。':'主动候选已暂停。';});
el('resume').onclick=()=>action(async()=>{await api('/contact/resume',{});el('notice').textContent='已恢复候选；其他发送条件仍然有效。';});
for(const [id,regenerate] of [['preview',false],['regenerate',true]])el(id).onclick=()=>action(async()=>{el('notice').textContent='正在结合聊天记忆准备候选……';const r=await api('/preview',{regenerate});el('notice').textContent=r.draft?.state==='draft'?'候选已准备，未发送到 QQ。':r.draft?.reason||'本次没有合适的开场，先不打扰。';});
el('refresh').onclick=()=>action(async()=>{el('notice').textContent='状态已刷新。';});
setInterval(async()=>{if(!authenticated||busy||dirty||document.activeElement?.tagName==='TEXTAREA')return;try{await refresh();setBusy(false);}catch(e){el('status').textContent=e.message;}},15000);
