from conversation_tools import definition
from codex_rpc import MODELS,EFFORTS,IMAGE_EFFORTS
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
    return '原创' in song or '原创' in text or bool(re.search(r'唱出来|唱这|唱那|这.{0,4}唱|编.{0,6}(?:调|旋律)|加.{0,6}(?:调|旋律)',text))
def sing_intent(text):
    from dialogue_context import plain_text,turn_kind
    text=plain_text(text)
    if turn_kind(text)=='correction' or re.search(r'(?:不要|别|不用|不是让|没让|没有让).{0,4}唱',text):return False
    named=bool(re.fullmatch(r'(?:请|快|再|那就|给我|帮我|麻烦你)?唱(?:《[^》]{1,60}》|[^？?，,。！!\n]{1,40})[。！!\s]*',text))
    return bool(named or re.search('唱歌|唱哥|唱[一首个段两几]|清唱|给我唱|你唱|唱春晓|唱春曉|唱出来|编.{0,6}(?:调|旋律)|我要听歌|想听歌|你倒是唱',text))
def draw_intent(text):
    from dialogue_context import plain_text,turn_kind
    text=plain_text(text)
    return bool(turn_kind(text)!='correction' and (image_edit_intent(text) or re.search('画[一张幅个]|画图|绘图|绘画|生成.{0,6}(?:图|插画)|(?:生成|制作)[一两几0-9张幅]|帮我画|给我画|重新画|重画|重绘|再画|改画',text)) and not re.search(r'(?:不要|别|不用|不是让|没让|没有让).{0,4}(?:画|修图|改图|P图|加特效)',text,re.I))
def image_edit_intent(text):
    if re.search(r'语音|声音|唱歌|歌声|配音|录音|音频',text) and not re.search(r'图|照片|画面',text):return False
    if re.search(r'怎么|如何|教程|原理|意思|能修图吗|可以修图吗',text,re.I) and not re.search(r'帮我|给我|请',text):return False
    return bool(re.search(r'修图|改图|批图|P图|ps图|图.{0,12}(?:加.{0,4}特效|调色|美化|改背景|换背景)|(?:给|加).{0,10}特效|(?:这张|原|刚才的)图.{0,12}(?:修|改|美化|基础|生成)',text,re.I) and not re.search(r'(?:不要|别|不用|没让|不是让).{0,6}(?:修图|改图|批图|P图|加.{0,4}特效)',text,re.I))

def soft_image_edit_intent(text):
    """Only a hint with an actual photo and an independently authorized tool call."""
    if re.search(r'别|不要|不用|不改|好看吗|好不好看|怎么样|是什么意思|教程',text):return False
    if re.search(r'画.{0,4}新|照.{0,6}风格|参考.{0,8}画',text):return False
    return bool(re.search(r'弄好看|好看(?:一点|点|些|一些)|美化一下|调整一下|处理一下|你看着(?:修|改|弄|办)|自由发挥',text))
def bot_repair_intent(text):
    if re.search(r'(?:不要|别|不用|不许|先别).{0,8}(?:修|改|更新|完善)|怎么|如何|教程|方案|建议|别人说|引用|转发',text):return False
    return bool(re.search('修复|排查|完善|更新|补齐|修好',text) and re.search('机器人|鲸娅|你的程序|你的功能|群聊功能|程序代码',text))

def codex_intent(text):return bot_repair_intent(text) or bool((re.search('codex|工作台|专门的对话',text,re.I) or re.search('继续.{0,8}(?:工作任务|工作台任务|Codex任务)',text,re.I)) and re.search('让|帮|委托|开|继续|做|干|处理|写|改|修|接入',text) and not re.search('不要.{0,5}codex|别.{0,5}codex|不用.{0,5}codex',text,re.I))
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
 definition('draw_picture','画新图或修收到的原图。原图修图自动进入理解要求、主修图、审查和按需定向返修流程，保留主体。仅当用户明确点名模型/强度时填写，否则让后台按角色选：管理员修图最高6.1 Sol/xhigh，Plus修图最高当前可用Terra/xhigh，普通修图仅Luna。新画图仍按原有权限。没有素材时先读取当前群图片或已有聊天原图，不用固定口令。',{'prompt':{'type':'string','minLength':2,'maxLength':1200},'model':{'type':'string','enum':MODELS},'effort':{'type':'string','enum':IMAGE_EFFORTS}},['prompt']),
 definition('delegate_codex','用户明确要求Codex干活时，委托deepseek娘项目的本地工作台。自行选新建或续接本工作台已有对话，最多GPT-6.1 Sol/high。不能操作其它Codex项目，不能修改运行中的机器人。完成后返回进度、结果与对话链接。',
    {'task':{'type':'string','minLength':2,'maxLength':2000},'mode':{'type':'string','enum':['auto','new','continue']},'thread_id':{'type':'string','maxLength':80},'model':{'type':'string','enum':MODELS},'effort':{'type':'string','enum':EFFORTS},
     'read_sources':{'type':'array','maxItems':12,'items':{'type':'string','enum':list(ALIASES)},'description':'仅当对方本次明确要求读取该资料时选择。数据库提供副本，不修改原库。'},
     'read_paths':{'type':'array','maxItems':3,'items':{'type':'string','maxLength':500},'description':'仅可填写对方本次原话里明确指定读取的绝对路径，权限只读。'}},['task','mode','model','effort'])]
