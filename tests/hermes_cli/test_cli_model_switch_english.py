"""English /model switch presentation."""

from __future__ import annotations



from hermes_cli import cli_model_switch_mixin as ms






def test_model_usage_block_lists_every_form_with_description(monkeypatch):
    """No authenticated providers → the usage block: one aligned row per /model form."""
    import cli as cli_mod
    printed: list[str] = []
    monkeypatch.setattr(cli_mod, "_cprint", lambda line: printed.append(line))

    class _Cli:
        def _open_model_picker(self, *a, **kw):  # pragma: no cover - not reached
            raise AssertionError("picker must not open without providers")

    ms._show_model_picker(_Cli(), None, force_refresh=False)  # ctx=None → no providers
    assert printed[0].strip() == 'No authenticated providers found.'
    rows = printed[2:]
    assert len(rows) == len(ms._MODEL_USAGE_ROWS) == 8
    for (form, key), line in zip(ms._MODEL_USAGE_ROWS, rows):
        assert line.startswith(f"  {form}")
        assert line.endswith(key)
    # Description column is aligned across rows.
    assert len({line.index(key) for (_, key), line in zip(ms._MODEL_USAGE_ROWS, rows)}) == 1
