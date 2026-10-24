"""Hardware recommendations and an honest inventory of optional capabilities.

Cloud model permissions are independent of hardware and cannot be expanded by a
performance preset. Missing models/accounts are shown as missing, never ready.
"""
from __future__ import annotations
import csv
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
import psutil

from self_update import read, atomic

CATALOG = [
    {'id': 'chat', 'name': 'DeepSeek 文字聊天', 'kind': 'core', 'page': '/qq', 'cost': '使用自己的 DeepSeek API 余额'},
    {'id': 'persona', 'name': '人格、上下文与长期记忆', 'kind': 'core', 'page': '/qq/persona', 'cost': '本机 SQLite；整理时可能调用自己的 API'},
    {'id': 'contacts', 'name': '联系人、管理员与 Plus 权限', 'kind': 'core', 'page': '/qq/contacts', 'cost': '本机；QQ 号为唯一身份键'},
    {'id': 'stickers', 'name': 'QQ 默认表情与本地表情包', 'kind': 'assets', 'page': '/stickers', 'cost': '图片素材自行导入，来源和许可分别登记'},
    {'id': 'images', 'name': '图片与表情的上下文理解', 'kind': 'cloud', 'page': '/qq', 'cost': '使用可识图的云端模型；本机先压缩'},
    {'id': 'videos', 'name': '视频抽帧理解', 'kind': 'cloud', 'page': '/qq', 'cost': 'CPU 有界抽帧 + 云端识图；不是完整逐帧观看'},
    {'id': 'group_files', 'name': '群文件与大照片读取', 'kind': 'core', 'page': '/qq/groups', 'cost': '仅限显式授权的群；保留原图，处理工作副本'},
    {'id': 'voice_input', 'name': 'QQ 语音输入', 'kind': 'local', 'page': '/qq', 'cost': 'SenseVoice int8，本地 CPU，按需调用'},
    {'id': 'voice_output', 'name': '自然语音、原文朗读与换声', 'kind': 'local', 'page': 'http://127.0.0.1:18765/', 'cost': '本地模型优先；云语音需服务自己的凭据'},
    {'id': 'singing', 'name': '清唱、谱面节奏与稳音', 'kind': 'local', 'page': '/qq/creative', 'cost': '需要许可允许的歌声模型、谱面和参考素材'},
    {'id': 'drawing', 'name': 'Codex 画图', 'kind': 'codex', 'page': '/qq/creative', 'cost': '自己的 Codex 账户；底层绘图引擎以实际回执为准'},
    {'id': 'photo_edit', 'name': '主修图、低成本复核与局部返修', 'kind': 'codex', 'page': '/qq/creative/workflow', 'cost': '角色决定模型上限；有界重试，检查实际成品'},
    {'id': 'web', 'name': '公开网页查询与资料整理', 'kind': 'codex', 'page': '/qq/creative', 'cost': '普通用户 Luna；15 分钟 2 次，每次最多 5 分钟'},
    {'id': 'workbench', 'name': '受控 Codex 工作对话', 'kind': 'codex', 'page': '/qq/creative', 'cost': '仅管理员；绑定自己的项目和专用工作目录'},
    {'id': 'proactive', 'name': '主动聊天与当天热点', 'kind': 'optional', 'page': '/qq/proactive', 'cost': '主人单独开启；频率、时段与未回复保护继续生效'},
    {'id': 'learning', 'name': '本地辅助学习与记忆候选', 'kind': 'local', 'page': '/learning', 'cost': '可选小模型，低优先级，有资源才运行'},
    {'id': 'mobile_reader', 'name': '独立模拟器与小黑盒阅读', 'kind': 'optional', 'page': '/mobile', 'cost': '自己的独立模拟器和登录；不接入日常手机信息'},
    {'id': 'balance', 'name': 'API 余额提醒与用量日志', 'kind': 'core', 'page': '/qq/creative', 'cost': '官方余额、实际回执与本机日志；不推算未知费用'},
    {'id': 'updates', 'name': '自动新版提醒与点击升级', 'kind': 'core', 'page': '/updates', 'cost': '校验、备份、等任务结束、失败回退'},
    {'id': 'bilibili', 'name': 'B站浏览、分享与单独授权的互动', 'kind': 'optional', 'page': '/qq/bilibili', 'cost': '仅管理员私聊；自己的B站登录；评论、投币另行确认'},
    {'id': 'astrbot', 'name': 'AstrBot 可选混合对话内核', 'kind': 'optional', 'page': '/qq/engine', 'cost': '独立环境，沿用自己的 DeepSeek API；默认仍是原有对话，需另行切换'},
]


