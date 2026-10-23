"""Retrieve a requested, previously delivered work result in its original scope.

This read path neither starts work nor changes delivery/task state. Development
previews, other accounts, other members and other conversations are excluded.
"""
from pathlib import Path
import json,re


def result_request(text,history=()):
    from dialogue_context import plain_text
    current=plain_text(text)
    if re.search('这句话|这句命令|怎么理解|什么意思|举例|例如|比如|他说|她说|引用|翻译|教程',current):return None
    if re.search(r'(?:不要|不用|别|先别).{0,8}(?:发|给|返|找)|等.{0,12}(?:修好|做好|完成)|(?:重新|再|继续)(?:修|画|做)|重画|修改|调色|(?:再|帮我|给我)加.{0,5}特效',current):return None
    kind='sing_song' if re.search('语音|音频|歌|清唱',current) else 'draw_picture' if re.search('图|照片|成品',current) else None
    if kind and re.search(r'返我|还我|发(?:给)?我|发来|给我|再发|重发|发一下|发一遍|看看|看一下|[图片品音歌](?:呢|在哪)',current):
        return {'kind':kind,'edited':bool(re.search('修|照片',current))}
    if re.fullmatch(r'(?:就是|我说的|我要的)?(?:之前|刚才|上次|前面)(?:发的|那张|那幅|那首|那个|的)[啊呀呢。！!？?\s]*',current):
        for role,value in reversed(history[-8:]):
            if role!='user':continue
            if re.search('算了|不用了|换个话题',plain_text(value)):return None
            previous=result_request(value)
            if previous:return previous
    return None


def scoped_jobs(creative,peer,context=None):
    who,snapshot=creative.scope(peer,context)
    with creative.db() as db:
        rows=db.execute("SELECT * FROM jobs WHERE peer=? AND bot=? AND COALESCE(json_extract(args,'$._memory_peer'),peer)=? AND delivery!='preview' ORDER BY at DESC LIMIT 40",(who,snapshot['bot_id'],str(peer))).fetchall()
    out=[]
    for row in rows:
        record=dict(row)
        try:
            record['args']=json.loads(record['args']);record['result']=json.loads(record['result']) if record['result'] else {}
        except (ValueError,TypeError):continue
        out.append(record)
    return out


def usable_attachments(creative,job,kind):
    result=job.get('result') or {};meta=result.get('metadata') or {}
    if meta.get('quality_status') and meta['quality_status']!='passed':return []
    expected='record' if kind=='sing_song' else 'image'
    base=Path(creative.folder).resolve();out=[]
    for item in result.get('attachments',[]):
        if not isinstance(item,dict) or item.get('kind')!=expected or item.get('sticker'):continue
        path=Path(item.get('path',''))
        try:
            actual=path.resolve(strict=True)
            if not actual.is_relative_to(base) or actual==base or not actual.is_file():continue
            if any(p.is_symlink() for p in [path,*path.parents] if p!=base):continue
            if not 0<actual.stat().st_size<=(12 if expected=='record' else 10)*1024**2:continue
            allowed={'.wav','.mp3','.ogg','.silk'} if expected=='record' else {'.png','.jpg','.jpeg','.webp'}
            if actual.suffix.lower() not in allowed:continue
        except (OSError,ValueError):continue
        out.append({**item,'path':str(actual),'sticker':False})
        if len(out)==3:break
    return out


