"""Copy only curated public character art into the drawing task's own folder."""
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit
import json,re,shutil
from PIL import Image
from sticker_library import StickerLibrary

PUBLIC_SOURCES={'wuwa_razer','wuwa_user_download','mingchao_github','deepseek_gengjing','deepseek_archive','deepseek_girl','blue_fish_archive','deepseek_wale_girl'}

def prepare_user_references(root,folder,hashes):
    from qq_images import QQImages
    if not isinstance(hashes,list) or not 1<=len(hashes)<=3:raise ValueError('修图需要1到3张已核对的聊天原图。')
    cache=QQImages(root);folder=Path(folder).resolve();references=[]
    for index,sha in enumerate(hashes):
        item=cache.cached_input(sha);source=Path(item['path']);extension=source.suffix.lower() if source.suffix.lower() in {'.jpg','.jpeg','.png'} else '.png'
        target=folder/('reference-user-'+sha+'-'+str(index+1)+extension)
        if not target.resolve().is_relative_to(folder) or target.is_symlink():raise ValueError('原图副本的目标路径不正确。')
        if extension==source.suffix.lower():shutil.copy2(source,target)
        else:
            with Image.open(source) as original:
                original.seek(0);original.convert('RGBA').save(target,format='PNG')
        references.append({'path':str(target),'source':'user_qq_image','sha256':sha})
    return references

def prepare_references(root,folder,request):
    library=StickerLibrary(root);folder=Path(folder);references=[]
    choices=[]
    if re.search('达妮娅|达尼娅',request):choices.append(('达妮娅','鸣潮','达妮娅','','dania'))
    if re.search('deepseek|鲸娘|鲸鱼娘',request,re.I):choices.append(('DeepSeek鲸娘','DeepSeek','DeepSeek娘','','deepseek'))
    for label,theme,character,query,name in choices:
        candidates=library.search(theme=theme,character=character,query=query,ready_only=True,limit=24)['items']
        candidates=[item for item in candidates if item.get('source_id') in PUBLIC_SOURCES and item.get('width',0)>=200 and item.get('height',0)>=200 and item.get('width',0)*item.get('height',0)<=20_000_000]
        candidates.sort(key=lambda item:(item.get('source_id')!='wuwa_razer',bool(item.get('animated')),item.get('bytes',0)))
        if not candidates:continue
        item=candidates[0];target=folder/('reference-'+name+'.png')
        with Image.open(library.file(item['id'])) as image:
            image.seek(0);frame=image.convert('RGBA');frame.thumbnail((900,900));frame.save(target,format='PNG')
        url=urlsplit(item.get('source_page',''));source=urlunsplit((url.scheme,url.netloc,url.path,'',''))
        references.append({'character':label,'path':str(target.resolve()),'source':source})
    if references:(folder/'character-references.json').write_text(json.dumps(references,ensure_ascii=False,indent=2),encoding='utf-8')
    return references
