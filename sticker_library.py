"""Small CPU-only index for the explicitly downloaded, public sticker collection."""
from pathlib import Path
import io, json, re, threading, time, unicodedata,hashlib
from collections import Counter
from PIL import Image

ROOT=Path(__file__).resolve().parent
PUBLIC_SOURCE='https://github.com/zhaoolee/ChineseBQB'
CATALOG_SOURCE='local-public-sticker-library-v2'
PUBLIC_SOURCES={
    'chinesebqb':PUBLIC_SOURCE,
    'deepseek_gengjing':'https://aigengtu.com/',
    'deepseek_archive':'https://github.com/EDMOK/blue-fish-archive',
    'deepseek_animated':'https://github.com/DejavuMoe/deepseek_wale_girl',
    'wuwa_builtin':'https://github.com/Xbaiyz12/MingChaoBQ',
    'wuwa_drive':'https://drive.google.com/drive/folders/1GY6YgPhhOsxN2oW0AbIywdO9Z1_OFe6K',
    'wuwa_razer':'https://www.razer.com/collabs/wuthering-waves',
    'wuwa_user_download':'https://emoji.wuwa.games/',
}
PRIMARY_THEMES=('DeepSeek','鸣潮')
DESCRIBED={'model_captioned','source_labeled'}


def themes_of(row):
    return row.get('themes') or [row.get('theme') or '其他']


