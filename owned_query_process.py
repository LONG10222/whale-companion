"""Owned research worker tree. Closing a Windows Job kills its Codex child too."""
import os,subprocess

class QueryProcess:
    def __init__(self,args,cwd,log):
        self.proc=None;self.job=None
        if os.name=='nt':
            import ctypes
            from ctypes import wintypes as w
            class IO(ctypes.Structure):_fields_=[(n,ctypes.c_uint64) for n in ['ReadOperationCount','WriteOperationCount','OtherOperationCount','ReadTransferCount','WriteTransferCount','OtherTransferCount']]
            class BASIC(ctypes.Structure):_fields_=[('PerProcessUserTimeLimit',ctypes.c_int64),('PerJobUserTimeLimit',ctypes.c_int64),('LimitFlags',w.DWORD),('MinimumWorkingSetSize',ctypes.c_size_t),('MaximumWorkingSetSize',ctypes.c_size_t),('ActiveProcessLimit',w.DWORD),('Affinity',ctypes.c_size_t),('PriorityClass',w.DWORD),('SchedulingClass',w.DWORD)]
            class EXTENDED(ctypes.Structure):_fields_=[('BasicLimitInformation',BASIC),('IoInfo',IO),('ProcessMemoryLimit',ctypes.c_size_t),('JobMemoryLimit',ctypes.c_size_t),('PeakProcessMemoryUsed',ctypes.c_size_t),('PeakJobMemoryUsed',ctypes.c_size_t)]
            kernel=ctypes.WinDLL('kernel32',use_last_error=True);self.kernel=kernel
            kernel.CreateJobObjectW.argtypes=[ctypes.c_void_p,w.LPCWSTR];kernel.CreateJobObjectW.restype=w.HANDLE
            kernel.SetInformationJobObject.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD];kernel.SetInformationJobObject.restype=w.BOOL
            kernel.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE];kernel.AssignProcessToJobObject.restype=w.BOOL
            kernel.CloseHandle.argtypes=[w.HANDLE];kernel.CloseHandle.restype=w.BOOL
            self.job=kernel.CreateJobObjectW(None,None);limits=EXTENDED();limits.BasicLimitInformation.LimitFlags=0x2000
            if not self.job or not kernel.SetInformationJobObject(self.job,9,ctypes.byref(limits),ctypes.sizeof(limits)):
                if self.job:kernel.CloseHandle(self.job);self.job=None
                raise RuntimeError('无法建立查询进程的退出保护，未启动查询。')
        try:
            self.proc=subprocess.Popen(args,cwd=cwd,stdout=log,stderr=log,stdin=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)|getattr(subprocess,'BELOW_NORMAL_PRIORITY_CLASS',0))
            if self.job and not self.kernel.AssignProcessToJobObject(self.job,int(self.proc._handle)):
                self.proc.terminate();self.proc.wait(3);raise RuntimeError('查询进程无法纳入退出保护，已停止。')
        except Exception:self.close();raise
    def poll(self):return self.proc.poll()
    def close(self):
        if self.job:self.kernel.CloseHandle(self.job);self.job=None
        if self.proc:
            if self.proc.poll() is None:
                if os.name!='nt':
                    import psutil
                    try:
                        for p in psutil.Process(self.proc.pid).children(recursive=True):p.terminate()
                    except psutil.Error:pass
                self.proc.terminate()
            try:self.proc.wait(3)
            except subprocess.TimeoutExpired:self.proc.kill();self.proc.wait(3)
