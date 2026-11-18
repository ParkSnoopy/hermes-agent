"""Release author labels come from Git data, without an external mapping."""

from scripts.releases.authors import resolve_author


def test_resolve_author_from_git_identity():
    assert resolve_author("Example", "123+user@users.noreply.github.com") == "@user"
    assert resolve_author("Example", "user@users.noreply.github.com") == "@user"
    assert resolve_author("Example Person", "person@example.org") == "Example Person"
