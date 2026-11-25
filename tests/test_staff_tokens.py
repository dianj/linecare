"""Staff tokens (review T18.A3): without STAFF_TOKENS the app must not accept the public demo tokens from the README;
it makes random ones for the run and prints them once, as the comment moderator does with its admin token."""
from linecare import config


def test_without_staff_tokens_the_public_demo_tokens_are_refused(capsys):
    tokens = config.load_staff_tokens(None)
    assert sorted(tokens.values()) == ["operator", "technician"]
    assert "demo-operator" not in tokens and "demo-technician" not in tokens
    assert all(len(token) >= 16 for token in tokens)
    printed = capsys.readouterr().out
    assert all(token in printed for token in tokens) and "STAFF_TOKENS" in printed
    assert config.load_staff_tokens(None) != tokens                      # new ones on every start


def test_staff_tokens_from_the_environment():
    assert config.load_staff_tokens("a1:operator, b2:technician,broken") == {"a1": "operator", "b2": "technician"}
