from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from binary_layer.service import validate_zp


def _windows_process_counters() -> tuple[int | None, int | None, int | None]:
    if os.name != "nt":
        return None, None, None
    from ctypes import wintypes

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = (
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
        )

    class IoCounters(ctypes.Structure):
        _fields_ = (
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        )

    kernel32 = ctypes.windll.kernel32
    psapi = ctypes.windll.psapi
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(ProcessMemoryCounters),
        wintypes.DWORD,
    )
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    kernel32.GetProcessIoCounters.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(IoCounters),
    )
    kernel32.GetProcessIoCounters.restype = wintypes.BOOL
    process = kernel32.GetCurrentProcess()
    memory = ProcessMemoryCounters()
    memory.cb = ctypes.sizeof(memory)
    io = IoCounters()
    peak_rss = None
    read_bytes = None
    write_bytes = None
    if psapi.GetProcessMemoryInfo(process, ctypes.byref(memory), memory.cb):
        peak_rss = int(memory.PeakWorkingSetSize)
    if kernel32.GetProcessIoCounters(process, ctypes.byref(io)):
        read_bytes = int(io.ReadTransferCount)
        write_bytes = int(io.WriteTransferCount)
    return peak_rss, read_bytes, write_bytes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zp", required=True, type=Path)
    parser.add_argument("--mode", choices=("quick", "deep"), required=True)
    parser.add_argument("--certificate", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    _peak_before, read_before, write_before = _windows_process_counters()
    wall_started = time.perf_counter()
    cpu_started = time.process_time()
    result = validate_zp(
        args.zp.resolve(),
        mode=args.mode,
        certificate_path=(
            args.certificate.resolve() if args.certificate is not None else None
        ),
    )
    wall_seconds = time.perf_counter() - wall_started
    cpu_seconds = time.process_time() - cpu_started
    peak_rss, read_after, write_after = _windows_process_counters()
    report = {
        "mode": args.mode,
        "valid": result.valid,
        "version": result.version,
        "checked_blocks": result.checked_blocks,
        "issues": [item.code for item in result.issues],
        "top_down_valid": result.top_down_valid,
        "top_down_issues": [item.code for item in result.top_down_issues],
        "bottom_up_valid": result.bottom_up_valid,
        "bottom_up_issues": [item.code for item in result.bottom_up_issues],
        "file_sha256": result.file_sha256,
        "certificate_valid": result.certificate_valid,
        "deep_validation_reused": result.deep_validation_reused,
        "wall_seconds": wall_seconds,
        "cpu_seconds": cpu_seconds,
        "peak_rss": peak_rss,
        "read_bytes": (
            read_after - read_before
            if read_after is not None and read_before is not None
            else None
        ),
        "write_bytes": (
            write_after - write_before
            if write_after is not None and write_before is not None
            else None
        ),
        "metrics": result.metrics,
    }
    raw = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(raw, encoding="utf-8")
    print(raw, end="", flush=True)
    return 0 if result.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
