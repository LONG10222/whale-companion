"""Finite conversational tools, with authority checked independently by their services."""
from conversation_tools import definition

SET_MY_NAME=definition('set_my_name','本人希望更改你对自己的称呼，或恢复QQ昵称时使用。可理解自然口语。只改当前发言者在本程序的显示称呼，不能改别人的名称、QQ真实昵称、QQ号主键、权限或管理员身份。',
    {'name':{'type':'string','maxLength':64,'description':'只填本人要求的新称呼，逐字取自当前原话；恢复QQ昵称时留空。'},'reset':{'type':'boolean'},
     'evidence':{'type':'string','minLength':1,'maxLength':300,'description':'可省略，由程序绑定本轮真实原话。若填写必须逐字复制当前用户原文片段，不能改写或概括。'}},['name','reset'])

END_CHAT=definition('end_chat_turn','对方只是确认收到、准备收尾或话题已自然结束时，可以选择安静结束本轮，不再硬接一句。不能跳过新任务、提问、求助或需要回应的反馈；不关闭QQ、不取消任务、不妨碍下一句聊天。单独使用，不与其他工具同时调用。',
    {'evidence':{'type':'string','minLength':1,'maxLength':200},'reason':{'type':'string','enum':['acknowledgement','goodbye','topic_end']}},['evidence','reason'])

MANAGE_PLUS=definition('manage_plus','管理员在真实私聊中自然提出开通或取消Plus、放开某人的查询次数限制时使用。文字与QQ语音均可，不需要固定口令。只能管理Plus，不能授予管理员、其他模型或电脑权限。目标不清楚时补问。',
    {'enabled':{'type':'boolean'},'targets':{'type':'array','items':{'type':'string','maxLength':80},'maxItems':20},'group':{'type':'string','maxLength':80},
     'all_group_members':{'type':'boolean'},'evidence':{'type':'string','minLength':1,'maxLength':300}},['enabled','targets','group','all_group_members','evidence'])
READ_GROUP_IMAGES=definition('read_group_images','按本轮用户意图读取QQ已授权群的图片文件。可用于“刚才传到群文件的照片”“那张文件里的图”等自然说法，不要求文件必须当聊天图片发出。只读图片，不运行程序、不读取电脑任意文件。群内只读本群；私聊仅管理员可选择已授权群。',
    {'group':{'type':'string','maxLength':80},'file_name':{'type':'string','maxLength':240,'description':'只填用户点名或本轮真实目录中已经列出的文件名；不能猜。对象不唯一时先确认。'},'latest':{'type':'boolean'},
     'evidence':{'type':'string','minLength':1,'maxLength':300,'description':'可省略，由程序绑定本轮真实原话；填写时逐字复制当前用户原文，不能复述旧消息或概括。'}},['group','file_name','latest'])
