import pytest

from telegram_news.resolve import parse_link, ParseError


def test_username_at_form():
    assert parse_link("@channelname") == ("username", "channelname")


def test_username_bare():
    assert parse_link("channelname") == ("username", "channelname")


def test_username_url_https():
    assert parse_link("https://t.me/channelname") == ("username", "channelname")


def test_username_url_http():
    assert parse_link("http://t.me/channelname") == ("username", "channelname")


def test_username_url_tg_resolve():
    assert parse_link("tg://resolve?domain=channelname") == ("username", "channelname")


def test_username_message_link():
    assert parse_link("https://t.me/channelname/123") == ("username", "channelname")


def test_private_message_link():
    assert parse_link("https://t.me/c/1234567890/567") == ("peer_id", -1001234567890)


def test_raw_negative_id():
    assert parse_link("-1001234567890") == ("peer_id", -1001234567890)


def test_invite_link_plus_form_rejected():
    with pytest.raises(ParseError, match="invite"):
        parse_link("https://t.me/+abcDEF123")


def test_invite_link_joinchat_form_rejected():
    with pytest.raises(ParseError, match="invite"):
        parse_link("https://t.me/joinchat/abcDEF123")


def test_garbage_rejected():
    with pytest.raises(ParseError):
        parse_link("not a link at all !!!")


def test_whitespace_trimmed():
    assert parse_link("  @channelname  ") == ("username", "channelname")
