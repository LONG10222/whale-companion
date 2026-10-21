"""Automatic usage and confirmed-money ledger. Unknown costs remain unknown."""
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime,timezone,timedelta
from decimal import Decimal
import contextvars,json,re,sqlite3,time,uuid

ZONE=timezone(timedelta(hours=8))
USAGE_CONTEXT=contextvars.ContextVar('whale_usage',default={})

def redacted(value):
    if isinstance(value,dict):return {k:('[已隐藏]' if re.search('key|token|password|authorization|cookie|secret|reasoning_content',k,re.I) and k not in {'input_tokens','output_tokens','total_tokens','cached_tokens'} else redacted(v)) for k,v in value.items()}
    if isinstance(value,list):return [redacted(v) for v in value[:30]]
    if isinstance(value,str):return re.sub(r'sk-[A-Za-z0-9_-]+|Bearer\s+\S+','[已隐藏]',value)[:800]
    return value

class Ledger:
    def __init__(self,root):
        self.root=Path(root);self.path=self.root/'data/qq/usage-ledger.sqlite3';self.path.parent.mkdir(parents=True,exist_ok=True)
        from workspace_policy import WORKSPACE
        # Temporary test roots export beside their own DB, never to the live folder.
        self.folder=WORKSPACE/'logs' if self.root==(Path(__file__).resolve().parent) else self.root/'logs/usage'
        self.folder.mkdir(parents=True,exist_ok=True)
        with self.db() as db:db.executescript('''PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,event_key TEXT UNIQUE,at REAL,kind TEXT,peer TEXT,role TEXT,job_id TEXT,model TEXT,effort TEXT,service_tier TEXT,state TEXT,duration REAL,
                input_tokens INTEGER,output_tokens INTEGER,total_tokens INTEGER,amount TEXT,currency TEXT,direction TEXT,details TEXT);
            CREATE INDEX IF NOT EXISTS ledger_at ON events(at);
            CREATE TABLE IF NOT EXISTS balances(account TEXT,currency TEXT,total TEXT,PRIMARY KEY(account,currency));''')
    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=15);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()
    def record(self,kind,*,event_key=None,at=None,peer='',role='',job_id='',model='',effort='',service_tier='default',state='',duration=None,input_tokens=None,output_tokens=None,total_tokens=None,amount=None,currency='',direction='',details=None):
        at=time.time() if at is None else at;key=event_key or uuid.uuid4().hex
        context=USAGE_CONTEXT.get();peer=peer or context.get('peer','');role=role or context.get('role','');job_id=job_id or context.get('job_id','')
        if direction and direction not in {'income','expense','balance_change'}:raise ValueError('未知收支类型。')
        if amount is not None:
            value=Decimal(str(amount))
            if not value.is_finite():raise ValueError('金额无效。')
            amount=str(value)
        for value in [input_tokens,output_tokens,total_tokens]:
            if value is not None and (type(value) is not int or value<0):raise ValueError('用量必须为非负整数。')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT id,at FROM events WHERE event_key=?',(key,)).fetchone();eid=old['id'] if old else uuid.uuid4().hex;at=old['at'] if old else at
            db.execute('INSERT OR REPLACE INTO events VALUES('+','.join('?'*19)+')',(eid,key,at,kind,str(peer),role,job_id,model,effort,service_tier,state,duration,input_tokens,output_tokens,total_tokens,amount,currency,direction,json.dumps(redacted(details or {}),ensure_ascii=False)))
            self.export(db,at)
        return eid
    def export(self,db,at):
        day=datetime.fromtimestamp(at,ZONE);start=day.replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
        rows=[dict(r) for r in db.execute('SELECT * FROM events WHERE at>=? AND at<? ORDER BY at,id',(start,start+86400))]
        file=self.folder/(day.strftime('%Y-%m-%d')+'-使用与收支.jsonl');stage=file.with_suffix('.'+uuid.uuid4().hex+'.tmp')
        stage.write_text(''.join(json.dumps(r|{'details':json.loads(r['details']),'time':datetime.fromtimestamp(r['at'],ZONE).isoformat(timespec='seconds')},ensure_ascii=False)+'\n' for r in rows),encoding='utf-8');stage.replace(file)
    def observe_balance(self,account,infos,at=None):
        at=time.time() if at is None else at
        for row in infos:
            currency=row['currency'];value=Decimal(row['total_balance'])
            with self.db() as db:
                old=db.execute('SELECT total FROM balances WHERE account=? AND currency=?',(account,currency)).fetchone()
                db.execute('INSERT OR REPLACE INTO balances VALUES(?,?,?)',(account,currency,str(value)))
            delta=value-Decimal(old[0]) if old else None
            self.record('balance_snapshot',at=at,state='observed',model='DeepSeek API',amount=value,currency=currency,details={'balance':str(value)})
            if delta:
                self.record('balance_change',at=at,state='unclassified',model='DeepSeek API',amount=delta,currency=currency,direction='balance_change',details={'previous':old[0],'current':str(value),'note':'净变化，不能单独认定为充值或消费。'})
    def manual_money(self,direction,amount,currency,note):
        value=Decimal(str(amount))
        if direction not in {'income','expense'} or not value.is_finite() or value<=0 or value>100000 or currency not in {'CNY','USD'}:raise ValueError('请输入有效的收支类型、币种和正金额。')
        return self.record('confirmed_money',amount=value,currency=currency,direction=direction,state='owner_confirmed',details={'note':note[:200],'source':'local_owner'})
    def state(self):
        with self.db() as db:
            rows=[dict(r) for r in db.execute('SELECT * FROM events ORDER BY at DESC LIMIT 80')]
            totals=[dict(r) for r in db.execute("SELECT model,SUM(input_tokens) AS input_tokens,SUM(output_tokens) AS output_tokens,SUM(total_tokens) AS total_tokens,COUNT(*) AS calls FROM events WHERE kind IN ('deepseek_usage','codex_usage') AND state!='cumulative' GROUP BY model")]
            money=list(db.execute("SELECT currency,direction,amount FROM events WHERE direction IN ('income','expense')"))
            counts={r[0]:r[1] for r in db.execute('SELECT kind,COUNT(*) FROM events GROUP BY kind')}
        sums={}
        for currency,direction,amount in money:sums[(currency,direction)]=sums.get((currency,direction),Decimal('0'))+Decimal(amount)
        for r in rows:r['details']=json.loads(r['details']);r['time']=datetime.fromtimestamp(r['at'],ZONE).strftime('%m-%d %H:%M:%S')
        return {'events':rows,'usage':totals,'counts':counts,'confirmed_money':[{'currency':c,'income':str(sums.get((c,'income'),0)),'expense':str(sums.get((c,'expense'),0))} for c in sorted({x[0] for x in sums})],
            'unknown_costs_are_zero':False,'note':'只汇总已确认金额；Token、订阅用量和余额净变化不自动折算成收入或支出。','database':str(self.path),'export_folder':str(self.folder)}
