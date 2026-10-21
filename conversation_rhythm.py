"""Small turn-specific dialogue guidance, based on actual delivered replies."""
import re

def guidance(text,rows):
    assistants=[t.split('\n[本轮已发送')[0] for r,t in rows if r=='assistant' and not t.startswith('[已发送')][-4:]
    rules=['接住本轮具体内容，沿用双方已经用过的自然称呼。可以有自己的判断和幽默；不为每句安排安慰、建议或反问。表情是交流动作。普通聊游戏时不用顺手叮嘱早点睡、别熬夜；没有具体依据就不自动夸对方今天很努力。']
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
