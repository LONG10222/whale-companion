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
