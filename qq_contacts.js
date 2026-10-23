(()=>{
 const el=id=>document.getElementById(id);let data=null,loading=false,refreshing=false;
 const roleNames={user:'普通用户',plus:'Plus',admin:'管理员'};
 async function api(path,body){
  const r=await fetch(path,{method:body===undefined?'GET':'POST',headers:{Authorization:'Bearer '+el('token').value,'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
  if(!r.ok){let d={};try{d=await r.json()}catch{}throw Error(d.detail||'联系人操作未完成。')}return r.json();
 }
 function permissionTable(){
  const rows=[['models','Codex工作模型'],['reasoning','推理强度'],['speed','运行速度'],['thread_cap','工作对话上限'],['query_limit','公开网页查询'],['abilities','可以做什么'],['computer_access','电脑与管理权限'],['concurrency','队列与并发']];
  el('permissions').replaceChildren();
  for(const [key,label] of rows){const tr=document.createElement('tr'),th=document.createElement('th');th.scope='row';th.textContent=label;tr.append(th);
   for(const role of data.permissions){const td=document.createElement('td'),value=role[key];td.textContent=Array.isArray(value)?value.join('；'):key==='thread_cap'?'最多 '+value+' 个':String(value);tr.append(td)}el('permissions').append(tr)}
 }
 function contacts(){
  if(!data)return;const query=el('filter').value.trim().toLocaleLowerCase(),role=el('roleFilter').value;
  const rows=data.contacts.filter(c=>(role==='all'||(role==='bot'?c.qq===data.bot_qq:c.qq!==data.bot_qq&&c.role===role))&&[c.qq,c.nickname,c.remark,c.preferred_name,c.display_name].some(v=>String(v||'').toLocaleLowerCase().includes(query)));
  el('count').textContent='显示 '+rows.length+' 位联系人，共读取 '+data.contacts.length+' 位；当前最多显示 '+data.visible_limit+' 位。';el('contacts').replaceChildren();
  for(const c of rows){const card=document.createElement('article'),head=document.createElement('div'),name=document.createElement('h3'),badge=document.createElement('span'),details=document.createElement('p'),source=document.createElement('p');head.className='person-head';name.textContent=c.display_name;badge.className='badge';badge.textContent=c.qq===data.bot_qq?'机器人账号':roleNames[c.role];head.append(name,badge);details.className='note';details.textContent='QQ '+c.qq+' · QQ昵称：'+(c.nickname||'暂未取得')+(c.remark?' · 备注：'+c.remark:'');source.className='note';source.textContent=c.preferred_name?'当前使用自定义称呼；身份和权限仍按此QQ号判断。':'当前使用'+(c.nickname?'QQ昵称。':'QQ号，取得昵称后会自动显示。');card.append(head,details,source);if(c.qq===data.bot_qq){source.textContent='这是当前机器人自己的QQ账号，不作为普通用户或Plus管理。';el('contacts').append(card);continue;}
   const form=document.createElement('div'),label=document.createElement('label'),input=document.createElement('input'),save=document.createElement('button'),reset=document.createElement('button');form.className='name-form';label.textContent='自定义称呼';input.type='text';input.maxLength=64;input.value=c.preferred_name||'';input.placeholder=c.nickname||'留空使用QQ昵称';input.autocomplete='off';input.setAttribute('aria-label','QQ '+c.qq+' 的自定义称呼');label.append(input);save.textContent='保存称呼';reset.textContent='恢复 QQ 昵称';reset.className='secondary';
   const rename=async name=>{save.disabled=reset.disabled=true;try{const updated=await api('/qq/contacts/'+encodeURIComponent(c.qq)+'/name',{name});el('notice').textContent='已保存：'+updated.display_name+'（QQ '+updated.qq+'）。权限没有改变。';await load()}catch(e){el('notice').textContent=e.message}finally{save.disabled=reset.disabled=false}};
   save.onclick=()=>rename(input.value);reset.onclick=()=>rename('');form.append(label,save,reset);card.append(form);
   if(c.role!=='admin'&&c.qq!==data.bot_qq){const toggle=document.createElement('button');toggle.className='secondary';toggle.textContent=c.role==='plus'?'取消 Plus':'设为 Plus';toggle.onclick=()=>setPlus(c.qq,c.role!=='plus',toggle);card.append(toggle)}el('contacts').append(card)}
  if(!rows.length)el('contacts').textContent='没有匹配的联系人。';
 }
 async function load(){if(loading)return;loading=true;try{data=await api('/qq/contacts/state');el('status').textContent=data.online?'QQ在线 · 身份唯一主键：QQ号':'QQ暂未在线 · 可管理已保存联系人，昵称刷新需等QQ接通';el('groupPolicy').textContent=(data.groups_enabled?'已启用授权群；':'群聊当前未启用；')+data.group_trigger+'。群内称呼与昵称都不能授予管理员身份。';permissionTable();contacts()}catch(e){el('notice').textContent=e.message}finally{loading=false}}
 async function setPlus(peer,enabled,button){if(button)button.disabled=true;try{const r=await api('/qq/contacts/'+encodeURIComponent(peer)+'/plus',{enabled});el('notice').textContent=r.display_name+'（QQ '+r.qq+'）已'+(enabled?'设为Plus，免除查询次数和单次时长限制。':'恢复普通用户，按15分钟2次、单次5分钟查询。')+(r.nickname_resolution?.reason?' '+r.nickname_resolution.reason:'');await load()}catch(e){el('notice').textContent=e.message}finally{if(button)button.disabled=false}}
 el('connect').onclick=load;el('filter').oninput=contacts;el('roleFilter').onchange=contacts;
 el('grantPlus').onclick=()=>setPlus(el('plusPeer').value.trim(),true,el('grantPlus'));el('revokePlus').onclick=()=>setPlus(el('plusPeer').value.trim(),false,el('revokePlus'));
 el('refreshNames').onclick=async()=>{if(refreshing)return;refreshing=true;el('refreshNames').disabled=true;try{const r=await api('/qq/contacts/refresh',{});el('notice').textContent='已刷新 '+r.refreshed+' 位联系人的QQ昵称，称呼和权限没有改变。'+r.warnings.join('');await load()}catch(e){el('notice').textContent=e.message}finally{refreshing=false;el('refreshNames').disabled=false}};
})();
