"""One QQ ingress, optional native dialogue kernel, existing execution policy."""
from pathlib import Path
from collections import deque
import hashlib,json,re,threading,time
from urllib.parse import urlsplit

NATIVE_TURN_TIMEOUT_SECONDS=55.0
NATIVE_CLIENT_TIMEOUT_SECONDS=NATIVE_TURN_TIMEOUT_SECONDS+5.0


def bounded_facts(items, limit=16000):
    """Keep complete, prioritized evidence records instead of slicing JSON."""
    selected=[]
    for item in items:
        candidate=json.dumps([*selected,item],ensure_ascii=False)
        if len(candidate)<=limit:selected.append(item)
    return json.dumps(selected,ensure_ascii=False)


def source_information(root):
    root=Path(root)
    try:version=json.loads((root/'configs/release.json').read_text(encoding='utf-8'))['version']
    except (OSError,ValueError,KeyError,TypeError):version='0.3.0'
    if not re.fullmatch(r'\d+\.\d+\.\d+',str(version)):version='0.3.0'
    default='https://github.com/LONG10222/whale-companion/tree/v'+version
    try:
        data=json.loads((root/'configs/chat-engine.local.json').read_text(encoding='utf-8'))
        custom=data.get('source_url','') if isinstance(data,dict) else ''
        parsed=urlsplit(str(custom))
    except (OSError,ValueError):custom='';parsed=urlsplit('')
    source=str(custom) if (len(str(custom))<=300 and parsed.scheme=='https' and parsed.hostname
        and not parsed.username and not parsed.password and not parsed.query) else default
    return {'application_license':'GPL-3.0','optional_kernel':'AstrBot 4.29.1',
            'optional_kernel_license':'AGPL-3.0-or-later','source_url':source,
            'upstream_source':'https://github.com/AstrBotDevs/AstrBot/tree/v4.29.1',
            'network_source_note':'运行修改版时，请向远程使用者提供实际对应源码和许可；私人凭据与聊天不属于公开发行内容。'}


def source_request(text):
    value=re.sub(r'[？?！!。\s]+$','',text.strip())
    return bool(re.fullmatch(r'关于鲸娅|鲸娅开源信息|开源信息|鲸娅许可证|你的源码(?:在哪|在哪里|地址)?|你是开源的吗|你用什么许可证',value))


def settings(root):
    path=Path(root)/'configs/chat-engine.local.json'
    try:cfg=json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):cfg={}
    if not isinstance(cfg,dict):cfg={}
    port=cfg.get('port',18769)
    if type(port)!=int or not 1024<=port<=65535:port=18769
    mode=cfg.get('mode','legacy')
    if not isinstance(mode,str) or mode not in {'legacy','hybrid'}:mode='legacy'
    return {'enabled':cfg.get('enabled') is True,'mode':mode,
            'python':str(cfg.get('python','')),'profile':str(cfg.get('profile','')),'port':port}


def eligible(text,history,*,has_media=False,approval=False):
    if not text.strip() or len(text)>2000 or has_media or approval:return False
    from creative_tools import requested_tool
    from conversation_tools import external_intent,phone_intent
    from qq_speech import speech_request
    from chat_style import sticker_request
    from dialogue_context import turn_kind,balance_intent
    from dialogue_pragmatics import pending_proposal
    from life_memory import activity_question
    from conversation_grounding import feedback_focus
    if requested_tool(text) or external_intent(text) or phone_intent(text) or balance_intent(text):return False
    if speech_request(text)['requested'] or sticker_request(text,history)['requested'] or pending_proposal(history):return False
    if activity_question(text) or feedback_focus(text):return False
    if re.search('图|照片|视频|表情|音频|语音|文件|还记得|你记得|以前说|上次说|喜欢什么|记着什么|你说过',text):return False
    if turn_kind(text)!='conversation' or re.search(r'Plus|普拉斯|权限|管理员|改名|叫我|昵称|B站|哔哩|bilibili',text,re.I):return False
    return True


