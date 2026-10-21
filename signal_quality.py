"""Check file integrity and unintended breaks; this is not a human-naturalness metric."""
from array import array
import math,wave

def check_wave(path,sections):
    with wave.open(str(path),'rb') as stream:
        rate=stream.getframerate();frames=stream.getnframes()
        if stream.getnchannels()!=1 or stream.getsampwidth()!=2:raise ValueError('歌声结果须为单声道16位PCM音频')
        samples=array('h',stream.readframes(frames))
    seconds=frames/rate;expected=max(float(s['offset'])+sum(map(float,s['note_dur'].split())) for s in sections)
    rms=math.sqrt(sum(x*x for x in samples)/max(len(samples),1))/32768
    clipping=sum(abs(x)>=32760 for x in samples)/max(len(samples),1)
    gaps=[]
    for section in sections:
        durs=list(map(float,section['note_dur'].split()))
        start=int((float(section['offset'])+durs[0]+.10)*rate)
        end=int((float(section['offset'])+sum(durs)-durs[-1]-.15)*rate)
        hop=round(.02*rate);run=longest=0
        for index in range(start,end-hop+1,hop):
            block=samples[index:index+hop]
            level=math.sqrt(sum(x*x for x in block)/len(block))/32768
            run=run+.02 if level<10**(-55/20) else 0
            longest=max(longest,run)
        gaps.append(round(longest,3))
    problems=[]
    if rms<.00005:problems.append('输出接近静音')
    if clipping>.0001:problems.append('音频出现明显削波')
    if abs(seconds-expected)>.25:problems.append('输出时长与乐谱不一致')
    if max(gaps,default=0)>.60:problems.append('乐句内部出现超过0.6秒的异常静音')
    return {'passed':not problems,'sample_rate':rate,'seconds':round(seconds,3),'rms_dbfs':round(20*math.log10(max(rms,1e-12)),1),
        'clipping_ratio':round(clipping,6),'longest_internal_quiet_seconds_per_phrase':gaps,'problems':problems,
        'method':'PCM integrity, clipping, score duration and internal quiet-gap checks; not emotion or human naturalness.'}
