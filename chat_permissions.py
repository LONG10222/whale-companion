"""One-use consent for a specific, already requested read-only phone task."""
from contextlib import contextmanager,closing
from pathlib import Path
from datetime import datetime,timezone,timedelta
import json,re,sqlite3,time,uuid

ZONE=timezone(timedelta(hours=8))
class PhoneQuotaApproval(ValueError):
    def __init__(self,notice):super().__init__(notice);self.notice=notice

def control_text(text):
    text=text.strip()
    return bool(re.fullmatch(r'(?:我)?(?:允许|同意|授权|批准|可以)(?:了)?(?:你)?(?:额外|再)?(?:开|用|看|执行)?(?:这|一)?(?:一次|次)?(?:[，, ]*(?:额外|仅这一次|就这一次|哦|吧))*[。！!\s]*',text)
        or re.fullmatch(r'(?:撤销|取消)(?:这次|本次|所有)?授权[。！!\s]*',text))

class ChatPermissions:
    def __init__(self,root,clock=time.time):
        self.root=Path(root);self.path=self.root/'data/qq/action-consent.sqlite3';self.path.parent.mkdir(parents=True,exist_ok=True);self.clock=clock
        with self.db() as db:db.executescript('''PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS approvals(id TEXT PRIMARY KEY,peer TEXT,bot TEXT,source_mid TEXT,source_local INTEGER,query TEXT,max_posts INTEGER,
        created REAL,expires REAL,status TEXT,confirmation_mid TEXT DEFAULT '',confirmation TEXT DEFAULT '',job TEXT DEFAULT '');
        CREATE INDEX IF NOT EXISTS approval_scope ON approvals(peer,bot,status,created);''')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if 'used_at' not in {r['name'] for r in db.execute('PRAGMA table_info(approvals)')}:db.execute('ALTER TABLE approvals ADD COLUMN used_at REAL DEFAULT 0')
    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()
    def origin(self,peer,context):
        if not context or not context.get('deliver') or not context.get('bot') or not context.get('mid'):return False
        file=self.root/'data/qq/qq-memory.sqlite3'
        if not file.exists():return False
        with closing(sqlite3.connect(file)) as db:
            row=db.execute('SELECT state FROM events WHERE bot=? AND mid=? AND peer=?',(context['bot'],str(context['mid']),peer)).fetchone()
        return bool(row and row[0]=='accepted')
    def latest_user(self,peer):
        with closing(sqlite3.connect(self.root/'data/qq/qq-memory.sqlite3')) as db:
            row=db.execute("SELECT max(id) FROM messages WHERE peer=? AND role='user'",(peer,)).fetchone()
        return row[0] or 0
    def block(self,peer,query,max_posts,context):
        if not self.origin(peer,context):raise ValueError('这小时的手机次数已到上限；本地预览不会创建聊天授权。')
        now=self.clock();local=self.latest_user(peer)
        with self.db() as db:
            db.execute("UPDATE approvals SET status='replaced' WHERE peer=? AND bot=? AND status IN ('pending','approved')",(peer,context['bot']))
            db.execute('INSERT INTO approvals(id,peer,bot,source_mid,source_local,query,max_posts,created,expires,status) VALUES(?,?,?,?,?,?,?,?,?,?)',
                (uuid.uuid4().hex,peer,context['bot'],str(context['mid']),local,query,max_posts,now,now+300,'pending'))
        raise PhoneQuotaApproval(f'这小时的手机次数到上限了。你可以回复“允许这次”，额外让我在小黑盒搜「{query}」、最多读{max_posts}篇；5分钟内有效，只读不发布。也可以说“取消授权”。')
    def resolve(self,peer,text,context):
        if not self.origin(peer,context):return None
        if re.fullmatch(r'(?:撤销|取消)(?:这次|本次|所有)?授权[。！!\s]*',text.strip()):
            with self.db() as db:db.execute("UPDATE approvals SET status='revoked' WHERE peer=? AND bot=? AND status IN ('pending','approved')",(peer,context['bot']))
            return {'reply':'这次还没使用的授权已撤销。'}
        if not control_text(text):return None
        now=self.clock()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT * FROM approvals WHERE peer=? AND bot=? AND status='pending' ORDER BY created DESC LIMIT 1",(peer,context['bot'])).fetchone()
            if not row:
                return {'reply':'没有待确认的手机任务。说一下想查什么，我先把这次要做的事列清楚。'} if re.search('允许|授权|批准|额外',text) else None
            if row['expires']<now or self.latest_user(peer)!=row['source_local']:
                db.execute("UPDATE approvals SET status='stale' WHERE id=?",(row['id'],));return {'reply':'上次的确认已经过期或话题变了。重新说一下这次想查什么吧。'}
            day=datetime.fromtimestamp(now,ZONE).replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
            if db.execute("SELECT count(*) FROM approvals WHERE peer=? AND status='used' AND used_at>=?",(peer,day)).fetchone()[0]>=1:
                return {'reply':'今天额外的一次已经用过了，等正常次数恢复再查。'}
            db.execute("UPDATE approvals SET status='approved',confirmation_mid=?,confirmation=? WHERE id=?",(str(context['mid']),text[:180],row['id']))
            return {'request_text':'去小黑盒搜索并只读查看：'+row['query'],'query':row['query'],'max_posts':row['max_posts'],'approval_id':row['id']}
    def consume(self,peer,context,query,max_posts,job):
        aid=(context or {}).get('approval_id')
        if not aid or not self.origin(peer,context):return False
        now=self.clock()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            r=db.execute("SELECT * FROM approvals WHERE id=? AND peer=? AND bot=? AND status='approved'",(aid,peer,context['bot'])).fetchone()
            if not r or r['expires']<now or r['query']!=query or r['max_posts']!=max_posts or r['confirmation_mid']!=str(context['mid']):return False
            day=datetime.fromtimestamp(now,ZONE).replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
            if db.execute("SELECT count(*) FROM approvals WHERE peer=? AND status='used' AND used_at>=?",(peer,day)).fetchone()[0]>=1:return False
            db.execute("UPDATE approvals SET status='used',job=?,used_at=? WHERE id=?",(job,now,aid));return True
    def refund_unstarted(self,job):
        with self.db() as db:db.execute("UPDATE approvals SET status='approved',job='',used_at=0 WHERE job=? AND status='used'",(job,))
    def revoke_bot(self,bot):
        with self.db() as db:db.execute("UPDATE approvals SET status='revoked' WHERE bot=? AND status IN ('pending','approved')",(bot,))
