"""Transport identities, persistent contact keys and capability ceilings."""
from pathlib import Path
from contextlib import contextmanager
import json,re,sqlite3,time

ADMIN='10000001'
CHAT_TOOLS={'use_sticker','use_qq_face','speak_text','recall_memory','sing_song','draw_picture','lookup_web'}
QUERY_POLICY={'window_seconds':900,'max_queries':2,'timeout_seconds':300,'model':'gpt-6-luna','service_tier':'default','daily_limit':None}

def qq_id(value):
    value=str(value)
    if not re.fullmatch(r'[1-9]\d{4,11}',value):raise ValueError('无效的QQ号。')
    return value

class Contacts:
    def __init__(self,root):
        self.root=Path(root);self.path=self.root/'data/qq/contacts.sqlite3';self.path.parent.mkdir(parents=True,exist_ok=True)
        # Only the owner's local configuration can select the administrator; chat never can.
        file=self.root/'qq.local.json'
        cfg=json.loads(file.read_text(encoding='utf-8')) if file.exists() else {}
        self._admin=qq_id((cfg.get('test_contact') or {}).get('user_id',ADMIN))
        with self.db() as db:
            db.executescript('''PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS contacts(qq TEXT PRIMARY KEY,nickname TEXT,remark TEXT,first_seen REAL,last_seen REAL);
            CREATE TABLE IF NOT EXISTS memberships(qq TEXT,group_id TEXT,card TEXT,last_seen REAL,PRIMARY KEY(qq,group_id));''')
            db.executescript('''CREATE TABLE IF NOT EXISTS plus_users(qq TEXT PRIMARY KEY,granted_at REAL);
            CREATE TABLE IF NOT EXISTS plus_audit(id INTEGER PRIMARY KEY,qq TEXT,enabled INTEGER,at REAL,origin TEXT);''')
        self.observe(self.admin,remark='owner')
    @property
    def admin(self):
        file=self.root/'qq.local.json'
        cfg=json.loads(file.read_text(encoding='utf-8')) if file.exists() else {}
        return qq_id((cfg.get('test_contact') or {}).get('user_id',self._admin))
    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()
    def observe(self,user_id,nickname='',remark='',group_id='',card=''):
        who=qq_id(user_id);now=time.time()
        clean=lambda s:re.sub(r'[\x00-\x1f\x7f]', ' ',str(s or ''))[:64]
        with self.db() as db:
            db.execute('''INSERT INTO contacts VALUES(?,?,?,?,?) ON CONFLICT(qq) DO UPDATE SET
                nickname=CASE WHEN excluded.nickname!='' THEN excluded.nickname ELSE contacts.nickname END,
                remark=CASE WHEN excluded.remark!='' THEN excluded.remark ELSE contacts.remark END,last_seen=excluded.last_seen''',
                (who,clean(nickname),clean(remark),now,now))
            if group_id:db.execute('INSERT OR REPLACE INTO memberships VALUES(?,?,?,?)',(who,qq_id(group_id),clean(card),now))
        return self.get(who)
    def role(self,peer):
        if str(peer)==self.admin:return 'admin'
        with self.db() as db:plus=db.execute('SELECT 1 FROM plus_users WHERE qq=?',(str(peer),)).fetchone()
        return 'plus' if plus else 'user'
    def set_plus(self,peer,enabled):
        """Called only by the authenticated local owner route; never an LLM tool."""
        peer=qq_id(peer)
        if peer==self.admin:raise ValueError('long已经是管理员，不需要设为Plus。')
        if type(enabled) is not bool:raise ValueError('Plus设置须为开或关。')
        self.observe(peer)
        with self.db() as db:
            if enabled:db.execute('INSERT OR REPLACE INTO plus_users VALUES(?,?)',(peer,time.time()))
            else:db.execute('DELETE FROM plus_users WHERE qq=?',(peer,))
            db.execute('INSERT INTO plus_audit(qq,enabled,at,origin) VALUES(?,?,?,?)',(peer,int(enabled),time.time(),'local_owner'))
        return self.get(peer)
    def get(self,peer):
        with self.db() as db:r=db.execute('SELECT * FROM contacts WHERE qq=?',(str(peer),)).fetchone()
        return (dict(r) if r else {'qq':str(peer),'nickname':'','remark':''})|{'role':self.role(peer)}
    def all(self):
        with self.db() as db:return [dict(r)|{'role':self.role(r['qq'])} for r in db.execute('SELECT * FROM contacts ORDER BY last_seen DESC LIMIT 500')]
    def label(self,peer):
        c=self.get(peer)
        return 'owner' if c['role']=='admin' else c.get('remark') or c.get('nickname') or 'QQ'+str(peer)[-4:]
    def command(self,peer,text):
        if re.search(r'(?:记录|记住|存|保存).{0,10}(?:我(?:的)?|当前好友的)?[Qq]{2}号|(?:我(?:的)?[Qq]{2}号).{0,8}(?:多少|记得|知道)',text):
            self.observe(peer)
            return '记着呢，你的QQ号是'+str(peer)+'。我按这个号码认人，改昵称也不会混。'
        return None

def group_trigger(obj,bot,allowed_groups=(),enabled=False,reply_is_bot=False):
    """Preparation only. The live bridge intentionally keeps groups disabled."""
    if not enabled or str(obj.get('group_id','')) not in {str(g) for g in allowed_groups}:return False
    return any(s.get('type')=='at' and str(s.get('data',{}).get('qq',''))==str(bot) for s in obj.get('message',[])) or bool(reply_is_bot)

def choose(role,kind,model=None,effort=None):
    from codex_rpc import validate_choice
    if role!='admin':
        if kind not in {'draw_picture','sing_song','lookup_web'}:raise ValueError('电脑里的文件我不能替你动，不过公开资料可以帮你查。')
        if model and model not in {'gpt-6-luna','gpt-5.6-luna'}:raise ValueError('普通用户只允许Luna模型。')
        model=model or 'gpt-6-luna'
    else:model=model or ('gpt-6-luna' if kind=='draw_picture' else 'gpt-6-sol')
    effort=effort or 'medium';validate_choice(model,effort)
    return model,effort