def hardware_snapshot():
    memory = psutil.virtual_memory()
    cpu = ''
    if os.name == 'nt':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'HARDWARE\DESCRIPTION\System\CentralProcessor\0') as key:
                cpu = winreg.QueryValueEx(key, 'ProcessorNameString')[0].strip()
        except OSError:
            pass
    result = {'cpu_name': cpu or '未识别的 CPU', 'physical_cores': psutil.cpu_count(logical=False) or 1,
              'logical_cores': psutil.cpu_count() or 1, 'ram_total_mib': memory.total // 1024 ** 2,
              'ram_available_mib': memory.available // 1024 ** 2, 'gpus': [], 'at': time.time()}
    binary = shutil.which('nvidia-smi')
    if binary:
        try:
            response = subprocess.run([binary, '--query-gpu=index,name,memory.total,memory.free,utilization.gpu',
                                       '--format=csv,noheader,nounits'], capture_output=True, text=True,
                                      timeout=4, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if response.returncode == 0:
                for row in csv.reader(response.stdout.splitlines()):
                    index, name, total, free, utilization = [s.strip() for s in row]
                    result['gpus'].append({'index': int(index), 'name': name, 'total_mib': int(total),
                                           'free_mib': int(free), 'utilization_percent': int(utilization)})
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    return result


def recommendation(hardware, mode='balanced'):
    if mode not in {'quiet', 'balanced', 'quality'}:
        raise ValueError('请选择日常优先、均衡或质量优先。')
    ram = hardware['ram_total_mib']
    gpu = sorted(hardware.get('gpus') or [{'index': 0, 'total_mib': 0, 'free_mib': 0}], key=lambda g: g.get('index',0))[0]
    capable = ram >= 24 * 1024 and gpu['total_mib'] >= 12 * 1024
    voice = {'quiet': 0.25, 'balanced': 0.4, 'quality': 0.5}[mode]
    settings = {'cpu_threads': min(4, max(1, hardware['physical_cores'] // 2)),
                'idle_seconds': 15 if mode == 'quiet' else 30,
                'gpu_memory_fraction': voice,
                'min_free_vram_mib': max(4096, int(gpu['total_mib'] * voice) +
                                          (6144 if gpu['total_mib'] >= 12 * 1024 else 4096)),
                'cache_max_mib': 256 if ram < 16 * 1024 else 512, 'cache_ttl_hours': 24,
                'worker_timeout_seconds': 240, 'max_audio_seconds': 55, 'max_text_chars': 120,
                'singing_gpu_memory_fraction': {'quiet': 0.55, 'balanced': 0.65, 'quality': 0.70}[mode],
                'conversion_gpu_memory_fraction': {'quiet': 0.45, 'balanced': 0.55, 'quality': 0.60}[mode],
                'singing_min_free_vram_mib': 10240}
    learning = {'mode': 'local' if capable else 'paused', 'min_free_vram_mib': 9216,
                'min_free_ram_mib': 8192, 'max_gpu_util_percent': 25, 'max_cpu_percent': 50,
                'reserve_vram_mib': 6144, 'reserve_ram_mib': 6144,
                'max_seconds': 35, 'threads': 2, 'context': 4096}
    selected = ['chat', 'persona', 'contacts', 'stickers', 'images', 'videos', 'group_files',
                'voice_input', 'balance', 'updates']
    if capable:
        selected += ['voice_output', 'singing', 'drawing', 'photo_edit', 'web', 'workbench']
    notes = ['聊天与识图优先使用云端；显卡不会因为打开设置页而加载模型。',
             '语音输入推荐 CPU int8；本地合成按需加载，空闲后退出工作进程。',
             '模型上限、普通速度、管理员/Plus/普通用户权限不随性能预设改变。']
    if capable:
        notes.append('这台电脑适合按需使用本地声音模型；后台辅助限 2 线程并预留至少 6 GiB 显存。')
    else:
        notes.append('先使用基础聊天和 CPU 语音输入；本地歌声/换声不默认启用，避免影响日常操作。')
    if gpu.get('free_mib', 0) < 9216:
        notes.append('当前可用显存不足后台辅助门槛，本地学习即使已配置也应暂缓。')
    return {'mode': mode, 'settings': settings, 'learning': learning, 'selected': selected,
            'local_gpu_suitable': capable, 'notes': notes,
            'scope': '资源预算是本项目的保守默认值，不是所有第三方模型的硬性显存隔离。'}


class FeatureProfiles:
    def __init__(self, root, *, probe=hardware_snapshot):
        self.root = Path(root)
        self.path = self.root / 'configs/feature-profile.local.json'
        self.probe = probe
        self.lock = threading.Lock()
        self.snapshot = None

    def hardware(self):
        if self.snapshot is None or time.time() - self.snapshot['at'] > 60:
            self.snapshot = self.probe()
        return self.snapshot

    def file(self, value):
        if not isinstance(value, str) or not value or value.startswith(('\\\\', '//')):
            return False
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = self.root / path
        return path.is_file()

    def exists(self, value):
        if not isinstance(value, str) or not value or value.startswith(('\\\\', '//')):
            return False
        path = Path(value).expanduser()
        return (path if path.is_absolute() else self.root / path).exists()

    def model_file(self, folder, name):
        if not isinstance(folder, str) or not folder:
            return False
        return self.file(str(Path(folder) / name))

    def readiness(self):
        agent = read(self.root / 'agent.local.json', {})
        key = bool(agent.get('api_key'))
        voice = read(self.root / 'configs/qq-voice.local.json', {})
        asr = (self.file(voice.get('asr_python')) and self.model_file(voice.get('asr_model'), 'model.int8.onnx')
               and self.model_file(voice.get('asr_model'), 'tokens.txt'))
        codex = bool(shutil.which('codex') or shutil.which('codex.cmd'))
        binding = read(self.root / 'configs/qq-workbench.local.json', {})
        codex_bound = codex and bool(binding.get('local_project_id') or binding.get('source_project_id'))
        speech_cfg = read(self.root / 'dania.local.json', {})
        settings = read(self.root / 'settings.local.json', {})
        speech = (self.file(settings.get('dania_python'))
                  and self.model_file(speech_cfg.get('dania_model_dir'), 'dania-e15.ckpt')
                  and self.model_file(speech_cfg.get('dania_model_dir'), 'dania_e16_s2192.pth'))
        singing_cfg = read(self.root / 'score.local.json', {})
        singing = bool(singing_cfg.get('enabled')) and all(self.exists(singing_cfg.get(key)) for key in
            ['python', 'source_root', 'voice_bank', 'asr_python', 'asr_model', 'phone_map'])
        helper = read(self.root / 'configs/local-learning.local.json', {})
        local_helper = self.file(helper.get('model')) and self.file(helper.get('runtime'))
        model = str(agent.get('model', ''))
        results = {}
        for item in CATALOG:
            kind = item['kind']
            ready = kind == 'core' or item['id'] in {'proactive', 'stickers'}
            reason = '内置功能；首次账号配置完成后可用。' if ready else '需要配置对应依赖。'
            if item['id'] == 'chat':
                ready, reason = key, '已保存本机 Key。' if key else '等待填写自己的 DeepSeek Key。'
            elif kind == 'cloud':
                ready = key and 'flash' in model
                reason = '已选择支持当前视觉链路的模型。' if ready else '需要确认 API 账号的识图模型；未就绪时不声称已看过图片。'
            elif kind == 'codex':
                ready = codex_bound
                reason = '已发现 Codex 与项目配置，仍以真实调用回执为准。' if ready else '需要安装并登录自己的 Codex，再绑定本地项目。'
            elif item['id'] == 'voice_input':
                ready, reason = asr, 'CPU int8 识别文件已找到。' if asr else '需要自己的 SenseVoice int8 模型、tokens 和识别环境。'
            elif item['id'] == 'voice_output':
                ready, reason = speech, '发现本地音色配置，仍需试听验收。' if speech else '需要导入有权使用的音色、参考音频和对应环境。'
            elif item['id'] == 'singing':
                ready, reason = singing, '发现歌声环境配置，仍需谱面和试听验收。' if singing else '需要歌声模型、运行环境和许可允许的谱面素材。'
            elif item['id'] == 'learning':
                ready, reason = local_helper, '已发现辅助模型路径。' if local_helper else '可选辅助模型尚未配置；不影响云端记忆与聊天。'
            elif item['id'] == 'mobile_reader':
                ready, reason = False, '需另行配置专用模拟器并本人登录；不会读取日常手机。'
            elif item['id'] == 'bilibili':
                ready, reason = (self.root/'bili_reader/patrol.py').is_file(), '模块已安装后仍需本人登录；浏览、主动分享和公开互动分别设置。'
            elif item['id'] == 'astrbot':
                from optional_engine_setup import environment_state
                ready = environment_state(self.root)['installed']
                reason = '独立依赖环境已找到；对话设置中的真实健康检查通过后才可切换。' if ready else '可在安装向导中选装独立环境；基础聊天不受影响。'
            elif item['id'] == 'stickers':
                reason = 'QQ 默认表情可用；本地图片包需要按许可自行导入。'
            results[item['id']] = {'ready': ready, 'detail': reason}
        return results

    def state(self):
        hardware = self.hardware()
        saved = read(self.path, {})
        recommended = recommendation(hardware)
        return {'hardware': hardware, 'recommended': recommended, 'saved': saved,
                'profiles': [{'id': 'quiet', 'name': '日常使用优先'}, {'id': 'balanced', 'name': '均衡（推荐）'},
                             {'id': 'quality', 'name': '质量优先'}],
                'catalog': CATALOG, 'readiness': self.readiness(),
                'role_policy': {'user': 'Luna；最多 3 个工作对话；查询 15 分钟 2 次、单次 5 分钟',
                                'plus': '一般工作 Luna；修图可用实际可用的 5.6 Terra 及以下；查询免这两项限制',
                                'admin': '管理权限；修图最高 6.1 Sol／极高，其他任务按已设上限'},
                'service_tier': 'default', 'activation': 'selected 表示准备配置的功能；缺少依赖的项目不会自动成为已可用。'}

    def save(self, mode, selected, overrides=None):
        with self.lock:
            profile = recommendation(self.hardware(), mode)
            known = {item['id'] for item in CATALOG}
            if not isinstance(selected, list) or not all(isinstance(s, str) and s in known for s in selected):
                raise ValueError('功能选择中含未知项目。')
            overrides = overrides or {}
            maximum_vram = max([16384] + [g['total_mib'] for g in self.hardware().get('gpus', [])])
            ranges = {'cpu_threads': (1, max(1, min(8, self.hardware()['physical_cores']))),
                      'idle_seconds': (10, 120), 'gpu_memory_fraction': (0.1, 0.6),
                      'min_free_vram_mib': (4096, maximum_vram), 'cache_max_mib': (128, 2048),
                      'cache_ttl_hours': (1, 168),
                      'singing_gpu_memory_fraction': (0.4, 0.75),
                      'conversion_gpu_memory_fraction': (0.3, 0.65),
                      'singing_min_free_vram_mib': (6144, maximum_vram)}
            for name, value in overrides.items():
                if name not in ranges or isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError('只能调整页面列出的资源参数。')
                low, high = ranges[name]
                if not low <= value <= high:
                    raise ValueError('资源参数超出允许范围。')
                if not name.endswith('memory_fraction') and int(value) != value:
                    raise ValueError('线程、时间和缓存请输入整数。')
                profile['settings'][name] = value
            profile['settings']['conversion_gpu_memory_fraction'] = min(
                profile['settings']['conversion_gpu_memory_fraction'], profile['settings']['singing_gpu_memory_fraction'])
            from scripts.beginner_setup import write_json
            settings = read(self.root / 'settings.local.json')
            if not settings:
                raise ValueError('请先运行安装器准备本机配置。')
            settings.update(profile['settings'])
            write_json(self.root / 'settings.local.json', settings)
            dania_path = self.root / 'dania.local.json'
            if dania_path.exists():
                dania = read(dania_path)
                for key in ['cpu_threads', 'gpu_memory_fraction', 'min_free_vram_mib']:
                    dania[key] = profile['settings'][key]
                write_json(dania_path, dania)
            score_path = self.root / 'score.local.json'
            if score_path.exists():
                score = read(score_path)
                shared = {'cpu_threads': profile['settings']['cpu_threads'],
                          'min_free_vram_mib': profile['settings']['singing_min_free_vram_mib']}
                score.update(shared)
                score['gpu_memory_fraction'] = profile['settings']['singing_gpu_memory_fraction']
                if isinstance(score.get('soulx'), dict):
                    score['soulx'].update(shared, gpu_memory_fraction=profile['settings']['singing_gpu_memory_fraction'])
                if isinstance(score.get('dania_conversion'), dict):
                    score['dania_conversion'].update(shared, gpu_memory_fraction=profile['settings']['conversion_gpu_memory_fraction'])
                write_json(score_path, score)
            for filename in ['music.local.json', 'singing.local.json']:
                file = self.root / filename
                if file.exists():
                    config = read(file)
                    config.update(cpu_threads=profile['settings']['cpu_threads'],
                                  min_free_vram_mib=profile['settings']['singing_min_free_vram_mib'],
                                  gpu_memory_fraction=profile['settings']['conversion_gpu_memory_fraction']
                                    if filename=='singing.local.json' else profile['settings']['singing_gpu_memory_fraction'])
                    write_json(file, config)
            learning = {**read(self.root / 'configs/local-learning.local.json', {}), **profile['learning']}
            if 'learning' not in selected:
                learning['mode'] = 'paused'
            write_json(self.root / 'configs/local-learning.local.json', learning)
            config = {'version': 1, 'mode': mode, 'selected': sorted(set(selected)),
                      'overrides': overrides, 'settings': profile['settings'], 'at': time.time(),
                      'dependencies_installed': False}
            write_json(self.path, config)
            return {'saved': True, 'profile': config, 'readiness': self.readiness(),
                    'message': '资源预算已保存。依赖缺失的功能仍待配置；运行中的声音服务会在下次启动时读取新参数。'}
