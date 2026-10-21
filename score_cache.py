"""Bounded cache of audio that passed both content and independent audio checks."""
from pathlib import Path
import hashlib,json,re,shutil,time

class ScoreCache:
    def __init__(self,root):self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
    def key(self,data):return hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    def get(self,key,output):
        metadata=self.root/(key+'.json');audio=self.root/(key+'.wav')
        if not metadata.is_file() or not audio.is_file() or time.time()-metadata.stat().st_mtime>86400:return None
        try:info=json.loads(metadata.read_text(encoding='utf-8'))
        except (ValueError,OSError):return None
        if info.get('quality_status')!='automated_review_passed_listening_pending':return None
        shutil.copy2(audio,output);return info
    def put(self,key,audio,metadata):
        shutil.copy2(audio,self.root/(key+'.wav'))
        temporary=self.root/(key+'.tmp')
        temporary.write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
        temporary.replace(self.root/(key+'.json'));self.prune()
    def prune(self):
        files=[p for p in self.root.iterdir() if p.is_file() and re.fullmatch(r'[a-f0-9]{64}\.(wav|json)',p.name)]
        total=sum(p.stat().st_size for p in files)
        for p in sorted(files,key=lambda f:f.stat().st_mtime):
            if time.time()-p.stat().st_mtime<=86400 and total<=512*1024**2:continue
            total-=p.stat().st_size;p.unlink()
