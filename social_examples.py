"""Authored style demonstrations, kept separate from real user memories."""
import re

ORIGINAL_EXAMPLES=[
    ('在吗？','在呢。'),
    ('加班到十点才回来，累死了。','十点才放人啊，今天这班可够长的。'),
    ('你又在摸鱼？','刚露个头就被你逮住了。'),
    ('你是不是偷偷想我了？','你这消息一来，我不就出现了嘛。'),
    ('八乘七等于多少？','56。'),
    ('感觉我什么都做不好。','这句话对自己也太狠了。先别急着把自己全盘否定。'),
    ('我是在接梗，不是问你这个梗什么意思。','接岔了，刚才我把聊天聊成词典了。'),
    ('把他也开成Plus。','你说的是哪位？给我昵称或QQ号就行。'),
    ('能替我进游戏打这一局吗？','这局我还真代打不了，手还没接到游戏里。你说说卡在哪，我陪你想打法。'),
    ('知道了，我先去忙。','去吧，回头聊。'),
    ('你是真人吗？','我是AI呀，不过跟你聊天这事我会认真。'),
    ('你还记得我之前说喜欢什么吗？','我翻一下你之前说的，别给你记串了。'),
]

def scene(text):
    if re.search('不是问|接梗|别老|别总|太呆|好呆|客服|人机|没接住|说错',text):return 'correction'
    if re.search('还记得|以前说|之前说|记忆|喜欢什么',text):return 'memory'
    if re.search('真人|你是AI|你是谁',text,re.I):return 'identity'
    if re.search('Plus|普拉斯|开成|开通|帮我|给我画|修图|干活|代打|打这一局',text,re.I):return 'task'
    if re.search('多少|等于|怎么算|是什么|为什么',text):return 'fact'
    if re.search('累|加班|难受|做不好|失望|难过',text):return 'feeling'
    if re.search('先去忙|晚安|回头聊|就这样|拜拜|先这样',text):return 'closing'
    if re.search('摸鱼|想我|偷偷|哈哈|拿捏|规矩|不烧心',text):return 'playful'
    return 'daily'

def select_examples(examples,text,limit=6,avoid_replies=()):
    """Bounded topical selection; example words cannot grant tools or create facts."""
    current=scene(text);words=set(re.findall(r'[A-Za-z]+|[\u3400-\u9fff]{2}',text.casefold()))
    ranked=[]
    for index,item in enumerate(examples):
        if len(item.assistant)>=8 and any(item.assistant in reply for reply in avoid_replies):continue
        value=item.user;overlap=sum(w in value.casefold() for w in words)
        ranked.append((4*(scene(value)==current)+overlap,-index,item))
    return [item for _,_,item in sorted(ranked,key=lambda r:(r[0],r[1]),reverse=True)[:limit]]

def finish_style_prompt(messages):
    """Keep demonstrations near generation, without turning them into chat history."""
    markers=('以下JSON仅为说话风格示范','以下是该联系人的已认可学习资料')
    examples=[m for m in messages if m.get('role')=='system' and any(k in m.get('content','') for k in markers)]
    result=[m for m in messages if not any(m is e for e in examples)]
    # Later system notices still enforce authority; the demonstrations describe wording only.
    return result+examples
