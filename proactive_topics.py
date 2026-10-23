"""Small, cached public headline pool. No account cookies, model, or full articles.

Only today's timestamped headlines from publisher-owned RSS endpoints are offered
as current news. A failed feed never prevents an ordinary conversation starter.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
from email.utils import parsedate_to_datetime
from html import unescape
import hashlib,json,re,threading,time
from pathlib import Path
from urllib.parse import urlparse
import xml.etree.ElementTree as ET
import httpx

ZONE=timezone(timedelta(hours=8))
FEEDS=(
    {'name':'中新网','url':'https://www.chinanews.com.cn/rss/scroll-news.xml','host':'chinanews.com.cn'},
    {'name':'IT之家','url':'https://www.ithome.com/rss/','host':'ithome.com'},
)
MAX_BYTES=512*1024

def today(at):return datetime.fromtimestamp(at,ZONE).strftime('%Y-%m-%d')

def allowed_link(url,host):
    try:
        value=urlparse(url)
        return (value.scheme=='https' and not value.username and not value.password and value.port in (None,443)
                and (value.hostname==host or (value.hostname or '').endswith('.'+host)) and len(url)<=600)
    except (ValueError,TypeError):return False

def plain(value,limit=140):
    return re.sub(r'\s+',' ',re.sub(r'<[^>]*>','',unescape(value or ''))).strip()[:limit]

def parse_feed(raw,feed,now):
    if len(raw)>MAX_BYTES or re.search(br'<!\s*(DOCTYPE|ENTITY)',raw,re.I):return []
    root=ET.fromstring(raw);items=[];seen=set()
    for entry in root.findall('.//item')[:80]:
        title=plain(entry.findtext('title'));url=(entry.findtext('link') or '').strip()
        try:
            date=parsedate_to_datetime(entry.findtext('pubDate') or '')
            if date.tzinfo is None:continue
            at=date.timestamp()
        except (ValueError,TypeError,OverflowError):continue
        if not title or not allowed_link(url,feed['host']) or today(at)!=today(now) or at>now+60:continue
        if url in seen:continue
        seen.add(url)
        items.append({'id':hashlib.sha256(url.encode()).hexdigest()[:16],'title':title,'url':url,
                      'publisher':feed['name'],'published_at':at,'date':today(at)})
    return sorted(items,key=lambda item:item['published_at'],reverse=True)[:12]

class PublicTopics:
    def __init__(self,root,clock=time.time,fetcher=None):
        self.root=Path(root);self.clock=clock;self.fetcher=fetcher or self.fetch
        self.path=self.root/'cache/proactive-topics/headlines.json';self.lock=threading.Lock()
    def fetch(self,feed):
        with httpx.Client(timeout=7,trust_env=False,follow_redirects=False) as client:
            with client.stream('GET',feed['url'],headers={'User-Agent':'WhaleCompanion/1.0 (personal RSS reader)','Accept':'application/rss+xml, application/xml, text/xml'}) as response:
                response.raise_for_status();raw=bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw)>MAX_BYTES:raise ValueError('Headline feed too large')
                return bytes(raw)
    def load(self):
        try:
            if self.path.stat().st_size>256*1024:return None
            data=json.loads(self.path.read_text(encoding='utf-8'))
            if not 0<=self.clock()-data['fetched_at']<1800 or data['day']!=today(self.clock()):return None
            return data
        except (OSError,ValueError,KeyError,TypeError):return None
    def snapshot(self):
        data=self.load()
        return {'enabled':True,'cached_today_items':len(data.get('items',[])) if data else 0,
                'fetched_at':data.get('fetched_at') if data else None,'feeds':data.get('feeds',[]) if data else []}
    def collect(self):
        with self.lock:
            old=self.load()
            if old:return old
            now=self.clock()
            def get(feed):
                try:
                    items=parse_feed(self.fetcher(feed),feed,now)
                    return items,{'publisher':feed['name'],'state':'ready' if items else 'no_today_items','count':len(items)}
                except Exception:
                    return [],{'publisher':feed['name'],'state':'unavailable','count':0}
            with ThreadPoolExecutor(max_workers=2,thread_name_prefix='proactive-headlines') as pool:results=list(pool.map(get,FEEDS))
            # Alternate providers so the model can choose from different subjects.
            items=[]
            for index in range(12):
                for rows,_ in results:
                    if len(rows)>index:items.append(rows[index])
            data={'day':today(now),'fetched_at':now,'items':items,'feeds':[state for _,state in results]}
            self.path.parent.mkdir(parents=True,exist_ok=True);tmp=self.path.with_suffix('.tmp')
            tmp.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8');tmp.replace(self.path)
            return data

def current_sources(sources,now):
    """Check again at delivery: never move yesterday's headline into today."""
    if not isinstance(sources,list):return []
    valid=[]
    for item in sources[:2]:
        try:
            feed=next((f for f in FEEDS if f['name']==item.get('publisher')),None)
            at=float(item['published_at'])
            if feed and today(at)==today(now) and at<=now+60 and allowed_link(item['url'],feed['host']):valid.append(item)
        except (KeyError,ValueError,TypeError,OverflowError):continue
    return valid
