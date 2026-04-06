from modules.subtitles import _format_ass_time


def test_format_ass_time_zero():
    assert _format_ass_time(0) == "0:00:00.00"


def test_format_ass_time_with_centiseconds():
    assert _format_ass_time(61.234) == "0:01:01.23"


def test_format_ass_time_over_one_hour():
    assert _format_ass_time(3661.5) == "1:01:01.50"
