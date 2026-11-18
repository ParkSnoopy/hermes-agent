"""For reviewed unit selections: fail closed on process or signal side effects."""
import os
import subprocess


def _forbidden(*args, **kwargs):
    raise AssertionError('external process or signal boundary reached')


class _ForbiddenPopen(subprocess.Popen):
    def __init__(self, *args, **kwargs):
        _forbidden(*args, **kwargs)


def pytest_configure(config):
    os.kill = _forbidden
    os.killpg = _forbidden
    subprocess.Popen = _ForbiddenPopen
