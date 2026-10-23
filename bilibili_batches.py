"""Conversation-scoped Bilibili batches. Status, approval and cancellation are distinct."""
from contextlib import closing
import hashlib,json,re,sqlite3,uuid

LABELS={'like':'点赞','coin':'投币','comment':'评论'}

def requested_kinds(text):
    # A negation covers its whole clause, including coordinated actions.
    # Contrast words start a new positive clause ("不要投币，只点赞").
    text=re.sub(r'(?:不要|不用|不必|不能|不许|禁止|不需要|不想|先不|(?<![分特区])别(?=给|帮|去|再|点赞|投币|评论|留言)|不(?=点赞|投币|评论|留言))(?:(?!但|只|不过|改为|而是|[，,。；;\n]).)*','',text)
    kinds=[]
    if re.search(r'点赞|点.{0,3}赞',text):kinds.append('like')
    if re.search(r'投币|投.{0,4}(?:枚|个币|硬币)',text):kinds.append('coin')
    if re.search(r'评论|留言|留.{0,4}句',text):kinds.append('comment')
    return kinds

def status_question(text):
    return bool(requested_kinds(text) and re.search(r'了吗|了没|没(?:有)?[？?]?$|成功.{0,3}[吗没]|状态|结果|怎么样|(?:发出|投了|点了).{0,3}[吗没？?]|(?:还|一直|怎么)?没(?:有)?(?:发|投|点|评论)|(?:点赞|投币|评论).{0,3}还?没',text)
                and not re.search(r'直接|现在就|帮我|请|先别|不要|别发',text))

def cancellation(text):
    return bool(re.fullmatch(r'(?:那就|这次|先|都)?(?:算了|不用了|取消|不弄了|不搞了|停下|停了|撤销|别发了|别投了|不要发了|不要投了)(?:吧|了|这次|这一批)?[。！!\s]*',text.strip())
                or len(text)<35 and re.match(r'^算了(?:算了|[，,。！!\s]|别|不用|这次|先)',text.strip()))

def affirmation(text):
    text=text.strip()
    if len(text)>50 or re.search(r'[?？]|不要|别|等等|先不|不行|不可以|改成|改一下|取消',text):return False
    return bool(re.fullmatch(r'(?:嗯[，, ]*)?(?:可以|好的?|同意|确认|没问题|行|OK|ok|就这样|就这么办)(?:了|吧|啊)?[。！!\s]*',text)
                or re.search(r'^(?:那就|就|直接|现在|马上|确认)?(?:照这个|按这个|这句|这条)?(?:发(?:这句|这条)?|投(?:[一二两12](?:枚|个)(?:币)?|币)?)(?:了|吧|啊)?[。！!\s]*$',text)
                or requested_kinds(text) and re.fullmatch(r'(?:那就|直接|现在|马上|确认|给这条|帮我)?(?:点赞|投币|评论|留言)(?:(?:并且|然后|并|和|、|及|再|，|,)?(?:点赞|投币|评论|留言))*(?:吧|了)?[。！!\s]*',text)
                or re.fullmatch(r'(?:你|那|就|请|帮我|麻烦你|直接|现在|照这个|按刚才|把刚才那条|把这句|这句|这条|评论|，|,|\s){1,20}(?:投币|发(?:布|出)?(?:去)?|点赞)(?:吧|就行|好了|啦|了)?[。！!\s]*',text))

