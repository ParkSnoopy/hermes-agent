"""Local-only profile selection for filesystem routes, without starting a web app."""

import pytest
from fastapi import HTTPException

from hermes_cli.local_file_profile import require_local_file_profile


def test_local_file_profile_is_allowed(tmp_path):
    require_local_file_profile(tmp_path)


@pytest.mark.parametrize("backend", ["docker", "ssh", "vercel_sandbox", "plugin"])
def test_retired_file_profile_is_refused(tmp_path, backend):
    (tmp_path / "config.yaml").write_text(f"terminal:\n  backend: {backend}\n")
    with pytest.raises(HTTPException) as exc:
        require_local_file_profile(tmp_path)
    assert exc.value.status_code == 409
