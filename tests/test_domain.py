from datetime import date

from tpby.domain import (
    contains_code,
    extract_code,
    is_deal_media_caption,
    is_recent_date_code,
)


def test_extracts_first_standalone_code_and_ignores_parenthesized_date() -> None:
    text = "编号： 168240 （260926）\n昵称：kiss609"
    assert extract_code(text) == "168240"


def test_code_boundaries_reject_digits_and_parentheses() -> None:
    assert extract_code("x1234567 编号：654321") == "654321"
    assert extract_code("(123456) （654321）") is None
    assert contains_code("编号：123456", "123456")
    assert not contains_code("编号：(123456)", "123456")


def test_recent_date_uses_six_calendar_month_window() -> None:
    today = date(2026, 9, 27)
    assert is_recent_date_code("260327", today)
    assert is_recent_date_code("260927", today)
    assert not is_recent_date_code("260326", today)
    assert not is_recent_date_code("260928", today)
    assert not is_recent_date_code("260231", today)


def test_deal_media_requires_parentheses() -> None:
    assert is_deal_media_caption("编号：383987 (验证视频)")
    assert is_deal_media_caption("编号：383987（ 验证视频 ）")
    assert not is_deal_media_caption("编号：383987 验证视频")
