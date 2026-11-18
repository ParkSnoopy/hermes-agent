"""Cached OS, architecture, CPU, memory and GPU facts for the current machine.

Facts read Linux procfs or sysfs, never the environment, a subprocess or a GPU driver library.
"""

from __future__ import annotations

import functools
import os
import platform
import sys
from typing import NamedTuple


_ARCH_NAMES = {
    "amd64": "amd64", "x86_64": "amd64", "x64": "amd64",
    "arm64": "arm64", "aarch64": "arm64",
    "x86": "x86", "i386": "x86", "i686": "x86",
}


def normalize_arch(raw: str | None) -> str:
    """Return a canonical architecture name."""
    return _ARCH_NAMES.get((raw or "").strip().lower(), "unknown")


@functools.cache
def os_family() -> str:
    """Return the current platform identifier."""
    return sys.platform


@functools.cache
def process_arch() -> str:
    """Return the architecture of this Python process."""
    return normalize_arch(platform.machine())


@functools.cache
def native_arch() -> str:
    """Return the architecture reported by the Linux kernel."""
    return process_arch()


def parse_cpuinfo(text: str, *, device_tree_model: str = "") -> str:
    """Return the first available CPU or device-tree model."""
    for key in ("model name", "Hardware"):
        for line in text.splitlines():
            if line.lower().startswith(key.lower()) and ":" in line:
                return line.split(":", 1)[1].strip()
    return device_tree_model.replace("\0", "").replace("_", " ").strip()


def _read_text(path: str, limit: int = 65536) -> str:
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as handle:
            return handle.read(limit)
    except OSError:
        return ""


@functools.cache
def cpu_model() -> str:
    """Return the CPU model, or an empty string when unavailable."""
    return parse_cpuinfo(_read_text("/proc/cpuinfo"), device_tree_model=_read_text("/proc/device-tree/model", 512))


# ARM's MIDR implementer codes, as arm64 Linux prints them in ``CPU implementer``.
_ARM_IMPLEMENTERS = {
    0x41: "ARM", 0x42: "Broadcom", 0x43: "Cavium", 0x46: "Fujitsu", 0x48: "HiSilicon", 0x4E: "NVIDIA",
    0x50: "Ampere", 0x51: "Qualcomm", 0x61: "Apple", 0x69: "Intel", 0x6D: "Microsoft", 0xC0: "Ampere",
}


def parse_cpuinfo_vendor(text: str) -> str:
    """Return ``vendor_id`` (x86) or the named ``CPU implementer`` (arm64) from ``/proc/cpuinfo``."""
    pairs = (line.split(":", 1) for line in text.splitlines() if ":" in line)
    fields = {key.strip(): value.strip() for key, value in pairs}
    if fields.get("vendor_id"):
        return fields["vendor_id"]
    try:
        return _ARM_IMPLEMENTERS.get(int(fields.get("CPU implementer", ""), 16), "")
    except ValueError:
        return ""


@functools.cache
def cpu_vendor() -> str:
    """Return the CPU vendor, or an empty string when unavailable.

    ARM Linux reports the core designer.
    """
    return parse_cpuinfo_vendor(_read_text("/proc/cpuinfo"))


def parse_meminfo_total(text: str) -> int | None:
    """Return ``MemTotal`` from ``/proc/meminfo`` text, in bytes."""
    for line in text.splitlines():
        if line.startswith("MemTotal:"):
            fields = line.split()
            if len(fields) >= 2 and fields[1].isdigit():
                return int(fields[1]) * 1024
    return None


@functools.cache
def ram_total_bytes() -> int | None:
    """Return the physical memory visible to this OS, or ``None`` when unavailable."""
    return parse_meminfo_total(_read_text("/proc/meminfo", 4096))


# PCI vendor ids; a discrete vendor wins over an integrated one on hybrid machines.
_GPU_PCI_VENDORS = {"10de": "nvidia", "1002": "amd", "8086": "intel"}
_GPU_PRIORITY = ("nvidia", "amd", "intel")
_GPU_SCAN_LIMIT = 16