class ChatEngine:
    def __init__(self,root):
        self.root=Path(root);self.failures=deque(maxlen=3);self.blocked_until=0;self.lock=threading.Lock();self.last_error=''
    def config(self):return settings(self.root)
    def owns_listener(self):
        import psutil
        from runtime_supervisor import Service,path_equal
        cfg=self.config()
        if not cfg['enabled'] or not cfg['python'] or not cfg['profile']:return False
        interpreter=Path(cfg['python']);profile=Path(cfg['profile'])
        if not interpreter.is_absolute():interpreter=self.root/interpreter
        if not profile.is_absolute():profile=self.root/profile
        interpreter=interpreter.resolve();profile=profile.resolve()
        expected=Service('astrbot',[str(interpreter),str(self.root/'astrbot_sidecar.py'),
            '--root',str(self.root),'--profile',str(profile),'--port',str(cfg['port'])],self.root,cfg['port'])
        for process in psutil.process_iter():
            try:
                if not expected.owns(process):continue
                args=process.cmdline()
                if not (path_equal(process.exe(),interpreter) or args and path_equal(args[0],interpreter)):continue
                if any(c.status==psutil.CONN_LISTEN and c.laddr and c.laddr.port==cfg['port']
                       for c in process.net_connections(kind='tcp')):return True
            except (psutil.Error,OSError,ValueError):pass
        return False
    def headers(self):
        if not self.owns_listener():raise RuntimeError('The configured dialogue service does not own its local port')
        cfg=json.loads((self.root/'settings.local.json').read_text(encoding='utf-8'))
        return {'Authorization':'Bearer '+cfg['api_token']}
    def status(self):
        import httpx
        cfg=self.config();result={k:cfg[k] for k in ['enabled','mode']}
        result.update(temporarily_fallback=time.monotonic()<self.blocked_until,last_error=self.last_error)
        try:
            with httpx.Client(trust_env=False,headers=self.headers(),timeout=2) as client:
                response=client.get(f'http://127.0.0.1:{cfg["port"]}/health');response.raise_for_status();result['kernel']=response.json()
        except Exception:result['kernel']={'ok':False}
        return result
    def converse(self,peer,text,chat,context):
        import httpx
        cfg=self.config()
        if not cfg['enabled'] or cfg['mode']!='hybrid' or time.monotonic()<self.blocked_until:return None
        from qq_scope import actor,group
        identity=actor(peer);scope=(context or {}).get('bot','local-preview')
        key=hashlib.sha256((str(scope)+'|'+str(peer)).encode()).hexdigest()
        recent=getattr(chat.memory,'active_recent',chat.memory.recent)
        rows=recent(peer,24)
        if (context or {}).get('_native_current_recorded') and rows and rows[-1][0]=='user' and rows[-1][1]==text:rows=rows[:-1]
        seed=[];size=0
        for role,value in reversed(rows):
            item={'role':role,'content':value.split('\n[本条QQ')[0].split('\n[本轮')[0][:1200]}
            length=len(json.dumps(item,ensure_ascii=False))
            if size+length>16000:break
            seed.insert(0,item);size+=length
        facts=[{'deployment_facts':{'dialogue_kernel':'AstrBot 4.29.1','chat_model':'DeepSeek API','qq_gateway':'NapCat / OneBot / 本地QQBridge','execution':'既有受控工具；管理员、Plus、普通用户权限不变','computer_tools_in_astrbot':False}}]
        persona=chat.persona.load()
        original=persona.model_dump()
        enabled=original.get('enabled',True) is True
        card={'enabled':enabled}
        if enabled:
            card.update({k:original.get(k,'') for k in ['name','description','personality','scenario','style']})
            from social_examples import select_examples
            card['examples']=[entry.model_dump() for entry in select_examples(getattr(persona,'examples',[]),text,limit=3,
                avoid_replies=[value for role,value in rows[-8:] if role=='assistant'])]
            if hasattr(chat,'learning'):
                learned,selected=chat.learning.prompt(peer,text,persona)
                if selected:facts.append({'approved_learning':learned['content']})
        life=getattr(chat.memory,'life',None)
        if life:
            learned_life=life.prompt(peer,text)
            if learned_life:facts.append({'source_backed_memories':learned_life['content']})
        profile=chat.memory.profile(peer)
        if profile:facts.append({'approved_profile':profile})
        if re.search('记得|以前|上次|之前|忘了',text):facts.append({'recalled':chat.memory.recall(peer,text)})
        try:
            if chat.creative and re.search('任务|结果|进度|前面|刚才|之前|做好|做完|修|唱|拿来|传来|成片',text):facts.append({'work_records':[j for j in chat.creative.prompt_context(peer) if j.get('delivery')!='preview'][:3]})
        except Exception:pass
        facts.append({'channel':'group' if group(peer) else 'private'})
        lore=[e.get('content','') for e in original.get('lorebook',[]) if enabled and e.get('enabled',True) and
              (e.get('constant') or any(key and key.casefold() in text.casefold() for key in e.get('keys',[])))]
        if lore:facts.append({'character_background_only':lore[:3]})
        payload={'session_key':key,'text':text,'persona':card,'seed_history':seed,
                 'facts':bounded_facts(facts),
                 'ledger_peer':str(peer),'ledger_role':chat.contacts.role(identity) if str(identity).isdigit() else 'preview',
                 'display_name':str(chat.contacts.label(identity))[:80] if str(identity).isdigit() else '本地预览'}
        try:
            with httpx.Client(trust_env=False,headers=self.headers(),timeout=NATIVE_CLIENT_TIMEOUT_SECONDS) as client:
                response=client.post(f'http://127.0.0.1:{cfg["port"]}/v1/converse',json=payload);response.raise_for_status();result=response.json()
            if result.get('route'):return None
            reply=str(result.get('reply','')).strip()
            from conversation_grounding import greeting_reply_issue
            if greeting_reply_issue(reply,text):return None
            if not reply and not result.get('silent'):raise ValueError('Dialogue kernel returned an empty response')
            if not re.fullmatch('[a-f0-9]{48}',result.get('ticket','')):raise ValueError('Dialogue receipt identifier is invalid')
            self.last_error='';self.failures.clear()
            if result.get('silent'):
                return {'reply':'','attachments':[],'tools':[],'silent':True,'dialogue_act':'closing','closing_reason':'native_context','native_ticket':result['ticket']}
            return {'reply':reply,'attachments':[],'tools':[],'native_ticket':result['ticket'],'engine':'astrbot'}
        except Exception as exc:
            with self.lock:
                now=time.monotonic();self.failures.append(now)
                if len(self.failures)==3 and now-self.failures[0]<300:self.blocked_until=now+120
            self.last_error=type(exc).__name__
            return None
    def confirm(self,ticket):
        import httpx
        if not re.fullmatch('[a-f0-9]{48}',str(ticket)):return False
        try:
            with httpx.Client(trust_env=False,headers=self.headers(),timeout=3) as client:
                response=client.post(f'http://127.0.0.1:{self.config()["port"]}/v1/confirm',json={'ticket':ticket});response.raise_for_status()
                return response.json().get('committed') is True
        except Exception:return False
