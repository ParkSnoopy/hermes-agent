"""Local terminal configuration notice."""

def setup_terminal_backend(config: dict):
    """Report the supported terminal environment without changing saved settings."""
    import hermes_cli.setup as _setup
    _setup.print_header("Terminal Backend")
    backend = _setup.cfg_get(config, "terminal", "backend", default="local")
    if str(backend or "local").strip().lower() != "local":
        _setup.print_warning(f"Terminal backend {backend!r} is unsupported; set terminal.backend to local.")
    else:
        _setup.print_info("Shell commands run locally. Configure terminal.cwd and terminal.timeout as needed.")
