from __future__ import annotations

import subprocess

import pytest

from hermes_platform.host import facts, products
from hermes_platform.host.facts import NvidiaGpu


_OTHER_NVIDIA_GPU = (NvidiaGpu("NVIDIA GeForce RTX 4090", 0x268410DE),)
_GB10_INFORMATION = "Model: \t\t NVIDIA GB10\nIRQ:   \t\t 196\nGPU UUID: \t GPU-0\n"
_GB10 = (NvidiaGpu(facts.parse_nvidia_gpu_model(_GB10_INFORMATION), None),)


@pytest.mark.parametrize(
    ("os_family", "native_arch", "cpu_model", "gpu_class", "gpus", "expected"),
    [

        ("linux", "arm64", "", "nvidia", _GB10, "dgx_spark"),
        ("linux", "arm64", "NVIDIA DGX Spark", "nvidia", (), "dgx_spark"),
        ("linux", "arm64", "", "nvidia", (NvidiaGpu("NVIDIA GH200 480GB", None),), "discrete"),
        ("linux", "amd64", "Intel Xeon", "nvidia", _OTHER_NVIDIA_GPU, "discrete"),
        ("linux", "amd64", "Intel Xeon", "none", (), None),
    ],
)
def test_nvidia_platform_from_host_facts(os_family, native_arch, cpu_model, gpu_class, gpus, expected):
    assert products.classify_nvidia_platform(
        os_family=os_family, native_arch=native_arch, cpu_model=cpu_model, gpu_class=gpu_class,
        nvidia_gpus=gpus) == expected


@pytest.mark.platforms("linux")
def test_linux_nvidia_gpus_come_from_the_driver_files_without_a_process(monkeypatch, tmp_path):
    proc, sysfs = tmp_path / "proc", tmp_path / "sys"
    (proc / "000f:01:00.0").mkdir(parents=True)
    (proc / "000f:01:00.0" / "information").write_text(_GB10_INFORMATION, encoding="utf-8")
    (proc / "0000:02:00.0").mkdir()  # a bus directory without an information file is skipped
    (sysfs / "000f:01:00.0").mkdir(parents=True)
    (sysfs / "000f:01:00.0" / "device").write_text("0x2e12\n", encoding="utf-8")
    monkeypatch.setattr(facts, "_NVIDIA_PROC_GPUS", str(proc))
    monkeypatch.setattr(facts, "_SYS_PCI_DEVICES", str(sysfs))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("nvidia_platform spawned a process"))
    facts.clear_caches()
    try:
        assert facts.nvidia_gpus() == (NvidiaGpu("NVIDIA GB10", 0x2E1210DE),)
        assert products.nvidia_platform() in ("dgx_spark", "discrete", None)
    finally:
        facts.clear_caches()
