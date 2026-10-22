"""Owner-only, default-off social adapters. Never exposed as model/chat tools.

Upstream contract reviewed at NapNeko/NapCatQQ commit
3420f0b76d7dc0ac92cde1663f21ea27f61e13bb: SendQzoneMsg and
SetFriendAddRequest. Installed-server support remains unverified until a receipt.
"""
from contextlib import contextmanager
from pathlib import Path
import copy
import hashlib
import json
import secrets
import sqlite3
import threading
import time

AUDIENCES = {'self': (64, '仅自己可见'), 'friends': (4, '好友可见'), 'public': (1, '所有人可见')}

class SocialError(ValueError):
    pass

class QQSocial:
    def __init__(self, bridge, clock=time.time):
        self.bridge = bridge
        self.clock = clock
        self.lock = threading.RLock()
        self.enabled = False  # Deliberately resets on each application restart.
        self.pending = {}
        self.requests = {}
        self.path = Path(bridge.root) / 'data/qq/social_attempts.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS attempts (fingerprint TEXT PRIMARY KEY, state TEXT, at REAL)')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def status(self):
        return {'enabled': self.enabled, 'confirmation_seconds': 120,
                'qzone_text': 'adapter_available_server_unverified',
                'friend_requests': 'received_requests_only',
                'qzone_images': False, 'qzone_browse': False, 'active_add_friend': False,
                'notice': '仅本机管理接口，未接入模型工具。接口存在不代表当前QQ账号可用；不自动重试。'}

    def configure(self, enabled):
        if type(enabled) is not bool:
            raise SocialError('开关必须为布尔值。')
        with self.lock, self.bridge.lock:
            self.enabled = enabled
            self.pending.clear()
        return self.status()

    def _account(self):
        b = self.bridge
        if not self.enabled:
            raise SocialError('QQ社交操作默认关闭，请先在本机明确启用。')
        if not b.connected or not b.cfg.get('enabled') or not b.login_id or b.login_id != b.cfg.get('bot_id'):
            raise SocialError('QQ未连接或登录账号与配置不一致。')
        return b.login_id, b.epoch

    def ingest(self, obj, epoch):
        """Called ONLY by the authenticated OneBot receive loop, never HTTP/chat."""
        b = self.bridge
        with self.lock, b.lock:
            if not (self.enabled and b.connected and b.cfg.get('enabled') and epoch == b.epoch
                    and b.login_id == b.cfg.get('bot_id') == str(obj.get('self_id', ''))
                    and obj.get('post_type') == 'request' and obj.get('request_type') == 'friend'):
                return False
            from qq_identity import qq_id
            try:
                who = qq_id(obj.get('user_id', ''))
            except ValueError:
                return False
            flag = obj.get('flag')
            at = obj.get('time')
            if (not isinstance(flag, str) or not 1 <= len(flag) <= 1024 or who == b.login_id
                    or type(at) not in (int, float) or not self.clock()-86400 <= at <= self.clock()+60):
                return False
            key = hashlib.sha256((b.login_id+'\0'+flag).encode()).hexdigest()
            self._prune()
            if key in self.requests or self._attempted(key):
                return False
            if len(self.requests) >= 100:
                return False
            self.requests[key] = {'id': key, 'user_id': who, 'flag': flag,
                                  'comment': str(obj.get('comment', ''))[:500],
                                  'bot': b.login_id, 'epoch': epoch, 'expires': at+86400}
            return True

    def _prune(self):
        now = self.clock()
        self.pending = {k:v for k,v in self.pending.items() if v['expires'] > now}
        self.requests = {k:v for k,v in self.requests.items() if v['expires'] > now}

    def list_requests(self):
        with self.lock, self.bridge.lock:
            bot, epoch = self._account()
            self._prune()
            # Request text is untrusted display-only data, not instructions.
            return [{k:v for k,v in r.items() if k not in {'flag', 'epoch'}}
                    for r in self.requests.values() if r['bot'] == bot and r['epoch'] == epoch
                    and not self._attempted(r['id'])]

    def _attempted(self, key):
        with self.db() as db:
            return db.execute('SELECT 1 FROM attempts WHERE fingerprint=?', (key,)).fetchone() is not None

    def prepare(self, principal, operation):
        """principal is derived by authenticated routes, never from request JSON."""
        with self.lock, self.bridge.lock:
            bot, epoch = self._account()
            if not principal:
                raise SocialError('缺少已认证的管理员会话。')
            self._prune()
            if not isinstance(operation, dict):
                raise SocialError('操作格式不正确。')
            kind = operation.get('kind')
            if kind == 'qzone_text':
                if set(operation) != {'kind', 'content', 'audience'}:
                    raise SocialError('仅支持纯文字和明确的受众；不支持图片或原始接口参数。')
                content = operation['content']
                audience = operation['audience']
                if not isinstance(content, str) or not content.strip() or len(content) > 2000 or '\x00' in content:
                    raise SocialError('空间正文须为1至2000字符的非空纯文字。')
                if not isinstance(audience, str) or audience not in AUDIENCES:
                    raise SocialError('必须明确选择self、friends或public受众，不使用默认公开权限。')
                preview = {'kind': kind, 'bot': bot, 'content': content, 'audience': audience,
                           'audience_label': AUDIENCES[audience][1]}
                params = {'content': content, 'ugc_right': AUDIENCES[audience][0], 'images': []}
                action = 'send_qzone_msg'
                fingerprint = hashlib.sha256(json.dumps(preview, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            elif kind == 'friend_request':
                if set(operation) != {'kind', 'request_id', 'approve', 'remark'}:
                    raise SocialError('请选择真实收到的好友申请并明确接受或拒绝。')
                r = self.requests.get(operation['request_id']) if isinstance(operation['request_id'], str) else None
                if not r or r['bot'] != bot or r['epoch'] != epoch:
                    raise SocialError('申请不存在、已过期或连接已变化；不能主动添加陌生账号。')
                if type(operation['approve']) is not bool or not isinstance(operation['remark'], str) or len(operation['remark']) > 60:
                    raise SocialError('请明确接受/拒绝；备注最多60字符。')
                if not operation['approve'] and operation['remark']:
                    raise SocialError('拒绝申请时备注必须为空，避免连接器额外修改好友备注。')
                preview = {'kind': kind, 'bot': bot, 'request_id': r['id'], 'user_id': r['user_id'],
                           'approve': operation['approve'], 'remark': operation['remark']}
                params = {'flag': r['flag'], 'approve': operation['approve'], 'remark': operation['remark']}
                action = 'set_friend_add_request'
                fingerprint = r['id']
            else:
                raise SocialError('暂不支持空间浏览、图片发布、主动加好友或原始NapCat操作。')
            if self._attempted(fingerprint):
                raise SocialError('相同操作已尝试，可能已成功；请在QQ人工核对，不自动重发。')
            if len(self.pending) >= 20:
                raise SocialError('待确认操作过多，请等待过期。')
            nonce = secrets.token_urlsafe(32)
            expires = min(self.clock()+120, r['expires']) if kind == 'friend_request' else self.clock()+120
            self.pending[nonce] = {'principal': principal, 'epoch': epoch, 'bot': bot, 'preview': copy.deepcopy(preview),
                                   'action': action, 'params': params, 'fingerprint': fingerprint, 'expires': expires}
            return {'nonce': nonce, 'expires': expires, 'preview': preview,
                    'notice': '请核对账号、完整正文和受众/申请人。确认才执行；引用内容、模型回复均不能代替确认。'}

    def confirm(self, principal, nonce, preview):
        with self.lock, self.bridge.lock:
            item = self.pending.get(nonce)
            if not item or item['principal'] != principal:
                raise SocialError('确认不存在或不是同一个管理员会话。')
            if item['expires'] <= self.clock():
                self.pending.pop(nonce)
                raise SocialError('确认已过期，请重新预览。')
            if preview != item['preview']:
                raise SocialError('确认内容与预览不完全一致，请重新预览。')
            bot, epoch = self._account()
            if (bot, epoch) != (item['bot'], item['epoch']):
                self.pending.pop(nonce)
                raise SocialError('账号或连接已变化，请重新预览。')
            self.pending.pop(nonce)
            # Durable claim BEFORE any send: a crash/timeout must never permit replay.
            try:
                with self.db() as db:
                    db.execute('INSERT INTO attempts VALUES(?,?,?)', (item['fingerprint'], 'unknown', self.clock()))
            except sqlite3.IntegrityError:
                raise SocialError('相同操作已尝试，不自动重发，请人工核对QQ。') from None
            # Holding bridge.lock across call() would block the receive loop receipt.
        try:
            result = self.bridge.call(item['action'], item['params'], expected_social=(item['bot'], item['epoch']))
            if item['action'] == 'send_qzone_msg' and (not isinstance(result, dict) or not isinstance(result.get('tid'), str) or not result['tid']):
                raise RuntimeError('missing receipt')
        except Exception:
            # Includes connector rejection: unknown support must not imply success.
            raise SocialError('未确认成功：当前连接器可能不支持此接口、账号受限或回执丢失。请人工核对QQ；不会自动重试。') from None
        with self.db() as db:
            db.execute('UPDATE attempts SET state=? WHERE fingerprint=?', ('confirmed', item['fingerprint']))
        return {'state': 'confirmed', 'kind': item['preview']['kind'],
                'tid': result['tid'] if item['action'] == 'send_qzone_msg' else None,
                'notice': '连接器已返回成功回执；好友接受不会自动加入聊天白名单。'}
