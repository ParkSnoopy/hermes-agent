"""Guard the shared MEDIA caption decision for outbound adapters."""

from gateway.outbound_senders import _DEFAULT_CAPTION_LIMIT, _media_caption_split


def test_single_image_short_text_becomes_caption():
    caption, body = _media_caption_split(
        "This Caption", [("/tmp/F22.png", False)], max_caption_len=_DEFAULT_CAPTION_LIMIT
    )
    assert caption == "This Caption"
    assert body == ""


def test_multi_file_keeps_separate_body():
    text = "two photos"
    caption, body = _media_caption_split(
        text,
        [("/tmp/a.png", False), ("/tmp/b.png", False)],
        max_caption_len=_DEFAULT_CAPTION_LIMIT,
    )
    assert caption is None
    assert body == text


def test_unknown_extension_keeps_separate_body():
    # A non-captionable kind (e.g. an audio note that isn't flagged voice)
    text = "some audio"
    caption, body = _media_caption_split(
        text, [("/tmp/song.mp3", False)], max_caption_len=_DEFAULT_CAPTION_LIMIT
    )
    assert caption is None
    assert body == text
