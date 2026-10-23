"""Transport-derived identities and independent conversation keys."""
import re

def parts(peer):
    match=re.fullmatch(r'group:([1-9]\d{4,11}):([1-9]\d{4,11})',str(peer))
    return (match.group(1),match.group(2)) if match else ('',str(peer))

def actor(peer):return parts(peer)[1]
def group(peer):return parts(peer)[0]
def memory_peer(event):
    uid=str(event['user_id'])
    return 'group:'+str(event['group_id'])+':'+uid if event.get('message_type')=='group' else uid
def event_mid(event):
    mid=str(event['message_id'])
    return 'group:'+str(event['group_id'])+':'+mid if event.get('message_type')=='group' else mid

def transport_context(event,epoch):
    result={'bot':str(event['self_id']),'mid':event_mid(event),'epoch':epoch,'deliver':True}
    if event.get('message_type')=='group':result.update(group_id=str(event['group_id']),actor_id=str(event['user_id']),memory_peer=memory_peer(event),group_revision=event.get('_group_revision'))
    return result

def destination(peer,message):
    gid,uid=parts(peer)
    return ('send_group_msg',{'group_id':int(gid),'message':message}) if gid else ('send_private_msg',{'user_id':int(uid),'message':message})
