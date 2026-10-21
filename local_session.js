/* Same-origin browser convenience; the persistent bearer token stays on the server. */
(()=>{
 const original=window.fetch.bind(window);let pending=null,until=0;
 async function connect(){
  if(Date.now()<until)return;
  if(!pending)pending=original('/local/session',{method:'POST',credentials:'same-origin',headers:{'X-Local-Client':'whale-local-ui-v1'}}).then(r=>{if(!r.ok)throw Error('本机连接暂未完成，稍后刷新页面重试。');until=Date.now()+11*3600000}).finally(()=>pending=null);
  return pending;
 }
 window.fetch=async(input,options={})=>{
  const url=new URL(typeof input==='string'?input:input.url,location.href);
  if(url.origin!==location.origin||url.pathname.startsWith('/local/'))return original(input,options);
  await connect();const headers=new Headers(options.headers||{});headers.set('X-Local-Client','whale-local-ui-v1');
  let r=await original(input,{...options,headers,credentials:'same-origin'});
  if(r.status===401){until=0;await connect();r=await original(input,{...options,headers,credentials:'same-origin'})}
  return r;
 };
 async function automatic(){
  try{
   await connect();const token=document.getElementById('token'),button=document.getElementById('connect');
   if(token&&button){
    token.value='';token.hidden=true;const label=token.closest('label')||document.querySelector('label[for="token"]');if(label)label.hidden=true;
    const section=token.closest('section');if(section)for(const detail of section.querySelectorAll('details'))if(detail.querySelector('input[type="file"]'))detail.hidden=true;
    button.hidden=true;button.click();
   }
  }catch(e){const status=document.getElementById('status')||document.getElementById('connection');if(status)status.textContent=e.message}
 }
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',automatic,{once:true});else automatic();
})();