class InteractionBatches:
    def __init__(self,actions):
        self.a=actions;self.s=actions.s
        with self.s.db() as db:
            columns={r[1] for r in db.execute('PRAGMA table_info(proposals)')}
            if 'batch_id' not in columns:db.execute("ALTER TABLE proposals ADD COLUMN batch_id TEXT NOT NULL DEFAULT ''")
            if 'error' not in columns:db.execute("ALTER TABLE proposals ADD COLUMN error TEXT NOT NULL DEFAULT ''")
            db.executescript('''
                CREATE TABLE IF NOT EXISTS social_batches(id TEXT PRIMARY KEY,peer TEXT,bvid TEXT,created REAL,expires REAL,state TEXT,source_local INTEGER);
                CREATE INDEX IF NOT EXISTS social_batches_peer ON social_batches(peer,created);
                CREATE UNIQUE INDEX IF NOT EXISTS proposals_batch_kind ON proposals(batch_id,kind) WHERE batch_id!='';
                CREATE TABLE IF NOT EXISTS social_cancellations(peer TEXT,bvid TEXT,at REAL,PRIMARY KEY(peer,bvid));
                CREATE TABLE IF NOT EXISTS social_decisions(id TEXT PRIMARY KEY,peer TEXT,at REAL,state TEXT);
            ''')
        self.import_legacy()

    def history(self,peer,after=0):
        path=self.s.root/'data/qq/qq-memory.sqlite3'
        if not path.is_file():return []
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.3)) as db:
            return db.execute('SELECT id,role,content,at FROM messages WHERE peer=? AND id>? ORDER BY id LIMIT 100',(str(peer),after)).fetchall()

    def import_legacy(self):
        """Keep both legacy drafts, and honor a later owner cancellation locally."""
        with self.s.db() as db:rows=[dict(r) for r in db.execute("SELECT * FROM proposals WHERE batch_id='' ORDER BY created")]
        grouped={}
        for row in rows:grouped.setdefault((row['peer'],row['bvid']),[]).append(row)
        for (peer,bvid),group in grouped.items():
            ident='legacy_'+hashlib.sha256((peer+':'+bvid+':'+str(group[0]['created'])).encode()).hexdigest()[:24]
            created=min(r['created'] for r in group);source_local=min(r['source_local'] for r in group)
            cancelled=next((r for r in reversed(self.history(peer,source_local)) if r[1]=='user' and r[3]>=created and cancellation(r[2])),None)
            state='cancelled' if cancelled else 'expired' if max(r['expires'] for r in group)<self.s.clock() else 'open'
            with self.s.db() as db:
                db.execute('INSERT OR IGNORE INTO social_batches VALUES(?,?,?,?,?,?,?)',(ident,peer,bvid,created,max(r['expires'] for r in group),state,source_local))
                chosen={r['kind']:r for r in group}
                for kind,row in chosen.items():
                    new_state='cancelled' if cancelled else 'pending' if state=='open' and row['state'] in {'pending','replaced'} else row['state']
                    db.execute('UPDATE proposals SET batch_id=?,state=? WHERE id=?',(ident,new_state,row['id']))
                for row in group:
                    if chosen[row['kind']]['id']!=row['id']:db.execute("UPDATE proposals SET batch_id=? WHERE id=?",('retired_'+row['id'],row['id']))
                if cancelled:db.execute('INSERT INTO social_cancellations VALUES(?,?,?) ON CONFLICT(peer,bvid) DO UPDATE SET at=max(at,excluded.at)',(peer,bvid,cancelled[3]))

    def current(self,peer,include_finished=True):
        with self.s.db() as db:
            row=db.execute('SELECT * FROM social_batches WHERE peer=? '+('' if include_finished else "AND state='open' ")+'ORDER BY created DESC,rowid DESC LIMIT 1',(str(peer),)).fetchone()
        return dict(row) if row else None

    def receipt_target(self,peer,text=''):
        batch=self.current(peer)
        explicit=re.search(r'BV[0-9A-Za-z]{10}',text)
        with self.s.db() as db:
            row=db.execute('SELECT bvid,at FROM social_actions WHERE peer=? '+('AND bvid=? ' if explicit else '')+'ORDER BY at DESC LIMIT 1',
                (str(peer),explicit.group()) if explicit else (str(peer),)).fetchone()
        if explicit and batch and batch['bvid']!=explicit.group():batch=None
        if row and (not batch or row['bvid']!=batch['bvid'] and row['at']>batch['created']):
            return {'id':'','peer':str(peer),'bvid':row['bvid'],'created':row['at'],'expires':0,'state':'completed','source_local':0}
        return batch

    def status_for(self,peer,text):
        target=self.receipt_target(peer,text);explicit=re.search(r'BV[0-9A-Za-z]{10}',text)
        if explicit and not target:
            message='这条视频还没有我的互动回执，先不说已经点了、投了或评论了。'
            return {'message':message,'review_text':message,'bvid':explicit.group(),'items':[],'status_only':True}
        return self.status(peer,target)

    def items(self,batch_id):
        with self.s.db() as db:return [dict(r) for r in db.execute('SELECT * FROM proposals WHERE batch_id=? ORDER BY CASE kind WHEN \'like\' THEN 0 WHEN \'coin\' THEN 1 ELSE 2 END',(batch_id,))]

    def automatic_allowed(self,peer,source):
        with self.s.db() as db:row=db.execute('SELECT at FROM social_cancellations WHERE peer=? AND bvid=?',(str(peer),source['bvid'])).fetchone()
        return not row or source.get('read_at',0)>row[0]

    def _reviewed_context(self,peer,batch,kinds=None):
        """Status questions may intervene; unrelated user topics form a barrier."""
        history=self.history(peer,batch['source_local'])
        for _,role,text,_ in history:
            if role!='user':continue
            if cancellation(text):return False
            explicit=re.search(r'BV[0-9A-Za-z]{10}',text)
            if explicit and explicit.group()!=batch['bvid']:return False
            if status_question(text) or affirmation(text) or re.fullmatch(r'(?:呃|额|啊|唉|哦|嗯|好|这|那|\.\.\.|…|[，。！!?？\s])+',text.strip()):continue
            if requested_kinds(text) and len(text)<70 and not re.search(r'[?？]|别的|另外|换一|如何|怎么|为什么',text):continue
            return False
        source=self.a.source(peer,batch['bvid']);visible='\n'.join(t for _,role,t,_ in history if role=='assistant')
        selected=[r for r in self.items(batch['id']) if r['state']=='pending' and (not kinds or r['kind'] in kinds)]
        if source['url'] not in visible:return False
        for row in selected:
            if row['kind']=='comment' and row['text'] not in visible:return False
            if row['kind']=='coin' and f'投{row["amount"]}枚币' not in visible:return False
        return bool(selected)

    def cancel(self,peer,batch=None):
        batch=batch or self.current(peer)
        if not batch:return None
        with self.s.db() as db:
            db.execute("UPDATE social_batches SET state='cancelled' WHERE id=?",(batch['id'],))
            db.execute("UPDATE proposals SET state='cancelled' WHERE batch_id=? AND state IN ('pending','draft_failed')",(batch['id'],))
            db.execute('INSERT INTO social_cancellations VALUES(?,?,?) ON CONFLICT(peer,bvid) DO UPDATE SET at=max(at,excluded.at)',(str(peer),batch['bvid'],self.s.clock()))
        message='好，这一批停下了，没执行的项目都不继续。已经完成的仍保留原来的结果。'
        return {'executed':False,'cancelled':True,'message':message,'review_text':message,'batch_id':batch['id']}

    def status(self,peer,batch=None):
        batch=batch or self.receipt_target(peer)
        if not batch:return {'message':'我这边还没有这轮B站互动记录，先不猜已经做了。','review_text':'我这边还没有这轮B站互动记录，先不猜已经做了。','items':[],'status_only':True}
        source=self.a.source(peer,batch['bvid']);proposals=self.items(batch['id']);states={r['kind']:r for r in proposals}
        with self.s.db() as db:
            actual=[dict(r) for r in db.execute('SELECT id,kind,state,amount,result FROM social_actions WHERE peer=? AND bvid=? ORDER BY at',(str(peer),batch['bvid']))]
        for item in actual:
            if item['state'] in {'confirmed','sending','uncertain'}:
                bound=next((r for r in proposals if 'proposal:'+r['id']==item['id']),None)
                try:published=json.loads(item['result']).get('comment','')
                except (ValueError,TypeError,AttributeError):published=''
                states[item['kind']]={**(bound or {}),**item,'text':published or (bound['text'] if bound else '')}
        phrases=[];out=[]
        for kind in ['like','coin','comment']:
            row=states.get(kind);state=row.get('state') if row else None
            if state=='confirmed':
                phrase='赞已经点上了' if kind=='like' else f'投了{row.get("amount",1)}枚币' if kind=='coin' else '评论已经发出'
                if kind=='comment' and row.get('text'):phrase+='：“'+row['text']+'”'
            elif state in {'uncertain','sending','consumed'}:phrase=LABELS[kind]+'的回执还没核对上，先不重复操作'
            elif state=='pending':phrase=LABELS[kind]+'还没执行，内容已经留好'
            elif state=='draft_failed':phrase='评论草稿这次没准备好，其他项各按各的结果'
            elif state=='failed':phrase=LABELS[kind]+'这次没做成'+('：'+row['error'] if row.get('error') else '')
            elif state=='expired':phrase=LABELS[kind]+'这份提案已过期，没有执行'
            elif state=='cancelled' or batch['state']=='cancelled':phrase=LABELS[kind]+'这项已停下'
            else:phrase='我这边还没有'+LABELS[kind]+'成功的记录'
            phrases.append(phrase);out.append({'action':kind,'state':state or 'not_requested','amount':row.get('amount',0) if row else 0})
        return {'message':'；'.join(phrases)+'。','review_text':'；'.join(phrases)+'。','items':out,'batch_id':batch['id'],'bvid':batch['bvid'],'source_url':source['url'],'status_only':True}

    def review_text(self,peer,batch):
        source=self.a.source(peer,batch['bvid']);parts=[]
        for row in self.items(batch['id']):
            if row['state']!='pending':continue
            if row['kind']=='comment':parts.append('评论草稿：\n“'+row['text']+'”')
            elif row['kind']=='coin':parts.append(f'投{row["amount"]}枚币')
            else:parts.append('给这条点赞')
        return '《'+source['title']+'》\n'+'\n'.join(parts)+'\n就按这份做吗？\n'+source['url']

    def prepare(self,peer,bvid,action,comment,amount,*,raw_text,context,local):
        source=self.a.source(peer,bvid)
        if not local:
            if not self.a.origin(peer,context):raise ValueError('需要本人真实私聊的这次请求。')
            if cancellation(raw_text):return self.cancel(peer)
            if status_question(raw_text):return self.status_for(peer,raw_text)
            if affirmation(raw_text):
                handled=self.handle_chat(peer,raw_text,context)
                if handled:return handled|{'review_text':handled['message']}
            kinds=requested_kinds(raw_text)
            if re.search(r'[?？]|能不能|可不可以|是不是|为什么|是否|如何',raw_text):raise ValueError('这句像是在问能不能做，这次先没动账号。')
            if action not in kinds:raise ValueError('这句还没明确要求这项操作，我先不替你决定。')
        else:kinds=[action]
        previous=self.current(peer,include_finished=False)
        if previous and previous['bvid']==bvid and previous['expires']>self.s.clock() and (local or self._reviewed_context(peer,previous)):batch=previous
        else:
            ident=uuid.uuid4().hex;now=self.s.clock()
            batch={'id':ident,'peer':str(peer),'bvid':bvid,'created':now,'expires':now+600,'state':'open','source_local':self.a.latest(peer)[0]}
            with self.s.db() as db:db.execute('INSERT INTO social_batches VALUES(?,?,?,?,?,?,?)',tuple(batch[k] for k in ['id','peer','bvid','created','expires','state','source_local']))
        existing={r['kind']:r for r in self.items(batch['id'])}
        for kind in kinds:
            if kind in existing:continue
            text='';state='pending';number=amount if kind=='coin' else 0
            if kind=='comment':
                try:text=self.a.safe_comment(peer,comment or self.a.draft_comment(source))
                except Exception:text='';state='draft_failed'
            if kind=='coin' and (type(number) is not int or number not in {1,2}):raise ValueError('一次只能投1或2枚币。')
            with self.s.db() as db:db.execute('INSERT INTO proposals(id,peer,bvid,kind,text,amount,created,expires,state,source_local,batch_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (uuid.uuid4().hex,str(peer),bvid,kind,text,number,batch['created'],batch['expires'],state,batch['source_local'],batch['id']))
        rows=self.items(batch['id']);chosen=next(r for r in rows if r['kind']==action)
        immediate=[r['kind'] for r in rows if r['state']=='pending' and not local and (r['kind']=='like' or self.a.mode(r['kind'])=='automatic')]
        if immediate:self.run(peer,batch,immediate)
        pending=any(r['state']=='pending' for r in self.items(batch['id']))
        reviewed=(self.status(peer,batch)['message']+'\n'+self.review_text(peer,batch)) if pending else self.status(peer,batch)['message']+'\n'+source['url']
        return {'prepared':pending,'proposal_id':chosen['id'],'batch_id':batch['id'],'action':action,'bvid':bvid,
            'comment':chosen['text'],'amount':chosen['amount'],'review_text':reviewed,'message':reviewed,'sent':not pending,
            'executed':any(r['state']=='confirmed' for r in self.status(peer,batch)['items']),
            'items':self.status(peer,batch)['items']}

    def run(self,peer,batch,kinds):
        for row in self.items(batch['id']):
            if row['kind'] not in kinds or row['state']!='pending':continue
            with self.s.db() as db:
                db.execute('BEGIN IMMEDIATE')
                current=db.execute('SELECT state FROM social_batches WHERE id=?',(batch['id'],)).fetchone()
                if not current or current['state']=='cancelled':break
                if not db.execute("UPDATE proposals SET state='consumed' WHERE id=? AND state='pending'",(row['id'],)).rowcount:continue
            error=''
            try:
                result=self.a.execute(peer,row['bvid'],row['kind'],row['amount'],row['text'],ident='proposal:'+row['id'])
                state='confirmed' if result.get('executed') or result.get('state')=='confirmed' else result.get('state','uncertain')
            except ValueError as exc:state=self.a.action_state('proposal:'+row['id']) or 'failed';error=str(exc)[:180]
            with self.s.db() as db:db.execute('UPDATE proposals SET state=?,error=? WHERE id=?',(state,error,row['id']))
        with self.s.db() as db:
            if not db.execute("SELECT 1 FROM proposals WHERE batch_id=? AND state='pending'",(batch['id'],)).fetchone():
                db.execute("UPDATE social_batches SET state='completed' WHERE id=? AND state='open'",(batch['id'],))
        return self.status(peer,batch)

    def confirm_local(self,peer,proposal_id):
        if not self.s.allowed(peer):raise ValueError('只有管理员本人可以确认。')
        with self.s.db() as db:row=db.execute("SELECT * FROM proposals WHERE id=? AND peer=? AND state='pending'",(proposal_id,str(peer))).fetchone()
        if not row or row['expires']<self.s.clock():raise ValueError('这份提案已过期或已处理，没有再次执行。')
        with self.s.db() as db:batch=db.execute('SELECT * FROM social_batches WHERE id=?',(row['batch_id'],)).fetchone()
        if not batch or batch['state']=='cancelled':raise ValueError('这批已取消，没有继续执行。')
        result=self.run(peer,dict(batch),[row['kind']]);result.update(executed=any(r['action']==row['kind'] and r['state']=='confirmed' for r in result['items']),action=row['kind'],amount=row['amount'])
        return result

    def handle_chat(self,peer,text,context):
        if not self.s.allowed(peer) or not self.a.origin(peer,context):return None
        if status_question(text):return self.status_for(peer,text)
        batch=self.current(peer)
        if not batch:return None
        if cancellation(text):return self.cancel(peer,batch)
        if not affirmation(text):return None
        if batch['state']=='cancelled':return {'message':'那一批已经停下，没有继续评论或投币。','cancelled':True,'executed':False}
        if batch['state']!='open' or batch['expires']<self.s.clock():return None
        if re.fullmatch(r'(?:嗯[，, ]*)?(?:好(?:的)?|可以|行|没问题)[。！!\s]*',text.strip()):
            # A status reply is not an approval question. "好" may simply close it.
            visible=next((t for _,role,t,_ in reversed(self.history(peer,batch['source_local'])) if role=='assistant'),'')
            if not re.search(r'发吗|确认吗|按这份做吗',visible):return None
        kinds=requested_kinds(text)
        if not kinds:
            if '发' in text:kinds=['comment']
            elif '投' in text:kinds=['coin']
        if not self._reviewed_context(peer,batch,kinds):return None
        if 'coin' in kinds and re.search(r'[一二两12]',text):
            amount=1 if re.search(r'[一1]',text) else 2
            coin=next((r for r in self.items(batch['id']) if r['kind']=='coin'),None)
            if coin and coin['amount']!=amount:raise ValueError('你这次说的枚数和刚才不同，先把提案改清楚再投。')
        wanted=kinds or [r['kind'] for r in self.items(batch['id']) if r['state']=='pending']
        result=self.run(peer,batch,wanted);result['executed']=any(r['state']=='confirmed' for r in result['items'])
        return result
