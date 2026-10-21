from conversation_tools import definition
from codex_rpc import MODELS,EFFORTS
from workspace_policy import ALIASES
import re

FACES={'微笑':'14','调皮':'12','呲牙':'13','偷笑':'20','可爱':'21','白眼':'22','困':'25','憨笑':'28','疑问':'32','拥抱':'49','爱心':'66','赞':'76','鼓掌':'99','坏笑':'101','委屈':'106','抱拳':'118','OK':'124'}
def sing_intent(text):return bool(re.search('唱歌|唱哥|唱[一首个段两几]|清唱|给我唱|你唱|唱春晓|唱春曉',text) and not re.search('不要唱|别唱|不用唱',text))
def draw_intent(text):return bool(re.search('画[一张幅个]|画图|绘图|绘画|生成.{0,6}(?:图|插画)|帮我画|给我画',text) and not re.search('不要画|别画|不用画',text))
def codex_intent(text):return bool((re.search('codex|工作台|专门的对话',text,re.I) or re.search('继续.{0,8}(?:工作任务|工作台任务|Codex任务)',text,re.I)) and re.search('让|帮|委托|开|继续|做|干|处理|写|改|修|接入',text) and not re.search('不要.{0,5}codex|别.{0,5}codex|不用.{0,5}codex',text,re.I))
def requested_tool(text):
    if lookup_intent(text) and not codex_intent(text):return 'lookup_web'
    if codex_intent(text):return 'delegate_codex'
    if draw_intent(text):return 'draw_picture'
    if sing_intent(text):return 'sing_song'
    return None

def lookup_intent(text):
    from conversation_tools import external_intent,phone_intent
    return external_intent(text) and not phone_intent(text)

FACE_TOOL=definition('use_qq_face','准备一个QQ自带小表情，通常接在文字后。和图片表情包不同，不需要上传图片；别每条都用。',{'name':{'type':'string','enum':list(FACES)}},['name'])
TOOLS=[
 definition('lookup_web','查询公开网页并用Luna整理来源。普通用户滚动15分钟最多2次，每次最多5分钟（含排队），Plus免除这两项限制。一个问题算一次，内部搜索不重复计次。只能查公开网页，不能操作电脑、读取私人文件或登录账号。',
    {'query':{'type':'string','minLength':2,'maxLength':200}},['query']),
 definition('sing_song','对方要求唱歌时后台生成真正带旋律的清唱，之后发送QQ语音。song填春晓/随便时用达妮娅参考声线；原创可作短歌。可按要求调tempo速度、rhythm句间衔接、pitch_stability稳音。指定其他歌需已登记乐谱，不能冒充原曲或用念歌词代替。',
    {'song':{'type':'string','minLength':1,'maxLength':80},'tempo':{'type':'number','minimum':.8,'maximum':1.15},'rhythm':{'type':'string','enum':['normal','connected','gentle']},
     'pitch_stability':{'type':'string','enum':['original','steady']},'phrase_gap':{'type':'number','minimum':.10,'maximum':.80}},['song']),
 definition('draw_picture','对方要求画图时调用Codex内置图像生成，生成真实新图片，完成后发到当前私聊；不是从表情库选图。',{'prompt':{'type':'string','minLength':2,'maxLength':1200}},['prompt']),
 definition('delegate_codex','用户明确要求Codex干活时，委托deepseek娘项目的本地工作台。自行选新建或续接本工作台已有对话，最多GPT-6.1 Sol/high。不能操作其它Codex项目，不能修改运行中的机器人。完成后返回进度、结果与对话链接。',
    {'task':{'type':'string','minLength':2,'maxLength':2000},'mode':{'type':'string','enum':['auto','new','continue']},'thread_id':{'type':'string','maxLength':80},'model':{'type':'string','enum':MODELS},'effort':{'type':'string','enum':EFFORTS},
     'read_sources':{'type':'array','maxItems':12,'items':{'type':'string','enum':list(ALIASES)},'description':'仅当对方本次明确要求读取该资料时选择。数据库提供副本，不修改原库。'},
     'read_paths':{'type':'array','maxItems':3,'items':{'type':'string','maxLength':500},'description':'仅可填写对方本次原话里明确指定读取的绝对路径，权限只读。'}},['task','mode','model','effort'])]
