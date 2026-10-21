"""DeepSeek API boundary. Credentials stay in local configuration or environment."""
from pathlib import Path
import json,os,tomllib,time,urllib.request,urllib.error

ROOT=Path(__file__).resolve().parent

def configuration():
    cfg={'model':'deepseek-flash','api_key':''}
    local=ROOT/'agent.local.json'
    if local.exists():cfg.update(json.loads(local.read_text(encoding='utf-8')))
    if not cfg.get('api_key'):
        source=ROOT/'robot-instance/config/model_config.toml'
        if source.exists():
            bot=tomllib.loads(source.read_text(encoding='utf-8'))
            provider=next((p for p in bot.get('api_providers',[]) if p.get('name')=='DeepSeek'),{})
            cfg['api_key']=provider.get('api_key','')
    cfg['api_key']=os.environ.get('DEEPSEEK_API_KEY') or cfg.get('api_key','')
    return cfg

def ready():
    key=configuration()['api_key']
    return key.startswith('sk-') and len(key)>=24 and all(x not in key.lower() for x in ('replace','your_','placeholder'))

def api_post(path,payload,timeout=180):
    if not ready():raise RuntimeError('请先在本地测试页填写 DeepSeek API Key；不需要把 Key 发到聊天里。')
    cfg=configuration()
    request=urllib.request.Request('https://api.deepseek.com'+path,data=json.dumps(payload,ensure_ascii=False).encode(),
        headers={'Content-Type':'application/json','Authorization':'Bearer '+cfg['api_key']})
    started=time.monotonic()
    try:
        with urllib.request.urlopen(request,timeout=timeout) as response:result=json.load(response)
        from usage_ledger import Ledger
        usage=result.get('usage',{})
        Ledger(ROOT).record('deepseek_usage',model=payload.get('model','DeepSeek Search'),state='completed',duration=time.monotonic()-started,
            input_tokens=usage.get('prompt_tokens'),output_tokens=usage.get('completion_tokens'),total_tokens=usage.get('total_tokens'),details={'endpoint':path})
        return result
    except urllib.error.HTTPError as exc:
        from usage_ledger import Ledger
        Ledger(ROOT).record('deepseek_request_failed',model=payload.get('model','DeepSeek Search'),state='failed',duration=time.monotonic()-started,details={'endpoint':path,'http_status':exc.code})
        descriptions={401:'DeepSeek Key 无效',402:'DeepSeek 账户余额不足',429:'DeepSeek 请求过于频繁'}
        raise RuntimeError(descriptions.get(exc.code,f'DeepSeek 请求失败（HTTP {exc.code}）')) from None
    except (TimeoutError,urllib.error.URLError) as exc:
        from usage_ledger import Ledger
        Ledger(ROOT).record('deepseek_request_failed',model=payload.get('model','DeepSeek Search'),state='failed',duration=time.monotonic()-started,details={'endpoint':path,'error_type':type(exc).__name__})
        raise RuntimeError('DeepSeek 网络连接失败或超时') from None

def complete(messages,tools,*,thinking=False,model=None,max_tokens=1800,timeout=180,tool_choice=None):
    payload={'model':model or configuration()['model'],'messages':messages,
        'thinking':{'type':'enabled' if thinking else 'disabled'},'max_tokens':max_tokens,'stream':False}
    if tools:payload.update(tools=tools,tool_choice=tool_choice or 'auto')
    if thinking:payload['reasoning_effort']='low'
    else:payload['temperature']=.6
    result=api_post('/chat/completions',payload,timeout=timeout)
    return result['choices'][0]['message'],result.get('usage',{})

def assistant_message(response):
    """Keep protocol metadata within this turn; never expose reasoning in UI or logs."""
    message={'role':'assistant','content':response.get('content')}
    for key in ('tool_calls','reasoning_content'):
        if response.get(key) is not None:message[key]=response[key]
    return message
