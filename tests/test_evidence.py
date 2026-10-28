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


# --- Cases from the LinkedIn and Notion live runs -----------------------------

TABLE = "| Status | Meaning |\n| --- | --- |\n| **App Review** | We are reviewing your developer application and screencast. |"
NOTION = ("Public connections work independently of Marketplace listings. Listing on the Marketplace is optional "
          "and helps your connection reach a wider audience, but your connection can be used via its OAuth flow "
          "without being listed.")


def test_quote_across_table_markup_is_found():
    assert quote_in_page("App Review: We are reviewing your developer application and screencast.", TABLE) == CheckResult.passed


def test_fragments_joined_with_ellipsis_are_found_in_order():
    q = "Public connections work independently of Marketplace listings. ... your connection can be used via its OAuth flow without being listed."
    assert quote_in_page(q, NOTION) == CheckResult.passed


def test_fragments_out_of_order_fail():
    q = "your connection can be used via its OAuth flow ... Public connections work independently of Marketplace listings."
    assert quote_in_page(q, NOTION) == CheckResult.failed


def test_a_changed_word_inside_a_fragment_fails():
    q = "Public connections work independently of Marketplace listings. ... your connection must be used via its OAuth flow"
    assert quote_in_page(q, NOTION) == CheckResult.failed


def test_tiny_fragments_cannot_stitch_a_quote():
    assert quote_in_page("Public connections ... OAuth flow", NOTION) == CheckResult.failed
