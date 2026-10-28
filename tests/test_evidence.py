from atlas.evidence import quote_in_page
from atlas.schema import CheckResult

PAGE = """# App lifecycle

Apps listed in the **Slack Marketplace** are reviewed against our requirements.
You can enter up to 10 callback URLs—it’s that simple.
"""


def test_exact_quote_passes():
    assert quote_in_page("You can enter up to 10 callback URLs", PAGE) == CheckResult.passed


def test_markdown_case_and_whitespace_are_ignored():
    assert quote_in_page("apps listed in the slack  marketplace are reviewed", PAGE) == CheckResult.passed


def test_curly_quotes_and_dashes_are_folded():
    assert quote_in_page("callback URLs-it's that simple.", PAGE) == CheckResult.passed


def test_paraphrase_fails():
    assert quote_in_page("You can add ten callback URLs", PAGE) == CheckResult.failed


def test_empty_quote_fails():
    assert quote_in_page("   ", PAGE) == CheckResult.failed
