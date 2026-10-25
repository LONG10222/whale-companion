"""Shared SQLite request budget/circuit breaker; no credentials or signed URLs stored.

Inspired by dsh-bili-whale traffic.mjs (ALTERSSSS, MIT, fixed 3e11454).
Every attempt is reserved before network; uncertain writes remain blocked.
"""
from contextlib import contextmanager
from datetime import datetime,timezone,timedelta
from pathlib import Path
import hashlib,json,os,sqlite3,time,uuid

def owner_alive(pid,created):
    if not pid:return False
    import psutil
    try:return abs(psutil.Process(int(pid)).create_time()-float(created))<.01
    except psutil.NoSuchProcess:return False
    except (psutil.AccessDenied,ValueError,TypeError):return True

class TrafficPaused(RuntimeError):pass

class Traffic:
    def __init__(self,home,account='anonymous',clock=time.time,gap=1.5):
        self.clock=clock;self.gap=gap;self.account=hashlib.sha256(account.encode()).hexdigest()
        self.path=Path(home)/'data/traffic.sqlite3';self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.db() as db:db.executescript('''PRAGMA journal_mode=WAL;
          CREATE TABLE IF NOT EXISTS guard(id INTEGER PRIMARY KEY CHECK(id=1),until REAL,manual INTEGER,dispatch REAL,lease TEXT,lease_until REAL);
          INSERT OR IGNORE INTO guard(id,until,manual,dispatch,lease,lease_until) VALUES(1,0,0,0,'',0);
          CREATE TABLE IF NOT EXISTS calls(id TEXT PRIMARY KEY,account TEXT,at REAL,day TEXT,write INTEGER,endpoint TEXT,state TEXT,operation TEXT);
          CREATE INDEX IF NOT EXISTS calls_time ON calls(at);
          CREATE TABLE IF NOT EXISTS metadata(account TEXT,key TEXT,at REAL,data TEXT,PRIMARY KEY(account,key));
        ''')
        with self.db() as db:
            columns={r[1] for r in db.execute('PRAGMA table_info(guard)')}
            if 'lease_pid' not in columns:db.execute('ALTER TABLE guard ADD COLUMN lease_pid INTEGER DEFAULT 0')
            if 'lease_created' not in columns:db.execute('ALTER TABLE guard ADD COLUMN lease_created REAL DEFAULT 0')
        import psutil
        self.pid=os.getpid();self.created=psutil.Process(self.pid).create_time()
    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=3);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()
    def status(self):
        with self.db() as db:r=dict(db.execute('SELECT until,manual,dispatch,lease_until FROM guard WHERE id=1').fetchone())
        return {**r,'paused':bool(r['manual'] or r['until']>self.clock())}
    def resume(self):
        # A manual login/captcha acknowledgement cannot override a timed platform pause.
        with self.db() as db:db.execute('UPDATE guard SET manual=0 WHERE id=1')
    def reserve(self,endpoint,deadline,*,writing=False,fields=None):
        now=self.clock();day=datetime.fromtimestamp(now,timezone(timedelta(hours=8))).date().isoformat()
        stable={k:v for k,v in (fields or {}).items() if k not in {'csrf','csrf_token','rnd','timestamp','msg[timestamp]','msg[dev_id]'}}
        operation=hashlib.sha256(json.dumps([endpoint,stable],sort_keys=True).encode()).hexdigest() if writing else ''
        token=uuid.uuid4().hex
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE');g=db.execute('SELECT * FROM guard WHERE id=1').fetchone()
            if g['manual'] or g['until']>now:raise TrafficPaused('B站请求保护已暂停；请等待平台限制结束或在本机处理登录验证。')
            hour=db.execute('SELECT count(*) FROM calls WHERE account=? AND write=? AND at>?',(self.account,int(writing),now-3600)).fetchone()[0]
            daily=db.execute('SELECT count(*) FROM calls WHERE account=? AND write=? AND day=?',(self.account,int(writing),day)).fetchone()[0]
            if hour >= (12 if writing else 120) or daily >= (30 if writing else 600):raise TrafficPaused('已达到B站本地请求预算，稍后再读。')
            if writing:
                if db.execute("SELECT 1 FROM calls WHERE account=? AND operation=? AND state IN ('reserved','dispatched','uncertain','confirmed') AND at>?",(self.account,operation,now-86400)).fetchone():raise TrafficPaused('同一操作已提交或回执待核查，不自动重复发送。')
                last=db.execute('SELECT max(at) FROM calls WHERE account=? AND write=1',(self.account,)).fetchone()[0] or 0
                if now-last<120:raise TrafficPaused('B站写操作冷却中。')
            db.execute('INSERT INTO calls VALUES(?,?,?,?,?,?,?,?)',(token,self.account,now,day,int(writing),endpoint,'reserved',operation))
        # The timestamp alone is not proof that a slow stream has ended. A live
        # owning process retains the flight even beyond the lease; PID birth time
        # prevents PID reuse from keeping a crashed worker's slot indefinitely.
        try:
            while True:
                deadline.check();now=self.clock()
                with self.db() as db:
                    db.execute('BEGIN IMMEDIATE');g=db.execute('SELECT * FROM guard WHERE id=1').fetchone()
                    if g['manual'] or g['until']>now:raise TrafficPaused('B站已要求暂停，本轮停止。')
                    held=bool(g['lease'] and (g['lease_until']>now or owner_alive(g['lease_pid'],g['lease_created'])))
                    if g['lease'] and not held:
                        db.execute("UPDATE calls SET state=CASE WHEN write=1 THEN 'uncertain' ELSE 'interrupted' END WHERE id=? AND state='dispatched'",(g['lease'],))
                    wait=max(g['dispatch']+self.gap-now, .1 if held else 0)
                    if wait<=0:
                        db.execute('UPDATE guard SET dispatch=?,lease=?,lease_until=?,lease_pid=?,lease_created=? WHERE id=1',(now,token,now+35,self.pid,self.created))
                        db.execute("UPDATE calls SET state='dispatched' WHERE id=?",(token,));return token
                deadline.wait(min(.5,wait))
        except Exception:
            self.finish(token,state='cancelled');raise
    def finish(self,token,*,http=200,code=0,retry_after=0,state=None):
        now=self.clock();risk=http in {403,412,429} or code in {-352,-412,-799,-509,-10403}
        manual=http==401 or code in {-101,-111,-105,12015}
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if risk:db.execute('UPDATE guard SET until=max(until,?) WHERE id=1',(now+max(1800,min(86400,retry_after)),))
            if manual:db.execute('UPDATE guard SET manual=1 WHERE id=1')
            db.execute("UPDATE guard SET lease='',lease_until=0 WHERE id=1 AND lease=?",(token,))
            outcome=state or ('confirmed' if http==200 and code==0 else 'rejected')
            db.execute('UPDATE calls SET state=? WHERE id=?',(outcome,token))
    def cached(self,endpoint,params):
        if endpoint not in {'/x/web-interface/view','/x/web-interface/wbi/view'}:return None
        key=json.dumps([endpoint,{k:v for k,v in (params or {}).items() if k not in {'wts','w_rid'}}],sort_keys=True)
        with self.db() as db:r=db.execute('SELECT data FROM metadata WHERE account=? AND key=? AND at>?',(self.account,key,self.clock()-86400)).fetchone()
        return json.loads(r[0]) if r else None
    def cache(self,endpoint,params,data):
        if endpoint not in {'/x/web-interface/view','/x/web-interface/wbi/view'}:return
        # Metadata contains a cover URL, never playback/subtitle authorization links.
        key=json.dumps([endpoint,{k:v for k,v in (params or {}).items() if k not in {'wts','w_rid'}}],sort_keys=True)
        with self.db() as db:db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?,?,?)',(self.account,key,self.clock(),json.dumps(data,ensure_ascii=False)))
