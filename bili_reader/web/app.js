'use strict';
const tokenInUrl=new URLSearchParams(location.hash.slice(1)).get('token');
if(tokenInUrl){sessionStorage.setItem('whaleBiliToken',tokenInUrl);history.replaceState(null,'',location.pathname);}
const token=sessionStorage.getItem('whaleBiliToken')||'';
const $=id=>document.getElementById(id);
let qrUrl=null,qrLoadedAt=0,autoStarted=false,lastResult='',lastWorkState='';
async function api(path,method='GET',body){
  const response=await fetch(path,{method,headers:{Authorization:'Bearer '+token,...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined,cache:'no-store'});
  if(!response.ok){const text=await response.json().catch(()=>({error:'本机程序暂时不可用'}));throw Error(text.error||'请求未完成');}
  return response;
}
function text(tag,value,className){const node=document.createElement(tag);node.textContent=value||'';if(className)node.className=className;return node;}
function renderResult(result){
  if(!result)return;
  const key=result.id||JSON.stringify(result);if(lastResult===key)return;lastResult=key;
  const box=$('results');box.replaceChildren();
  const selected=(result.items||[]).filter(x=>x.status==='selected');
  if(result.notice)box.append(text('p',result.notice,'message'));
  if(!selected.length)box.append(text('p','这轮还没有足够的实际阅读依据推荐视频，先不硬凑。','muted'));
  for(const item of selected){
    const card=text('article','','video'),heading=document.createElement('h3'),link=document.createElement('a');
    link.textContent=item.title;link.href='https://www.bilibili.com/video/'+item.bvid+'/';link.target='_blank';link.rel='noopener noreferrer';heading.append(link);card.append(heading);
    const date=new Date(item.published*1000).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'});
    const seconds=item.duration_seconds||0;
    card.append(text('p',`${item.author?item.author+' · ':''}${date} · ${Math.floor(seconds/60)}:${String(seconds%60).padStart(2,'0')} · 首 P / 共 ${item.parts_total||1} P`,'meta'));
    card.append(text('p',item.scope||'首P字幕；未读取画面','meta'));card.append(text('p',item.summary),text('p','推荐理由：'+item.reason),text('p','鲸娅的一句话：'+item.take),text('p','阅读依据：'+item.evidence,'evidence'));box.append(card);
  }
  for(const item of result.items||[]){if(item.status!=='selected')box.append(text('p',(item.title||item.bvid)+'：'+(item.reason||item.status),'skip'));}
}
async function refresh(){
  try{
    const state=await (await api('/api/state')).json();const login=state.login;
    $('connection').className='notice';$('connection').textContent='本机程序已连接 · '+(state.api_ready?'已读取现有 DeepSeek 配置':'请先在鲸娅页面保存 API Key');
    $('login-title').textContent=login.logged_in?'登录成功'+(login.name?'，'+login.name:''):'用手机扫一下';
    $('login-message').textContent=login.message;$('remaining').textContent=state.has_qr?'二维码剩余约 '+state.qr_seconds_left+' 秒':'';
    if(state.has_qr&&state.qr_seconds_left>0){
      if(!qrUrl||state.qr_seconds_left>qrLoadedAt+3){
        const blob=await (await api('/api/login/qr')).blob();if(qrUrl)URL.revokeObjectURL(qrUrl);qrUrl=URL.createObjectURL(blob);$('qr').src=qrUrl;
      }
      qrLoadedAt=state.qr_seconds_left;$('qr').hidden=false;$('qr-placeholder').hidden=true;
    }else{$('qr').hidden=true;$('qr-placeholder').hidden=false;$('qr-placeholder').textContent=login.logged_in?'已确认登录有效':login.state==='generating'?'正在生成二维码…':'点击刷新二维码';}
    $('refresh').disabled=login.state==='generating';$('refresh').textContent=login.logged_in?'更换 B 站账号':'刷新二维码';
    $('work-message').textContent=state.work.message;$('browse').disabled=state.work.state==='running'||!state.api_ready;$('cancel').disabled=state.work.state!=='running';
    $('result-state').textContent={idle:'尚未开始',running:'正在读取',completed:'本轮完成',partial:'部分完成',failed:'需要处理',cancelled:'已停止'}[state.work.state]||state.work.state;
    if(state.work.state==='running'&&lastWorkState!=='running'){
      lastResult='';$('results').replaceChildren(text('p','正在读取本轮视频，结果会在完成后显示。','muted'));
    }
    lastWorkState=state.work.state;
    renderResult(state.work.result);
    if(state.work.state==='idle'&&!state.work.result&&state.share_candidates?.length){
      const cached=state.share_candidates.map(item=>({...item,status:'selected',published:item.published_at,parts_total:1}));
      renderResult({id:'cached-'+cached[0].id,items:cached});$('result-state').textContent='已读内容';
      $('work-message').textContent='下面是最近实际读过的内容；可以直接聊，也可以开始新一轮。';
    }
    if(!autoStarted&&login.state==='idle'){autoStarted=true;await api('/api/login/start','POST',{});}
  }catch(error){$('connection').className='notice error';$('connection').textContent=error.message;}
}
$('refresh').onclick=async()=>{try{if(qrUrl){URL.revokeObjectURL(qrUrl);qrUrl=null;}await api('/api/login/start','POST',{refresh:true});await refresh();}catch(error){$('login-message').textContent=error.message;}};
$('browse').onclick=async()=>{try{await api('/api/browse','POST',{keyword:$('keyword').value,video:$('video').value});await refresh();}catch(error){$('work-message').textContent=error.message;}};
$('cancel').onclick=async()=>{try{await api('/api/browse/cancel','POST',{});$('work-message').textContent='正在停止；不再开始下一步，等待当前请求收尾。';}catch(error){$('work-message').textContent=error.message;}};
refresh();setInterval(refresh,2500);
