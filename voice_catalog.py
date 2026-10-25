"""Fixed, licensed upstream speaker identities; no paths or credentials in tool input."""
import re

VOICES = {
    'dania': {'model':'dania-gptsovits','speaker':'dania','label':'达妮娅中文参考声线','aliases':['达妮娅','达尼娅','默认声线','默认音色']},
    'vivian': {'model':'qwen3-tts-local','speaker':'vivian','label':'Vivian 清亮女声','aliases':['vivian','薇薇安','清亮女声']},
    'serena': {'model':'qwen3-tts-local','speaker':'serena','label':'Serena 温柔女声','aliases':['serena','瑟琳娜','温柔女声']},
    'dylan': {'model':'qwen3-tts-local','speaker':'dylan','label':'Dylan 青年男声','aliases':['dylan','迪伦','青年男声']},
    'zh-3': {'model':'kokoro-cpu','speaker':'zh-3','label':'Kokoro CPU 女声','aliases':['kokoro女声','cpu女声']},
    'zh-12': {'model':'kokoro-cpu','speaker':'zh-12','label':'Kokoro CPU 男声','aliases':['kokoro男声','cpu男声']},
}

def select_voice(text):
    # Never let voice names inside the requested reading change the synthesis config.
    command=re.split(r'[：:]',text,maxsplit=1)[0]
    command=re.sub(r'[“「『"]+[^”」』"]*[”」』"]+', '',command)
    if re.search(r'(?:不要|不用|别).{0,5}(?:换|切换|改)',command):return ''
    return next((key for key,profile in VOICES.items() if any(alias.lower() in command.lower() for alias in profile['aliases'])), '')

def profile(voice='dania'):
    if voice not in VOICES:raise ValueError('这个音色尚未登记，可以选达妮娅、Vivian、Serena或Dylan。')
    return VOICES[voice]

def available(config):
    from pathlib import Path
    return [dict(id=key,label=value['label'],model=value['model'],ready=(
        Path(config.get('dania_config','__missing__')).is_file() if key=='dania' else
        Path(config.get('kokoro_model_dir' if key.startswith('zh-') else 'model_dir','__missing__')).is_dir()),
        singing_compatible=key=='dania') for key,value in VOICES.items()]
