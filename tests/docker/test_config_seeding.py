"""Runtime smoke test for Docker's first-boot config seeding and ownership."""
from __future__ import annotations

from tests.docker.conftest import docker_exec_sh, start_container


def test_config_seeded_on_boot(
    built_image: str, container_name: str,
) -> None:
    """A fresh home receives a config.yaml owned by the runtime user."""
    # Start container
    start_container(built_image, container_name)

    # Verify config.yaml exists (should be seeded by stage2 if not present)
    r = docker_exec_sh(
        container_name,
        "test -f /opt/data/config.yaml && echo EXISTS || echo MISSING",
        timeout=10,
    )
    assert "EXISTS" in r.stdout, (
        f"config.yaml not found in $HERMES_HOME: {r.stdout}"
    )

    # The supervised process must be able to read and update its seeded config.
    r = docker_exec_sh(
        container_name,
        'stat -c "%U" /opt/data/config.yaml',
        timeout=10,
    )
    assert r.stdout.strip() == "hermes", (
        f"seeded config.yaml not owned by hermes: "
        f"{r.stdout.strip()}"
    )


