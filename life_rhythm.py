"""Persistent local-calendar activity opportunities, adapted from dsh-bili-whale.

Design reference: ALTERSSSS, MIT, commit 3e11454 (see licenses/dsh-bili-whale).
Uses the existing worker and QQ sender; this module never sends a message.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import hashlib, json, math, random, re, sqlite3, threading, time, uuid

ZONE = timezone(timedelta(hours=8))

def minute(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{2}:\d{2}', value):
        raise ValueError('作息时间使用 HH:MM。')
    h, m = map(int, value.split(':'))
    if h > 24 or m > 59 or (h == 24 and m):raise ValueError('作息时间无效。')
    return h * 60 + m

def validate(cfg):
    windows = cfg.get('windows', [{'start':'09:00','end':'22:00','wakes':cfg['daily_activities']}])
    if not isinstance(windows, list) or not 1 <= len(windows) <= 4:raise ValueError('请设置1至4段作息。')
    labels=set()
    for w in windows:
        if not isinstance(w, dict) or set(w) != {'start','end','wakes'}:raise ValueError('作息字段不完整。')
        a,b=minute(w['start']),minute(w['end'])
        if a == 1440 or a == b or type(w['wakes']) is not int or not 0 <= w['wakes'] <= 4:raise ValueError('作息起止或次数无效。')
        label=(a,b)
        if label in labels:raise ValueError('作息窗口不能重复。')
        labels.add(label)
    chance=cfg.get('skip_chance', .3)
    if type(chance) not in (int,float) or not math.isfinite(chance) or not 0 <= chance <= 1:raise ValueError('跳过概率需要在0至1之间。')
    return windows

class LifeRhythm:
    def __init__(self, root, clock=time.time, draw=random.random):
        self.clock=clock;self.draw=draw;self.owner=uuid.uuid4().hex;self.path=Path(root)/'data/qq/life-rhythm.sqlite3'
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.db() as db:
            db.executescript('''PRAGMA journal_mode=WAL;
              CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY,peer TEXT,signature TEXT,window_key TEXT,
                start REAL,end REAL,due REAL,action TEXT,state TEXT,created REAL,claimed REAL DEFAULT 0,detail TEXT DEFAULT '');
              CREATE INDEX IF NOT EXISTS plans_peer ON plans(peer,created);
              CREATE TABLE IF NOT EXISTS health(peer TEXT PRIMARY KEY,tick REAL,success REAL DEFAULT 0,error TEXT DEFAULT '');
            ''')
            columns={r[1] for r in db.execute('PRAGMA table_info(plans)')}
            if 'lease_until' not in columns:db.execute('ALTER TABLE plans ADD COLUMN lease_until REAL DEFAULT 0')
            if 'owner' not in columns:db.execute("ALTER TABLE plans ADD COLUMN owner TEXT DEFAULT ''")
            # A lost worker consumes its opportunity; never repeat an uncertain activity.
            db.execute("UPDATE plans SET state='interrupted',detail='运行中断，未自动重做' WHERE state='running' AND max(lease_until,claimed+600)<=?",(clock(),))

    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=5);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()

    def occurrences(self,cfg,now):
        day=datetime.fromtimestamp(now,ZONE).replace(hour=0,minute=0,second=0,microsecond=0)
        out=[]
        for w in validate(cfg):
            a,b=minute(w['start']),minute(w['end']);end=b if b>a else b+1440
            for delta in (-1,0,1):
                date=day+timedelta(days=delta)
                start=(date+timedelta(minutes=a)).timestamp();stop=(date+timedelta(minutes=end)).timestamp()
                if stop>now:
                    out.append({**w,'key':date.date().isoformat()+'|'+w['start']+'-'+w['end'],'start_at':start,'end_at':stop})
        return sorted(out,key=lambda w:(w['end_at'],w['start_at']))

    def plan(self,peer,cfg):
        now=self.clock();windows=validate(cfg)
        signature=hashlib.sha256(json.dumps({'windows':windows,'skip':cfg.get('skip_chance',.3),'gap':cfg['interval_minutes'],'limit':cfg['daily_activities']},sort_keys=True).encode()).hexdigest()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            # Reap on every planning pass, not only construction. A restart inside the
            # original lease must still recover when it subsequently expires.
            db.execute("UPDATE plans SET state='interrupted',detail='任务租约已结束，未自动重做' WHERE peer=? AND state='running' AND max(lease_until,claimed+600)<=?",(peer,now))
            db.execute("UPDATE plans SET state='expired' WHERE peer=? AND state='pending' AND end<=?",(peer,now))
            db.execute("UPDATE plans SET state='replanned' WHERE peer=? AND state='pending' AND signature!=?",(peer,signature))
            row=db.execute("SELECT * FROM plans WHERE peer=? AND state IN ('pending','running') ORDER BY created LIMIT 1",(peer,)).fetchone()
            if row:return dict(row)
            last=db.execute("SELECT max(claimed) FROM plans WHERE peer=? AND claimed>0",(peer,)).fetchone()[0] or 0
            for w in self.occurrences(cfg,now):
                used=db.execute('SELECT count(*) FROM plans WHERE peer=? AND window_key=? AND claimed>0',(peer,w['key'])).fetchone()[0]
                if used>=w['wakes']:continue
                earliest=max(now,w['start_at'],last+cfg['interval_minutes']*60)
                if earliest>=w['end_at']-30:continue
                due=earliest+self.draw()*max(1,w['end_at']-30-earliest)
                ident=uuid.uuid4().hex;action='skip' if self.draw()<cfg.get('skip_chance',.3) else 'choose'
                db.execute('INSERT INTO plans(id,peer,signature,window_key,start,end,due,action,state,created) VALUES(?,?,?,?,?,?,?,?,?,?)',
                  (ident,peer,signature,w['key'],w['start_at'],w['end_at'],due,action,'pending',now))
                return dict(db.execute('SELECT * FROM plans WHERE id=?',(ident,)).fetchone())
        return None

    def claim(self,peer,plan,cfg):
        now=self.clock();today=datetime.fromtimestamp(now,ZONE).replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            count=db.execute('SELECT count(*) FROM plans WHERE peer=? AND claimed>=?',(peer,today)).fetchone()[0]
            if count>=cfg['daily_activities']:return False
            return bool(db.execute("UPDATE plans SET state='running',claimed=?,lease_until=?,owner=? WHERE id=? AND peer=? AND state='pending' AND due<=? AND start<=? AND end>?",(now,now+600,self.owner,plan['id'],peer,now,now,now)).rowcount)

    def renew(self,ident):
        with self.db() as db:return bool(db.execute("UPDATE plans SET lease_until=? WHERE id=? AND state='running' AND owner=?",(self.clock()+600,ident,self.owner)).rowcount)

    @contextmanager
    def maintaining(self,ident):
        """Renew only while the actual owning activity call is in progress."""
        stop=threading.Event()
        def run():
            while not stop.wait(20):
                try:
                    if not self.renew(ident):break
                except sqlite3.Error:pass  # Existing lease still has ten minutes of margin.
        worker=threading.Thread(target=run,name='life-activity-lease',daemon=True);worker.start()
        try:yield
        finally:stop.set();worker.join(timeout=1)

    def finish(self,ident,state,detail=''):
        if state not in {'completed','skipped','deferred','failed'}:raise ValueError('活动状态无效。')
        with self.db() as db:db.execute("UPDATE plans SET state=?,detail=? WHERE id=? AND state='running' AND owner=?",(state,detail[:180],ident,self.owner))

    def heartbeat(self,peer,error=''):
        with self.db() as db:db.execute('INSERT INTO health(peer,tick,success,error) VALUES(?,?,?,?) ON CONFLICT(peer) DO UPDATE SET tick=excluded.tick,success=CASE WHEN excluded.error=\'\' THEN excluded.success ELSE health.success END,error=excluded.error',(peer,self.clock(),0 if error else self.clock(),error[:180]))

    def snapshot(self,peer,cfg,running=False):
        with self.db() as db:
            h=db.execute('SELECT * FROM health WHERE peer=?',(peer,)).fetchone()
            p=db.execute("SELECT id,due,start,end,action,state,window_key,detail FROM plans WHERE peer=? ORDER BY created DESC,rowid DESC LIMIT 1",(peer,)).fetchone()
        age=self.clock()-h['tick'] if h else None
        return {'worker_alive':running,'last_tick':h['tick'] if h else None,'heartbeat_age_seconds':round(age,1) if age is not None else None,
          'healthy':bool(running and age is not None and age<150 and not h['error']), 'last_error':h['error'] if h else '',
          'paused':not cfg['enabled'] or not cfg['autonomy'],'timezone':'Asia/Shanghai','plan':dict(p) if p else None}
