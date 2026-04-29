from telegram_news.dialog_cache import CachedDialog, DialogCache


def _seed(cache, items):
    cache._items = items


def test_search_substring_case_insensitive():
    c = DialogCache()
    _seed(c, [
        CachedDialog(-1, "Crypto News Daily", "cryptonews", "channel"),
        CachedDialog(-2, "Dev Chat", None, "megagroup"),
        CachedDialog(-3, "Random", "random_room", "megagroup"),
    ])
    out = c.search("crypto", exclude=set(), limit=10)
    assert [d.peer_id for d in out] == [-1]


def test_search_excludes_selected():
    c = DialogCache()
    _seed(c, [
        CachedDialog(-1, "Foo", None, "channel"),
        CachedDialog(-2, "Foo", None, "channel"),
    ])
    assert [d.peer_id for d in c.search("foo", exclude={-1}, limit=10)] == [-2]


def test_search_respects_limit():
    c = DialogCache()
    _seed(c, [CachedDialog(i, f"Foo{i}", None, "channel") for i in range(20)])
    assert len(c.search("foo", exclude=set(), limit=5)) == 5


def test_search_empty_query_returns_all_until_limit():
    c = DialogCache()
    _seed(c, [CachedDialog(i, f"x{i}", None, "channel") for i in range(3)])
    assert len(c.search("", exclude=set(), limit=10)) == 3
