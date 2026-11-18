"""Update receipt readers preserve the durable outcome record."""

import json

import pytest

from hermes_cli import update_receipt


@pytest.mark.parametrize("outcome", ["success", "partial", "running"])
def test_latest_receipt_preserves_outcome_and_steps(tmp_path, monkeypatch, outcome):
    receipt = {
        "schema": 1, "outcome": outcome, "action_id": "d" * 32,
        "pre_update": {"sha": "a" * 40},
        "post_update": {"sha": "b" * 40, "version": "0.20.5"},
        "steps": [{"name": "pre_update_backup", "ok": True}],
        "fleet": [{"profile": "default", "state": "current"}],
    }
    (tmp_path / "latest.json").write_text(json.dumps(receipt), encoding="utf-8")
    monkeypatch.setattr(update_receipt, "_receipt_dirs", lambda: [tmp_path])
    assert update_receipt.read_latest_receipt() == receipt
    assert update_receipt.read_receipt_for_action(receipt["action_id"]) == receipt
    assert update_receipt.read_receipt_for_action("unrelated") is None


def test_missing_receipt_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(update_receipt, "_receipt_dirs", lambda: [tmp_path])
    assert update_receipt.read_latest_receipt() is None