class StickerLibrary:
    def __init__(self, root=ROOT):
        self.root=Path(root)
        self.folder=self.root/'assets/stickers'
        self.catalog=self.folder/'catalog.json'
        self.thumbs=self.root/'cache/stickers/thumbs'
        self.lock=threading.RLock()
        self._mtime=None
        self._data=None
        self._by_id={}

    def load(self):
        with self.lock:
            stat=self.catalog.stat()
            if stat.st_size>32*1024**2:raise ValueError('表情索引超过大小限制。')
            if self._mtime!=stat.st_mtime_ns:
                data=json.loads(self.catalog.read_text(encoding='utf-8'))
                valid=data.get('source')==PUBLIC_SOURCE
                if data.get('source')==CATALOG_SOURCE:
                    sources=data.get('sources') or []
                    valid=bool(sources) and all(s.get('id') in PUBLIC_SOURCES and s.get('url')==PUBLIC_SOURCES[s['id']] for s in sources)
                    ids={s['id'] for s in sources}
                    valid=valid and all(row.get('source_id') in ids for row in data.get('images',[]))
                if not valid or len(data.get('images',[]))>20000:
                    raise ValueError('此入口仅提供已下载的公开表情包，不读取个人图片。')
                lookup={x['id']:x for x in data['images']}
                if len(lookup)!=len(data['images']):raise ValueError('表情索引包含重复编号。')
                self._data=data;self._by_id=lookup;self._mtime=stat.st_mtime_ns
            return self._data

    @staticmethod
    def public(item):
        keys=['id','pack','packs','title','caption','ocr_text','emotions','topics','animated','frames','bytes','width','height','annotation_status','annotation_source','auto_select','character','characters','collection','artist','source_page','source_id','large_original']
        out={k:item.get(k) for k in keys}
        out['image_url']='/stickers/images/'+item['id']
        out['thumbnail_url']='/stickers/thumbs/'+item['id']
        out['themes']=themes_of(item)
        out['file_extension']=Path(item['file']).suffix
        return out

    def status(self):
        data=self.load()
        rows=data['images'];counts=Counter();role_counts=Counter();theme_stats={};role_themes={};pack_themes={}
        for row in rows:
            for p in row['packs']:
                counts[p]+=1;pack_themes.setdefault(p,set()).update(themes_of(row))
            for role in row.get('characters') or []:
                role_counts[role]+=1;role_themes.setdefault(role,set()).update(themes_of(row))
            for theme in themes_of(row):
                stat=theme_stats.setdefault(theme,{'images':0,'animated':0,'bytes':0,'described':0})
                stat['images']+=1;stat['animated']+=int(row['animated']);stat['bytes']+=row['bytes']
                stat['described']+=int(row.get('annotation_status') in DESCRIBED)
        return {'stats':data['stats'],'source':data['source'],
            'sources':data.get('sources') or [{'id':'chinesebqb','name':'ChineseBQB','url':PUBLIC_SOURCE}],
            'packs':[{'name':p['name'],'count':counts[p['name']],'themes':sorted(pack_themes.get(p['name'],set()))} for p in data['packs']],
            'themes':theme_stats,'characters':[{'name':k,'count':v,'themes':sorted(role_themes[k])} for k,v in sorted(role_counts.items())],
            'topics':sorted({t for x in rows for t in x.get('topics',[])}),
            'emotions':sorted({t for x in data['images'] for t in x['emotions']}),
            'coverage':data.get('coverage',{}),
            'gpu_required':False,'external_chat_connected':False}

    def search(self, query='', pack='', emotion='', ready_only=False, page=1, limit=24,
               theme='',character='',topic='',kind=''):
        if not isinstance(query,str) or len(query)>100 or len(pack)>40 or len(emotion)>20:
            raise ValueError('检索条件过长。')
        if not isinstance(page,int) or page<1 or not isinstance(limit,int) or not 1<=limit<=48:
            raise ValueError('分页参数无效。')
        if any(not isinstance(s,str) or len(s)>80 for s in [theme,character,topic]) or kind not in ('','static','animated'):
            raise ValueError('筛选条件无效。')
        data=self.load()
        for old,new in [('达尼娅','达妮娅'),('达妮亚','达妮娅'),('deep seek','DeepSeek'),('大肥鱼','DeepSeek'),('深度求索','DeepSeek')]:
            query=query.replace(old,new)
        terms=unicodedata.normalize('NFKC',query).strip().casefold().split()
        matches=[]
        for row in data['images']:
            themes=themes_of(row)
            if theme=='featured' and not any(t in PRIMARY_THEMES for t in themes):continue
            if theme and theme!='featured' and theme not in themes:continue
            if character and character not in row.get('characters',[]):continue
            if topic and topic not in row.get('topics',[]):continue
            if kind=='animated' and not row['animated']:continue
            if kind=='static' and row['animated']:continue
            if pack and pack not in row['packs']:continue
            if emotion and emotion not in row['emotions']:continue
            if ready_only and not (row['auto_select'] and row['annotation_status'] in DESCRIBED):continue
            hay=unicodedata.normalize('NFKC',' '.join([row['title'],row.get('caption',''),row.get('ocr_text',''),
                row.get('artist',''),row.get('collection',''),*themes,*row.get('characters',[]),*row.get('topics',[]),*row['emotions'],*row['packs']])).casefold()
            if all(term in hay for term in terms):matches.append(row)
        matches.sort(key=lambda x:(not x['auto_select'],x['pack_code'],x['id']))
        if theme=='featured':
            # Alternate the two requested themes in the main library view.
            first=[x for x in matches if 'DeepSeek' in themes_of(x)]
            second=[x for x in matches if 'DeepSeek' not in themes_of(x)]
            mixed=[]
            for i in range(max(len(first),len(second))):
                if i<len(first):mixed.append(first[i])
                if i<len(second):mixed.append(second[i])
            matches=mixed
        return {'total':len(matches),'page':page,'limit':limit,
            'items':[self.public(x) for x in matches[(page-1)*limit:page*limit]],
            'meaning':'本地检索结果；没有发送到任何聊天账号。'}

    def file(self, item_id):
        if not re.fullmatch(r'[a-f0-9]{24}',item_id):raise ValueError('表情编号无效。')
        self.load()
        item=self._by_id.get(item_id)
        if item is None:raise FileNotFoundError('表情不存在。')
        digest=item['sha256']
        if not re.fullmatch(r'[a-f0-9]{64}',digest) or not digest.startswith(item_id):
            raise ValueError('表情哈希记录不匹配。')
        rel=item['file']
        if not re.fullmatch(r'files/'+digest+r'\.(?:png|jpg|gif|webp)',rel):
            raise ValueError('表情文件路径无效。')
        file=(self.folder/rel).resolve()
        if file.parent!=(self.folder/'files').resolve() or not file.is_file():
            raise FileNotFoundError('表情文件不存在。')
        return file

    def thumbnail(self,item_id):
        source=self.file(item_id)
        self.thumbs.mkdir(parents=True,exist_ok=True)
        file=self.thumbs/(item_id+'.webp')
        if not file.exists():
            with self.lock:
                with Image.open(source) as im:
                    if im.width*im.height>20_000_000:raise ValueError('图片尺寸过大。')
                    im.seek(0);frame=im.convert('RGBA');frame.thumbnail((240,240))
                    out=io.BytesIO();frame.save(out,format='WEBP',quality=78)
                file.write_bytes(out.getvalue())
                self.prune_thumbnails(file)
        return file

    def prune_thumbnails(self,protect,max_bytes=32*1024**2):
        """Only remove owned derived thumbnails; original images are retained."""
        entries=[];total=0
        for path in self.thumbs.glob('*.webp'):
            if (not re.fullmatch(r'[a-f0-9]{24}\.webp',path.name)
                    or path.resolve().parent!=self.thumbs.resolve() or not path.is_file()):continue
            stat=path.stat();entries.append((stat.st_mtime,stat.st_size,path));total+=stat.st_size
        for modified,size,path in sorted(entries):
            if path==protect:continue
            if total>max_bytes or time.time()-modified>7*86400:
                path.unlink(missing_ok=True);total-=size

    def choose(self,emotion,query='',limit=3):
        """Future chat adapters can call this; it only returns local candidates."""
        size=min(max(limit,1),5)
        result=self.search(query=query,emotion=emotion,ready_only=True,limit=size,theme='featured')
        if not result['total']:
            result=self.search(query=query,emotion=emotion,ready_only=True,limit=size)
        result['sent']=False
        return result

    def reaction(self,emotion,*,theme='featured',character='',query='',recent=None,seed='',require_query=False,limit=1):
        """Rank the full approved local index, without API calls or arbitrary fallback."""
        recent=recent or [];recent_set=set(recent);candidates=[];terms=query.casefold().split()
        for row in self.load()['images']:
            if not row['auto_select'] or row['annotation_status'] not in DESCRIBED or row['bytes']>10*1024**2:continue
            tags=set(row['emotions']);themes=themes_of(row)
            if emotion not in tags:continue
            if theme=='featured' and not any(t in PRIMARY_THEMES for t in themes):continue
            if theme and theme!='featured' and theme not in themes:continue
            if character and character not in row.get('characters',[]):continue
            caption=' '.join([row.get('caption',''),row.get('ocr_text',''),row['title']]).casefold()
            if re.search(r'去死|傻逼|滚开|死亡威胁',caption):continue
            if emotion in {'开心','调皮','问候','感谢','喜欢','赞同'} and tags & {'生气','害怕','嫌弃','难过'}:continue
            if emotion=='安慰' and tags & {'生气','嫌弃','调皮'}:continue
            overlap=sum(t in caption for t in terms)
            if require_query and not overlap:continue
            score=4*(row['annotation_status']=='model_captioned')+min(3,overlap)
            rank=int(hashlib.sha256((seed+row['id']).encode()).hexdigest()[:12],16)
            candidates.append((row,score,rank))
        fresh=[c for c in candidates if c[0]['id'] not in recent_set]
        if fresh:candidates=fresh
        else:
            # Small emotion pools may rotate older pictures, but never repeat the last image.
            candidates=[c for c in candidates if not recent or c[0]['id']!=recent[0]]
            if candidates:
                oldest=max((recent.index(c[0]['id']) if c[0]['id'] in recent else len(recent)) for c in candidates)
                candidates=[c for c in candidates if (recent.index(c[0]['id']) if c[0]['id'] in recent else len(recent))==oldest]
        if not candidates:return [] if limit>1 else None
        ranked=sorted(candidates,key=lambda c:(c[1],c[2]),reverse=True)
        return [self.public(c[0]) for c in ranked[:min(24,limit)]] if limit>1 else self.public(ranked[0][0])
