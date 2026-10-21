from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel,Field,ConfigDict
from balance_watch import BalanceWatch

class Config(BaseModel):
    model_config=ConfigDict(extra='forbid')
    enabled:bool=True
    threshold_cny:str=Field(default='1.00',max_length=12)
    interval_minutes:int=Field(default=15,ge=15,le=240)

def register_balance_routes(app,*,root,authorize,bridge):
    service=BalanceWatch(root,bridge);router=APIRouter(prefix='/qq/balance',dependencies=[Depends(authorize)])
    @router.get('/state')
    def state():return service.state()
    @router.post('/refresh')
    def refresh():return service.refresh()
    @router.post('/config')
    def config(body:Config):
        try:service.configure(body.enabled,body.threshold_cny,body.interval_minutes)
        except Exception:raise HTTPException(400,'提醒金额或间隔不正确。') from None
        return service.state()
    app.include_router(router);return service
