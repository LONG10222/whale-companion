"""Convert typed Mandarin lyrics and notes to the voicebank's explicit score format."""
from pathlib import Path
import json,re
from pypinyin import lazy_pinyin
from pypinyin import Style

def make_soulx_metadata(phrases,phone_set):
    allowed=set(json.loads(Path(phone_set).read_text(encoding='utf-8')))
    phones=[];durations=[];pitches=[];types=[];words=[]
    for phrase in phrases:
        text=''.join(re.findall('[\u3400-\u9fff]',phrase['text']))
        syllables=phrase.get('pinyin') or lazy_pinyin(text,style=Style.TONE3,neutral_tone_with_five=True)
        tokens=['zh_'+x.lower().replace('ü','v') for x in syllables]
        missing=[x for x in tokens if x not in allowed]
        if missing:raise ValueError('SoulX需要声库支持的带声调拼音：'+','.join(missing))
        phones+=['<SP>']+tokens+['<SP>'];durations+=[phrase.get('pre_gap',.18)]+list(phrase['durations'])+[phrase.get('post_gap',.30)]
        pitches+=[0]+[midi(n) for n in phrase['notes']]+[0];types+=[1]+[2]*len(text)+[1];words.append(text)
    return {'index':'local_typed_score','language':'Mandarin','time':[0,round(sum(durations)*1000)],
        'text':' '.join(words),'phoneme':' '.join(phones),'duration':' '.join(map(str,durations)),
        'note_pitch':' '.join(map(str,pitches)),'note_type':' '.join(map(str,types))}

def midi(note):
    match=re.fullmatch(r'([A-G])([#b]?)([3-5])',note)
    if not match:raise ValueError('音符请使用 C4、D#4、Bb4 等格式，音区限制为三至五组')
    letter,accidental,octave=match.groups()
    value=(int(octave)+1)*12+{'C':0,'D':2,'E':4,'F':5,'G':7,'A':9,'B':11}[letter]
    return value+(1 if accidental=='#' else -1 if accidental=='b' else 0)

def make_score(phrases,phone_map):
    mapping=json.loads(Path(phone_map).read_text(encoding='utf-8'))['mapping']
    offset=0;sections=[];all_words=[]
    if not 1<=len(phrases)<=8:raise ValueError('每次生成一至八句歌词')
    for phrase in phrases:
        text=''.join(re.findall('[\u3400-\u9fff]',phrase['text']))
        if not text:raise ValueError('本地歌手目前接受中文歌词')
        notes=phrase['notes'];durations=phrase['durations']
        if len(text)!=len(notes) or len(notes)!=len(durations):raise ValueError('每个字需要对应一个音符和一个时长')
        for note in notes:midi(note)
        if any(not .2<=float(x)<=3 for x in durations):raise ValueError('单个音符时长需在 0.2 至 3 秒之间')
        syllables=phrase.get('pinyin') or lazy_pinyin(text)
        if len(syllables)!=len(text):raise ValueError('拼音数量需要与歌词字数相同')
        groups=[['SP']]
        for syllable in syllables:
            syllable=re.sub('[1-5]$','',syllable.lower().replace('ü','v'))
            if syllable not in mapping:raise ValueError('声库词典不支持拼音：'+syllable)
            groups.append(mapping[syllable])
        groups.append(['SP']);durations=[phrase.get('pre_gap',.18)]+[float(x) for x in durations]+[phrase.get('post_gap',.30)]
        section={'offset':round(offset,6),'text':'SP '+text+' SP','lang':'zh',
            'ph_seq':' '.join(p for group in groups for p in group),'ph_num':' '.join(str(len(x)) for x in groups),
            'note_seq':' '.join(['rest']+notes+['rest']),'note_slur':' '.join('0' for _ in groups),
            'note_dur':' '.join(str(x) for x in durations),'expr':float(phrase.get('expression',1.0))}
        sections.append(section);offset+=sum(durations);all_words.append(text)
    if offset>35:raise ValueError('当前先生成不超过 35 秒的片段，便于逐段审核')
    return sections,'\n'.join(all_words),round(offset,3)

def demo_request():
    lines=['春眠不觉晓','处处闻啼鸟','夜来风雨声','花落知多少']
    notes=[['E4','G4','A4','G4','E4'],['D4','E4','G4','E4','D4'],['E4','G4','B4','A4','G4'],['A4','G4','E4','D4','C4']]
    return {'title':'春晓 · 绮萱 DiffSinger AI合成清唱','phrases':[
        {'text':text,'notes':pitches,'durations':[.65,.65,1.10,.65,1.95]} for text,pitches in zip(lines,notes)],
        'seed':20261021,'lyrics_origin':'public_domain','source_url':'https://zh.wikisource.org/wiki/春曉_(孟浩然)'}
