"""QQ login secrets encrypted for the current Windows user; never exported to chat."""
from pathlib import Path
import base64,ctypes,json,os,re,threading,uuid

def dpapi(value,decrypt=False):
    if os.name!='nt':raise ValueError('保存QQ登录凭据需要Windows本机加密。')
    from ctypes import wintypes
    class Blob(ctypes.Structure):_fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]
    buffer=ctypes.create_string_buffer(value);source=Blob(len(value),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)));result=Blob()
    crypt=ctypes.WinDLL('crypt32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    function=crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)];function.restype=wintypes.BOOL
    kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
    if not function(ctypes.byref(source),None,None,None,None,1,ctypes.byref(result)):raise ValueError('本机QQ登录凭据无法解密，请重新填写密码。' if decrypt else '本机QQ登录凭据加密失败。')
    try:return ctypes.string_at(result.data,result.size)
    finally:kernel.LocalFree(result.data)

class LoginVault:
    def __init__(self,root,encrypt=None,decrypt=None):
        self.path=Path(root)/'configs/qq-login.secrets.local.json';self.lock=threading.RLock()
        self.encrypt=encrypt or (lambda x:dpapi(x));self.decrypt=decrypt or (lambda x:dpapi(x,True))
    def load(self):
        if not self.path.exists():return {'version':1,'automatic':True,'default':'','accounts':{},'switch_target':''}
        try:
            if self.path.stat().st_size>65536:raise ValueError()
            d=json.loads(self.path.read_text(encoding='utf-8'));assert d['version']==1 and isinstance(d['accounts'],dict);return d
        except Exception:raise ValueError('QQ登录配置无法读取，请在本机检查或恢复备份。') from None
    def update(self,change):
        with self.lock:
            d=self.load();change(d);self.path.parent.mkdir(parents=True,exist_ok=True)
            stage=self.path.with_name('qq-login-'+uuid.uuid4().hex+'.tmp')
            try:stage.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8');stage.replace(self.path)
            finally:stage.unlink(missing_ok=True)
            return d
    def save(self,uin,digest=None,label='',remember=True):
        from qq_identity import qq_id
        uin=qq_id(uin)
        if digest is not None and not re.fullmatch('[a-f0-9]{32}',digest):raise ValueError('登录凭据格式不正确。')
        encrypted=base64.b64encode(self.encrypt(digest.encode())).decode() if digest and remember else None
        def change(d):
            if uin not in d['accounts'] and len(d['accounts'])>=8:raise ValueError('最多保存8个机器人账号。')
            item=d['accounts'].setdefault(uin,{});item.update(label=label[:40],blocked=False)
            item.pop('failure_reason',None)
            if encrypted:item['protected']=encrypted
            if not remember:item.pop('protected',None)
            d['default']=uin;d['automatic']=True
        self.update(change)
    def secret(self,uin):
        item=self.load()['accounts'].get(str(uin),{});secret=item.get('protected')
        if not secret:return ''
        try:value=self.decrypt(base64.b64decode(secret,validate=True)).decode()
        except Exception:raise ValueError('已保存凭据不属于当前Windows用户或已失效，请重新填写。') from None
        if not re.fullmatch('[a-f0-9]{32}',value):raise ValueError('已保存凭据已损坏，请重新填写。')
        return value
    def public(self):
        d=self.load();return {'automatic':d['automatic'],'default':d['default'],'accounts':[{'uin':uin,'label':v.get('label',''),'password_saved':bool(v.get('protected')),'blocked':bool(v.get('blocked'))} for uin,v in d['accounts'].items()]}
    def set_pending(self,pending):
        if pending:
            p=dict(pending)
            if p.get('png'):p['png']=base64.b64encode(p['png']).decode()
            protected=base64.b64encode(self.encrypt(json.dumps(p).encode())).decode()
        else:protected=None
        self.update(lambda d:d.update(pending=protected))
    def pending(self):
        raw=self.load().get('pending')
        if not raw:return None
        try:
            p=json.loads(self.decrypt(base64.b64decode(raw,validate=True)))
            if p.get('png'):p['png']=base64.b64decode(p['png'],validate=True)
            return p
        except Exception:raise ValueError('上次QQ验证已无法恢复，请重新登录。') from None
    def forget(self,uin):
        def change(d):
            d['accounts'].pop(str(uin),None)
            if d.get('default')==str(uin):d['default']=''
        self.update(change)
