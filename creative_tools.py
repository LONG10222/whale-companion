from conversation_tools import definition
from codex_rpc import MODELS,EFFORTS
from workspace_policy import ALIASES
import re

FACES={'微笑':'14','调皮':'12','呲牙':'13','偷笑':'20','可爱':'21','白眼':'22','困':'25','憨笑':'28','疑问':'32','拥抱':'49','爱心':'66','赞':'76','鼓掌':'99','坏笑':'101','委屈':'106','抱拳':'118','OK':'124'}
DEMO_SONGS={'春晓','春曉','随便','随便一首','演示','清唱','短歌',''}
class RetryableChoiceError(ValueError):pass

def named_song(text):
    for line in text.splitlines():
        line=line.rsplit('要求：',1)[-1].strip()
        quoted=re.search(r'(?:唱|听).{0,5}《([^》]{1,60})》',line)
        if quoted:return quoted.group(1)
        first=re.split('[，,。！？?!]',line)[0]
        match=re.fullmatch(r'(?:请|快|给我|帮我)?唱(?:一首)?([\u3400-\u9fffA-Za-z0-9·]{2,30})',first)
        if match:
            song=re.sub(r'(?:给我听|给我|听听|一遍|一下|一次|吧|啊|呀|哦)$','',match.group(1))
            if song and song not in {'歌','哥','歌给我听','歌我想听','首歌','一段','一小段','原创','原创短歌','原创歌','一首歌'} and not song.startswith(('歌','原创','一段','一小段','清唱')):return song
    return ''

def original_song_requested(song,text):
    if re.search(r'(?:不要|别|不用|不想).{0,3}原创',text):return False
    return '原创' in song or '原创' in text
def sing_intent(text):
    from dialogue_context import plain_text,turn_kind
    text=plain_text(text)
    if turn_kind(text)=='correction' or re.search(r'(?:不要|别|不用|不是让|没让|没有让).{0,4}唱',text):return False
    named=bool(re.fullmatch(r'(?:请|快|再|那就|给我|帮我|麻烦你)?唱(?:《[^》]{1,60}》|[^？?，,。！!\n]{1,40})[。！!\s]*',text))
    return bool(named or re.search('唱歌|唱哥|唱[一首个段两几]|清唱|给我唱|你唱|唱春晓|唱春曉|我要听歌|想听歌|你倒是唱',text))
def draw_intent(text):
    from dialogue_context import plain_text,turn_kind
    text=plain_text(text)
    return bool(turn_kind(text)!='correction' and re.search('画[一张幅个]|画图|绘图|绘画|生成.{0,6}(?:图|插画)|(?:生成|制作)[一两几0-9张幅]|帮我画|给我画|重新画|重画|重绘|再画|改画',text) and not re.search(r'(?:不要|别|不用|不是让|没让|没有让).{0,4}画',text))
def codex_intent(text):return bool((re.search('codex|工作台|专门的对话',text,re.I) or re.search('继续.{0,8}(?:工作任务|工作台任务|Codex任务)',text,re.I)) and re.search('让|帮|委托|开|继续|做|干|处理|写|改|修|接入',text) and not re.search('不要.{0,5}codex|别.{0,5}codex|不用.{0,5}codex',text,re.I))
def requested_tool(text):
    from dialogue_context import task_status_question
    if task_status_question(text):return None
    if codex_intent(text):return 'delegate_codex'
    if draw_intent(text):return 'draw_picture'
    if sing_intent(text):return 'sing_song'
    if lookup_intent(text):return 'lookup_web'
    return None

def lookup_intent(text):
    from conversation_tools import external_intent,phone_intent
    from dialogue_context import task_status_question
    return not task_status_question(text) and external_intent(text) and not phone_intent(text)

FACE_TOOL=definition('use_qq_face','准备一个QQ自带小表情，通常接在文字后。和图片表情包不同，不需要上传图片；别每条都用。',{'name':{'type':'string','enum':list(FACES)}},['name'])
TOOLS=[
 definition('lookup_web','查询公开网页并用Luna整理来源。普通用户滚动15分钟最多2次，每次最多5分钟（含排队），Plus免除这两项限制。一个问题算一次，内部搜索不重复计次。只能查公开网页，不能操作电脑、读取私人文件或登录账号。',
    {'query':{'type':'string','minLength':2,'maxLength':200}},['query']),
 definition('sing_song','对方要求唱歌时后台生成真正带旋律的清唱，之后发送QQ语音。song填春晓/随便时用达妮娅参考声线；原创可作短歌。可按要求调tempo速度、rhythm句间衔接、pitch_stability稳音。指定其他歌需已登记乐谱，不能冒充原曲或用念歌词代替。',
    {'song':{'type':'string','minLength':1,'maxLength':80},'tempo':{'type':'number','minimum':.8,'maximum':1.15},'rhythm':{'type':'string','enum':['normal','connected','gentle']},
     'pitch_stability':{'type':'string','enum':['original','steady']},'phrase_gap':{'type':'number','minimum':.10,'maximum':.80}},['song']),
 definition('draw_picture','对方要求画图或重新画时调用Codex内置图像生成，生成真实新图片后发送。重画保留原始要求并加入本轮反馈；不是从表情库选图。管理员明确要最高模型时用gpt-6.1-sol/high；普通和Plus只能Luna。',{'prompt':{'type':'string','minLength':2,'maxLength':1200},'model':{'type':'string','enum':MODELS},'effort':{'type':'string','enum':EFFORTS}},['prompt']),
 definition('delegate_codex','用户明确要求Codex干活时，委托deepseek娘项目的本地工作台。自行选新建或续接本工作台已有对话，最多GPT-6.1 Sol/high。不能操作其它Codex项目，不能修改运行中的机器人。完成后返回进度、结果与对话链接。',
    {'task':{'type':'string','minLength':2,'maxLength':2000},'mode':{'type':'string','enum':['auto','new','continue']},'thread_id':{'type':'string','maxLength':80},'model':{'type':'string','enum':MODELS},'effort':{'type':'string','enum':EFFORTS},
     'read_sources':{'type':'array','maxItems':12,'items':{'type':'string','enum':list(ALIASES)},'description':'仅当对方本次明确要求读取该资料时选择。数据库提供副本，不修改原库。'},
     'read_paths':{'type':'array','maxItems':3,'items':{'type':'string','maxLength':500},'description':'仅可填写对方本次原话里明确指定读取的绝对路径，权限只读。'}},['task','mode','model','effort'])]
