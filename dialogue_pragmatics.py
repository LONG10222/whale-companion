"""Per-turn social meaning. These decisions can suppress replies, never grant tools."""
import json,re

ACKS={'好','好的','好吧','好哒','好嘞','ok','ok啊','okk','嗯','嗯嗯','哦','噢','噢噢','行','行吧','可以','收到','知道了','明白了','了解','妥','👌'}
ACTS={'acknowledge','tease','agree','comfort','end_topic','answer','question','request','unknown'}


def words(text):return str(text).split('\n[本条QQ')[0].split('\n[对方发来')[0].strip()
def normalized(text):return re.sub(r'\s+','',re.sub(r'\[QQ表情[：:][^\]]*\]','',words(text))).strip('。.!！~～…·').lower()


def visual_evidence(text):
    """Compact image facts remain separate from the sender's actual words."""
    if '\n[本条QQ消息附图' not in text:return []
    try:
        payload=text.split('\n[本条QQ消息附图',1)[1].split('\n',1)[1].lstrip()
        items,_=json.JSONDecoder().raw_decode(payload)
    except (ValueError,IndexError,TypeError):return []
    if not isinstance(items,list):return []
    out=[]
    for item in items[:3]:
        if not isinstance(item,dict):continue
        turn=item.get('conversation_turn') or {};turn=turn if isinstance(turn,dict) else {}
        out.append({'id':str(item.get('id',''))[:64],'kind':str(item.get('image_kind','unknown'))[:20],
            'visible_text':str(item.get('text',''))[:160],'possible_meanings':[v[:100] for v in item.get('possible_meanings',[])[:2] if isinstance(v,str)] if isinstance(item.get('possible_meanings'),list) else [],
            'previous_context_meaning':str(turn.get('meaning',''))[:180],'previous_act':str(turn.get('response_act','unknown'))[:30]})
    return out


def vision_history(history):
    turns=[]
    for row in history[-8:]:
        role,text=row[:2]
        if role=='user':
            value=words(text)
            if value.startswith('[收到图片'):value=''
            turns.append({'role':role,'text':value[:600],'image_evidence':visual_evidence(text)})
        elif role=='assistant':turns.append({'role':role,'text':text.split('\n[本轮')[0][:600]})
    return turns


def pending_proposal(history):
    last=history[-1][1].split('\n[本轮')[0] if history and history[-1][0]=='assistant' else ''
    if not last or re.search(r'已接收|已排队|开始.{0,4}(?:画|查|唱)|正在|画好.{0,4}发|做好.{0,4}发|任务.{0,5}(?:已|正在)',last):return False
    if re.search(r'文件|数据库|密码|密钥|账号|余额|权限|管理员|Plus|本机|电脑|删除|上传|发布|发帖',last,re.I):return False
    return bool(re.search(r'要不要|要不|需要我|我(?:来|帮|按)|那我',last) and re.search(r'画|修图|唱|查|搜索|生成|整理',last)
        and re.search(r'[?？]|行吗|好吗|要的话',last))


def confirmed_proposal(text,history,tool):
    if normalized(text) not in ACKS or not pending_proposal(history) or tool not in {'draw_picture','sing_song','lookup_web'}:return None
    last=history[-1][1].split('\n[本轮')[0][:800]
    # A bare acknowledgement confirms only the offered action, never another tool.
    actions={'draw_picture':r'画|修图|绘图','sing_song':r'唱|清唱','lookup_web':r'查询|搜索|查一下|帮你查|我来查'}
    matched=[name for name,pattern in actions.items() if re.search(pattern,last)]
    if matched!=[tool]:return None
    before=next((words(row[1])[:600] for row in reversed(history[:-1]) if row[0]=='user'),'')
    action={'draw_picture':'请画图','sing_song':'请唱歌','lookup_web':'请查询公开资料'}[tool]
    return action+'。用户本轮确认执行紧邻的这个提议：'+last+('\n提议前当前用户的话（仅理解对象，以紧邻提议为准）：'+before if before else '')+'\n本轮确认：'+words(text)


def closing_decision(text,history,semantic=None,has_media=False,explain=False):
    value=normalized(text)
    if not has_media:
        parts=re.split('[，,]',value,maxsplit=1)
        if len(parts)==2 and parts[0] in ACKS:value=parts[1]
        if value in {'谢谢','谢谢啦','谢了','感谢','多谢'}:
            return {'reason':'thanks_signoff','reply':'不客气～' if not any(row[0]=='assistant' and '不客气' in row[1] for row in history[-2:]) else ''}
        if value in {'晚安','晚安啦','晚安咯','拜拜','拜','再见','下次聊','回头聊','先这样','先这样吧','就这样','就这样吧','不聊了','先不聊了','我去忙了','忙去了'}:
            last=next((row[1] for row in reversed(history) if row[0]=='assistant'),'')
            reply='晚安。' if value.startswith('晚安') and '晚安' not in last else '拜拜。' if value in {'拜拜','拜','再见','下次聊','回头聊'} and not re.search('拜拜|再见',last) else ''
            return {'reason':'explicit_signoff','reply':reply}
        if value in ACKS and not pending_proposal(history):return {'reason':'terminal_acknowledgement','reply':''}
    semantic=semantic or {}
    if has_media and not explain and (not words(text) or value in ACKS) and semantic.get('response_act')=='end_topic' and semantic.get('certainty') in {'high','medium'}:
        return {'reason':'contextual_sticker_signoff','reply':''}
    ocr=normalized(str(semantic.get('visible_text','')).strip('“”"「」'))
    last=next((row[1] for row in reversed(history) if row[0]=='assistant'),'')
    explanation=len(last)>=90 or len(last)>=35 and bool(re.search('含义|意思是|用来表达|通常用于|具体来说',last))
    quiet_variant=explanation and bool(re.fullmatch(r'(?:哦|噢|嗯|好|收到|知道了)[^？?！!\n]{0,6}',ocr))
    if has_media and not explain and (not words(text) or value in ACKS) and semantic.get('response_act')=='acknowledge' and semantic.get('certainty') in {'high','medium'} and (ocr in ACKS or quiet_variant) and not pending_proposal(history):
        return {'reason':'contextual_sticker_acknowledgement','reply':''}
    return None


def closing_result(decision):
    return {'reply':decision['reply'],'attachments':[],'tools':[],'silent':not bool(decision['reply']),'dialogue_act':'closing','closing_reason':decision['reason']}


def creative_topic(history):
    """A newly supplied photo is a new object, not an older character drawing."""
    for index in range(len(history)-1,-1,-1):
        if history[index][0]!='user':continue
        images=visual_evidence(history[index][1])
        if any(i['kind'] in {'photo','screenshot'} for i in images):
            from creative_tools import image_edit_intent
            start=index
            for before in range(index-1,max(-1,index-9),-1):
                if history[before][0]=='user':
                    if image_edit_intent(words(history[before][1])):start=before
                    elif re.search(r'群文件|发过|传了|上传|发(?:给|到)|看(?:见|到|不到)|读不到',words(history[before][1])):continue
                    break
            return history[start:],images
    return history,[]
