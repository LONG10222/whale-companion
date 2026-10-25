'use strict';
(()=>{
 const el=id=>document.getElementById(id);let filled=false,busy=false;
 const labels={comment:'评论',dm:'主人私信',live:'直播弹幕',baseline:'已建立基线',empty:'没有新消息',hit:'读到新消息',failed:'读取失败，游标保留',baseline_failed:'尚未建立基线',disabled:'已暂停',cooldown:'等待下次检查',outside_window:'当前在作息外'};
 function node(tag,text){const n=document.createElement(tag);n.textContent=text;return n;}
 async function api(path,body){const r=await fetch('/qq/bilibili'+path,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});const data=await r.json();if(!r.ok)throw Error(data.detail||'本次未完成');return data;}
 async function refresh(){
  const s=await api('/listeners/state');if(!filled){el('owner').value=s.config.owner_mid;el('room').value=s.config.room_id;for(const k of ['comment','dm','live'])el(k).checked=s.config[k];el('allDay').checked=s.config.all_day;el('interval').value=s.config.interval_seconds;filled=true;}
  el('health').textContent=s.health.map(h=>(labels[h.channel]||h.channel)+'：'+(labels[h.state]||h.state)+' · 连续失败 '+h.failures).join('\n')||'尚未开启读取。';
  el('events').replaceChildren();for(const e of s.events){const box=node('div','');box.className='entry';box.append(node('strong',(labels[e.channel]||e.channel)+' · mid '+e.sender),node('p',e.text),node('p','目标坐标：'+e.coordinates));const d=s.drafts.find(d=>d.event_id===e.id);if(d){box.append(node('pre',d.text),node('p','草稿状态：'+d.state));if(d.state==='draft'){const b=node('button','确认发送这份原文');b.onclick=()=>action(async()=>{const r=await api('/listeners/confirm',{draft_id:d.id,approved_text:d.text});el('status').textContent=r.receipt_received?'已取得平台消息编号。':'请核对发送状态。';});box.append(b);}}else{const b=node('button','仅准备回复草稿');b.onclick=()=>action(async()=>{await api('/listeners/preview',{event_id:e.id});el('status').textContent='草稿已准备，没有发送。';});box.append(b);}el('events').append(box);}
  const t=await api('/traffic/state');el('traffic').textContent=t.paused?(t.manual?'平台要求处理登录或验证码。':'平台限制暂停至 '+new Date(t.until*1000).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})):'请求保护正常。';
 }
 async function action(fn){if(busy)return;busy=true;try{await fn();}catch(e){el('status').textContent=e.message;}finally{busy=false;try{await refresh();}catch(e){el('status').textContent=e.message;}}}
 document.addEventListener('DOMContentLoaded',()=>{
  el('save').onclick=()=>action(async()=>{await api('/listeners/config',{owner_mid:el('owner').value.trim(),room_id:el('room').value.trim(),comment:el('comment').checked,dm:el('dm').checked,live:el('live').checked,all_day:el('allDay').checked,interval_seconds:Number(el('interval').value)});el('status').textContent='已保存。新渠道回复仍先审核。';});
  for(const b of document.querySelectorAll('[data-poll]'))b.onclick=()=>action(async()=>{const r=await api('/listeners/poll/'+b.dataset.poll,{});el('status').textContent=(labels[r.state]||r.state)+' · '+r.count+' 条';});
  el('resume').onclick=()=>action(async()=>{await api('/traffic/resume',{});el('status').textContent='已记录人工处理，限流等待仍保留。';});
  refresh().then(()=>el('status').textContent='已连接本机。').catch(e=>el('status').textContent=e.message);setInterval(()=>{if(!busy)refresh().catch(()=>{});},15000);
 },{once:true});
})();
