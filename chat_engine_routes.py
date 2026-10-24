"""Local owner controls for a reversible, single-ingress dialogue rollout."""
from pathlib import Path
from typing import Literal
import json,uuid
from fastapi import Depends,HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel,ConfigDict


class EngineMode(BaseModel):
    model_config=ConfigDict(extra='forbid')
    mode:Literal['legacy','hybrid']


def register_engine_routes(app,*,root,bridge,authorize):
    root=Path(root)
    @app.get('/qq/engine/source')
    def source():
        from chat_engine import source_information
        return source_information(root)
    @app.get('/qq/engine')
    def page():return FileResponse(root/'chat_engine.html')
    @app.get('/qq/engine/state',dependencies=[Depends(authorize)])
    def state():
        result=bridge.chat.native_engine.status()
        snapshot=bridge.snapshot()
        result['qq_connected']=bool(snapshot.get('connected') and snapshot.get('account_matches'))
        result['execution_policy']='QQ号身份、管理员／Plus／普通用户、模型上限和文件目录权限均由原有执行层核对。'
        return result
    @app.post('/qq/engine/config',dependencies=[Depends(authorize)])
    def configure(request:EngineMode):
        if request.mode=='hybrid' and not bridge.chat.native_engine.status().get('kernel',{}).get('ok'):
            raise HTTPException(409,'新对话环境还没准备好，当前继续使用原有对话。')
        path=root/'configs/chat-engine.local.json'
        try:cfg=json.loads(path.read_text(encoding='utf-8'))
        except (OSError,ValueError):cfg={}
        cfg['mode']=request.mode
        path.parent.mkdir(parents=True,exist_ok=True);stage=path.with_name('engine-'+uuid.uuid4().hex+'.tmp')
        stage.write_text(json.dumps(cfg,ensure_ascii=False,indent=2),encoding='utf-8');stage.replace(path)
        return state()
