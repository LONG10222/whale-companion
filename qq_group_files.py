"""Read image files from authorized QQ groups; never run or browse arbitrary files."""
from pathlib import Path
import hashlib,io,json,re,threading,time,uuid
import httpx
from PIL import Image,ImageOps
from qq_images import QQImages,qq_url,MAX_BYTES

EXTENSIONS={'.jpg','.jpeg','.png','.webp','.gif','.bmp','.mpo','.heic','.heif'}
MAX_DOWNLOAD=64*1024**2
NORMALIZE=threading.BoundedSemaphore(1)
FILE_READ=threading.RLock()


class QQGroupFiles:
    pipeline_version=3
    def __init__(self,bridge):
        self.bridge=bridge;self.root=bridge.root;self.reader=getattr(bridge.chat,'images',None) or QQImages(self.root);self.cache=self.root/'cache/qq-group-images';self.cache.mkdir(parents=True,exist_ok=True)
    def wants(self,text):
        if re.search(r'(?:不要|别|不用).{0,6}(?:读|看|打开|下载)',text):return False
        return bool(re.search(r'群文件|群里.{0,4}文件|刚(?:才)?上传.{0,6}(?:图|照)|(?:文件里|文件里的).{0,6}(?:照片|图片)',text) and not re.search('怎么实现|原理|代码|教程',text))
    def scope(self,event,selected=''):
        b=self.bridge
        if not b.connected or str(event.get('self_id'))!=str(b.cfg['bot_id']) or b.login_id!=str(b.cfg['bot_id']):raise ValueError('QQ账号连接变了，先不读取文件。')
        state=b.groups.state();groups=[g for g in state['groups'] if g['enabled']]
        if event.get('message_type')=='group':
            gid=str(event['group_id']);target=next((g for g in groups if g['gid']==gid),None)
            if not target:raise ValueError('这个群尚未启用。')
            if selected and selected not in {gid,target['name']}:raise ValueError('群内只读当前群的图片文件。')
            return target
        if str(event.get('user_id'))!=b.contacts.admin:raise ValueError('跨群读取只能由long在私聊里提出。')
        matches=[g for g in groups if selected and selected in {g['gid'],g['name']}]
        if not selected and len(groups)==1:matches=groups
        if len(matches)!=1:raise ValueError('要看哪个群的文件？告诉我已接通的群名或群号就行。')
        return matches[0]
    def catalog(self,event,selected=''):
        """List real image metadata for a current edit request, without downloading."""
        target=self.scope(event,selected);gid=target['gid'];revision=self.bridge.groups.grant(gid)['revision']
        data=self.bridge.call('get_group_root_files',{'group_id':int(gid)}) or {}
        if not self.bridge.groups.allowed(gid,revision):raise ValueError('群授权刚有变化，目录不再进入本轮。')
        files=[]
        for item in sorted(data.get('files',[])[:500],key=lambda f:float(f.get('upload_time') or 0),reverse=True):
            name=str(item.get('file_name',item.get('name','')))
            if Path(name).suffix.lower() not in EXTENSIONS:continue
            files.append({'name':name[:240],'bytes':int(item.get('file_size',item.get('size',0)) or 0),
                          'uploaded_by_requester':str(item.get('uploader',''))==str(event.get('user_id','')),'upload_time':item.get('upload_time')})
        return {'group_name':target['name'],'images':files[:20],'image_count':len(files),
                'folders':[str(f.get('folder_name',''))[:120] for f in data.get('folders',[])[:20]],'pixels_read':False}
    def select(self,event,text,group='',file_name='',latest=False):
        target=self.scope(event,group);gid=target['gid'];revision=self.bridge.groups.grant(gid)['revision']
        data=self.bridge.call('get_group_root_files',{'group_id':int(gid)}) or {};files=data.get('files',[])
        folders=data.get('folders',[]);chosen=[f for f in folders if str(f.get('folder_name','')) and str(f.get('folder_name')) in text]
        for folder in chosen[:3]:
            more=self.bridge.call('get_group_files_by_folder',{'group_id':int(gid),'folder_id':folder.get('folder_id',folder.get('folder'))}) or {}
            files+=more.get('files',[])[:200]
        pictures=[f for f in files[:500] if Path(str(f.get('file_name',f.get('name','')))).suffix.lower() in EXTENSIONS]
        exact=[f for f in pictures if file_name and file_name.lower()==str(f.get('file_name',f.get('name',''))).lower() or str(f.get('file_name',f.get('name',''))).lower() in text.lower()]
        if file_name and not exact:raise ValueError('没找到这张图片文件，请核对文件名。')
        if not exact and not file_name and re.search(r'[^\s，。/\\]{1,120}\.(?:jpe?g|png|webp|gif|bmp)\b',text,re.I):return {'reply':'没找到这个文件名。当前可读的图片有：'+('、'.join(str(f.get('file_name',f.get('name','')))[:100] for f in pictures[:6]) or '暂无')+'。','images':[],'context':{}}
        if exact:pictures=exact
        elif len(pictures)>1:
            if latest or re.search('刚才|最新|刚上传|刚传|最近',text):
                own=[f for f in pictures if str(f.get('uploader',''))==str(event.get('user_id',''))]
                pictures=sorted(own or pictures,key=lambda f:float(f.get('upload_time') or 0),reverse=True)[:1]
            else:return {'reply':'群文件里有几张图片：'+'、'.join(str(f.get('file_name',f.get('name','')))[:100] for f in pictures[:6])+'。你要看哪张？','images':[],'context':{}}
        if not pictures:return {'reply':'群文件根目录暂时没找到图片。若放在文件夹里，告诉我文件夹名，我再去看。','images':[],'context':{}}
        if not self.bridge.groups.allowed(gid,revision):raise ValueError('群授权刚有变化，这次没有读取。')
        images=[];details=[]
        for item in pictures[:3]:
            image=self.fetch(gid,item);images.append(image);details.append(image.pop('group_file_metadata'))
        self.scope(event,group)
        if not self.bridge.groups.allowed(gid,revision):raise ValueError('群授权已变化，图片不再进入对话。')
        return {'images':images,'context':{'source':'QQ群文件图片','group_name':target['name'],'files':details},'reply':''}
    def fetch(self,gid,item):
        with FILE_READ:return self._fetch(gid,item)
    def _fetch(self,gid,item):
        name=str(item.get('file_name',item.get('name','')));size=int(item.get('file_size',item.get('size',0)) or 0)
        if Path(name).suffix.lower() not in EXTENSIONS:raise ValueError('这里只读取图片，不打开安装包或其他文件。')
        if size>MAX_DOWNLOAD:raise ValueError('这张原图超过64MB，压缩一下再传，我就能看了。')
        fid=str(item.get('file_id',item.get('id','')))
        if not fid or len(fid)>256:raise ValueError('QQ没有提供有效的图片文件编号。')
        key=hashlib.sha256(json.dumps([str(self.bridge.cfg['bot_id']),str(gid),fid,size,item.get('upload_time'),item.get('modify_time')]).encode()).hexdigest();index=self.cache/(key+'.index.json');path=None
        try:
            saved=json.loads(index.read_text(encoding='utf-8'));sha=saved['source_hash'];candidate=self.cache/(sha+'.source')
            if re.fullmatch('[a-f0-9]{64}',sha) and time.time()-saved['at']<86400 and candidate.is_file() and candidate.resolve().parent==self.cache.resolve() and candidate.stat().st_size<=MAX_DOWNLOAD:path=candidate
        except (OSError,ValueError,KeyError,TypeError):pass
        if path is None:
            result=self.bridge.call('get_group_file_url',{'group_id':int(gid),'file_id':fid,'busid':int(item.get('busid') or 0)}) or {}
            url=result.get('url')
            if not isinstance(url,str) or not qq_url(url):raise ValueError('QQ没有返回可读取的图片下载地址。')
            path=self.download(url)
            if re.fullmatch('[a-f0-9]{64}',path.stem):index.write_text(json.dumps({'source_hash':path.stem,'at':time.time()}),encoding='utf-8')
        image=self.prepare(path)
        image['group_file_metadata']={'name':name[:240],'original_bytes':path.stat().st_size,'working_bytes':Path(image['path']).stat().st_size,'preview_compressed':True,**image.get('original_metadata',{})}
        return image
    def download(self,url):
        part=self.cache/(uuid.uuid4().hex+'.part');digest=hashlib.sha256();size=0
        try:
            with httpx.Client(trust_env=False,follow_redirects=False,timeout=35) as client:
                for _ in range(3):
                    if not qq_url(url):raise ValueError('图片下载地址不属于QQ服务。')
                    with client.stream('GET',url) as response:
                        if response.is_redirect:
                            from urllib.parse import urljoin
                            url=urljoin(url,response.headers.get('location',''));continue
                        response.raise_for_status()
                        if int(response.headers.get('content-length','0') or 0)>MAX_DOWNLOAD:raise ValueError('图片文件超过64MB。')
                        with part.open('wb') as output:
                            for chunk in response.iter_bytes(65536):
                                size+=len(chunk)
                                if size>MAX_DOWNLOAD:raise ValueError('图片文件超过64MB。')
                                digest.update(chunk);output.write(chunk)
                        break
                else:raise ValueError('QQ图片下载跳转过多。')
            target=self.cache/(digest.hexdigest()+'.source');part.replace(target);self.prune(target);return target
        finally:part.unlink(missing_ok=True)
    def prepare(self,path):
        from image_ingest import ImageIngest
        return ImageIngest(self.root,self.reader.prepare).ingest(path,retain=False,preserve_small=False)
    def prune(self,protect):
        files=[p for p in self.cache.glob('*.source') if re.fullmatch('[a-f0-9]{64}.source',p.name) and p.is_file() and p.resolve().parent==self.cache.resolve()]
        total=sum(p.stat().st_size for p in files)
        for p in sorted(files,key=lambda p:p.stat().st_mtime):
            if p==protect:continue
            if total>256*1024**2 or time.time()-p.stat().st_mtime>86400:total-=p.stat().st_size;p.unlink()
        indexes=[p for p in self.cache.glob('*.index.json') if re.fullmatch('[a-f0-9]{64}.index.json',p.name) and p.resolve().parent==self.cache.resolve()]
        for i,p in enumerate(sorted(indexes,key=lambda p:p.stat().st_mtime,reverse=True)):
            if i>=128 or time.time()-p.stat().st_mtime>86400:p.unlink()
