"""Cheap conversation-level sticker opportunities; previews never consume quota."""
import re

def reaction_mood(text,reply='',has_images=False):
    current=text.strip()
    # A casual reaction should not decorate a serious answer with a random joke.
    if re.search(r'认真|严肃|别开玩笑|报警|判刑|去世|住院|病危|自杀|抑郁|受伤|事故|葬礼|肝疼|医生|人机感|太客服|太呆|别总|不要再|怎么实现|代码|报错|API|配置|安装|部署|教程|文档|显存|内存|公式|论文|怎么办',current+'\n'+reply,re.I):return None
    if len(current)>220 or len(reply)>320:return None
    for emotion,pattern in [
        ('安慰',r'累了|好累|委屈|难过|不开心|抱抱|难受'),
        ('开心',r'好耶|太好了|开心|出货|中了|赢了|终于过|成功啦|成功了'),
        ('调皮',r'哈哈|笑死|绷不住|逗你|逗我|贫嘴|会贫|摸鱼|笨蛋|乐了|斗图'),
        ('无语',r'无语|离谱|服了|坐牢|歪了|可恶|气死|破防'),
        ('喜欢',r'喜欢你|想你|可爱|好看|贴贴'),
        ('感谢',r'谢谢|谢啦|感谢'),
        ('问候',r'早上好|早安|你好|晚上好|嗨[，,！!~～\s]*$')]:
        if re.search(pattern,current+'\n'+reply):return {'emotion':emotion,'strong':True,'query':''}
    if re.search('晚安|拜拜|睡了|休息了|睡觉',current):return {'emotion':'问候','strong':True,'query':'晚安 睡觉','require_query':True}
    if has_images and not current:return None
    # Ordinary short, light conversation can occasionally use a friendly reaction.
    return {'emotion':'调皮' if re.search('哈哈|嘿嘿|哼|逗|调皮',reply) else '开心','strong':False,'query':''}

def sticker_opportunity(mode,text,reply,activity,now,*,has_images=False,has_media=False,trace=None,forbid=False,chosen_emotion=None):
    if mode=='requested' or forbid:return {'allowed':False,'due':False,'reason':'本轮仅文字或只按明确要求发图'}
    if has_media or any(t['tool'] not in {'use_sticker','speak_text'} for t in (trace or [])):
        return {'allowed':False,'due':False,'reason':'本轮先完成资料或视频内容的回应'}
    mood=reaction_mood(text,reply,has_images)
    if mood is None and has_images and not text.strip() and chosen_emotion:mood={'emotion':chosen_emotion,'strong':True,'query':''}
    if not mood:return {'allowed':False,'due':False,'reason':'当前是认真内容或长回复'}
    last=activity['last_at'];plain=activity['plain_turns'];minimum=1 if mode=='frequent' else 2
    if last and (now-last<45 or plain<minimum):return {'allowed':False,'due':False,'reason':'刚发过表情，先留一点文字聊天空间'}
    due=(not last or plain>=2 or mood['strong'] and plain>=1) if mode=='frequent' else (plain>=4 or mood['strong'] and plain>=3)
    return {'allowed':True,'due':bool(due),**mood,'reason':'轻松接话，优先配一张合适的反应图' if due else '由模型看语境选择'}
