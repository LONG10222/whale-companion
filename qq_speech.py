"""Bounded normal-speed read-aloud, preserving punctuation and word order."""
from pathlib import Path
import re,uuid,wave

def speech_request(text):
    """Only the current request authorizes speech; talking about audio is insufficient."""
    negative=re.search(r'(?:不要|不用|别|不必|不需要).{0,5}(?:发语音|语音回复|朗读|读出来|念出来|读给|念给)',text)
    if re.search(r'语音.{0,8}(?:转成|转为|转|变成)(?:文字|文本)|(?:转写|转录|识别|听写).{0,10}语音',text) and not re.search(r'再.{0,8}(?:发语音|读出来|念出来)',text):return {'requested':False,'read_exact':False,'literal':''}
    read=bool(re.search(r'朗读|(?:读|念).{0,5}(?:出来|一下|一遍|给我|给你听)|(?:读|念)[给一这那]|复刻.{0,8}(?:语音|声音)|不是[，,、\s]*(?:发|用)语音',text))
    reference=bool(re.search(r'(?:原文|原话|文字|这段|这句|这个|这条|这些|上面|刚才).{0,16}(?:语音|声音)',text) and re.search('发|转|读|念|按照|改成|换成|复刻',text))
    read=read or reference
    voice=bool(re.search(r'(?:发|用|转成|转为|生成|合成|做成|换成|来条|来一条).{0,8}(?:语音|声音|配音)|(?:语音|声音).{0,8}(?:发给|发我|回复|读|念|来一条)|^语音.{0,3}(?:说|讲)|说一句|说句话',text))
    if negative:return {'requested':False,'read_exact':False,'literal':''}
    if re.search(r'(?:不要|不用|别).{0,4}(?:复刻|照读|照着读|读原文)',text):read=False
    # A read-aloud correction takes precedence over a stale singing discussion.
    if re.search(r'唱歌|清唱|唱一|唱个|给我唱|你唱',text) and not read:return {'requested':False,'read_exact':False,'literal':''}
    literal=''
    if read or voice:
        matches=re.findall(r'[“「『"]([^”」』"]+)[”」』"]',text,re.S)
        if matches:literal=matches[-1].strip();read=True
        elif read:
            colon=re.search(r'[：:]\s*(\S[\s\S]*)$',text)
            if colon:literal=colon.group(1).strip()
    return {'requested':read or voice,'read_exact':read,'literal':literal}

def read_target(request,text,recent,quoted=None,descriptions=None):
    if request['literal']:return request['literal'],'literal'
    if quoted is not None and quoted.get('kind')=='image':
        found='\n'.join(d.get('text','').strip() for d in descriptions or [] if isinstance(d,dict) and isinstance(d.get('text'),str) and d.get('text','').strip())
        if found:return found,'quoted_image_text'
    if quoted is not None:
        if quoted.get('text'):return quoted['text'],'quoted'
        raise ValueError('没取到你引用的那段原文，重新引用那条文字消息，我就读给你听。')
    if re.search('图|里面|里边|这里面',text):
        found='\n'.join(d.get('text','').strip() for d in descriptions or [] if isinstance(d,dict) and isinstance(d.get('text'),str) and d.get('text','').strip())
        if found:return found,'image_text'
        raise ValueError('把要读的文字发来，或者引用那条文字消息，我就按原文念。')
    role='user' if re.search('我刚才|我说的|我的话|我发的|我上[一句段条]',text) else 'assistant'
    for speaker,content in reversed(recent):
        if speaker!=role:continue
        content=content.split('\n[本轮')[0].strip()
        if content.startswith('[已发送') or not content:continue
        if re.search('语音.{0,8}(?:没|失败|报错)|没.{0,5}合成|后台报错|本轮没识别',content):break
        return content,'previous_'+role
    raise ValueError('引用一下你想让我念的那段话，我就按原文读，不会擅自改写。')

def chunks(text,limit=100):
    text=text.strip()
    if not text or len(text)>600:raise ValueError('单次朗读请控制在600字以内。')
    out=[];current=''
    for part in re.findall(r'[^。！？；\n]+[。！？；\n]*|[。！？；\n]+',text):
        while part:
            room=limit-len(current);current+=part[:room];part=part[room:]
            if len(current)==limit:out.append(current);current=''
        if len(current)>=50:out.append(current);current=''
    if current:out.append(current)
    return out or [text]

def read_aloud(speaker,text,folder):
    parts=chunks(text);files=[Path(speaker(p)) for p in parts]
    return join_audio(files,folder)

def read_aloud_parts(speaker,text,folder):
    """Keep every word; split long readings into normal-length QQ voice messages."""
    chunks(text)  # Validate the complete source before synthesizing any fragment.
    # Longer paragraphs can swallow their short opening sentence in this voice.
    # Preserve short chat prosody; give full sentences their own contour in long reads.
    sentences=[text.strip()] if len(text.strip())<=60 else re.findall(r'[^。！？\n]+[。！？\n]*|[。！？\n]+',text.strip())
    parts=[part for sentence in sentences for part in chunks(sentence,60)];groups=[];current=[];seconds=0
    for part in parts:
        file=Path(speaker(part))
        with wave.open(str(file),'rb') as audio:duration=audio.getnframes()/audio.getframerate()
        if not 0<duration<=80:raise ValueError('语音分段过长或无效，请缩短后重试。')
        if current and seconds+duration+.14>80:groups.append(current);current=[];seconds=0
        current.append((part,file));seconds+=duration+.14
    if current:groups.append(current)
    return [{'path':join_audio([file for _,file in group],folder),'text':''.join(part for part,_ in group)} for group in groups]

def join_audio(files,folder):
    if len(files)==1:return files[0]
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True);target=folder/(uuid.uuid4().hex+'.wav')
    parameters=None;frames=[];size=0
    for file in files:
        with wave.open(str(file),'rb') as audio:
            actual=(audio.getnchannels(),audio.getsampwidth(),audio.getframerate(),audio.getcomptype())
            if parameters is not None and actual!=parameters:raise ValueError('分段语音格式不一致，未发送拼接音频。')
            parameters=actual;data=audio.readframes(audio.getnframes());frames.append(data);size+=len(data)
            if size>12*1024**2 or size/(actual[0]*actual[1]*actual[2])>100:raise ValueError('这段朗读过长，请拆成两次。')
    with wave.open(str(target),'wb') as audio:
        audio.setnchannels(parameters[0]);audio.setsampwidth(parameters[1]);audio.setframerate(parameters[2])
        for index,data in enumerate(frames):
            if index:audio.writeframes(b'\x00'*int(parameters[0]*parameters[1]*parameters[2]*.14))
            audio.writeframes(data)
    return target
