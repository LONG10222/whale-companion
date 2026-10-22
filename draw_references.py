"""Copy only curated public character art into the drawing task's own folder."""
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit
import json,re
from PIL import Image
from sticker_library import StickerLibrary

PUBLIC_SOURCES={'wuwa_razer','wuwa_user_download','mingchao_github','deepseek_gengjing','deepseek_archive','deepseek_girl','blue_fish_archive','deepseek_wale_girl'}

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
