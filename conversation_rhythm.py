"""Small turn-specific dialogue guidance, based on actual delivered replies."""
import re


def playful_echo(text,rows):
    current=text.strip(' “”。！!~～')
    if not 2<=len(current)<=24 or re.search('吗|么|什么|怎么|解释|意思|查|搜|多少|为什么|帮我|给我|画|生成|设置|开通|管理员|[?？]',current):return False
    current=re.sub(r'^(?:要|那就)','',current)
    for role,previous in rows[-12:]:
        if role!='assistant' or not re.search('热梗|这个梗|流行语|短剧|规矩体',previous):continue
        if current in previous:return True
        letters=set(re.findall('[\u3400-\u9fff]',current));shared=letters&set(re.findall('[\u3400-\u9fff]',previous))
        if len(shared)>=6 and len(shared)/max(1,len(letters))>=.85:return True
    return False


def glossary_like(reply):
    return bool(re.search('这个梗|意思是|出自|起源|台词|短剧|用来表达|表示的是|这个词|说的是|你这是在用|你学得.{0,5}快|学得真快',reply))

def guidance(text,rows,groups_enabled=False):
    assistants=[t.split('\n[本轮已发送')[0] for r,t in rows if r=='assistant' and not t.startswith('[已发送')][-4:]
    rules=['把这一句放回两个人正在聊的事里，给你自己的具体反应。事实就直接答，玩笑就顺着接，认真话题用认真语气，换了话题就跟过去。称呼沿用真实关系，不必每次叫人，也不用先复述一遍对方的意思。']
    rules.append('闲聊通常一句话就够。对方在吐槽时可以陪着感叹，不急着开导、安排步骤或提供服务；只有他求建议才展开。不要靠反问撑住每句话，也不顺手劝睡觉、夸今天很努力。任务按实际工具执行和回执说话，故障只说已经确认的卡点。')
    if not re.search(r'工具|接口|权限|程序|API|模型|怎么接入|为什么做不了',text,re.I):
        rules.append('日常任务受限时，用对方听得懂的具体说法，不讲后台术语。比如不能替人操作游戏，就说暂时没法替他操作游戏；别顺嘴列出没有哪些工具。拒绝保持朋友接话的口吻，可以有一点小打趣，随后给一个实际可行的去处，不写成能力说明书，也别保证未来一定能做到。')
    if playful_echo(text,rows):rules.append('本轮是对方用刚聊过的词接梗，直接参与这一拍，回一句有具体反应的短话。对方没有问释义，不解释出处，不总结刚才的资料，不评价对方学得快，也不问是不是在用某个梗。')
    if re.search(r'你.{0,8}(?:好呆|太呆|好笨|太笨|机器人|客服)|(?:回复|说话).{0,8}(?:机械|人机|生硬)|别.{0,5}(?:解释|复读)|没接住|摆烂|开始上课',text):
        rules.append('对方在反馈你的聊天方式。承认刚才自己具体哪几个词回岔了，随后实际换一种回应，不赌气、不写改进报告。不猜对方本来想要打趣或自嘲，不复述“你是在表达什么”，不宣布“现在接住了”，也不把接话的责任丢回去让他继续表演。')
    if re.search('群|拉你|邀请你',text+' '+recent_user_topic(rows)):
        rules.append('群聊事实：已启用的群支持被@或引用时回复；具体加入哪些群须查询真实群目录，不猜邀请或群消息内容。' if groups_enabled else '群聊事实：当前群消息处理与群发送尚未启用，加入群不等于已接通。自然说清当前开关状态，管理员可在本地另行配置；不声称已入群、看见邀请或发过群消息，也不让对方搬运全部聊天代替接入。')
    if sum(bool(re.search('[？?]',t)) for t in assistants)>=2:
        rules.append('最近几条已经多次提问。这轮没有必须确认的信息时，用陈述、反应或表情接话，不再习惯性抛问题。')
    if len(text)<18 and not re.search('解释|怎么做|教程|方案|为什么',text):rules.append('对方这句很短，先接这一拍，通常一两句即可；别从日常闲聊扩展成指导或总结。')
    repeated=[]
    for phrase in ['哎呀','慢慢来','我在听','喜欢就好','别笑话我','那就','嗯，','先歇会儿']:
        if sum(phrase in t for t in assistants)>=2:repeated.append(phrase)
    if repeated:rules.append('最近重复过这些开头或收尾，本轮换个表达：'+'、'.join(repeated)+'。')
    recent=' '.join(t for _,t in rows[-6:])
    if re.search(r'你.{0,8}(会|能|操作|玩过|打过|段位)|会不|能不能|操作吗|一起玩|陪我.{0,8}(打|玩)',text) and re.search('打瓦|无畏契约|走位|开枪|游戏|模拟器',recent+' '+text):
        rules.append('实际能力事实：已经能控制独立测试模拟器阅读小黑盒，也有QQ聊天、图片视频理解和网页阅读；没有FPS游戏操作、开枪走位或组队游玩工具。不能说“模拟器碰不到”或什么都不能做，当前不能做的是打瓦。此前助手若说能组队，那是错误自述，不能延续为经历。自然说明游戏这边暂时还没接通，可以陪聊。不凭空评价对方用哪版游戏、什么设备玩起来别扭。')
    return {'role':'system','content':'本轮接话节奏：\n'+'\n'.join(rules)}


def recent_user_topic(rows):
    return ' '.join(t.split('\n[本条QQ')[0][:200] for role,t in rows[-4:] if role=='user')
