/* Local owner login panel. Passwords are cleared before the network response. */
(()=>{
 const get=id=>document.getElementById(id);let busy=false,polling=false,challengeKey='',captcha=null,deviceURL='',deviceKey='';
 function passwordVisibility(visible){
  get('accountPassword').type=visible?'text':'password';
  const button=get('toggleAccountPassword');button.textContent=visible?'隐藏':'显示';button.setAttribute('aria-pressed',String(visible));button.setAttribute('aria-label',(visible?'隐藏':'显示')+' QQ 密码');
 }
 async function request(path,body,method){
  const r=await fetch(path,{method:method||(body===undefined?'GET':'POST'),headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
  if(!r.ok){const d=await r.json();throw Error(d.detail||'QQ登录尚未完成。')}return r;
 }
 function script(src){return new Promise((resolve,reject)=>{if(window.TencentCaptcha)return resolve();const s=document.createElement('script');s.src=src;s.onload=resolve;s.onerror=reject;document.head.append(s)})}
 async function verify(data){
  if(challengeKey===JSON.stringify(data.captcha))return;
  challengeKey=JSON.stringify(data.captcha);
  try{
   try{await script('https://captcha.gtimg.com/TCaptcha.js')}catch{await script('https://ssl.captcha.qq.com/TCaptcha.js')}
   const p=data.captcha;
   captcha=new window.TencentCaptcha(p.appid,async answer=>{
    if(answer.ret!==0){get('accountNote').textContent='QQ安全验证未完成，点击“继续 QQ 验证”可重试。';return}
    try{const r=await request('/qq/login/captcha',{ticket:answer.ticket,randstr:answer.randstr,challenge_id:data.challenge_id});await render(await r.json())}catch(e){get('accountNote').textContent=e.message}
   },{type:'popup',showHeader:false,uin:p.uin,sid:p.sid,login_appid:p.login_appid,enableAged:true});captcha.show();
  }catch{challengeKey='';get('accountNote').textContent='QQ安全验证组件未能加载，请点击继续验证，或改用扫码登录。'}
 }
 async function render(data){
  get('accountNote').textContent=(data.message||'保存机器人小号后，启动时会自动尝试恢复登录。')+(data.challenge&&data.expires_in!=null?'（本次验证剩余 '+data.expires_in+' 秒）':'');
  const drops=data.login_health?.recent_disconnects||[];
  get('loginHealth').textContent=drops.length?'最近 QQ 下线记录：'+drops.slice(-3).map(d=>new Date(d.at*1000).toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai'})+' '+d.message).join('；'):'';
  const selected=get('savedAccount').value;get('savedAccount').replaceChildren(new Option('选择已保存的小号，或填写新账号',''));
  for(const account of data.accounts||[]){const label=(account.label?account.label+' · ':'')+account.uin+(account.password_saved?'（已保存密码）':'（快速登录）');get('savedAccount').add(new Option(label,account.uin))}
  if([...get('savedAccount').options].some(o=>o.value===selected))get('savedAccount').value=selected;
  if(!get('accountUin').value)get('accountUin').value=data.default||data.expected_bot||'';
  get('continueVerify').hidden=!['captcha','new_device'].includes(data.phase);
  if(data.phase==='captcha'){get('deviceQr').hidden=true;await verify(data)}
  else if(data.phase==='new_device'&&data.device_qr_ready){
   if(!deviceURL||deviceKey!==data.challenge_id){const r=await request('/qq/login/device-qr');if(deviceURL)URL.revokeObjectURL(deviceURL);deviceURL=URL.createObjectURL(await r.blob());deviceKey=data.challenge_id;get('deviceQr').src=deviceURL}
   get('deviceQr').hidden=false;
  }else{
   get('deviceQr').hidden=true;if(deviceURL){URL.revokeObjectURL(deviceURL);deviceURL=''}
   challengeKey='';if(captcha){captcha.destroy();captcha=null}
  }
 }
 async function refresh(){if(polling||busy)return;polling=true;try{await render(await(await request('/qq/login/accounts')).json())}catch(e){get('accountNote').textContent=e.message}finally{polling=false}}
 async function login(){
  if(busy)return;busy=true;get('accountLogin').disabled=true;
  const body={uin:get('accountUin').value.trim(),password:get('accountPassword').value,label:get('accountLabel').value.trim(),remember:get('rememberAccount').checked};get('accountPassword').value='';passwordVisibility(false);
  get('accountNote').textContent='正在登录；更换账号时会先暂停原小号的回复……';
  try{await render(await(await request('/qq/login/account',body)).json())}catch(e){get('accountNote').textContent=e.message}finally{body.password='';busy=false;get('accountLogin').disabled=false}
 }
 function init(){
  if(!get('accountLogin'))return;
  passwordVisibility(false);
  get('toggleAccountPassword').onclick=()=>passwordVisibility(get('accountPassword').type==='password');
  get('accountLogin').onclick=login;
  get('recoverQr').onclick=async()=>{
   if(busy)return;busy=true;get('recoverQr').disabled=true;
   try{
    const r=await request('/qq/login/use-qr',{}),state=await r.json();
    if(captcha){captcha.destroy();captcha=null}challengeKey='';get('deviceQr').hidden=true;
    get('accountNote').textContent=state.message||'已切换到备用二维码，请用机器人小号扫码并在手机确认。';
    if(typeof qr==='function')await qr(false);
   }catch(e){get('accountNote').textContent=e.message}finally{busy=false;get('recoverQr').disabled=false;refresh()}
  };
  get('savedAccount').onchange=()=>{if(get('savedAccount').value)get('accountUin').value=get('savedAccount').value;get('accountPassword').value='';passwordVisibility(false);get('accountLabel').value=''};
  get('continueVerify').onclick=()=>{if(captcha)captcha.show();else{challengeKey='';refresh()}};
  get('forgetAccount').onclick=async()=>{try{await render(await(await request('/qq/login/accounts/'+encodeURIComponent(get('accountUin').value.trim()),undefined,'DELETE')).json());get('accountNote').textContent='此小号的本机密码已清除，聊天记录保留。'}catch(e){get('accountNote').textContent=e.message}};
  refresh();setInterval(refresh,3000);
 }
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});else init();
})();
