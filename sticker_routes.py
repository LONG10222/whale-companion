"""Read-only gallery for public bundled assets. No credentials or send actions."""
from pathlib import Path
from fastapi import HTTPException,Query
from fastapi.responses import HTMLResponse,FileResponse
from sticker_library import StickerLibrary


def register_sticker_routes(app,*,root):
    library=StickerLibrary(root)

    @app.get('/stickers',response_class=HTMLResponse)
    def page():
        return (Path(root)/'stickers.html').read_text(encoding='utf-8')

    @app.get('/stickers/api/status')
    def status():
        return library.status()

    @app.get('/stickers/api/list')
    def listing(q:str=Query(default='',max_length=100),pack:str=Query(default='',max_length=40),
                emotion:str=Query(default='',max_length=20),ready_only:bool=True,
                page:int=Query(default=1,ge=1),limit:int=Query(default=24,ge=1,le=48),
                theme:str=Query(default='',max_length=30),character:str=Query(default='',max_length=80),
                topic:str=Query(default='',max_length=40),kind:str=Query(default='',pattern='^(|static|animated)$')):
        return library.search(q,pack,emotion,ready_only,page,limit,theme,character,topic,kind)

    @app.get('/stickers/images/{item_id}')
    def image(item_id:str):
        try:return FileResponse(library.file(item_id),headers={'Cache-Control':'public,max-age=86400'})
        except (ValueError,FileNotFoundError):raise HTTPException(404,'没有这张表情。') from None

    @app.get('/stickers/thumbs/{item_id}')
    def thumbnail(item_id:str):
        try:return FileResponse(library.thumbnail(item_id),media_type='image/webp',headers={'Cache-Control':'public,max-age=86400'})
        except (ValueError,FileNotFoundError):raise HTTPException(404,'没有这张表情。') from None

    return library
