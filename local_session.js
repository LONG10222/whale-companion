/* Same-origin browser convenience; the persistent bearer token stays on the server. */
(()=>{
 const original=window.fetch.bind(window);let pending=null,until=0;const mediaReady=new WeakMap();
 function mediaRoute(url){
  if(url.protocol!=='http:'||!['127.0.0.1','localhost','[::1]'].includes(url.hostname)||url.port!=='18765'||url.origin===location.origin)return null;
  if(!/^\/(artifacts\/[a-f0-9]{32}|samples\/[a-z0-9-]+\.wav)$/.test(url.pathname))return null;
  return '/local/media'+url.pathname+url.search;
 }
 async function connect(){
  if(Date.now()<until)return;
  if(!pending)pending=original('/local/session',{method:'POST',credentials:'same-origin',headers:{'X-Local-Client':'whale-local-ui-v1'}}).then(r=>{if(!r.ok)throw Error('本机连接暂未完成，稍后刷新页面重试。');until=Date.now()+11*3600000}).finally(()=>pending=null);
  return pending;
 }
 window.fetch=async(input,options={})=>{
  let url=new URL(typeof input==='string'||input instanceof URL?String(input):input.url,location.href);
  const route=['GET','HEAD'].includes(String(options.method||input.method||'GET').toUpperCase())?mediaRoute(url):null;
  if(route){input=typeof Request!=='undefined'&&input instanceof Request?new Request(new URL(route,location.href),input):route;url=new URL(route,location.href);}
  if(url.origin!==location.origin||(url.pathname.startsWith('/local/')&&!url.pathname.startsWith('/local/media/')))return original(input,options);
  await connect();const headers=new Headers(options.headers||(typeof Request!=='undefined'&&input instanceof Request?input.headers:{}));headers.set('X-Local-Client','whale-local-ui-v1');
  let r=await original(input,{...options,headers,credentials:'same-origin'});
  if(r.status===401){until=0;await connect();r=await original(input,{...options,headers,credentials:'same-origin'})}
  return r;
 };
 async function prepareMedia(media){
  if(!media||!['AUDIO','VIDEO'].includes(media.tagName)||!media.getAttribute('src'))return;
  let url=new URL(media.getAttribute('src'),location.href);const route=mediaRoute(url);
  if(route){media.setAttribute('src',route);url=new URL(route,location.href);}
  if(url.origin!==location.origin||!/^\/(artifacts|samples|local\/media)\//.test(url.pathname))return;
  const source=media.getAttribute('src');if(mediaReady.get(media)===source)return;mediaReady.set(media,source);
  try{await connect();if(media.getAttribute('src')===source&&(media.error||media.readyState===0))media.load();}catch{mediaReady.delete(media);}
 }
 new MutationObserver(records=>{for(const record of records){
  if(record.type==='attributes')prepareMedia(record.target);
  else for(const node of record.addedNodes){prepareMedia(node);if(node.querySelectorAll)for(const media of node.querySelectorAll('audio[src],video[src]'))prepareMedia(media);}
 }}).observe(document.documentElement,{subtree:true,childList:true,attributes:true,attributeFilter:['src']});
 async function automatic(){
  try{
   await connect();const token=document.getElementById('token'),button=document.getElementById('connect');
   if(token){
    token.value='';token.hidden=true;const label=token.closest('label')||document.querySelector('label[for="token"]');if(label)label.hidden=true;
    const section=token.closest('section');if(section)for(const detail of section.querySelectorAll('details'))if(detail.querySelector('input[type="file"]'))detail.hidden=true;
   }
   if(button){button.hidden=true;button.click()}
   for(const media of document.querySelectorAll('audio[src],video[src]'))prepareMedia(media);
  }catch(e){const status=document.getElementById('status')||document.getElementById('connection');if(status)status.textContent=e.message}
 }
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',automatic,{once:true});else automatic();
})();
