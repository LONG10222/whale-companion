"""Bound preview concurrency, cumulative decode work, wall time and Windows RAM."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

SLOTS = threading.BoundedSemaphore(2)


class MediaJob:
    def __init__(self, process):
        self.handle = None
        if os.name != 'nt':
            return
        import ctypes
        from ctypes import wintypes as w
        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ['ReadOperationCount','WriteOperationCount','OtherOperationCount','ReadTransferCount','WriteTransferCount','OtherTransferCount']]
        class Basic(ctypes.Structure):
            _fields_ = [('process_time',ctypes.c_int64),('job_time',ctypes.c_int64),('flags',w.DWORD),
                        ('min_ws',ctypes.c_size_t),('max_ws',ctypes.c_size_t),('active',w.DWORD),
                        ('affinity',ctypes.c_size_t),('priority',w.DWORD),('scheduling',w.DWORD)]
        class Extended(ctypes.Structure):
            _fields_ = [('basic',Basic),('io',IO),('process_memory',ctypes.c_size_t),
                        ('job_memory',ctypes.c_size_t),('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [w.LPVOID,w.LPCWSTR]
        kernel.CreateJobObjectW.restype = w.HANDLE
        kernel.SetInformationJobObject.argtypes = [w.HANDLE,ctypes.c_int,w.LPVOID,w.DWORD]
        kernel.AssignProcessToJobObject.argtypes = [w.HANDLE,w.HANDLE]
        kernel.CloseHandle.argtypes = [w.HANDLE]
        self.kernel = kernel
        self.handle = kernel.CreateJobObjectW(None, None)
        limits = Extended()
        limits.basic.flags = 0x2000 | 0x100 | 0x2  # kill on close, memory, process CPU time
        limits.basic.process_time = 5 * 10_000_000
        limits.process_memory = 512 * 1024 ** 2
        if (not self.handle or not kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits))
                or not kernel.AssignProcessToJobObject(self.handle, int(process._handle))):
            self.close()
            raise ValueError('暂时无法建立图片处理保护，请稍后再试。')

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def preview(data, root):
    if not SLOTS.acquire(timeout=1):
        raise ValueError('图片处理正在排队，稍后再看这张。')
    process, job = None, None
    try:
        root = Path(root)
        settings = root / 'settings.local.json'
        config = json.loads(settings.read_text(encoding='utf-8')) if settings.exists() else {}
        python = Path(config.get('python') or sys.executable)
        if not python.is_absolute():
            python = root / python
        worker = Path(__file__).resolve().with_name('animation_preview_worker.py')
        process = subprocess.Popen([str(python), '-X', 'utf8', '-I', str(worker)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) | getattr(subprocess, 'BELOW_NORMAL_PRIORITY_CLASS', 0))
        job = MediaJob(process)
        output, _ = process.communicate(data, timeout=4)
        if process.returncode or len(output) > 3 * 1024 ** 2:
            raise ValueError('这张动图没有在处理预算内完成，可以换静态图再发。')
        result = json.loads(output)
        if not isinstance(result.get('jpeg'), str) or result.get('sampled_frames', 0) > 3:
            raise ValueError('动图预览未通过检查。')
        return result
    except subprocess.TimeoutExpired:
        raise ValueError('这张动图处理时间较长，先停下来；发截图也可以。') from None
    finally:
        if job:
            job.close()
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        SLOTS.release()
