"""``hermes_platform.host.summary``: the machine and account facts desktop first-run onboarding reads."""

from __future__ import annotations

import pytest

from hermes_platform.host import facts, summary


@pytest.fixture
def machine(monkeypatch):
    """Pin every hardware fact; returns a setter for the ones a case changes."""
    values = {"os_family": "linux", "native_arch": "amd64", "gpu_class": "unknown", "cpu_model": "",
              "cpu_vendor": "", "ram_total_bytes": 16 * 2**30, "nvidia_gpus": ()}

    def pin(**changes):
        values.update(changes)
        for name, value in values.items():
            monkeypatch.setattr(facts, name, lambda value=value: value)
        summary.summary.cache_clear()
        return summary.summary()

    monkeypatch.setattr(summary, "_account", lambda: ("ada", "Ada Lovelace"))
    yield pin
    summary.summary.cache_clear()


def test_shape(machine):
    result = machine()

    assert set(result) == {"machine", "machine_kind", "has_nvidia_gpu", "nvidia_platform", "full_name"}
    assert result["machine"]["os_family"] == "linux"
    assert result["machine"]["ram_gb"] == 16
    assert "gpu_class" not in result["machine"]  # unmeasured keys are left out, not sent as null
    assert result["full_name"] == "Ada Lovelace"



def test_dgx_spark_cpu_string_is_a_spark(machine):
    result = machine(native_arch="arm64", cpu_model="NVIDIA DGX Spark", cpu_vendor="NVIDIA")

    assert result["nvidia_platform"] == "dgx_spark"
    assert result["machine_kind"] == "Spark"


@pytest.mark.parametrize(("os_family", "arch", "gpu", "kind", "nvidia"), [
    ("linux", "amd64", "nvidia", "computer", True),
    ("linux", "amd64", "amd", "computer", False),
])
def test_ordinary_machines_are_not_sparks(machine, os_family, arch, gpu, kind, nvidia):
    result = machine(os_family=os_family, native_arch=arch, gpu_class=gpu, cpu_model="Generic CPU")

    assert result["nvidia_platform"] == ("discrete" if nvidia else None)
    assert result["machine_kind"] == kind
    assert result["has_nvidia_gpu"] is nvidia


@pytest.mark.parametrize("full", ["ada", "Ada", "p14", "root", "x", "ada lovelace"])
def test_a_login_handle_is_never_offered_as_the_name(machine, monkeypatch, full):
    monkeypatch.setattr(summary, "_account", lambda: ("ada", full))

    assert machine()["full_name"] is None


def test_os_release_is_the_kernel_release(machine, monkeypatch):
    monkeypatch.setattr(summary.platform, "release", lambda: "25.0.0")

    assert machine()["machine"]["os_release"] == "25.0.0"


def test_cached_per_process(machine, monkeypatch):
    first = machine()
    monkeypatch.setattr(facts, "cpu_model", lambda: "changed")

    assert summary.summary() is first
