"""Bounded CPU image ingestion shared by chat uploads and group-file photos."""
from pathlib import Path
import hashlib,io,re,shutil,threading,time,uuid
from PIL import Image,ImageOps,UnidentifiedImageError

MAX_ORIGINAL=64*1024**2
MAX_PIXELS=80_000_000
NORMALIZE=threading.BoundedSemaphore(1)

class ImageIngest:
    def __init__(self,root,prepare):
        self.cache=Path(root)/'cache/qq-original-images';self.cache.mkdir(parents=True,exist_ok=True);self.prepare=prepare
    def retain(self,path):
        path=Path(path)
        if path.stat().st_size>MAX_ORIGINAL:raise ValueError('原图超过64MiB的本机处理上限；请导出较小的图片文件。')
        digest=hashlib.sha256();part=self.cache/(uuid.uuid4().hex+'.part');size=0
        try:
            with path.open('rb') as src,part.open('wb') as dst:
                for chunk in iter(lambda:src.read(65536),b''):
                    size+=len(chunk)
                    if size>MAX_ORIGINAL:raise ValueError('原图超过64MiB的本机处理上限。')
                    digest.update(chunk);dst.write(chunk)
            target=self.cache/(digest.hexdigest()+'.source');part.replace(target)
            total=sum(p.stat().st_size for p in self.cache.glob('*.source'))
            for p in sorted(self.cache.glob('*.source'),key=lambda p:p.stat().st_mtime):
                if p==target:continue
                if total>256*1024**2 or time.time()-p.stat().st_mtime>86400:total-=p.stat().st_size;p.unlink()
            return target
        finally:part.unlink(missing_ok=True)
    def ingest(self,path,*,retain=True,preserve_small=True):
        path=self.retain(path) if retain else Path(path)
        if path.stat().st_size>MAX_ORIGINAL:raise ValueError('原图超过64MiB的本机处理上限。')
        with NORMALIZE:
            try:
                with Image.open(path) as original:
                    size=list(original.size);fmt=original.format
                    if fmt not in {'JPEG','MPO','PNG','WEBP','GIF','BMP'}:raise ValueError('图片实际格式'+str(fmt)+'暂不支持；可导出JPEG或PNG，原文件保留。')
                    if original.width*original.height>MAX_PIXELS:raise ValueError('这张图片超过8000万像素上限，当前不解码全图；可导出较小的JPEG或PNG。')
                    metadata={'original_bytes':path.stat().st_size,'original_dimensions':size,'original_format':fmt,'source_sha256':path.stem}
                    if (preserve_small or fmt=='GIF') and fmt in {'JPEG','PNG','WEBP','GIF'} and max(original.size)<=4096 and path.stat().st_size<=12*1024**2 and original.width*original.height<=20_000_000:
                        return {**self.prepare(path.read_bytes()),'original_metadata':metadata}
                    original.seek(0);ratio=max(1,max(original.size)/4096)
                    original.draft('RGB',(max(1,int(original.width/ratio)),max(1,int(original.height/ratio))))
                    import psutil
                    if psutil.virtual_memory().available<original.width*original.height*10+256*1024**2:raise ValueError('可用内存不足以安全解码这张大图；原图已保留，电脑空闲后可重试。')
                    original.thumbnail((4096,4096))
                    alpha=original.mode in {'RGBA','LA'} or 'transparency' in original.info
                    image=ImageOps.exif_transpose(original).convert('RGBA' if alpha else 'RGB')
                    output=io.BytesIO();image.save(output,format='PNG' if alpha else 'JPEG',**({'compress_level':3} if alpha else {'quality':90}))
                    if output.tell()>6*1024**2:
                        image.thumbnail((3072,3072));output=io.BytesIO();image.save(output,format='WEBP' if alpha else 'JPEG',quality=85)
                    if output.tell()>12*1024**2:raise ValueError('工作副本仍超过12MiB，未把大原图直接发送给模型。')
                    result=self.prepare(output.getvalue())
                    return {**result,'original_metadata':{**metadata,'working_dimensions':list(image.size),'working_bytes':output.tell()}}
            except UnidentifiedImageError:raise ValueError('未能解码图片实际格式，可能是HEIC/HEIF或损坏文件；请导出JPEG/PNG，原文件已保留。') from None
