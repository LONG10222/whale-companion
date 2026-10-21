async function localAssistantStatus(){
  const state=await(await api('/assistant')).json(),r=state.runtime,limits=state.policy;
  $('assistantMode').value=state.mode;
  const mode={local:'本地辅助，电脑忙时暂缓',cloud:'云端辅助',paused:'辅助已暂停'}[state.mode];
  const resources=r.resources?` · 空闲显存 ${(r.resources.gpu_free_mib/1024).toFixed(1)}GB · 可用内存 ${(r.resources.ram_free_mib/1024).toFixed(1)}GB`:'';
  $('assistantStatus').textContent=`${mode} · ${r.prepared?'Qwen3 1.7B 已准备':'模型尚未准备'} · ${r.running?'正在运行':'模型未驻留'}${resources}。单次最多 ${limits.max_seconds} 秒，CPU最多 ${limits.threads} 线程。`;
  if(r.last&&r.last.seconds)$('assistantMetrics').textContent=r.last.cache_hit?'上次任务命中缓存，未加载模型进程。':`上次本地任务：${r.last.seconds} 秒；模型进程已退出。`;
}
function assistantMetrics(m){return m?.source==='local'?(m.cache_hit?'本地 Qwen3 · 缓存命中 · 未加载模型':`本地 Qwen3 · ${m.seconds} 秒 · 用后退出`):'云端 DeepSeek'}
$('saveAssistant').onclick=()=>task($('saveAssistant'),async()=>{await api('/assistant',{mode:$('assistantMode').value});await localAssistantStatus();say('辅助方式已保存。普通QQ聊天仍使用原来的 DeepSeek。')});
$('refreshAssistant').onclick=()=>task($('refreshAssistant'),localAssistantStatus);
$('pauseAssistant').onclick=()=>task($('pauseAssistant'),async()=>{await api('/assistant',{mode:'paused'});await localAssistantStatus();say('已暂停学习辅助，并通知当前本地任务停止。')});
$('curate').onclick=()=>task($('curate'),async()=>{say('正在检查已认可的训练样例……');const result=await(await api('/curate',{})).json();$('curation').replaceChildren();for(const row of result.data.entries){$('curation').append(node('p',`示例 #${row.id}：${row.issues.length?row.issues.join('；'):'未提出具体疑点，仍需你核对。'}`))}say(assistantMetrics(result.metrics)+'；检查建议未自动修改学习库。');await localAssistantStatus()});
