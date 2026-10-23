"""Stable local diagnostics; credentials never belong in a tool failure record."""
import re

def safe_reason(value):
    value=re.sub(r'\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{15,}|github_pat_[A-Za-z0-9_]+)\b','[凭据已遮蔽]',str(value))
    value=re.sub(r'(?i)Bearer\s+\S+','Bearer [已遮蔽]',value)
    value=re.sub(r'(?i)(token|password|api_key|secret)=([^&\s]+)',r'\1=[已遮蔽]',value)
    return value[:240] or '这次工具没有返回可用结果。'

def failure_info(exc):
    reason=safe_reason(exc);code='execution_failed'
    if type(exc).__name__=='ValidationError':code='invalid_arguments'
    elif re.search('没有要求|没有提出|未开放|执行请求|新任务',reason):code='action_not_requested'
    elif re.search('上限|这15分钟|次数|名额',reason):code='quota_reached'
    elif re.search('管理员|私聊|权限|只读|地址|范围|授权|绑定|发言者',reason):code='scope_denied'
    elif re.search('显卡|显存|内存|电脑当前较忙',reason):code='resource_busy'
    elif re.search('乐谱|未接入|尚未|不可用|未找到',reason):code='capability_unavailable'
    elif re.search('超时|网络|连接',reason):code='connection_failed'
    return {'prepared':False,'error':reason,'error_code':code,'exception_type':type(exc).__name__}
