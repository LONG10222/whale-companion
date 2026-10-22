"""Authenticated loopback OneBot transport. Only configured private peers can reply."""
from pathlib import Path
import concurrent.futures,json,queue,re,secrets,threading,time,uuid
from websockets.sync.client import connect
from qq_chat import QQConversation
from qq_videos import video_segment

ROOT=Path(__file__).resolve().parent
class QQActionRejected(RuntimeError):pass

class QQBridge:
    def __init__(self,root=ROOT,conversation=None):
        self.root=Path(root);self.file=self.root/'qq.local.json';self.lock=threading.RLock();self.stop=threading.Event()
        self.ws=None;self.epoch='';self.login_id='';self.connected=False;self.last_error='';self.events=[];self.pending={}
        self.pool=concurrent.futures.ThreadPoolExecutor(max_workers=1);self.slots=threading.BoundedSemaphore(4)
        self.chat=conversation or QQConversation(self.root);self.proactive=None
        from qq_identity import Contacts
        self.contacts=Contacts(self.root)
        faces=self.root/'configs/qq-face-descriptions.json'
        self.faces=json.loads(faces.read_text(encoding='utf-8')) if faces.exists() else {}
        if not self.file.exists():self.file.write_text(json.dumps({'enabled':False,'bot_id':'','allowed_private_ids':[],
            'voice_enabled':True,'onebot_token':secrets.token_urlsafe(32)},indent=2),encoding='utf-8')
        self.cfg=json.loads(self.file.read_text(encoding='utf-8'));self.thread=None
        from qq_batch import ReceiveBatcher
        self.batcher=ReceiveBatcher(self)
    def configure(self,enabled,bot_id,peers,voice_enabled):
        target=self.cfg.get('test_contact')
        if target and any(p!=target['user_id'] for p in peers):raise ValueError('本次只允许给 owner 测试，不能添加其他好友。')
        if self.cfg.get('locked_bot_id') and bot_id!=self.cfg['locked_bot_id']:raise ValueError('本次只连接已经扫码确认的测试小号。')
        if enabled and (not re.fullmatch(r'[1-9]\d{4,11}',bot_id) or not peers):raise ValueError('请填写机器人QQ号和允许私聊的好友QQ号。')
        if any(not re.fullmatch(r'[1-9]\d{4,11}',p) or p==bot_id for p in peers):raise ValueError('好友QQ号不正确，或与机器人账号相同。')
        with self.lock:
            self.cfg.update(enabled=enabled,bot_id=bot_id,allowed_private_ids=list(dict.fromkeys(peers)),voice_enabled=voice_enabled)
            stage=self.file.with_suffix('.staging.json');stage.write_text(json.dumps(self.cfg,ensure_ascii=False,indent=2),encoding='utf-8');stage.replace(self.file)
        self.batcher.wake()
    def event(self,kind,detail):
        with self.lock:self.events.append({'at':time.time(),'kind':kind,'detail':detail});self.events=self.events[-40:]
    def snapshot(self):
        with self.lock:return {'connected':self.connected,'login_id':self.login_id,'enabled':self.cfg['enabled'],
            'bot_id':self.cfg['bot_id'],'allowed_private_ids':self.cfg['allowed_private_ids'],'voice_enabled':self.cfg['voice_enabled'],
            'account_matches':bool(self.login_id and self.login_id==self.cfg['bot_id']),
            'last_error':self.last_error,'events':self.events[-20:],'groups_enabled':False,'test_contact':self.cfg.get('test_contact'),
            'voice_input':self.chat.records.status() if hasattr(self.chat,'records') else {'ready':False},'dialogue_context_version':3}
    def start(self):
        if self.thread and self.thread.is_alive():return
        self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()
    def close(self):
        self.stop.set()
        self.batcher.wake()
        if self.ws:self.ws.close()
        self.pool.shutdown(wait=False,cancel_futures=True)
    def call(self,action,params=None,timeout=20):
        echo=uuid.uuid4().hex;box=queue.Queue(maxsize=1)
        with self.lock:
            ws=self.ws
            if not ws:raise RuntimeError('QQ连接已断开。')
            self.pending[echo]=box
        try:
            ws.send(json.dumps({'action':action,'params':params or {},'echo':echo},ensure_ascii=False))
            try:result=box.get(timeout=timeout)
            except queue.Empty:raise RuntimeError('QQ未返回操作回执；不会自动重发。') from None
            if result.get('status')!='ok' or result.get('retcode')!=0:raise QQActionRejected('QQ连接器报告操作失败。')
            return result.get('data')
        finally:
            with self.lock:self.pending.pop(echo,None)
    def identify(self,epoch):
        try:
            result=self.call('get_login_info');account=str(result.get('user_id',''))
            with self.lock:
                if epoch==self.epoch:self.login_id=account
            self.event('connection','QQ已连接，请核对登录账号。')
            if account==self.cfg.get('locked_bot_id')==self.cfg.get('bot_id'):
                try:
                    for friend in self.call('get_friend_list') or []:
                        self.contacts.observe(friend['user_id'],friend.get('nickname',''),friend.get('remark',''))
                except Exception:self.event('contacts','本次好友目录未能刷新，保留已有联系人记录。')
        except Exception as exc:self.event('error',str(exc))
    def run(self):
        while not self.stop.is_set():
            try:
                with connect('ws://127.0.0.1:3001',additional_headers={'Authorization':'Bearer '+self.cfg['onebot_token']},
                             open_timeout=5,max_size=20*1024**2,max_queue=4,proxy=None) as ws:
                    with self.lock:self.ws=ws;self.connected=True;self.login_id='';self.epoch=uuid.uuid4().hex;self.last_error=''
                    epoch=self.epoch;threading.Thread(target=self.identify,args=(epoch,),daemon=True).start()
                    while not self.stop.is_set():
                        try:raw=ws.recv(timeout=1)
                        except TimeoutError:continue
                        obj=json.loads(raw)
                        with self.lock:box=self.pending.get(obj.get('echo'))
                        if box:
                            try:box.put_nowait(obj)
                            except queue.Full:pass
                        elif obj.get('post_type')=='message':self.accept(obj,epoch)
            except Exception:
                with self.lock:self.connected=False;self.login_id='';self.last_error='等待独立QQ登录并开启连接。'
            finally:
                with self.lock:self.ws=None;self.connected=False
            self.stop.wait(4)
    def accepted(self,obj,epoch):
        c=self.cfg;peer=str(obj.get('user_id',''));bot=str(obj.get('self_id',''))
        return bool(c['enabled'] and epoch==self.epoch and self.connected and self.login_id==c['bot_id']==bot
            and obj.get('message_type')=='private' and obj.get('sub_type')=='friend'
            and peer in c['allowed_private_ids'] and peer!=bot and obj.get('message_id') is not None)
    def accept(self,obj,epoch):
        with self.lock:
            if not self.accepted(obj,epoch):return False
        segments=obj.get('message')
        if not isinstance(segments,list):return False
        sender=obj.get('sender') or {}
        self.contacts.observe(obj['user_id'],sender.get('nickname',''),sender.get('remark',''))
        if self.proactive:
            try:self.proactive.incoming(obj)
            except Exception:self.event('proactive_error','主动话题的来信状态未能更新，暂不准备新话题。');self.proactive.configure(False)
        text=''.join(s.get('data',{}).get('text','') if s.get('type')=='text' else
            '[QQ表情：'+self.faces.get(str(s.get('data',{}).get('id')),'未识别编号')+']' if s.get('type')=='face' else '' for s in segments).strip()[:2000]
        image_segments=[s for s in segments if s.get('type') in ('image','mface')][:3]
        videos=[s for s in segments if video_segment(s)][:1]
        records=[s for s in segments if s.get('type')=='record']
        if not text and not image_segments and not videos and not records:return False
        peer=str(obj['user_id']);bot=str(obj['self_id']);mid=str(obj['message_id'])
        return self.batcher.submit(obj,epoch,text,image_segments,videos)
    def recheck_video(self):
        target=self.cfg.get('test_contact') or {}
        if target.get('name')!='owner':raise ValueError('本轮仅允许读取owner的视频。')
        history=self.call('get_friend_msg_history',{'user_id':int(target['user_id']),'count':50})
        messages=sorted((history or {}).get('messages',[]),key=lambda m:m.get('time',0),reverse=True)
        for message in messages:
            segments=message.get('message')
            if str(message.get('sender',{}).get('user_id'))!=target['user_id'] or not isinstance(segments,list):continue
            if not any(video_segment(s) for s in segments):continue
            event={'post_type':'message','message_type':'private','sub_type':'friend','self_id':self.cfg['bot_id'],
                   'user_id':target['user_id'],'message_id':message['message_id'],'message':segments}
            accepted=self.accept(event,self.epoch)
            return {'accepted':accepted,'message':'已开始读取owner最近的视频。' if accepted else '这条视频已处理过、任务较多或回复尚未启用。请稍后检查，或让owner重发。'}
        return {'accepted':False,'message':'最近50条消息中没有owner发来的视频，请重新发送。'}
    def quoted_context(self,peer,bot,remote_id):
        # A quote is resolved only within this exact private conversation and bot.
        with self.chat.memory.learning.db() as db:
            row=db.execute('SELECT local_id FROM channel_deliveries WHERE peer=? AND bot=? AND remote_id=?',(peer,bot,str(remote_id))).fetchone()
        if row:
            with self.chat.memory.db() as db:
                message=db.execute('SELECT role,content FROM messages WHERE peer=? AND id=?',(peer,row['local_id'])).fetchone()
            if message:return {'role':message[0],'text':message[1].split('\n[本轮')[0][:6000]}
        try:
            history=self.call('get_friend_msg_history',{'user_id':int(peer),'count':100}) or {}
            message=next((m for m in history.get('messages',[]) if str(m.get('message_id'))==str(remote_id) and str((m.get('sender') or {}).get('user_id')) in {peer,bot}),None)
            if not message:return {'missing':True}
            segments=message.get('message',[])
            if not isinstance(segments,list):return {'missing':True}
            text=''.join(s.get('data',{}).get('text','') for s in segments if s.get('type')=='text')
            if not text:
                record=next((s for s in segments if s.get('type')=='record'),None)
                if record:text=self.chat.records.analyze(record,self.call,self.event)['text']
            return {'text':text[:6000],'kind':'image' if any(s.get('type')=='image' for s in segments) else 'text','images':[s for s in segments if s.get('type') in {'image','mface'}][:3]}
        except Exception:return {'missing':True}
    def process(self,obj,epoch,text,image_segments=None,videos=None,batch_mids=None):
        peer=str(obj['user_id']);bot=str(obj['self_id']);mid=str(obj['message_id'])
        def mark(state):
            for source_mid in (batch_mids or [mid]):self.chat.memory.state(bot,source_mid,state)
        outbound_started=False;confirmed_packets=[];delivery_ids=[];history_recorded=False;result=None
        from usage_ledger import USAGE_CONTEXT,Ledger
        usage_token=USAGE_CONTEXT.set({'peer':peer,'role':self.contacts.role(peer),'job_id':'qq:'+bot+':'+mid})
        try:
            with self.lock:
                if not self.accepted(obj,epoch):mark('cancelled');return
            self.event('received','收到获准好友的私聊，正在生成回复。')
            quoted=next((s.get('data',{}).get('id') for s in obj.get('message',[]) if s.get('type')=='reply'),None)
            records=[s for s in obj.get('message',[]) if s.get('type')=='record'];heard=[]
            if records:
                texts=[]
                for source in obj.get('_batch_objects',[obj]):
                    pieces=[]
                    for segment in source.get('message',[]):
                        if segment.get('type')=='text':pieces.append(segment.get('data',{}).get('text',''))
                        elif segment.get('type')=='face':pieces.append('[QQ表情：'+self.faces.get(str(segment.get('data',{}).get('id')),'未识别编号')+']')
                        elif segment.get('type')=='record':
                            try:
                                recognized=self.chat.records.analyze(segment,self.call,self.event);pieces.append(recognized['text']);heard.append({k:recognized[k] for k in ['seconds','backend'] if k in recognized})
                            except Exception as exc:
                                self.event('voice_error',str(exc)[:160]);pieces.append('[对方发来语音，但本次没有听清。请自然说明，没有读到的内容不能猜。]')
                    texts.append(''.join(pieces))
                text='\n'.join(texts)[:6000]
                self.event('voice_received',f'本条来信的{len(heard)}段语音已本地识别，准备按内容回应。')
            with self.lock:
                if not self.accepted(obj,epoch):mark('cancelled');return
            learning_reply=self.chat.learning_command(peer,text,quoted) if hasattr(self.chat,'learning_command') and not image_segments and not videos else None
            if learning_reply is not None:
                # Control replies do not become conversation examples or the next feedback target.
                with self.lock:
                    if not self.accepted(obj,epoch):raise RuntimeError('回复范围已变化。')
                mark('sending')
                outbound_started=True
                receipt=self.call('send_private_msg',{'user_id':int(peer),'message':[{'type':'text','data':{'text':learning_reply}}]})
                if not isinstance(receipt,dict) or not receipt.get('message_id'):raise RuntimeError('QQ未提供消息编号，不自动重发。')
                mark('sent');self.event('learning','已处理一条明确的培养反馈。');return
            media=[]
            for segment in videos or []:
                try:media.append(self.chat.videos.analyze(segment,self.call,self.event))
                except Exception as exc:
                    self.event('video_error',str(exc)[:180]);text+='\n[对方发来视频，但本次读取失败：'+str(exc)[:150]+'。请如实说明，不猜内容。]'
            extra={'media_context':media} if media else {}
            if records:extra['audio_context']={'recognized_count':len(heard),'segments':heard}
            if quoted is not None:
                reference=self.quoted_context(peer,bot,quoted);extra['quoted_context']={k:v for k,v in reference.items() if k!='images'}
                if reference.get('images') and not image_segments:image_segments=reference['images']
            if hasattr(self.chat,'contacts') or getattr(self.chat,'tasks',None) or getattr(self.chat,'creative',None):extra['task_context']={'bot':bot,'mid':mid,'epoch':epoch,'deliver':True}
            images=[]
            for segment in image_segments or []:
                try:images.append(self.chat.images.fetch(segment,self.call))
                except Exception:
                    text+='\n[对方发来一张图片，但本次无法取得原图，请如实说明，不猜图中内容。]'
                    self.event('image_error','本次未能取得QQ图片原图。')
            if images:
                self.event('image_read',f'正在用DeepSeek识别{len(images)}张图片；动图采用抽样帧。')
                try:result=self.chat.prepare(peer,text,voice_enabled=self.cfg['voice_enabled'],images=images,**extra)
                except Exception:
                    self.event('image_error','本次图像识别失败，已转为文字说明。')
                    result=self.chat.prepare(peer,text+'\n[本次图像识别失败，请如实告诉对方，没有读到图片内容。]',voice_enabled=self.cfg['voice_enabled'],**extra)
            else:result=self.chat.prepare(peer,text,voice_enabled=self.cfg['voice_enabled'],**extra)
            for tool in result.get('tools',[]):
                info=tool.get('result',{});name=tool['tool']
                Ledger(self.root).record('tool_used',peer=peer,state='failed' if info.get('error') else 'accepted' if info.get('accepted') else 'prepared' if info.get('prepared') else 'completed',details={'tool':name})
                if info.get('error'):detail=info['error']
                elif name=='recall_memory':detail='找到'+str(len(info.get('records',[])))+'条相关历史'
                elif name=='search_web':detail='查到'+str(len(info.get('results',[])))+'条网页来源'
                elif name=='read_web_page' and info.get('ok'):detail='已读取网页'
                elif name=='browse_phone' and info.get('accepted'):detail='后台手机任务已接收'
                else:detail='已准备' if info.get('prepared') else info.get('reason','未准备')
                self.event('tool',name+'：'+detail)
            from qq_delivery import batches as make_batches
            layout=self.chat.agent_settings().get('image_layout','mostly_separate') if hasattr(self.chat,'agent_settings') else 'mostly_separate'
            batches=make_batches(result,bot+':'+mid,layout)
            delivery_ids=[]
            if not batches:raise RuntimeError('本轮没有可发送的回复或附件。')
            for batch in batches:
                with self.lock:
                    if not self.accepted(obj,epoch):raise RuntimeError('账号或启用范围已变化，本轮未继续发送。')
                mark('sending')
                outbound_started=True
                receipt=self.call('send_private_msg',{'user_id':int(peer),'message':batch})
                if not isinstance(receipt,dict) or not receipt.get('message_id'):raise RuntimeError('QQ未提供消息编号，不自动重发。')
                delivery_ids.append(receipt['message_id'])
                confirmed_packets.append(batch)
            mark('sent')
            images_sent=sum(item['kind']=='image' for item in result['attachments'])
            audio_sent=any(item['kind']=='record' for item in result['attachments'])
            spoken=''.join(item.get('text','') for item in result['attachments'] if item['kind']=='record')
            delivery_summary=result['reply'] or spoken or (f'[已发送{images_sent}张表情]' if images_sent else '[已发送语音]' if audio_sent else '')
            faces_sent=[item.get('name','') for item in result['attachments'] if item['kind']=='face']
            if faces_sent:delivery_summary+='\n[本轮QQ默认表情：'+'、'.join(faces_sent)+']'
            if images_sent:
                descriptions='；'.join(item.get('caption','') for item in result['attachments'] if item['kind']=='image' and item.get('caption'))
                if descriptions:delivery_summary+='\n[本轮已发送表情的内容记录，仅供后续追问参考：'+descriptions[:600]+']'
            local_id=self.chat.memory.add(peer,'assistant',delivery_summary);history_recorded=True
            for remote_id in delivery_ids:self.chat.memory.learning.delivery(peer,remote_id,local_id)
            for item in result['attachments']:
                if item['kind']=='image':self.chat.memory.sticker_sent(peer,item['id'])
            self.event('sent','QQ已返回消息编号，回复已提交。')
            try:self.chat.memory.compact(peer,self.chat.client)
            except Exception:pass
        except Exception as exc:
            mark('failed_or_unconfirmed');self.event('error',str(exc)[:180])
            if confirmed_packets and result is not None and not history_recorded:
                from qq_delivery import confirmed_history
                local_id=self.chat.memory.add(peer,'assistant',confirmed_history(result,confirmed_packets,partial=True))
                for remote_id in delivery_ids:self.chat.memory.learning.delivery(peer,remote_id,local_id)
                sent_files={segment.get('data',{}).get('file') for packet in confirmed_packets for segment in packet if segment['type']=='image'}
                for item in result.get('attachments',[]):
                    if item['kind']=='image' and Path(item['path']).as_uri() in sent_files:self.chat.memory.sticker_sent(peer,item['id'])
            if not outbound_started or isinstance(exc,QQActionRejected):
                with self.lock:allowed=self.accepted(obj,epoch)
                if allowed:
                    try:
                        notice='前一部分已发出，但后面的附件没发成。' if confirmed_packets else '刚才这条没处理好，暂时没发成。'
                        receipt=self.call('send_private_msg',{'user_id':int(peer),'message':[{'type':'text','data':{'text':notice}}]})
                        if isinstance(receipt,dict) and receipt.get('message_id'):
                            self.chat.memory.add(peer,'assistant',notice);mark('failed_notified')
                            self.event('failure_notice','本轮准备失败，已向当前获准好友说明。')
                    except Exception:pass
        finally:USAGE_CONTEXT.reset(usage_token);self.slots.release()
