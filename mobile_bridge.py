"""Read-and-draft Android tools pinned to one independently created MuMu device.

The model cannot submit shell commands, arbitrary coordinates, app packages,
keyboard text outside search fields, or publishing actions.
"""
from pathlib import Path
import base64, hashlib, io, json, re, subprocess, threading, time, uuid
import xml.etree.ElementTree as ET
from PIL import Image

ROOT = Path(__file__).resolve().parent
PACKAGE = 'com.max.xiaoheihe'
IME = 'com.android.adbkeyboard/.AdbIME'
HIDDEN = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


class MobileError(RuntimeError):
    pass


def bounds(value):
    match = re.fullmatch(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', value or '')
    return tuple(map(int, match.groups())) if match else (0, 0, 0, 0)


def parse_screen(xml):
    root = ET.fromstring(xml)
    parents = {child: parent for parent in root.iter() for child in parent}
    nodes = []
    for element in root.iter('node'):
        a = element.attrib
        if a.get('package') != PACKAGE:
            continue
        if a.get('password') == 'true':
            continue
        text = (a.get('text') or a.get('content-desc') or '').strip()
        rid = a.get('resource-id', '').split('/')[-1]
        rect = bounds(a.get('bounds'))
        if rect[2] <= rect[0] or rect[3] <= rect[1]:
            continue
        # Strict, semantic whitelist. Unknown buttons are observation-only.
        role = 'text'
        if a.get('class') == 'android.widget.EditText' and re.search(r'search|keyword', rid, re.I):
            role = 'search_field'
        elif (rid == 'iv_home_search' or re.fullmatch(r'(?:ib_|iv_|btn_|tv_|vg_)?(?:search|search_button|search_bar|search_layout|search_container|search_input)', rid)
              or text in ('搜索', '搜索游戏、帖子、用户')) and a.get('class') != 'android.widget.EditText':
            role = 'search'
        elif text in ('帖子', '综合', '最新', '相关', '热度') and a.get('clickable') == 'true':
            role = 'navigation'
        in_post_card = False
        parent = parents.get(element)
        for _ in range(8):
            if parent is None:
                break
            if parent.get('resource-id', '').split('/')[-1] in ('epoxy_model_group_child_container', 'll_container'):
                in_post_card = any(n.get('resource-id', '').endswith('/tv_content') for n in parent.iter('node'))
                break
            parent = parents.get(parent)
        # Only title views whose exact resource IDs have been verified locally.
        nodes.append({'id': str(len(nodes)), 'text': text[:1800], 'resource_id': rid,
                      'class': a.get('class'), 'role': role, 'bounds': rect,
                      'post_card': in_post_card,
                      'focused': a.get('focused') == 'true',
                      'enabled': a.get('enabled') == 'true'})
    texts = [n['text'] for n in nodes if n['text']]
    login = any(n['resource_id'] in ('et_number', 'tv_toggle_login', 'et_password') for n in nodes)
    login = login or any(t in ('验证码登录', '密码登录', '输入您的手机号') for t in texts)
    terms = any(t == '用户协议与隐私政策' for t in texts)
    return nodes, texts, 'login_required' if login else 'onboarding_required' if terms else None


class MobileBridge:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.config_path = self.root / 'mobile.local.json'
        self.cache = self.root / 'cache/mobile'
        self.cache.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.last = None
        self.last_used = time.monotonic()
        self.launched_here = False
        self.evidence = []
        self.pending_post = None
        self._ui_device = None
        self._ui_serial = None

    def config(self):
        return json.loads(self.config_path.read_text(encoding='utf-8'))

    def _run(self, args, timeout=20, binary=False):
        try:
            r = subprocess.run(list(map(str, args)), capture_output=True, timeout=timeout,
                               creationflags=HIDDEN)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise MobileError('手机连接命令失败或超时，请查看模拟器状态。') from exc
        # MuMu Android 15's UIAutomator can exit 139 during teardown after a
        # successful dump. Accept only its explicit success marker for this
        # exact owned file; the XML is still parsed before use.
        dumped = (list(map(str, args))[-4:] == ['shell', 'uiautomator', 'dump', '/sdcard/companion-view.xml']
                  and r.returncode == 139 and b'UI hierchary dumped to: /sdcard/companion-view.xml' in r.stdout)
        if r.returncode and not dumped:
            raise MobileError('手机连接命令未完成：' + (r.stderr or r.stdout).decode('utf-8', 'replace')[-300:])
        return r.stdout if binary else r.stdout.decode('utf-8', 'replace')

    def info(self):
        c = self.config()
        raw = self._run([c['mumu_cli'], 'info', '-v', str(c['instance'])])
        info = json.loads(raw[raw.index('{'):])
        if str(info.get('index')) != str(c['instance']) or str(info.get('created_timestamp')) != str(c['created_timestamp']):
            raise MobileError('测试手机身份发生变化，已停止连接，避免操作其他实例。')
        if info.get('name') != c['name']:
            raise MobileError('测试手机名称不匹配，已停止连接。')
        return info

    def _serial(self):
        info = self.info()
        if not info.get('is_android_started'):
            raise MobileError('测试手机尚未启动，请先点击“启动测试手机”。')
        if info.get('adb_host_ip') != '127.0.0.1':
            raise MobileError('只允许连接这台电脑上的测试手机。')
        port = int(info.get('adb_port', 0))
        if not 1024 <= port <= 65535:
            raise MobileError('测试手机端口无效。')
        return f'127.0.0.1:{port}'

    def _adb(self, *args, binary=False, timeout=20):
        c = self.config()
        serial = self._serial()
        self._run([c['adb'], 'connect', serial])
        self.last_used = time.monotonic()
        return self._run([c['adb'], '-s', serial, *args], timeout, binary)

    def foreground(self):
        raw = self._adb('shell', 'dumpsys', 'activity', 'activities')
        match = re.search(r'(?:mResumedActivity|topResumedActivity|ResumedActivity)\s*[:=][^\n]*?\s([\w.]+)/', raw)
        return match.group(1) if match else ''

    def hierarchy(self):
        # The stock `uiautomator dump` blocks on animated feeds in MuMu.
        # UIAutomator2 reads the hierarchy without waiting for animations to end.
        import uiautomator2 as u2
        serial = self._serial()
        if self._ui_device is None or self._ui_serial != serial:
            self._ui_device = u2.connect(serial)
            self._ui_serial = serial
            self._ui_device.jsonrpc.setConfigurator({'waitForIdleTimeout': 0, 'waitForSelectorTimeout': 0})
        return self._ui_device.dump_hierarchy(compressed=False, max_depth=80)

    def status(self):
        try:
            i = self.info()
            return {'ok': True, 'name': i['name'], 'running': bool(i.get('is_android_started')),
                    'instance': int(i['index']), 'mode': 'read_and_local_draft',
                    'idle_shutdown_seconds': self.config().get('idle_seconds', 300),
                    'auto_shutdown_owned': self.launched_here,
                    'last_screen': self.public_screen(self.last) if self.last else None}
        except (MobileError, OSError, ValueError) as exc:
            return {'ok': False, 'running': False, 'error': str(exc)}

    def start(self, cancelled=lambda: False):
        with self.lock:
            i = self.info()
            if not i.get('is_process_started'):
                c = self.config()
                raw = self._run([c['mumu_cli'], 'control', '-v', str(c['instance']), 'launch'], timeout=35)
                if json.loads(raw).get('errcode') != 0:
                    raise MobileError('模拟器未能启动。')
                self.launched_here = True
            self._ui_device = None
            self._ui_serial = None
            return self.wait_ready(cancelled)

    def wait_ready(self, cancelled=lambda: False):
        """Manager flags precede ADB readiness. Reconnect only the pinned local instance."""
        with self.lock:
            deadline = time.monotonic() + 55
            while time.monotonic() < deadline:
                if cancelled():
                    raise MobileError('任务已停止。')
                if self.info().get('is_android_started'):
                    serial = self._serial()  # Includes instance identity and loopback checks.
                    c = self.config()
                    try:
                        self._run([c['adb'], 'connect', serial], timeout=5)
                        state = self._run([c['adb'], '-s', serial, 'get-state'], timeout=5).strip()
                        booted = self._run([c['adb'], '-s', serial, 'shell', 'getprop', 'sys.boot_completed'], timeout=5).strip()
                        if state == 'device' and booted == '1':
                            self.last_used = time.monotonic()
                            return {'ok': True, 'running': True, 'adb_ready': True}
                    except MobileError:
                        self._ui_device = None
                        self._ui_serial = None
                        try:self._run([c['adb'], 'disconnect', serial], timeout=5)
                        except MobileError:pass
                time.sleep(1)
            raise MobileError('测试手机连接尚未就绪，本轮已暂停，请稍后重试。')

    def stop(self):
        with self.lock:
            i = self.info()
            if i.get('is_process_started'):
                c = self.config()
                raw = self._run([c['mumu_cli'], 'control', '-v', str(c['instance']), 'shutdown'], timeout=30)
                if json.loads(raw).get('errcode') != 0:
                    raise MobileError('手机尚未关闭，请在 MuMu 窗口检查。')
                deadline = time.monotonic() + 25
                while self.info().get('is_process_started'):
                    if time.monotonic() >= deadline:
                        raise MobileError('测试手机仍在退出，暂不开始下一轮。')
                    time.sleep(.5)
            self.last = None
            self.pending_post = None
            self.launched_here = False
            self._ui_device = None
            self._ui_serial = None
            return {'ok': True, 'running': False}

    def idle_check(self, busy=False):
        if not busy and self.launched_here and time.monotonic() - self.last_used > self.config().get('idle_seconds', 300):
            self.stop()

    @staticmethod
    def public_screen(screen):
        if not screen:
            return None
        return {k: v for k, v in screen.items() if k != 'xml_hash'}

    def observe(self):
        with self.lock:
            package = self.foreground()
            if package != PACKAGE:
                self.last = None
                return {'ok': False, 'state': 'other_app', 'package': package,
                        'message': '前台不是小黑盒；只允许读取测试手机上的小黑盒。'}
            xml = self.hierarchy()
            nodes, texts, blocked = parse_screen(xml)
            if self.foreground() != PACKAGE:
                self.last = None
                raise MobileError('读取期间前台应用变化，请重新读取。')
            # Never capture or forward login, OTP, password or onboarding screens.
            if blocked:
                self.last = None
                return {'ok': False, 'state': blocked,
                        'message': '请在新测试手机里手动完成登录，再重新读取。' if blocked == 'login_required' else '请在手机中完成首次进入。'}
            allowed = set(self.config().get('verified_post_title_ids', []))
            for n in nodes:
                if (n['resource_id'] in allowed and n['class'] == 'android.widget.TextView' and len(n['text']) >= 2
                        and (n['resource_id'] != 'tv_title' or n['post_card'])):
                    n['role'] = 'post'
            shot = self._adb('exec-out', 'screencap', '-p', binary=True)
            image = Image.open(io.BytesIO(shot)).convert('RGB')
            image.thumbnail((720, 1280))
            sid = uuid.uuid4().hex
            image.save(self.cache / (sid + '.jpg'), quality=78)
            screen = {'ok': True, 'snapshot_id': sid, 'state': 'ready', 'package': PACKAGE,
                      'captured_at': time.time(), 'nodes': [n for n in nodes if n['text'] or n['role'] != 'text'],
                      'text': '\n'.join(dict.fromkeys(texts))[:18000],
                      'image_url': '/mobile/screens/' + sid,
                      'xml_hash': hashlib.sha256(xml.encode()).hexdigest()}
            self.last = screen
            self.prune_cache()
            detail = any(n['resource_id'] == 'et_editor' for n in nodes)
            if self.pending_post and detail and screen['text']:
                self.evidence.append({'snapshot_id': sid, 'title': self.pending_post,
                                      'text': screen['text'][:12000], 'captured_at': screen['captured_at'],
                                      'source_app': PACKAGE, 'image_sha256': hashlib.sha256((self.cache/(sid+'.jpg')).read_bytes()).hexdigest(),
                                      'reading_mode': 'visible_text_and_screenshot'})
            screen['reading_sources'] = [{'snapshot_id': e['snapshot_id'], 'title': e['title']} for e in self.evidence[-12:]]
            screen['page_kind'] = 'post_detail' if detail else 'list_or_search'
            return self.public_screen(screen)

    def prune_cache(self):
        files = sorted(self.cache.glob('*.jpg'), key=lambda p: p.stat().st_mtime, reverse=True)
        total = 0
        for i, p in enumerate(files):
            if not re.fullmatch(r'[a-f0-9]{32}\.jpg', p.name):
                continue
            total += p.stat().st_size
            if i >= 48 or total > 32 * 1024**2 or time.time() - p.stat().st_mtime > 24 * 3600:
                p.unlink(missing_ok=True)

    def open(self):
        with self.lock:
            self._adb('shell', 'am', 'start', '-n', PACKAGE + '/com.max.xiaoheihe.SplashActivity')
            self.pending_post = None
            time.sleep(1)
            return self.observe()

    def _fresh_node(self, sid, node_id):
        old = self.last
        if not old or sid != old['snapshot_id'] or time.time() - old['captured_at'] > 90:
            raise MobileError('页面记录已过期，请先重新读取。')
        node = next((n for n in old['nodes'] if n['id'] == node_id), None)
        if node is None or not node['enabled']:
            raise MobileError('该页面元素不存在或不可用。')
        fresh = self.observe()
        signature = lambda n: (n['resource_id'], n['text'], n['class'], tuple(n['bounds']), n['role'])
        if not fresh.get('ok'):
            raise MobileError('页面内容已经变化，请根据新页面重新选择。')
        current = next((n for n in self.last['nodes'] if signature(n) == signature(node)), None)
        if current is None:
            raise MobileError('目标元素已经变化，请根据新页面重新选择。')
        return current

    def _tap(self, node):
        if self.foreground() != PACKAGE:
            raise MobileError('前台应用发生变化，已停止点击。')
        current, _, blocked = parse_screen(self.hierarchy())
        signature = lambda n: (n['resource_id'], n['text'], n['class'], tuple(n['bounds']))
        if blocked or not any(n['enabled'] and signature(n) == signature(node) for n in current):
            raise MobileError('目标位置或页面发生变化，已停止点击，请重新读取。')
        x1, y1, x2, y2 = node['bounds']
        self._adb('shell', 'input', 'tap', str((x1+x2)//2), str((y1+y2)//2))
        self.last = None
        time.sleep(.6)

    def navigate(self, snapshot_id, node_id):
        with self.lock:
            node = self._fresh_node(snapshot_id, node_id)
            if node['role'] not in ('search', 'navigation', 'post'):
                raise MobileError('此元素不属于已验证的搜索、筛选或帖子标题；已禁止点击。')
            self.pending_post = node['text'] if node['role'] == 'post' else None
            self._tap(node)
            return self.observe()

    def search(self, query):
        if not re.fullmatch(r'[\u3400-\u9fff\w ，。！？·\-]{1,60}', query):
            raise MobileError('搜索词只接受最多60字的普通中英文。')
        with self.lock:
            s = self.observe()
            if not s.get('ok'):
                return s
            fields = [n for n in s['nodes'] if n['role'] == 'search_field']
            if not fields:
                search = next((n for n in s['nodes'] if n['role'] == 'search'), None)
                if not search:
                    raise MobileError('当前页面未找到已识别的搜索入口，请在手机上打开搜索页后重试。')
                self._tap(search)
                s = self.observe()
                if not s.get('ok'):
                    return s
                fields = [n for n in s['nodes'] if n['role'] == 'search_field']
            if len(fields) != 1:
                raise MobileError('尚未识别唯一的搜索输入框，停止输入。')
            field = fields[0]
            self._tap(field)
            # Search-only IME activation. Restore prior keyboard even on failure.
            previous = self._adb('shell', 'settings', 'get', 'secure', 'default_input_method').strip()
            if not re.fullmatch(r'[\w.$]+/[\w.$]+', previous):
                raise MobileError('未能读取原输入法，已停止输入。')
            try:
                self._adb('shell', 'ime', 'enable', IME)
                self._adb('shell', 'ime', 'set', IME)
                time.sleep(.2)
                focused = self.observe()
                if not focused.get('ok') or not any(n['role'] == 'search_field' and n['focused'] for n in focused['nodes']):
                    raise MobileError('焦点不在搜索框，已停止输入。')
                self._adb('shell', 'am', 'broadcast', '-a', 'ADB_CLEAR_TEXT', '-p', 'com.android.adbkeyboard')
                encoded = base64.b64encode(query.encode('utf-8')).decode('ascii')
                self._adb('shell', 'am', 'broadcast', '-a', 'ADB_INPUT_B64', '-p', 'com.android.adbkeyboard', '--es', 'msg', encoded)
                self._adb('shell', 'am', 'broadcast', '-a', 'ADB_EDITOR_CODE', '-p', 'com.android.adbkeyboard', '--ei', 'code', '3')
            finally:
                self._adb('shell', 'ime', 'set', previous)
            self.last = None
            self.pending_post = None
            time.sleep(1)
            return self.observe()

    def scroll(self, direction='down'):
        with self.lock:
            s = self.observe()
            if not s.get('ok'):
                return s
            # Coordinates are derived from the last actual screenshot, not model input.
            p = self.cache / (s['snapshot_id'] + '.jpg')
            with Image.open(p) as im:
                w, h = im.size
            a, b = (int(h*.72), int(h*.32)) if direction == 'down' else (int(h*.32), int(h*.72))
            self._adb('shell', 'input', 'swipe', str(w//2), str(a), str(w//2), str(b), '400')
            self.last = None
            time.sleep(.6)
            return self.observe()

    def back(self):
        with self.lock:
            s = self.observe()
            if not s.get('ok'):
                return s
            self._adb('shell', 'input', 'keyevent', '4')
            self.last = None
            self.pending_post = None
            return self.observe()

    def image_part(self, sid):
        if not self.last or sid != self.last['snapshot_id']:
            raise MobileError('截图不是本轮页面。')
        p = self.cache / (sid + '.jpg')
        return {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + base64.b64encode(p.read_bytes()).decode(), 'detail': 'high'}}

    def save_draft(self, title, body, source_snapshot_ids):
        if not 1 <= len(title) <= 100 or not 20 <= len(body) <= 5000:
            raise MobileError('草稿标题或正文长度不合适。')
        sources = [dict(e) for e in self.evidence if e['snapshot_id'] in source_snapshot_ids]
        if not source_snapshot_ids or set(source_snapshot_ids) != {e['snapshot_id'] for e in sources}:
            raise MobileError('草稿必须引用本轮实际打开并读取的帖子记录。')
        did = uuid.uuid4().hex
        d = {'draft_id': did, 'title': title, 'body': body, 'sources': sources,
             'created_at': time.time(), 'published': False, 'destination': 'local_only'}
        folder = self.root / 'mobile-drafts'
        folder.mkdir(exist_ok=True)
        for i, source in enumerate(sources):
            picture = self.cache / (source['snapshot_id']+'.jpg')
            if picture.is_file():
                stored = folder / (did+f'-source-{i+1}.jpg')
                stored.write_bytes(picture.read_bytes())
                source['local_screenshot'] = str(stored)
        (folder / (did + '.json')).write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding='utf-8')
        return {'ok': True, 'draft_id': did, 'title': title, 'body': body, 'published': False,
                'source_snapshot_ids': source_snapshot_ids, 'message': '已保存本地草稿，未发送到小黑盒。'}
