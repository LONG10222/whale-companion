"""Two source-backed diaries. Internal means activity accounting, never chain of thought."""
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime,timezone,timedelta
import hashlib,json,re,sqlite3,time

ZONE=timezone(timedelta(hours=8))
def safe(value,limit=1500):
    value=str(value or '')[:limit]
    value=re.sub(r'(?i)(?:sk-[\w-]{12,}|(?:SESSDATA|bili_jct|api_key|access_token|password)\s*[:=]\s*[^\s,;]+)','[已过滤]',value)
    return value

class LifeDiary:
    def __init__(self,root,clock=time.time):
        self.clock=clock;self.path=Path(root)/'data/qq/life-diary.sqlite3';self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.db() as db:db.executescript('''CREATE TABLE IF NOT EXISTS entries(
          id TEXT PRIMARY KEY,peer TEXT,at REAL,day TEXT,kind TEXT,share TEXT,internal TEXT,sources TEXT);
          CREATE TABLE IF NOT EXISTS feedback(id TEXT PRIMARY KEY,peer TEXT,at REAL,topic TEXT,value INTEGER,source TEXT);
        ''')
    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=5);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()
    def record(self,peer,ident,kind,*,sources=(),summary='',state='completed'):
        """Only observed facts enter the event record; no raw model responses/errors."""
        if kind not in {'web','bilibili','phone','rest','activity'}:raise ValueError('日记类型无效。')
        clean=[]
        for s in list(sources)[:8]:
            clean.append({k:safe(s[k],800) if isinstance(s[k],str) else s[k] for k in ('url','title','quote','kind','scope','start','end','snapshot_id') if k in s})
        if kind in {'web','bilibili','phone'} and (not clean or not summary):raise ValueError('阅读日记需要真实来源。')
        share=safe(summary,1000) if clean else '这轮没有浏览，没有新增阅读经历。'
        internal={'state':state,'kind':kind,'source_count':len(clean),'observation':share,'sent':False}
        now=self.clock();day=datetime.fromtimestamp(now,ZONE).date().isoformat()
        with self.db() as db:db.execute('INSERT OR IGNORE INTO entries VALUES(?,?,?,?,?,?,?,?)',(safe(ident,180),str(peer),now,day,kind,share,json.dumps(internal,ensure_ascii=False),json.dumps(clean,ensure_ascii=False)))
    def feedback(self,peer,event_id,topic,value,source):
        if type(value) is not int or value not in {-1,1}:raise ValueError('反馈需要喜欢或少看。')
        if not isinstance(source,dict) or not source.get('kind'):raise ValueError('反馈需要可追溯来源。')
        ident=hashlib.sha256((str(peer)+':'+str(event_id)).encode()).hexdigest()
        with self.db() as db:db.execute('INSERT INTO feedback VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value,at=excluded.at',(ident,str(peer),self.clock(),safe(topic,80),value,json.dumps({k:safe(source[k],160) for k in ('kind','id') if k in source})))
    def preferences(self,peer):
        with self.db() as db:return [dict(r) for r in db.execute('SELECT topic,sum(value) AS weight FROM feedback WHERE peer=? AND at>? GROUP BY topic ORDER BY weight DESC LIMIT 12',(str(peer),self.clock()-30*86400))]
    def list(self,peer,limit=20):
        with self.db() as db:rows=db.execute('SELECT * FROM entries WHERE peer=? ORDER BY at DESC,rowid DESC LIMIT ?',(str(peer),min(50,max(1,limit)))).fetchall()
        return [{**dict(r),'internal':json.loads(r['internal']),'sources':json.loads(r['sources'])} for r in rows]
