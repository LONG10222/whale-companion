'use strict';
(()=>{
 let current=null,busy=false,proposal=null;
 const el=id=>document.getElementById(id);
 async function api(path,body){const r=await fetch('/qq/bilibili'+path,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});const v=await r.json();if(!r.ok)throw Error(v.detail||'这次没有完成');return v;}
 async function refresh(){
  if(busy)return;current=await api('/api/state');const s=current.sharing;
  if(!['bili-enabled','bili-auto','bili-proactive','bili-like','bili-comment-auto','bili-coin-auto'].includes(document.activeElement?.id)){
   el('bili-enabled').checked=s.enabled;el('bili-auto').checked=s.auto_read_enabled;el('bili-proactive').checked=current.proactive_enabled;
   el('bili-like').checked=Boolean(s.social_policy?.auto_like);
   el('bili-comment-auto').checked=s.social_policy?.comment_mode==='automatic';el('bili-coin-auto').checked=s.social_policy?.coin_mode==='automatic';
  }
  el('bili-policy-state').textContent='当前：点赞'+(s.social_policy?.auto_like?'自动':'仅明确要求')+'；评论'+(s.social_policy?.comment_mode==='automatic'?'自动':'先确认')+'；投币'+(s.social_policy?.coin_mode==='automatic'?'自动':'先确认')+'。';
  el('bili-share').disabled=!current.share_candidates?.length;
 }
 async function action(fn){if(busy)return;busy=true;try{await fn();}catch(e){el('bili-notice').textContent=e.message;}finally{busy=false;await refresh();}}
 document.addEventListener('DOMContentLoaded',()=>{
  el('bili-save').onclick=()=>action(async()=>{await api('/config',{enabled:el('bili-enabled').checked,auto_read_enabled:el('bili-auto').checked,proactive_enabled:el('bili-proactive').checked,auto_like:el('bili-like').checked,auto_comment:el('bili-comment-auto').checked,auto_coin:el('bili-coin-auto').checked});el('bili-notice').textContent='设置已保存；主动分享仍与原有主动聊天共用每日次数。';});
  el('bili-share').onclick=()=>action(async()=>{const bvid=current?.share_candidates?.[0]?.bvid;if(!bvid)return;el('bili-notice').textContent='正在组织成一条自然的分享…';const v=await api('/share',{bvid});el('bili-notice').textContent=v.receipt_received?'QQ已返回消息编号，已发给long。':'请核对发送记录。';});
  async function prepareSocial(kind){await action(async()=>{const bvid=current?.share_candidates?.[0]?.bvid;if(!bvid)throw Error('先读到一条视频，再准备互动。');proposal=await api('/social/propose',{bvid,action:kind,comment:el('bili-comment').value,amount:Number(el('bili-coins').value)});el('bili-proposal-text').textContent=proposal.review_text;el('bili-confirm').hidden=false;el('bili-confirm').textContent=kind==='comment'?'确认发布这句评论':'确认投'+proposal.amount+'枚币';el('bili-social-notice').textContent='尚未执行。请核对上面的目标和内容。';});}
  el('bili-comment-proposal').onclick=()=>prepareSocial('comment');el('bili-coin-proposal').onclick=()=>prepareSocial('coin');
  el('bili-confirm').onclick=()=>action(async()=>{if(!proposal)return;const v=await api('/social/confirm',{proposal_id:proposal.proposal_id});el('bili-confirm').hidden=true;proposal=null;el('bili-social-notice').textContent=v.message||'这份提案已处理。';});
  refresh().catch(e=>el('bili-notice').textContent=e.message);setInterval(()=>refresh().catch(()=>{}),10000);
 },{once:true});
})();
