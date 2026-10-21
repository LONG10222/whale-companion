"""Import an explicitly selected local folder of permitted public stickers."""
from pathlib import Path
from collections import Counter
import argparse,hashlib,json,shutil,sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from sticker_library import PUBLIC_SOURCES,CATALOG_SOURCE
from PIL import Image

ROOT=Path(__file__).resolve().parent.parent

def main():
    parser=argparse.ArgumentParser(description='Import public, permitted images only. No downloads or archive extraction.')
    parser.add_argument('directory',type=Path);parser.add_argument('--source',required=True,choices=sorted(PUBLIC_SOURCES));parser.add_argument('--pack',required=True)
    parser.add_argument('--theme',choices=['DeepSeek','鸣潮','其他'],default='其他');parser.add_argument('--emotion',default='');args=parser.parse_args()
    base=args.directory.expanduser().resolve()
    if not base.is_dir():parser.error('Select an existing local directory')
    target=ROOT/'assets/stickers';target.mkdir(parents=True,exist_ok=True);images=target/'files';images.mkdir(exist_ok=True)
    file=target/'catalog.json';old=json.loads(file.read_text(encoding='utf-8')) if file.exists() else {'images':[],'packs':[],'sources':[]}
    records={r['id']:r for r in old['images']};total=0
    for source in base.rglob('*'):
        if not source.is_file() or source.is_symlink() or source.suffix.lower() not in {'.png','.jpg','.jpeg','.webp','.gif'}:continue
        if source.stat().st_size>12*1024**2:continue
        total+=source.stat().st_size
        if total>512*1024**2 or len(records)>=20000:raise ValueError('Import size limit reached; split the selected collection')
        try:
            with Image.open(source) as im:width,height=im.size;frames=getattr(im,'n_frames',1)
        except (OSError,ValueError):continue
        digest=hashlib.sha256(source.read_bytes()).hexdigest();suffix='.jpg' if source.suffix.lower()=='.jpeg' else source.suffix.lower();dest=images/(digest+suffix)
        if not dest.exists():shutil.copy2(source,dest)
        records[digest[:24]]={'id':digest[:24],'sha256':digest,'file':str(dest.relative_to(target)).replace('\\','/'),'pack':args.pack,'pack_code':args.source+'-'+hashlib.sha256(args.pack.encode()).hexdigest()[:8], 'packs':[args.pack],'title':source.stem[:100],
            'caption':'','ocr_text':'','emotions':[args.emotion] if args.emotion else [],'topics':[],'themes':[args.theme],'theme':args.theme,
            'animated':frames>1,'frames':frames,'bytes':dest.stat().st_size,'width':width,'height':height,'source_id':args.source,'source_page':PUBLIC_SOURCES[args.source],
            'artist':'See original collection credits','annotation_status':'source_labeled' if args.emotion else 'unlabeled','annotation_source':'operator-provided --emotion' if args.emotion else '', 'auto_select':bool(args.emotion),'characters':[],'collection':args.pack}
    sources={s['id']:s for s in old.get('sources',[])};sources[args.source]={'id':args.source,'name':args.source,'url':PUBLIC_SOURCES[args.source]}
    packs={p['name']:p for p in old.get('packs',[])};packs[args.pack]={'name':args.pack,'source_page':PUBLIC_SOURCES[args.source]}
    rows=list(records.values());data={'version':2,'source':CATALOG_SOURCE,'sources':list(sources.values()),'packs':list(packs.values()),'images':rows,
        'stats':{'images':len(rows),'packs':len(packs),'animated':sum(r['animated'] for r in rows),'bytes':sum(r['bytes'] for r in rows)}}
    stage=file.with_suffix('.tmp');stage.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');stage.replace(file)
    print('Imported catalog entries:',len(rows),'Only explicitly labeled entries are eligible for automatic selection.')

if __name__=='__main__':main()
