import json

import httpx
import pytest
import respx

from stockbot.search.anysearch import AnySearch, FakeSearch, Hit, NoSearch, SearchError

SEARCH = "https://api.anysearch.com/v1/search"
EXTRACT = "https://api.anysearch.com/v1/extract"


def envelope(data, code=0, message="success"):
    return {"code": code, "message": message, "request_id": "r", "data": data}


@respx.mock
async def test_search_parses_hits():
    route = respx.post(SEARCH).mock(return_value=httpx.Response(200, json=envelope({"results": [
        {"title": "Iran exclusion zone", "url": "https://a.example/1", "snippet": "short",
         "content": "short ... date: 6 hours ago"},
        {"title": "No content", "url": "https://b.example/2", "snippet": "only snippet"}]})))
    hits = await AnySearch("k").search("hormuz", 10)
    assert hits == [Hit("Iran exclusion zone", "https://a.example/1", "short ... date: 6 hours ago"),
                    Hit("No content", "https://b.example/2", "only snippet")]
    req = route.calls.last.request
    assert json.loads(req.content) == {"query": "hormuz", "max_results": 10}
    assert req.headers["Authorization"] == "Bearer k"


@respx.mock
async def test_bearer_only_with_key():
    route = respx.post(SEARCH).mock(return_value=httpx.Response(200, json=envelope({"results": []})))
    assert await AnySearch(None).search("q", 3) == []
    assert "Authorization" not in route.calls.last.request.headers


@respx.mock
async def test_code_nonzero_raises():
    respx.post(SEARCH).mock(return_value=httpx.Response(200, json=envelope(None, -1, "quota exhausted")))
    with pytest.raises(SearchError, match="quota exhausted"):
        await AnySearch("k").search("q", 3)


@respx.mock
async def test_extract_422_raises():
    respx.post(EXTRACT).mock(return_value=httpx.Response(422, json={
        "code": -1, "message": "Unable to extract content from the URL.", "error_code": "extract_failed"}))
    with pytest.raises(SearchError, match="Unable to extract"):
        await AnySearch("k").extract("https://www.wsj.com/x")


@respx.mock
async def test_non_json_raises():
    respx.post(SEARCH).mock(return_value=httpx.Response(502, text="<html>bad gateway</html>"))
    with pytest.raises(SearchError, match="502"):
        await AnySearch("k").search("q", 3)


@respx.mock
async def test_transport_error_raises():
    respx.post(SEARCH).mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(SearchError, match="ConnectError"):
        await AnySearch("k").search("q", 3)


@respx.mock
@pytest.mark.parametrize("data", [["not", "a", "dict"], {"results": "x"}, {"results": {"a": 1}}])
async def test_malformed_search_body_raises(data):
    respx.post(SEARCH).mock(return_value=httpx.Response(200, json=envelope(data)))
    with pytest.raises(SearchError, match="unexpected"):
        await AnySearch("k").search("q", 3)


@respx.mock
async def test_non_object_results_skipped():
    respx.post(SEARCH).mock(return_value=httpx.Response(200, json=envelope(
        {"results": ["junk", None, {"title": "T", "url": "https://a", "snippet": "s"}]})))
    assert await AnySearch("k").search("q", 3) == [Hit("T", "https://a", "s")]


@respx.mock
async def test_non_text_extract_raises():
    respx.post(EXTRACT).mock(return_value=httpx.Response(200, json=envelope({"content": {"html": "x"}})))
    with pytest.raises(SearchError, match="unexpected"):
        await AnySearch("k").extract("https://a")


@respx.mock
async def test_extract_returns_content():
    route = respx.post(EXTRACT).mock(return_value=httpx.Response(200, json=envelope(
        {"url": "https://a.example/1", "title": "T", "content": "full page text"})))
    assert await AnySearch("k").extract("https://a.example/1") == "full page text"
    assert json.loads(route.calls.last.request.content) == {"url": "https://a.example/1"}


async def test_fake_search_records_and_fails_unknown():
    s = FakeSearch([Hit("t", "u1", "x")], {"u1": "page", "u2": SearchError("blocked")})
    assert await s.search("q", 5) == [Hit("t", "u1", "x")]
    assert await s.extract("u1") == "page"
    with pytest.raises(SearchError, match="blocked"):
        await s.extract("u2")
    with pytest.raises(SearchError, match="not found"):
        await s.extract("u3")
    assert s.queries == ["q"] and s.extracted == ["u1", "u2", "u3"]


async def test_no_search_always_fails():
    with pytest.raises(SearchError, match="unavailable in replay"):
        await NoSearch().search("q", 5)
    with pytest.raises(SearchError, match="unavailable in replay"):
        await NoSearch().extract("u")
