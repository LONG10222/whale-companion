"""WBI request signing ported from dsh-bili-whale (ALTERSSSS, MIT, 3e11454).
This signs normal logged-in requests; errors never cause endpoint/identity rotation.
"""
import hashlib,time
from urllib.parse import urlencode,urlparse
MIXIN=(46,47,18,2,53,8,23,32,15,50,10,31,58,3,45,35,27,43,5,49,33,9,42,19,29,28,14,39,12,38,41,13,37,48,7,16,24,55,40,61,26,17,0,1,60,51,30,4,22,25,54,21,56,59,6,63,57,62,11,36,20,34,44,52)
def sign(params,images,now=None):
    def key(url):return urlparse(url).path.rsplit('/',1)[-1].split('.')[0]
    raw=key(images.get('img_url',''))+key(images.get('sub_url',''))
    if len(raw)<64:raise ValueError('B站签名资料不完整，未继续请求。')
    secret=''.join(raw[i] for i in MIXIN)[:32]
    args={**params,'wts':round(time.time() if now is None else now)}
    args={k:''.join(c for c in str(v) if c not in "!'()*") for k,v in sorted(args.items())}
    query=urlencode(args).replace('+','%20');args['w_rid']=hashlib.md5((query+secret).encode()).hexdigest()
    return args
