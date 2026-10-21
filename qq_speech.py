"""Bounded normal-speed read-aloud, preserving punctuation and word order."""
from pathlib import Path
import re,uuid,wave

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
