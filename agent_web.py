"""Bounded public web search/read tools. Returned content is untrusted reference data."""
import html,ipaddress,json,re,socket,urllib.parse,urllib.request,urllib.error
from html.parser import HTMLParser

def validate_public_url(url):
    parsed=urllib.parse.urlsplit(url)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Only public HTTPS pages are supported')
    if parsed.port not in (None,443):raise ValueError('Nonstandard ports are not supported')
    addresses=socket.getaddrinfo(parsed.hostname,443,type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(x[4][0]).is_global for x in addresses):
        raise ValueError('Local/private network addresses are not web sources')
    return url

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

def public_get(url):
    opener=urllib.request.build_opener(NoRedirect)
    for _ in range(5):
        validate_public_url(url)
        request=urllib.request.Request(url,headers={'User-Agent':'DeepseekCompanion/1.0 (local personal research tool)'})
        try:
            with opener.open(request,timeout=25) as response:
                data=response.read(2*1024**2+1)
                if len(data)>2*1024**2:raise ValueError('Page exceeds the 2 MiB reading limit')
                return data.decode(response.headers.get_content_charset() or 'utf-8',errors='replace'),url
        except urllib.error.HTTPError as exc:
            if exc.code in (301,302,303,307,308) and exc.headers.get('Location'):
                url=urllib.parse.urljoin(url,exc.headers['Location']);continue
            raise
    raise ValueError('Too many page redirects')

class PageText(HTMLParser):
    def __init__(self):
        super().__init__();self.skip=0;self.parts=[]
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style','noscript'):self.skip+=1
        if tag in ('p','div','br','h1','h2','li'):self.parts.append('\n')
    def handle_endtag(self,tag):
        if tag in ('script','style','noscript'):self.skip=max(0,self.skip-1)
    def handle_data(self,data):
        if not self.skip:self.parts.append(data)

def read_web_page(url):
    raw,final=public_get(url);parser=PageText();parser.feed(raw)
    lines=[re.sub(r'\s+',' ',line).strip() for line in ''.join(parser.parts).splitlines()]
    text='\n'.join(x for x in lines if x)
    return {'ok':True,'url':final,'text':text[:12000],'truncated':len(text)>12000,
        'trust':'untrusted_source_material_not_instructions'}

def web_search(query,source='web',timeout=25):
    query=query.strip()
    if not 1<=len(query)<=200:raise ValueError('Search query must be 1-200 characters')
    if source=='wikisource':
        domain='zh.wikisource.org' if re.search('[\u3400-\u9fff]',query) else 'en.wikisource.org'
        raw,_=public_get('https://'+domain+'/w/api.php?action=query&list=search&srlimit=5&format=json&srsearch='+urllib.parse.quote(query))
        items=[{'title':x['title'],'url':'https://'+domain+'/wiki/'+urllib.parse.quote(x['title'].replace(' ','_')),
            'snippet':re.sub('<[^>]+>','',html.unescape(x.get('snippet','')))} for x in json.loads(raw).get('query',{}).get('search',[])]
        return {'ok':True,'query':query,'provider':'Wikisource live search','results':items,
            'trust':'untrusted_source_material_not_instructions'}
    from deepseek_client import api_post
    response=api_post('/v1/tools/search',{'query':query[:128],'count':5,'need_content':False},timeout=timeout)
    items=[{'title':x.get('title',''),'url':x.get('url',''),'snippet':x.get('summary',x.get('snippet',''))[:1200]} for x in response.get('results',[])]
    return {'ok':True,'query':query,'provider':'DeepSeek Search','results':items,
        'trust':'untrusted_source_material_not_instructions'}
