"""Approved QQ voice files: bounded private cache, one-shot CPU recognition."""
from pathlib import Path
from urllib.parse import urljoin,unquote,urlparse
import base64,hashlib,json,re,subprocess,tempfile,time,uuid
import httpx
from qq_images import qq_url

MAX_BYTES=12*1024**2

class QQRecords:
    def __init__(self,root):
        self.root=Path(root);self.cache=self.root/'cache/qq-voices';self.cache.mkdir(parents=True,exist_ok=True)
        self.jobs=self.root/'cache/qq-voice-jobs';self.jobs.mkdir(parents=True,exist_ok=True)
    def roots(self):
        cfg=json.loads((self.root/'configs/qq-client.local.json').read_text(encoding='utf-8'))
        return [Path(cfg['qq_data']).resolve(),(Path(cfg['napcat_root'])/'cache').resolve()]
    def settings(self):
        file=self.root/'configs/qq-voice.local.json'
        if not file.exists():file=self.root/'music.local.json'
        if not file.exists():raise ValueError('本地语音识别尚未配置，请在本机设置识别模型。')
        return json.loads(file.read_text(encoding='utf-8'))
    def status(self):
        try:
            cfg=self.settings();model=Path(cfg.get('asr_model',''))
            ready=Path(cfg.get('asr_python','')).is_file() and (model/'model.int8.onnx').is_file() and (model/'tokens.txt').is_file()
        except (OSError,ValueError):ready=False
        return {'ready':ready,'backend':'SenseVoice int8 CPU','max_seconds':120,'cache_max_mib':128,'cache_ttl_hours':24,'resident_gpu':False}
    def local(self,value):
        if not isinstance(value,str):return None
        if value.startswith('file:'):
            parsed=urlparse(value)
            if parsed.netloc: return None
            value=unquote(parsed.path)
            if re.match(r'^/[A-Za-z]:',value):value=value[1:]
        path=Path(value)
        if not path.is_absolute() or str(path).startswith('\\\\'):return None
        resolved=path.resolve()
        if not any(resolved.is_relative_to(root) for root in self.roots()) or not path.is_file():return None
        return path
    def store(self,chunks):
        temporary=self.cache/(uuid.uuid4().hex+'.part');size=0;sha=hashlib.sha256()
        try:
            with temporary.open('wb') as out:
                for chunk in chunks:
                    size+=len(chunk)
                    if size>MAX_BYTES:raise ValueError('这条语音文件过大，请发两分钟以内的语音。')
                    sha.update(chunk);out.write(chunk)
            if not size:raise ValueError('QQ提供的语音是空文件，请重新发送。')
            path=self.cache/(sha.hexdigest()+'.audio')
            if path.exists():path.touch()
            else:temporary.replace(path)
            self.prune(path);return path
        finally:temporary.unlink(missing_ok=True)
    def download(self,url):
        started=time.monotonic()
        with httpx.Client(follow_redirects=False,timeout=20,trust_env=False) as client:
            for _ in range(3):
                if not qq_url(url):raise ValueError('QQ语音地址无效，请重新发送这条语音。')
                with client.stream('GET',url) as response:
                    if response.is_redirect:url=urljoin(url,response.headers.get('location',''));continue
                    response.raise_for_status()
                    if int(response.headers.get('content-length','0'))>MAX_BYTES:raise ValueError('这条语音文件过大，请分段发送。')
                    def chunks():
                        for chunk in response.iter_bytes(65536):
                            if time.monotonic()-started>45:raise ValueError('语音下载超时，可以重新发一下吗？')
                            yield chunk
                    return self.store(chunks())
        raise ValueError('QQ语音地址暂时失效，可以重新发一下吗？')
    def fetch(self,segment,rpc):
        data=segment.get('data',{});identifier=data.get('file') or data.get('file_id');metadata={}
        if identifier:
            if not isinstance(identifier,str) or len(identifier)>2048:raise ValueError('QQ语音文件编号无效。')
            if identifier.startswith(('http:','https:')):
                if not qq_url(identifier):raise ValueError('QQ语音地址无效。')
            elif '/' in identifier or '\\' in identifier or ':' in identifier:
                if self.local(identifier) is None:raise ValueError('无法读取QQ语音缓存以外的文件。')
            try:metadata=rpc('get_record',{'file':identifier,'out_format':'wav'},timeout=35) or {}
            except Exception:metadata={}
        for key in ['base64','file_base64']:
            encoded=metadata.get(key)
            if isinstance(encoded,str) and encoded:
                if len(encoded)>MAX_BYTES*4//3+1024:raise ValueError('语音文件过大。')
                return self.store([base64.b64decode(encoded.removeprefix('base64://'),validate=True)])
        for value in [metadata.get(k) for k in ['file','file_path','path']]+[identifier]:
            path=self.local(value)
            if path is not None:
                if path.stat().st_size>MAX_BYTES:raise ValueError('语音文件过大。')
                with path.open('rb') as f:return self.store(iter(lambda:f.read(65536),b''))
        for value in [metadata.get('url'),metadata.get('file'),data.get('url')]:
            if isinstance(value,str) and qq_url(value):return self.download(value)
        raise ValueError('QQ没能取到这条语音，可以再发一次吗？')
    def analyze(self,segment,rpc,emit=None):
        source=self.fetch(segment,rpc);key=source.stem;cached=self.cache/(key+'-v1.json')
        if cached.exists():
            try:
                result=json.loads(cached.read_text(encoding='utf-8'))
                if result.get('ok') and isinstance(result.get('text'),str) and result['text'].strip():cached.touch();return {**result,'cache_hit':True}
            except (OSError,ValueError):pass
        settings=self.settings()
        if emit:emit('voice_read','正在听取QQ语音，本地识别不占用显存。')
        with tempfile.TemporaryDirectory(dir=self.jobs) as folder:
            job=Path(folder)/'request.json';job.write_text(json.dumps({'source':str(source),'folder':folder,'model':settings['asr_model']},ensure_ascii=False),encoding='utf-8')
            try:r=subprocess.run([settings['asr_python'],str(self.root/'qq_record_worker.py'),str(job)],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=90,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            except subprocess.TimeoutExpired:raise ValueError('这条语音识别超时，请拆短一点再发。') from None
            try:result=json.loads(r.stdout.strip().splitlines()[-1])
            except (ValueError,IndexError):raise ValueError('本次没有听清语音，可以再说一次吗？') from None
            if r.returncode or not result.get('ok'):raise ValueError(result.get('error','本次没有听清语音。'))
        result={**result,'worker_exited':True};stage=cached.with_suffix('.'+uuid.uuid4().hex+'.tmp');stage.write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8');stage.replace(cached);self.prune(source)
        return {**result,'cache_hit':False}
    def prune(self,protect=None):
        files=sorted((p for p in self.cache.iterdir() if p.is_file() and re.fullmatch(r'(?:[a-f0-9]{64}\.audio|[a-f0-9]{64}-v1\.json)',p.name)),key=lambda p:p.stat().st_mtime,reverse=True)
        total=0
        for i,p in enumerate(files):
            total+=p.stat().st_size
            if p!=protect and (i>=128 or total>128*1024**2 or time.time()-p.stat().st_mtime>24*3600):p.unlink(missing_ok=True)
