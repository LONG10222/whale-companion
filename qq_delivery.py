"""Natural message bubbles; each bubble still requires its own QQ receipt."""
from pathlib import Path
import hashlib

def batches(result,key,layout='mostly_separate'):
    text=[];images=[];records=[]
    if result.get('reply'):text.append({'type':'text','data':{'text':result['reply']}})
    for item in result.get('attachments',[]):
        if item['kind']=='face':text.append({'type':'face','data':{'id':str(item['id'])}})
        elif item['kind']=='image':images.append({'type':'image','data':{'file':Path(item['path']).as_uri(),'sub_type':1 if item.get('sticker',True) else 0}})
        elif item['kind']=='record':records.append([{'type':'record','data':{'file':Path(item['path']).as_uri()}}])
    split=layout=='separate' or layout=='mostly_separate' and int.from_bytes(hashlib.sha256(key.encode()).digest()[:2],'big')%4!=0
    if text and images:
        out=[text,images] if split else [text+images]
    else:out=[text or images] if text or images else []
    return out+records

def confirmed_history(result,packets,partial=False):
    """Record only bubbles for which QQ actually returned a message identifier."""
    text=[];images=[];faces=[];records=[]
    attached={Path(a['path']).as_uri():a for a in result.get('attachments',[]) if a.get('path')}
    for packet in packets:
        for segment in packet:
            kind=segment['type'];data=segment.get('data',{})
            if kind=='text':text.append(data.get('text',''))
            elif kind=='image':images.append(attached.get(data.get('file'),{}))
            elif kind=='record':records.append(attached.get(data.get('file'),{}))
            elif kind=='face':faces.append(next((a.get('name',str(data['id'])) for a in result.get('attachments',[]) if a.get('kind')=='face' and str(a.get('id'))==str(data.get('id'))),str(data.get('id',''))))
    value='\n'.join(text) or ''.join(a.get('text','') for a in records) or (f'[已发送{len(images)}张表情]' if images else '[已发送语音]' if records else '')
    if faces:value+='\n[本轮QQ默认表情：'+'、'.join(faces)+']'
    captions='；'.join(a.get('caption','') for a in images if a.get('caption'))
    if captions:value+='\n[本轮已发送表情的内容记录，仅供后续追问参考：'+captions[:600]+']'
    if partial:value+='\n[发送状态记录：仅以上内容取得QQ回执；后续消息未确认发送，不能声称附件都已发出。]'
    return value
