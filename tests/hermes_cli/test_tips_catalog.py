"""Discovery tips remain ordinary English strings."""

from hermes_cli import tips


def test_random_tip_and_placeholder_come_from_english_pools(monkeypatch):
    monkeypatch.setattr(tips.random, "choice", lambda values: values[0])
    assert tips.get_random_tip() == tips.TIPS[0]
    assert tips.get_random_composer_placeholder() == tips.COMPOSER_PLACEHOLDERS[0]
    assert tips.TIPS[0].startswith("/bg <prompt>")
    assert all(isinstance(value, str) and value for value in tips.TIPS)
    assert all(isinstance(value, str) and value for value in tips.COMPOSER_PLACEHOLDERS)
