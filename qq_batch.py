"""Short, bounded receive window. Source events remain individually deduplicated."""
import threading,time
from chat_permissions import control_text

class ReceiveBatcher:
    def __init__(self,bridge,quiet=1.4):
        self.bridge=bridge;self.quiet=quiet;self.lock=threading.RLock();self.condition=threading.Condition(self.lock);self.pending={}
    def immediate(self,obj,text):
        return control_text(text) or any(s.get('type')=='reply' for s in obj.get('message',[])) or text.startswith(('反馈','记住','停止手机任务','取消手机任务','手机任务状态','撤销授权','停止工作任务','取消工作任务','停止创作任务','取消创作任务'))
    def submit(self,obj,epoch,text,images,videos):
        b=self.bridge;peer=str(obj['user_id']);bot=str(obj['self_id']);mid=str(obj['message_id']);key=(epoch,bot,peer);now=time.monotonic()
        with self.condition:
            batch=self.pending.get(key)
            standalone=self.immediate(obj,text)
            if batch and standalone:
                batch['immediate']=True;self.pending.pop(key,None);self.condition.notify_all();batch=None
            if batch and not standalone and len(batch['objects'])<12 and batch['chars']+len(text)<=6000 and len(batch['images'])+len(images)<=3 and len(batch['videos'])+len(videos)<=1 and sum(s.get('type')=='record' for o in batch['objects']+[obj] for s in o.get('message',[]))<=3:
                if not b.chat.memory.claim(bot,mid,peer):return False
                batch['objects'].append(obj);batch['texts'].append(text);batch['images'].extend(images);batch['videos'].extend(videos);batch['last']=now;batch['chars']+=len(text)
                self.condition.notify_all();return True
            if not b.slots.acquire(False):return False
            if not b.chat.memory.claim(bot,mid,peer):b.slots.release();return False
            batch={'key':key,'epoch':epoch,'objects':[obj],'texts':[text],'images':list(images),'videos':list(videos),'created':now,'last':now,'chars':len(text),'immediate':standalone}
            if not standalone:self.pending[key]=batch
            try:b.pool.submit(self.run,batch)
            except RuntimeError:b.chat.memory.state(bot,mid,'cancelled');b.slots.release();return False
            self.condition.notify_all();return True
    def run(self,batch):
        b=self.bridge;first=batch['objects'][0];entered=False
        try:
            with self.condition:
                while not batch['immediate']:
                    if b.stop.is_set() or not b.accepted(first,batch['epoch']):break
                    quiet=2.5 if len(batch['objects'])==1 and batch['texts'][0].strip() in {'现在','然后','就是','等一下'} else self.quiet
                    left=min(batch['last']+quiet,batch['created']+6)-time.monotonic()
                    if left<=0:break
                    self.condition.wait(left)
                if self.pending.get(batch['key']) is batch:self.pending.pop(batch['key'],None)
                objects=list(batch['objects']);mids=[str(o['message_id']) for o in objects]
                merged={**first,'message':[s for o in objects for s in o.get('message',[])],'_batch_objects':objects}
                text='\n'.join(t for t in batch['texts'] if t)
            if len(objects)>1:b.event('message_batch',f'已将同一好友连续的{len(objects)}条消息一起处理。')
            entered=True;b.process(merged,batch['epoch'],text,batch['images'],batch['videos'],batch_mids=mids)
        finally:
            if not entered:
                for o in batch['objects']:b.chat.memory.state(str(o['self_id']),str(o['message_id']),'cancelled')
                b.slots.release()
    def wake(self):
        with self.condition:self.condition.notify_all()