def classify_gpu_vendors(vendor_ids: list[str]) -> str:
    """Return the highest-priority GPU class for PCI vendor ids, or ``none``.

    ``unknown`` when devices were seen but none of their vendor ids could be read.
    """
    if vendor_ids and not any(v.strip() for v in vendor_ids):
        return "unknown"
    found = {_GPU_PCI_VENDORS.get(v.strip().lower().removeprefix("0x")) for v in vendor_ids}
    return next((name for name in _GPU_PRIORITY if name in found), "none")


def _linux_gpu_vendor_ids() -> list[str] | None:
    """PCI vendor ids of DRM cards; ``None`` when sysfs exposes no DRM tree at all."""
    root = "/sys/class/drm"
    try:
        entries = sorted(os.listdir(root))[:_GPU_SCAN_LIMIT * 4]
    except OSError:
        return None
    cards = [e for e in entries if e.startswith("card") and "-" not in e][:_GPU_SCAN_LIMIT]
    return [_read_text(f"{root}/{card}/device/vendor", 16).strip() for card in cards]


# The WSL GPU bridge mounts the Windows NVIDIA driver's CUDA library here only when that driver is installed.
_WSL_NVIDIA_CUDA = "/usr/lib/wsl/lib/libcuda.so.1"


@functools.cache
def gpu_class() -> str:
    """Return ``nvidia``/``amd``/``intel``/``none``, or ``unknown``.

    Check the NVIDIA driver procfs, the WSL GPU bridge, then sysfs DRM cards.
    """
    try:
        if os.path.exists("/proc/driver/nvidia/version") or os.path.exists(_WSL_NVIDIA_CUDA):
            return "nvidia"
        else:
            vendors = _linux_gpu_vendor_ids()
    except (AttributeError, OSError, TypeError, ValueError):
        return "unknown"
    return "unknown" if vendors is None else classify_gpu_vendors(vendors)


_NVIDIA_PROC_GPUS = "/proc/driver/nvidia/gpus"
_NVIDIA_PCI_VENDOR = 0x10DE
_SYS_PCI_DEVICES = "/sys/bus/pci/devices"


def parse_nvidia_gpu_model(information: str) -> str:
    """Return the ``Model:`` value of a ``/proc/driver/nvidia/gpus/<bus>/information`` file."""
    for line in information.splitlines():
        if line.startswith("Model:"):
            return line.split(":", 1)[1].strip()
    return ""


class NvidiaGpu(NamedTuple):
    name: str
    pci_id: int | None  # NVML packing: device id above the 16-bit vendor id


def _pci_id(device_hex: str) -> int | None:
    try:
        return int(device_hex, 16) << 16 | _NVIDIA_PCI_VENDOR
    except ValueError:
        return None


def _linux_nvidia_gpus() -> tuple[NvidiaGpu, ...]:
    try:
        buses = sorted(os.listdir(_NVIDIA_PROC_GPUS))[:_GPU_SCAN_LIMIT]
    except OSError:
        return ()
    gpus = (NvidiaGpu(parse_nvidia_gpu_model(_read_text(f"{_NVIDIA_PROC_GPUS}/{bus}/information", 4096)),
                      _pci_id(_read_text(f"{_SYS_PCI_DEVICES}/{bus}/device", 16).strip().removeprefix("0x")))
            for bus in buses)
    return tuple(gpu for gpu in gpus if gpu.name)


@functools.cache
def nvidia_gpus() -> tuple[NvidiaGpu, ...]:
    """Name and PCI id of each NVIDIA GPU from driver procfs and sysfs."""
    return _linux_nvidia_gpus()


def interactive_session() -> bool:
    """Return whether this process can reach an interactive user session."""
    session_id = _read_text("/proc/self/sessionid", 64).strip()
    return bool(session_id and session_id != "4294967295" and os.path.isdir(f"/run/user/{os.getuid()}"))


def clear_caches() -> None:
    """Clear every cached host fact."""
    for fact in (os_family, process_arch, native_arch, cpu_model, cpu_vendor, ram_total_bytes, gpu_class,
                 nvidia_gpus):
        fact.cache_clear()
