"""Conservative tracked-source audit. Reports locations, never matched secrets."""
from pathlib import Path
import re,subprocess,sys

ROOT=Path(__file__).resolve().parent.parent
PATTERNS={
    'provider_credential':re.compile(r'\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b'),
    'personal_windows_path':re.compile(r'(?i)[A-Z]:[/\\]+Users[/\\]+(?!EXAMPLE(?:[/\\]|$))[^/\\\s\x27\x22]+[/\\]'),
    'live_project_id':re.compile(r'\bg-p-[a-f0-9]{32}\b'),
}
BLOCKED_SUFFIXES={'.sqlite','.sqlite3','.db','.wav','.mp3','.mp4','.onnx','.gguf','.pth','.pt','.ckpt','.safetensors','.zip','.7z','.lnk','.png','.jpg','.jpeg'}

def files():
    r=subprocess.run(['git','ls-files','-z'],cwd=ROOT,capture_output=True)
    if r.returncode==0:return [ROOT/p for p in r.stdout.decode('utf-8').split('\0') if p]
    return [p for p in ROOT.rglob('*') if p.is_file() and not any(x in p.relative_to(ROOT).parts for x in {'.git','__pycache__','workspace','data','logs','cache','.venv'}) and '.local.' not in p.name and p.name!='.release-owned']

def main():
    issues=[];count=0
    for p in files():
        count+=1;rel=p.relative_to(ROOT).as_posix()
        if '.local.' in p.name or p.suffix.lower() in BLOCKED_SUFFIXES or any(x in p.relative_to(ROOT).parts for x in ['data','logs','cache','models','robot-instance']):issues.append((rel,'runtime_or_binary_file'));continue
        try:text=p.read_text(encoding='utf-8-sig')
        except (UnicodeDecodeError,OSError):issues.append((rel,'unreviewed_binary'));continue
        if p.name=='scan_public.py':continue
        for label,pattern in PATTERNS.items():
            if pattern.search(text):issues.append((rel,label))
    if issues:
        for file,label in issues:print(file+': '+label)
        print('Public source audit failed; no matched value was printed.');return 1
    print(f'Public source audit passed: {count} tracked/candidate files; no runtime databases, media or credential patterns found.');return 0

if __name__=='__main__':sys.exit(main())
