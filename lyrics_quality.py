"""Compare unprompted ASR text with supplied lyrics; scores support human listening."""
import re,unicodedata
from difflib import SequenceMatcher

def normalize(text):
    text=re.sub(r'\[[^\]]*\]','',text)
    from opencc import OpenCC
    text=OpenCC('t2s').convert(unicodedata.normalize('NFKC',text).lower())
    return ''.join(re.findall(r'[\u3400-\u9fff]|[a-z0-9]+',text))

def assess(expected,recognized):
    target=normalize(expected);heard=normalize(recognized)
    if not target:raise ValueError('Expected lyrics contain no words')
    previous=list(range(len(heard)+1))
    for i,a in enumerate(target,1):
        current=[i]
        for j,b in enumerate(heard,1):current.append(min(current[-1]+1,previous[j]+1,previous[j-1]+(a!=b)))
        previous=current
    cer=previous[-1]/len(target)
    matched=sum(x.size for x in SequenceMatcher(None,target,heard,autojunk=False).get_matching_blocks())
    coverage=matched/len(target)
    passed=cer<=.05 and coverage>=.95
    return {'passed':passed,'asr_text':recognized,'character_error_rate':round(cer,3),
        'ordered_lyric_coverage':round(coverage,3),'expected_characters':len(target),
        'recognized_characters':len(heard),'expected_lyrics':expected,
        'method':'unprompted local ASR; not a substitute for listening'}
