from __future__ import annotations

import threading
import time
import os
import sys
from pathlib import Path

try:
    import psutil
except ImportError:  # pragma: no cover - benchmark optional dependency boundary
    psutil = None  # type: ignore[assignment]


def _windows_memory(process_id: int | None = None) -> tuple[int, int] | None:
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class ProcessMemoryCountersEx(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
            ("PrivateUsage", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    psapi.GetProcessMemoryInfo.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(ProcessMemoryCountersEx),
        wintypes.DWORD,
    )
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    close_handle = False
    if process_id is None:
        handle = kernel32.GetCurrentProcess()
    else:
        handle = kernel32.OpenProcess(0x1000 | 0x0010, False, process_id)
        close_handle = True
    if not handle:
        return None
    counters = ProcessMemoryCountersEx()
    counters.cb = ctypes.sizeof(counters)
    try:
        if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return None
        return int(counters.WorkingSetSize), int(counters.PeakWorkingSetSize)
    finally:
        if close_handle:
            kernel32.CloseHandle(handle)


def process_memory(process_id: int | None = None) -> tuple[int, int | None] | None:
    if psutil is not None:
        try:
            info = psutil.Process(process_id).memory_info() if process_id is not None else psutil.Process().memory_info()
            peak = getattr(info, "peak_wset", None)
            return int(info.rss), int(peak) if peak is not None else None
        except Exception:
            return None
    windows = _windows_memory(process_id)
    if windows is not None:
        return windows
    if process_id is not None:
        try:
            pages = int(Path(f"/proc/{process_id}/statm").read_text().split()[1])
            return pages * int(os.sysconf("SC_PAGE_SIZE")), None
        except (OSError, ValueError, AttributeError):
            return None
    return None


def physical_memory_bytes() -> int | None:
    if psutil is not None:
        return int(psutil.virtual_memory().total)
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", wintypes.DWORD),
                ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.ullTotalPhys)
    try:
        return int(os.sysconf("SC_PHYS_PAGES")) * int(os.sysconf("SC_PAGE_SIZE"))
    except (ValueError, OSError, AttributeError):
        return None


class ProcessMonitor:
    def __init__(self, temporary_path: Path | None = None, interval_seconds: float = 0.05) -> None:
        self.temporary_path = temporary_path
        self.interval_seconds = interval_seconds
        self.rss_start_bytes: int | None = None
        self.rss_peak_bytes: int | None = None
        self.rss_end_bytes: int | None = None
        self.windows_peak_working_set_bytes: int | None = None
        self.temporary_file_peak_bytes = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._memory_available = process_memory() is not None

    @property
    def available(self) -> bool:
        return self._memory_available

    def start(self) -> None:
        memory = process_memory()
        if memory is not None:
            self.rss_start_bytes = memory[0]
            self.rss_peak_bytes = self.rss_start_bytes
            self.windows_peak_working_set_bytes = memory[1]
        self._thread = threading.Thread(target=self._sample_loop, name="p1-b6-rss-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.interval_seconds * 4))
        self._sample()
        memory = process_memory()
        self.rss_end_bytes = memory[0] if memory is not None else None

    def _sample_loop(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self._sample()

    def _sample(self) -> None:
        memory = process_memory()
        if memory is not None:
            try:
                rss, peak_wset = memory
                self.rss_peak_bytes = rss if self.rss_peak_bytes is None else max(self.rss_peak_bytes, rss)
                if peak_wset is not None:
                    current = self.windows_peak_working_set_bytes or 0
                    self.windows_peak_working_set_bytes = max(current, peak_wset)
            except Exception:
                pass
        if self.temporary_path is not None:
            try:
                self.temporary_file_peak_bytes = max(self.temporary_file_peak_bytes, self.temporary_path.stat().st_size)
            except OSError:
                pass


def monitor_child_limits(
    process_id: int,
    *,
    started_at: float,
    max_rss_bytes: int,
    max_runtime_seconds: float,
    max_output_bytes: int,
    output_paths: tuple[Path, ...],
) -> str | None:
    if time.perf_counter() - started_at > max_runtime_seconds:
        return "max runtime exceeded"
    total_output = sum(path.stat().st_size for path in output_paths if path.exists())
    if total_output > max_output_bytes:
        return "max output size exceeded"
    memory = process_memory(process_id)
    if memory is not None and memory[0] > max_rss_bytes:
        return "max RSS exceeded"
    return None
