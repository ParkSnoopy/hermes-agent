"""Regression tests for packaging metadata in pyproject.toml."""

from pathlib import Path
import tomllib

def _load_optional_dependencies():
    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with pyproject_path.open("rb") as handle:
        project = tomllib.load(handle)["project"]
    return project["optional-dependencies"]


def test_direct_overrides_preserve_the_declared_exact_version():
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    root = Path(__file__).resolve().parents[1]
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8-sig"))
    locked = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8-sig"))
    # uv.lock records normalized names; pyproject keeps the declared spelling (Pillow).
    direct = {canonicalize_name(req.name): req for spec in metadata["project"]["dependencies"]
              if (req := Requirement(spec)).specifier}
    for spec in metadata["tool"]["uv"].get("override-dependencies", []):
        override = Requirement(spec)
        name = canonicalize_name(override.name)
        requirement = direct.get(name)
        if requirement is None or not any(s.operator == "==" for s in requirement.specifier):
            continue
        assert override.specifier == requirement.specifier, (
            f"{override.name}: override {override.specifier} contradicts exact requirement {requirement.specifier}"
        )
        versions = {row["version"] for row in locked["package"] if canonicalize_name(row["name"]) == name}
        assert versions and all(version in requirement.specifier for version in versions)



def test_lazy_installable_extras_excluded_from_all():
    """Policy (2026-05-12): opt-in backends stay out of [all].

    On-demand install exists so one quarantined PyPI release
    (e.g. mistralai 2.4.6) can't break every fresh install. Putting a
    backend in [all] defeats that — fresh installs eager-install it and
    inherit whatever's broken upstream. Opt-in backends are extras that
    install at first use via pm.ensure_import(extra).
    """
    optional_dependencies = _load_optional_dependencies()

    # The on-demand backends as of 2026-05-12. Deliberately a literal
    # list so the test stays a contract — adding a new opt-in backend
    # means updating this list AND verifying [all] doesn't contain it.
    lazy_covered_extras = {
        "anthropic", "bedrock",
        "exa", "firecrawl", "parallel-web",
        "fal",
        "vercel",

        "keychain",  # keyring — vault key in the OS keychain, installed by `hermes vault key migrate`

    }
    all_extra_specs = optional_dependencies["all"]
    for extra in lazy_covered_extras:
        offending = [
            spec for spec in all_extra_specs
            if f"hermes-agent[{extra}]" in spec
        ]
        assert not offending, (
            f"[{extra}] is in [all] but also in LAZY_DEPS. "
            f"Remove it from [all] in pyproject.toml — it lazy-installs "
            f"at first use. Found in [all]: {offending}"
        )


def _exact_pins(specs):
    pins = {}
    for spec in specs:
        requirement = spec.split(";", 1)[0].strip()
        if "==" not in requirement:
            continue
        package, version = requirement.split("==", 1)
        package = package.split("[", 1)[0].lower().replace("_", "-")
        pins[package] = version
    return pins




def test_extras_pin_each_package_at_one_version():
    """One package, one version, across every extra.

    tools/lazy_deps.py is gone — pyproject.toml is the single authority for
    optional-dependency pins (pm syncs the venv from uv.lock, which resolves
    from here). The drift class that killed us before (#31817: two documents
    pinning the same package differently, update ping-ponging the version)
    is now only possible BETWEEN extras — so pin consistency across extras
    is the whole remaining contract.
    """
    optional_dependencies = _load_optional_dependencies()

    pins: dict[str, dict[str, set[str]]] = {}
    for extra, specs in optional_dependencies.items():
        for package, version in _exact_pins(specs).items():
            pins.setdefault(package, {}).setdefault(version, set()).add(extra)

    drift = {
        package: {v: sorted(extras) for v, extras in versions.items()}
        for package, versions in pins.items()
        if len(versions) > 1
    }
    assert not drift, (
        "extras pin the same package at different versions — uv sync would "
        f"resolve whichever wins and silently downgrade the other: {drift}"
    )
