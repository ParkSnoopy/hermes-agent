"""Paused work remains discoverable, and terminal phase changes honor pause intent."""

from __future__ import annotations

import threading

import pytest

from pm.downloader import DownloadPaused
def test_pause_request_prevents_transition_into_activation():
    from hermes_cli.local_runtime import local_models as lm

    job = lm._job("quickstart", "model")
    job["phase"] = "downloading"
    pause = threading.Event()
    lm._RUNNING[job["job_id"]] = {"pause": pause}
    assert lm._job_view(job)["can_pause"]
    pause.set()
    with pytest.raises(DownloadPaused):
        lm._step(job, "starting-server", "Starting")
    assert job["phase"] == "downloading"
