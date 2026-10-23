"""Official DeepSeek balance API and explicitly authorized threshold alerts to long."""
from pathlib import Path
from contextlib import contextmanager
from decimal import Decimal,InvalidOperation
import hashlib,json,sqlite3,threading,time,urllib.request,urllib.error
import deepseek_client

def fetch_balance():
    cfg=deepseek_client.configuration();key=cfg.get('api_key','')
    if not key:raise ValueError('DeepSeek Key 尚未配置。')
    request=urllib.request.Request('https://api.deepseek.com/user/balance',headers={'Authorization':'Bearer '+key})
    try:
        with urllib.request.urlopen(request,timeout=15) as response:data=json.load(response)
    except urllib.error.HTTPError as exc:raise ValueError('余额接口暂不可用（HTTP '+str(exc.code)+'）。') from None
    except (OSError,ValueError):raise ValueError('余额查询超时或响应不可用。') from None
    values=[]
    for row in data.get('balance_infos',[]):
        if row.get('currency') not in {'CNY','USD'}:continue
        try:value=Decimal(row['total_balance'])
        except (KeyError,InvalidOperation):raise ValueError('余额接口返回了无效金额。') from None
        if not value.is_finite():raise ValueError('余额金额无效。')
        values.append({'currency':row['currency'],'total_balance':str(value)})
    if not values:raise ValueError('余额接口未返回可识别币种。')
    return {'is_available':bool(data.get('is_available')),'balance_infos':values,'key_fingerprint':hashlib.sha256(key.encode()).hexdigest()[:20]}