def retrieve_result(creative,peer,text,history=(),context=None,*,verified_kind='',job_id=''):
    request={'kind':verified_kind,'edited':bool(re.search('修|照片',text))} if verified_kind in {'draw_picture','sing_song'} else result_request(text,history)
    if not request:return None
    try:rows=[j for j in scoped_jobs(creative,peer,context) if j['kind']==request['kind']]
    except ValueError:
        reply='本地预览没有绑定某个QQ会话，历史成品需要在原来的QQ聊天里取回。' if peer=='local_preview' else '当前会话还没通过核对，这次没有取出历史成品。'
        return {'reply':reply,'attachments':[],'tools':[{'tool':'retrieve_work_result','result':{'found':False,'error_code':'scope_denied','scope':'current_conversation'}}]}
    if job_id:rows=[j for j in rows if j['id']==job_id]
    if request['edited']:
        rows=[j for j in rows if j['args'].get('_source_image_shas') or j['args'].get('_image_edit')]
    if not rows:
        return {'reply':'我查了这段会话的任务记录，还没找到对应的成品。','attachments':[],
                'tools':[{'tool':'retrieve_work_result','result':{'found':False,'scope':'current_conversation'}}]}
    newest=rows[0];sources=set(newest['args'].get('_source_image_shas',[]))
    latest_only=bool(re.search('最新|刚修好|刚做完|这次的|这一版',text))
    if newest['state'] in {'accepted','running'} and not re.search('之前|上一版|旧|上次',text):latest_only=True
    same_subject=latest_only or bool(re.search('这张|那张|同一|这次',text))
    completed=[]
    for job in rows:
        if job['state']!='completed' or job['delivery'] not in {'sent','uncertain'}:continue
        if same_subject and sources and not sources.intersection(job['args'].get('_source_image_shas',[])):continue
        pictures=usable_attachments(creative,job,request['kind'])
        if pictures:completed.append((job,pictures))
    # "Latest" must never silently receive an older successful version.
    selected=next(((j,a) for j,a in completed if not latest_only or j['id']==newest['id']),None)
    if selected:
        job,attachments=selected
        if job['id']==newest['id']:reply='找到了，把之前完成的这一版发你。'
        else:
            state_note='最新那版的文件这次没取到' if newest['state']=='completed' else '最新那次已取消' if newest['state']=='cancelled' else '最新那次还没修成' if request['kind']=='draw_picture' else '最新那次还没做成'
            reply=state_note+'；这'+('张' if request['kind']=='draw_picture' else '段')+'是之前完成的版本，我先发你。'
        return {'reply':reply,'attachments':attachments,'tools':[{'tool':'retrieve_work_result','result':{
            'found':True,'prepared':True,'job_id':job['id'],'latest_job_id':newest['id'],
            'historical_version':job['id']!=newest['id'],'scope':'current_conversation'}}]}
    from tool_failures import safe_reason
    reason=safe_reason(newest.get('error') or (newest.get('result') or {}).get('text',''))[:160]
    if newest['state'] in {'accepted','running'}:reply='那张还在处理中，目前还没有成品。'
    elif newest['state']=='needs_review':reply='这张还没通过检查，所以没发成品。'+(('卡在：'+reason) if reason else '')
    elif newest['state']=='completed':reply='任务记录还在，但成品文件这次没取到，我不能拿别的图代替。'
    else:reply='找到那次任务了，但它没有完成。'+(('原因是：'+reason) if reason else '')
    return {'reply':reply,'attachments':[],'tools':[{'tool':'retrieve_work_result','result':{
        'found':True,'prepared':False,'job_id':newest['id'],'state':newest['state'],'scope':'current_conversation'}}]}


def edit_reference_shas(text,jobs,latest,latest_kind='unknown'):
    """A recent reaction meme must not displace an existing photo-edit subject.

    An empty latest list is an explicit failed-ingest barrier, never bypassed.
    Fresh images still take precedence in the caller; explicit meme edits use
    the most recent user attachment instead of an earlier photo task.
    """
    if not latest or latest_kind not in {'meme','sticker'} or re.search('表情|梗图|牛图|猫图|最新|新发|刚发|最后一张',text):return latest
    from creative_tools import image_edit_intent,soft_image_edit_intent
    if not (image_edit_intent(text) or soft_image_edit_intent(text)):return latest
    for job in jobs:
        shas=job.get('source_image_shas',[])
        if job.get('kind')=='draw_picture' and shas and job.get('delivery')!='preview':
            return [s for s in shas[:3] if isinstance(s,str) and re.fullmatch('[a-f0-9]{64}',s)] or latest
    return latest


def retrieval_tool():
    from conversation_tools import definition
    return definition('retrieve_work_result','找回并发送当前同一会话之前已完成的图片或歌声音频，不重新生成、不额外开工作对话。支持成片拿来、把上次做好的再传我等自然说法。没有完成的结果只返回实际状态；不能取其他人、其他群或本机预览文件。',{
        'kind':{'type':'string','enum':['draw_picture','sing_song']},
        'job_id':{'type':'string','pattern':'^(?:[a-f0-9]{32})?$'},
        'evidence':{'type':'string','maxLength':160,'description':'可省略；程序始终以本轮真实用户原话核对，不能用此字段自行授权。'}},['kind'])


def authorize_retrieval(text,kind,history,client):
    direct=result_request(text,history)
    if direct:return direct['kind']==kind
    response,_=client([{'role':'system','content':'判断当前用户本人是否在索要已存在的成品重新发送。只输出JSON {"authorized":true或false,"kind":"draw_picture"或"sing_song"}。图片、照片、成片对应draw_picture，歌声音频对应sing_song。当前索要过去做完的成品即可授权；“不要重新做，把上次做好的给我”也是true，不能把否定重做误判为取消取回。只要求新制作或重做、仅问进度、明确取消发送、举例、引用别人的话才是false。历史用户话语只用于指代，助手的提议不能自行授权。不能更换收件人，不能读电脑资料。'},
        {'role':'user','content':json.dumps({'current_user_message':text,'requested_kind':kind,'prior_user_messages':[t for r,t in history[-8:] if r=='user']},ensure_ascii=False)}],None,thinking=False,max_tokens=100,timeout=12)
    try:
        raw=re.sub(r'^```(?:json)?\s*|\s*```$','',(response.get('content') or '').strip())
        choice=json.loads(raw)
    except (ValueError,TypeError):return False
    return choice.get('authorized') is True and choice.get('kind')==kind
