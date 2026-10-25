"""Narrow read-only Bilibili API client, plus explicit user QR login.

No general URL fetcher, signatures guessed from errors, social write methods,
browser profile access, proxy switching or automatic CAPTCHA attempts.
"""
from __future__ import annotations
import html, json, re, time
from urllib.parse import urlparse, parse_qs
import httpx
from .common import Deadline, clean_text, safe_bvid
from .credentials import SessionStore, ALLOWED
from .traffic import Traffic

class BiliError(RuntimeError):
    pass

class Bilibili:
    def __init__(self, home, *, deadline=None, transport=None,max_requests=30,load_session=True):
        self.store=SessionStore(home)
        self.deadline=deadline or Deadline()
        self.requests=0;self.last_request=0;self.qr_key=None;self.max_requests=min(80,max_requests)
        self.client=httpx.Client(timeout=18,follow_redirects=False,transport=transport,
            headers={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
                     "Referer":"https://www.bilibili.com/"})
        for key, value in (self.store.load() if load_session else {}).items():
            self.client.cookies.set(key,value,domain=".bilibili.com",path="/")
        identity=next((c.value for c in self.client.cookies.jar if c.name=='DedeUserID'),'anonymous')
        self.traffic=Traffic(home,identity)
        self.wbi_images=None

    def close(self):
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self,*args):
        self.close()

    @staticmethod
    def validate_url(url, *, subtitle=False):
        parsed=urlparse(url)
        host=parsed.hostname or ""
        if parsed.scheme!="https" or parsed.username or parsed.password or parsed.port not in {None,443}:
            raise BiliError("远端返回了不允许的地址，已停止。")
        if subtitle:
            if host!="aisubtitle.hdslb.com" and not host.endswith(".hdslb.com"):
                raise BiliError("字幕地址不属于 B 站字幕域名，已拒绝读取。")
            # Current AI subtitle URLs are extensionless under bfs/ai_subtitle/prod/.
            # This is JSON input, never a downloaded executable or local filename.
            path=parsed.path
            if (not path.startswith(('/bfs/subtitle/','/bfs/ai_subtitle/'))
                    or any(part in {'.','..'} for part in path.split('/'))
                    or ('.' in path.rsplit('/',1)[-1] and not path.endswith('.json'))):
                raise BiliError("字幕文件类型不符。")
        elif host not in {"api.bilibili.com","passport.bilibili.com","api.vc.bilibili.com","api.live.bilibili.com","comment.bilibili.com"}:
            raise BiliError("不允许向这个地址发送请求。")
        return url

    def request(self, url, params=None, *, subtitle=False, limit=2_000_000, raw=False):
        self.validate_url(url,subtitle=subtitle)
        self.deadline.check()
        endpoint=urlparse(url).path
        signed_paths={'/x/web-interface/view':'/x/web-interface/wbi/view','/x/player/v2':'/x/player/wbi/v2'}
        if not subtitle and endpoint in signed_paths:
            return self.signed('https://api.bilibili.com'+signed_paths[endpoint],params or {})
        cached=self.traffic.cached(endpoint,params)
        if cached is not None:return cached
        if self.requests>=self.max_requests:
            raise BiliError(f"本轮已达到 {self.max_requests} 次网络请求上限。")
        gap=.8-(time.monotonic()-self.last_request)
        if gap>0:self.deadline.wait(gap)
        for attempt in range(2):
            if self.requests>=self.max_requests:raise BiliError('本轮网络请求次数已用完。')
            self.requests+=1;self.last_request=time.monotonic()
            token=self.traffic.reserve(endpoint,self.deadline)
            try:
                # Do not send account cookies to subtitle CDN hosts.
                sender=httpx.Client(headers={"User-Agent":"Mozilla/5.0"},trust_env=True,follow_redirects=False) if subtitle else None
                try:
                    with (sender or self.client).stream("GET",url,params=params,timeout=self.deadline.timeout()) as response:
                        if response.status_code in {401,403,412,429}:
                            retry=response.headers.get('Retry-After','0')
                            self.traffic.finish(token,http=response.status_code,code=None,retry_after=int(retry) if retry.isdigit() else 0)
                            raise BiliError(f"B 站要求登录或验证（HTTP {response.status_code}），本轮停止，不会反复重试。")
                        response.raise_for_status();buf=bytearray()
                        for chunk in response.iter_bytes():
                            self.deadline.check();buf.extend(chunk)
                            if len(buf)>limit:raise BiliError("远端内容超出本轮大小限制。")
                    if raw:
                        self.traffic.finish(token);return bytes(buf)
                    obj=json.loads(buf)
                    self.traffic.finish(token,code=obj.get('code',0) if isinstance(obj,dict) else None)
                finally:
                    if sender:sender.close()
                break
            except (httpx.TimeoutException,httpx.NetworkError,httpx.RemoteProtocolError):
                self.traffic.finish(token,state='failed')
                if attempt:raise BiliError("B 站网络连接失败，已停止本轮。") from None
                self.deadline.wait(1.5)
            except (httpx.HTTPStatusError,ValueError):
                self.traffic.finish(token,state='failed')
                raise BiliError("B 站返回了暂时不可用的数据。") from None
            except Exception:
                self.traffic.finish(token,state='failed');raise
        if not isinstance(obj,dict):raise BiliError("B 站返回的格式不符。")
        if not subtitle and obj.get("code")!=0:
            code=obj.get("code")
            if code in {-101,-111,-352,-412,-403}:
                raise BiliError(f"B 站要求登录或验证（{code}），请先在本地扫码。")
            raise BiliError(f"B 站接口暂时不可用（{code}）。")
        data=obj if subtitle else obj.get("data",{})
        if not subtitle:self.traffic.cache(endpoint,params,data)
        return data

    def metadata(self,bvid):
        bvid=safe_bvid(bvid);info=self.request('https://api.bilibili.com/x/web-interface/view',{'bvid':bvid})
        pages=info.get('pages') or [];duration=int((pages[0].get('duration') if pages else info.get('duration')) or 0)
        return {'bvid':bvid,'aid':int(info.get('aid') or 0),'cid':int((pages[0].get('cid') if pages else info.get('cid')) or 0),
          'title':clean_text(info.get('title'),300),'description':clean_text(info.get('desc'),1800),'cover_url':info.get('pic',''),
          'author':clean_text((info.get('owner') or {}).get('name'),100),'published':int(info.get('pubdate') or 0),
          'duration_seconds':duration,'parts_total':len(pages),'part_read':1,'url':f'https://www.bilibili.com/video/{bvid}/'}

    def comments(self,aid,count=6):
        data=self.request('https://api.bilibili.com/x/v2/reply',{'type':1,'oid':int(aid),'sort':2,'pn':1,'ps':min(20,count)})
        return [{'id':str(r.get('rpid','')),'text':clean_text((r.get('content') or {}).get('message'),450),
          'likes':int(r.get('like') or 0)} for r in (data.get('replies') or [])[:count]]

    def signed(self,url,params):
        from .wbi import sign
        if self.wbi_images is None:self.wbi_images=self.request('https://api.bilibili.com/x/web-interface/nav').get('wbi_img') or {}
        return self.request(url,sign(params,self.wbi_images))

    def danmaku(self,cid,duration):
        import xml.etree.ElementTree as ET
        data=self.request(f'https://comment.bilibili.com/{int(cid)}.xml',raw=True,limit=1024*1024)
        if re.search(br'<!\s*(DOCTYPE|ENTITY)',data,re.I):raise BiliError('弹幕文档格式不安全，本轮停止。')
        root=ET.fromstring(data);items=[]
        for d in root.findall('d')[:1000]:
            try:at=float(d.get('p','').split(',')[0])
            except (TypeError,ValueError):continue
            if 0<=at<=duration and d.text:items.append({'start':round(at,2),'text':clean_text(d.text,100)})
        # Keep bounded examples from the busiest thirty-second buckets.
        buckets={}
        for r in items:buckets.setdefault(int(r['start']//30),[]).append(r)
        return [{'start':b*30,'end':min(duration,(b+1)*30),'count':len(rs),'samples':rs[:4]} for b,rs in sorted(buckets.items(),key=lambda kv:-len(kv[1]))[:5]]

    def dossier(self,bvid):
        meta=self.metadata(bvid);row=self.transcript(bvid)
        row.update({k:v for k,v in meta.items() if k not in row});row['channels']={'description':'read','cover':'url_only','subtitle':row['status']}
        row['comments']=self.comments(meta['aid']) if meta['aid'] else []
        row['channels']['comments']='read' if row['comments'] else 'empty'
        row['danmaku']=self.danmaku(meta['cid'],meta['duration_seconds']) if meta['cid'] else []
        row['channels']['danmaku']='read' if row['danmaku'] else 'empty'
        return row

    def search(self, keyword, count=5):
        if not isinstance(keyword,str) or not 1<=len(keyword.strip())<=80:
            raise ValueError("关键词需要 1–80 个字。")
        order='pubdate' if re.search('今天|今日|最新|最近|刚出|新发布',keyword) else 'totalrank'
        data=self.request("https://api.bilibili.com/x/web-interface/search/type",
            {"search_type":"video","keyword":keyword.strip(),"order":order,"page":1,"page_size":20})
        rows=[]
        for item in data.get("result",[])[:min(8,max(1,count))]:
            bvid=item.get("bvid","")
            if not re.fullmatch(r"BV[0-9A-Za-z]{10}",bvid):continue
            rows.append({"bvid":bvid,"title":clean_text(item.get("title"),300),
                "author":clean_text(item.get("author"),100),"published":int(item.get("pubdate") or 0),
                "duration":clean_text(item.get("duration"),30),"url":f"https://www.bilibili.com/video/{bvid}/"})
        return rows

    def transcript(self, bvid):
        bvid=safe_bvid(bvid)
        info=self.request("https://api.bilibili.com/x/web-interface/view",{"bvid":bvid})
        pages=info.get("pages") or []
        cid=(pages[0].get("cid") if pages else info.get("cid"))
        row={"bvid":bvid,"title":clean_text(info.get("title"),300),
            "author":clean_text((info.get("owner") or {}).get("name"),100),
            "published":int(info.get("pubdate") or 0),"duration_seconds":int((pages[0].get('duration') if pages else info.get("duration")) or 0),
            "url":f"https://www.bilibili.com/video/{bvid}/","parts_total":len(pages),"part_read":1}
        if not cid:return row|{"status":"no_subtitle","reason":"未取得首 P 标识。"}
        player=self.request("https://api.bilibili.com/x/player/v2",{"bvid":bvid,"cid":cid})
        sub=player.get("subtitle") or {}
        tracks=sub.get("subtitles") or []
        if not tracks:
            return row|{"status":"login_required" if sub.get("need_login_subtitle") else "no_subtitle",
                        "reason":"未取得字幕；可能未提供字幕或当前登录不足。未按标题推断内容。"}
        target=next((x for x in tracks if x.get("lan") in {"ai-zh","zh-CN","zh-Hans","zh"}),tracks[0])
        url=target.get("subtitle_url","")
        if url.startswith("//"):url="https:"+url
        data=self.request(url,subtitle=True)
        if not isinstance(data.get('body'),list):
            raise BiliError('官方字幕返回的内容格式不符，本条未用于分析。')
        segments=[]
        for item in data.get("body",[])[:12000]:
            text=clean_text(item.get("content"),2000)
            if text:segments.append({"start":round(float(item.get("from",0)),2),"end":round(float(item.get("to",0)),2),"text":text})
        full="\n".join(x["text"] for x in segments)
        if not full:return row|{"status":"no_subtitle","reason":"字幕轨道为空。"}
        if len(full)>50000:return row|{"status":"too_long","reason":"字幕超过 5 万字，本轮跳过，未伪装全文分析。"}
        return row|{"status":"read","language":clean_text(target.get("lan"),30),"segments":segments,"transcript":full}

    def start_login(self):
        data=self.request("https://passport.bilibili.com/x/passport-login/web/qrcode/generate",{'source':'main-fe-header'})
        key=data.get("qrcode_key");url=data.get("url","")
        parsed=urlparse(url)
        if (not key or parsed.scheme!="https" or parsed.hostname not in {"account.bilibili.com","passport.bilibili.com","passport.biligame.com"}
                or parsed.username or parsed.password or parsed.port not in {None,443}):
            raise BiliError("未取得有效的 B 站二维码。")
        self.qr_key=key
        return {"url":url,"expires_in":180}

    def poll_login(self,*,save=True):
        if not self.qr_key:raise BiliError("请先生成登录二维码。")
        data=self.request("https://passport.bilibili.com/x/passport-login/web/qrcode/poll",{"qrcode_key":self.qr_key,'source':'main-fe-header'})
        code=data.get("code")
        if code==0:
            # Credentials come from official Set-Cookie, never the cross-site game-ticket URL.
            cookies={c.name:c.value for c in self.client.cookies.jar if c.name in ALLOWED and (c.domain=="bilibili.com" or c.domain.endswith(".bilibili.com"))}
            if not cookies.get('SESSDATA'):
                raise BiliError('手机已确认，但 B 站未返回有效登录 Cookie，请刷新二维码。')
            if not cookies.get('buvid3'):
                try:
                    device=self.request('https://api.bilibili.com/x/frontend/finger/spi')
                    if device.get('b_3'):cookies['buvid3']=str(device['b_3'])
                    if device.get('b_4'):cookies['buvid4']=str(device['b_4'])
                except BiliError:pass  # A missing optional device cookie must not discard a valid login.
            if save:self.store.save(cookies)
            self.qr_key=None
            result={"state":"logged_in","message":"已登录，登录信息已由 Windows 加密保存在本机。"}
            if not save:result['_cookies']=cookies  # Internal caller only; never serialize this result to a web response.
            return result
        return {"state":{86101:"waiting_scan",86090:"waiting_confirm",86038:"expired"}.get(code,"verification"),
                "message":{86101:"等待手机 B 站扫码",86090:"已扫码，请在手机确认",86038:"二维码已过期，请重新生成"}.get(code,"请在手机完成 B 站要求的验证")}

    def login_status(self):
        data=self.request('https://api.bilibili.com/x/web-interface/nav')
        return {'logged_in':bool(data.get('isLogin')),'name':clean_text(data.get('uname'),80)}

    def post_action(self,action,fields):
        paths={'like':'/x/web-interface/archive/like','coin':'/x/web-interface/coin/add','comment':'/x/v2/reply/add',
          'owner_dm':'https://api.vc.bilibili.com/web_im/v1/web_im/send_msg','live_reply':'https://api.live.bilibili.com/msg/send'}
        if action not in paths:raise BiliError('这项B站操作没有开放。')
        csrf=next((c.value for c in self.client.cookies.jar if c.name=='bili_jct' and c.domain.lstrip('.')=='bilibili.com'),'')
        if not csrf:raise BiliError('B站登录缺少有效凭据，请在本机重新扫码。')
        self.deadline.check()
        if self.requests>=self.max_requests:raise BiliError('本轮网络请求次数已用完。')
        self.requests+=1
        token=self.traffic.reserve(paths[action],self.deadline,writing=True,fields=fields)
        try:
            target=paths[action] if paths[action].startswith('https:') else 'https://api.bilibili.com'+paths[action]
            response=self.client.post(target,data={**fields,'csrf':csrf,'csrf_token':csrf},timeout=self.deadline.timeout())
            if response.status_code in {401,403,412,429}:
                self.traffic.finish(token,http=response.status_code,code=None)
                raise BiliError('B站要求登录或安全验证，这次操作停止，不自动重试。')
            response.raise_for_status();result=response.json()
        except (httpx.HTTPError,ValueError):
            self.traffic.finish(token,state='uncertain');raise BiliError('没有取得B站操作回执，结果待核对，不自动重试。') from None
        self.traffic.finish(token,http=response.status_code,code=result.get('code'))
        if result.get('code')!=0:raise BiliError('B站没有接受这次操作（'+str(result.get('code'))+'），未继续重试。')
        return result.get('data') or {}
