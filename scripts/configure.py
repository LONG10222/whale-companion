"""Create private local configuration without putting secrets in arguments or Git."""
from pathlib import Path
import argparse,getpass,json,re,secrets,sys

ROOT=Path(__file__).resolve().parent.parent
def write(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():raise FileExistsError(str(path)+' already exists; refusing to overwrite personal configuration')
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')

def main():
    parser=argparse.ArgumentParser(description='Create local Whale Companion settings. Existing files are never overwritten.')
    parser.add_argument('--admin-qq');parser.add_argument('--bot-qq');parser.add_argument('--skip-key',action='store_true');parser.add_argument('--non-interactive',action='store_true')
    args=parser.parse_args()
    admin=args.admin_qq or ('' if args.non_interactive else input('Administrator QQ number (saved locally only): ').strip())
    bot=args.bot_qq or ('' if args.non_interactive else input('Dedicated bot QQ number: ').strip())
    if not all(re.fullmatch(r'[1-9]\d{4,11}',x or '') for x in [admin,bot]) or admin==bot:parser.error('Provide two different valid QQ account numbers')
    required=[ROOT/'settings.local.json',ROOT/'qq.local.json',ROOT/'agent.local.json']
    if any(p.exists() for p in required):parser.error('Local settings already exist; edit them locally instead of resetting credentials')
    cfg=json.loads((ROOT/'configs/settings.example.json').read_text(encoding='utf-8'))
    cfg.update(api_token=secrets.token_urlsafe(36),python=sys.executable,dania_python=sys.executable,host='127.0.0.1',port=18765)
    write(required[0],cfg)
    write(required[1],{'enabled':False,'bot_id':bot,'locked_bot_id':bot,'allowed_private_ids':[admin],'test_contact':{'name':'owner','user_id':admin},'voice_enabled':False,'onebot_token':secrets.token_urlsafe(36)})
    key='' if args.skip_key or args.non_interactive else getpass.getpass('DeepSeek API key (hidden; Enter to configure in the local page later): ').strip()
    write(required[2],{'model':'deepseek-flash','api_key':key})
    write(ROOT/'bot-runtime.local.json',{})
    write(ROOT/'configs/launcher.local.json',json.loads((ROOT/'configs/launcher.example.json').read_text(encoding='utf-8')))
    catalog=ROOT/'assets/stickers/catalog.json'
    if not catalog.exists():write(catalog,json.loads((ROOT/'assets/stickers/catalog.example.json').read_text(encoding='utf-8')))
    print('Local files created. Messaging is paused, groups are disabled, and voice models are optional. No keys were printed.')

if __name__=='__main__':main()