class BalanceWatch:
    def __init__(self,root,bridge,fetch=None,clock=time.time):
        self.root=Path(root);self.bridge=bridge;self.fetch=fetch or fetch_balance;self.clock=clock;self.gate=threading.Lock();self.exit=threading.Event();self.thread=None
        self.path=self.root/'data/qq/balance.sqlite3';self.config_path=self.root/'configs/balance-watch.local.json';self.path.parent.mkdir(parents=True,exist_ok=True)
        self.config_path.parent.mkdir(parents=True,exist_ok=True)
        if not self.config_path.exists():self.config_path.write_text(json.dumps({'enabled':True,'threshold_cny':'1.00','interval_minutes':15},indent=2),encoding='utf-8')
        with self.db() as db:
            db.executescript('''PRAGMA journal_mode=WAL;CREATE TABLE IF NOT EXISTS state(id INTEGER PRIMARY KEY,value TEXT);
            CREATE TABLE IF NOT EXISTS alerts(id INTEGER PRIMARY KEY,at REAL,level TEXT,state TEXT,receipt TEXT);''')
        bridge.chat.balance=self
    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=10)
        try:
            with db:yield db
        finally:db.close()
    def config(self):return json.loads(self.config_path.read_text(encoding='utf-8'))
    def configure(self,enabled,threshold,interval):
        value=Decimal(str(threshold))
        if not value.is_finite() or not Decimal('.01')<=value<=1000 or not 15<=interval<=240:raise ValueError('提醒金额或检查间隔超出范围。')
        temporary=self.config_path.with_suffix('.tmp');temporary.write_text(json.dumps({'enabled':bool(enabled),'threshold_cny':str(value),'interval_minutes':interval},ensure_ascii=False,indent=2),encoding='utf-8');temporary.replace(self.config_path)
    def state(self):
        with self.db() as db:row=db.execute('SELECT value FROM state WHERE id=1').fetchone()
        data=json.loads(row[0]) if row else {};data.pop('key_fingerprint',None)
        return {'config':self.config(),'balance':data,'contact':'long','source':'DeepSeek 官方 /user/balance','running':bool(self.thread and self.thread.is_alive())}
    def refresh(self,notify=False):
        if not self.gate.acquire(False):return self.state()
        try:
            with self.db() as db:row=db.execute('SELECT value FROM state WHERE id=1').fetchone()
            old=json.loads(row[0]) if row else {};now=self.clock();cfg=self.config()
            if now-old.get('checked_at',0)<30 and not old.get('error'):return self.state()
            try:data=self.fetch()
            except Exception as exc:
                old.update(error=str(exc)[:180],last_attempt=now)
                with self.db() as db:db.execute('INSERT OR REPLACE INTO state VALUES(1,?)',(json.dumps(old,ensure_ascii=False),))
                return self.state()
            amount=next((Decimal(r['total_balance']) for r in data['balance_infos'] if r['currency']=='CNY'),None)
            previous=old.get('alerted_level','') if data['key_fingerprint']==old.get('key_fingerprint') else ''
            level='empty' if amount is not None and (amount<=0 or not data['is_available']) else 'low' if amount is not None and amount<Decimal(cfg['threshold_cny']) else ''
            if not level:previous=''
            data.update(checked_at=now,error='',alerted_level=previous)
            from usage_ledger import Ledger
            Ledger(self.root).observe_balance(data['key_fingerprint'],data['balance_infos'],now)
            if notify and cfg['enabled'] and level and (not previous or level=='empty' and previous!='empty'):
                peer,s=self.scope()
                if s['connected'] and s['enabled'] and s['account_matches']:
                    # Persist the intent first. An unknown receipt is not retried every poll.
                    data['alerted_level']=level
                    with self.db() as db:
                        aid=db.execute('INSERT INTO alerts(at,level,state,receipt) VALUES(?,?,?,?)',(now,level,'sending','')).lastrowid
                        db.execute('INSERT OR REPLACE INTO state VALUES(1,?)',(json.dumps(data,ensure_ascii=False),))
                    text=('DeepSeek API 余额已耗尽或不可用，' if level=='empty' else 'DeepSeek API 余额提醒：')+'当前 '+str(amount)+' 元，低于设定的 '+cfg['threshold_cny']+' 元。需要时可到官方平台充值。'
                    try:
                        _,again=self.scope()
                        if not again['enabled'] or not again['connected'] or not again['account_matches']:raise ValueError('QQ状态已变化。')
                        receipt=self.bridge.call('send_private_msg',{'user_id':int(peer),'message':[{'type':'text','data':{'text':text}}]})
                        if not isinstance(receipt,dict) or not receipt.get('message_id'):raise RuntimeError('QQ未返回消息编号。')
                        with self.db() as db:db.execute("UPDATE alerts SET state='sent',receipt=? WHERE id=?",(str(receipt['message_id']),aid))
                        self.bridge.chat.memory.add(peer,'assistant',text,origin='task');self.bridge.event('balance_alert','低余额提醒已返回long，QQ已确认回执。')
                    except Exception:
                        with self.db() as db:db.execute("UPDATE alerts SET state='uncertain' WHERE id=?",(aid,))
            with self.db() as db:db.execute('INSERT OR REPLACE INTO state VALUES(1,?)',(json.dumps(data,ensure_ascii=False),))
            return self.state()
        finally:self.gate.release()
    def scope(self):
        s=self.bridge.snapshot();who=str((s.get('test_contact') or {}).get('user_id',''))
        if (s.get('test_contact') or {}).get('name')!='long' or s['allowed_private_ids']!=[who] or self.bridge.cfg.get('locked_bot_id')!=s['bot_id']:raise ValueError('余额提醒只发送给已锁定的long。')
        return who,s
    def text(self):
        state=self.refresh();data=state['balance'];cfg=state['config']
        if data.get('error'):return '这次余额没查到：'+data['error']
        row=next((r for r in data.get('balance_infos',[]) if r['currency']=='CNY'),None)
        if not row:return '官方接口返回的币种不是人民币，请在本地页面查看。'
        return '当前机器人使用的 DeepSeek API 账户可用余额 '+row['total_balance']+' 元。'+('低于 '+cfg['threshold_cny']+' 元会在这里提醒你。' if cfg['enabled'] else '低余额提醒目前关闭。')
    def start(self):
        def work():
            while not self.exit.is_set():
                try:self.refresh(notify=True)
                except Exception:pass
                self.exit.wait(self.config()['interval_minutes']*60)
        self.thread=threading.Thread(target=work,name='deepseek-balance',daemon=True);self.thread.start()
    def close(self):self.exit.set()
