"""Run a native compiler and its descendants as one Windows job."""

import _winapi
import asyncio
import ctypes
import msvcrt
import os
import subprocess
import threading
from contextlib import ExitStack
from ctypes import wintypes
from pathlib import Path
from typing import BinaryIO

_kernel = ctypes.WinDLL("kernel32", use_last_error=True)
_HANDLE = wintypes.HANDLE
_kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
_kernel.CreateJobObjectW.restype = _HANDLE
_kernel.AssignProcessToJobObject.argtypes = [_HANDLE, _HANDLE]
_kernel.AssignProcessToJobObject.restype = wintypes.BOOL
_kernel.ResumeThread.argtypes = [_HANDLE]
_kernel.ResumeThread.restype = wintypes.DWORD
_kernel.TerminateJobObject.argtypes = [_HANDLE, wintypes.UINT]
_kernel.TerminateJobObject.restype = wintypes.BOOL
_kernel.SetInformationJobObject.argtypes = [
    _HANDLE,
    ctypes.c_int,
    ctypes.c_void_p,
    wintypes.DWORD,
]
_kernel.SetInformationJobObject.restype = wintypes.BOOL
_kernel.QueryInformationJobObject.argtypes = [
    _HANDLE,
    ctypes.c_int,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.c_void_p,
]
_kernel.QueryInformationJobObject.restype = wintypes.BOOL
# A job's active count may reach zero before its terminating processes have
# released their file handles. Wait on the processes themselves.
_kernel.IsProcessInJob.argtypes = [_HANDLE, _HANDLE, ctypes.POINTER(wintypes.BOOL)]
_kernel.IsProcessInJob.restype = wintypes.BOOL
_CREATE_SUSPENDED = 0x00000004
_JOB_PROCESS_IDS = 3
_JOB_EXTENDED_LIMIT_INFO = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_WAIT_QUERY_ACCESS = 0x00101000  # SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
        ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _member_handles(job: int, handles: ExitStack) -> list[int]:
    """Open waitable handles without trusting a recycled PID.

    Args:
        job: Handle of this compiler stage's job.
        handles: Scope owning the opened handles.

    Returns:
        Handles of processes confirmed to belong to the job.
    """
    capacity = 8
    while True:
        buf = ctypes.create_string_buffer(8 + capacity * ctypes.sizeof(ctypes.c_size_t))
        if _kernel.QueryInformationJobObject(
            job,
            _JOB_PROCESS_IDS,
            buf,
            ctypes.sizeof(buf),
            None,
        ):
            break
        if ctypes.get_last_error() != 234:  # ERROR_MORE_DATA
            raise ctypes.WinError(ctypes.get_last_error())
        capacity *= 2
    count = wintypes.DWORD.from_buffer(buf, 4).value
    members = []
    for pid in (ctypes.c_size_t * count).from_buffer(buf, 8):
        try:
            handle = _winapi.OpenProcess(_WAIT_QUERY_ACCESS, False, pid)
        except OSError as exc:
            # 87: already exited; 5: PID already recycled by a protected process.
            if exc.winerror in (5, 87):
                continue
            raise
        handles.callback(_winapi.CloseHandle, handle)
        belongs = wintypes.BOOL()
        if not _kernel.IsProcessInJob(handle, job, ctypes.byref(belongs)):
            raise ctypes.WinError(ctypes.get_last_error())
        if belongs.value:
            members.append(handle)
    return members


def _run_job(
    command: list[str], work: Path, env: dict, log: BinaryIO, cancelled: threading.Event
) -> int:
    """Own all OS handles on one worker until the stage and its helpers exit."""
    if cancelled.is_set():
        return 1
    with ExitStack() as handles:
        job = _kernel.CreateJobObjectW(None, None)
        if not job:
            raise ctypes.WinError(ctypes.get_last_error())
        handles.callback(_winapi.CloseHandle, job)
        limits = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not _kernel.SetInformationJobObject(
            job,
            _JOB_EXTENDED_LIMIT_INFO,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            raise ctypes.WinError(ctypes.get_last_error())

        # Limit inherited handles to stdin and the shared stdout/stderr log.
        with ExitStack() as streams:
            devnull = streams.enter_context(open(os.devnull, "rb"))
            inherit = []
            current = _winapi.GetCurrentProcess()
            for file in (devnull, log):
                handle = _winapi.DuplicateHandle(
                    current,
                    msvcrt.get_osfhandle(file.fileno()),
                    current,
                    0,
                    True,
                    _winapi.DUPLICATE_SAME_ACCESS,
                )
                streams.callback(_winapi.CloseHandle, handle)
                inherit.append(handle)
            startup = subprocess.STARTUPINFO(
                dwFlags=subprocess.STARTF_USESTDHANDLES,
                hStdInput=inherit[0],
                hStdOutput=inherit[1],
                hStdError=inherit[1],
                lpAttributeList={"handle_list": inherit},
            )
            process, thread, _, _ = _winapi.CreateProcess(
                command[0],
                subprocess.list2cmdline(command),
                None,
                None,
                True,
                _CREATE_SUSPENDED | subprocess.CREATE_NO_WINDOW,
                env,
                str(work),
                startup,
            )
        handles.callback(_winapi.CloseHandle, process)
        handles.callback(_winapi.CloseHandle, thread)
        assigned = False
        try:
            # Join before executing any user-mode code: even a fast launcher
            # cannot start a child outside this stage's job.
            if not _kernel.AssignProcessToJobObject(job, process):
                raise ctypes.WinError(ctypes.get_last_error())
            assigned = True
            if cancelled.is_set():
                return 1
            if _kernel.ResumeThread(thread) == 0xFFFFFFFF:
                raise ctypes.WinError(ctypes.get_last_error())
            while not cancelled.is_set():
                if _winapi.WaitForSingleObject(process, 50) == _winapi.WAIT_OBJECT_0:
                    return _winapi.GetExitCodeProcess(process)
            return 1
        finally:
            if assigned:
                members: list[int] = []
                try:
                    members = _member_handles(job, handles)
                finally:
                    if not _kernel.TerminateJobObject(job, 1):
                        raise ctypes.WinError(ctypes.get_last_error())
                extra = _member_handles(job, handles)
                for handle in [*members, *extra]:
                    _winapi.WaitForSingleObject(handle, _winapi.INFINITE)
            else:
                _winapi.TerminateProcess(process, 1)
            _winapi.WaitForSingleObject(process, _winapi.INFINITE)


async def run_windows_stage(
    command: list[str], work: Path, env: dict, log: BinaryIO
) -> int:
    """Wait for a stage without blocking the event loop; cancel its whole job.

    Args:
        command: Executable and arguments, without a shell.
        work: Stage working directory.
        env: Restricted compiler environment.
        log: Open binary output stream, owned by the caller.

    Returns:
        The exit code of the command.
    """
    cancelled = threading.Event()
    worker = asyncio.get_running_loop().run_in_executor(
        None, _run_job, command, work, env, log, cancelled
    )
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        cancelled.set()
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
        worker.result()
        raise
