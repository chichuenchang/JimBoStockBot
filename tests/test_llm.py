import json
from types import SimpleNamespace

import httpx
import pytest
import respx

from stockbot.config import ModelCfg
from stockbot.llm.anthropic_client import AnthropicClient
from stockbot.llm.base import LLMError, Usage, cost_usd, make_client
from stockbot.llm.fake import FakeLLM

KIMI = ModelCfg("kimi-k3", "openai_compat", "https://api.moonshot.ai/v1", "kimi-k3",
                "MOONSHOT_API_KEY", 3.0, 0.30, 15.0, 80, True)
URL = "https://api.moonshot.ai/v1/chat/completions"


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setenv("MOONSHOT_API_KEY", "k")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ak")


def ok_body(content='{"a":1}', usage=None):
    return {"choices": [{"message": {"content": content}}],
            "usage": usage or {"prompt_tokens": 100, "completion_tokens": 20,
                               "prompt_cache_hit_tokens": 60}}


def test_cost_usd():
    assert cost_usd(KIMI, Usage(25000, 20000, 4000)) == pytest.approx(0.081)


@respx.mock
async def test_openai_compat_parses_content_and_usage():
    respx.post(URL).mock(return_value=httpx.Response(200, json=ok_body()))
    r = await make_client(KIMI, timeout_s=5).complete("sys", "user")
    assert r.text == '{"a":1}'
    assert r.usage == Usage(100, 60, 20)


@respx.mock
async def test_openai_cached_tokens_details_field():
    usage = {"prompt_tokens": 50, "completion_tokens": 5,
             "prompt_tokens_details": {"cached_tokens": 30}}
    respx.post(URL).mock(return_value=httpx.Response(200, json=ok_body(usage=usage)))
    r = await make_client(KIMI, timeout_s=5).complete("s", "u")
    assert r.usage == Usage(50, 30, 5)


@respx.mock
async def test_openai_compat_sends_json_mode_and_bearer():
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=ok_body()))
    await make_client(KIMI, timeout_s=5).complete("sys", "user")
    req = route.calls.last.request
    body = json.loads(req.content)
    assert body["response_format"] == {"type": "json_object"}
    assert body["model"] == "kimi-k3"
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert req.headers["Authorization"] == "Bearer k"


@respx.mock
async def test_http_429_raises_llm_error():
    respx.post(URL).mock(return_value=httpx.Response(429, json={"error": "slow down"}))
    with pytest.raises(LLMError, match="429"):
        await make_client(KIMI, timeout_s=5).complete("s", "u")


@respx.mock
async def test_timeout_raises_llm_error():
    respx.post(URL).mock(side_effect=httpx.ReadTimeout("t"))
    with pytest.raises(LLMError, match="timeout"):
        await make_client(KIMI, timeout_s=5).complete("s", "u")


@respx.mock
async def test_empty_content_raises():
    respx.post(URL).mock(return_value=httpx.Response(200, json=ok_body(content="")))
    with pytest.raises(LLMError, match="empty") as e:
        await make_client(KIMI, timeout_s=5).complete("s", "u")
    assert e.value.billable  # HTTP 200: the provider ran and charged


@respx.mock
@pytest.mark.parametrize("resp", [httpx.Response(200, text="<html>proxy</html>"),
                                  httpx.Response(200, json={"choices": [{"message": None}]}),
                                  httpx.Response(200, json={"choices": []})])
async def test_unusable_200_is_billable_llm_error(resp):
    respx.post(URL).mock(return_value=resp)
    with pytest.raises(LLMError, match="malformed") as e:
        await make_client(KIMI, timeout_s=5).complete("s", "u")
    assert e.value.billable


async def test_fake_llm_cycles_replies():
    f = FakeLLM(["a", "b"])
    assert [(await f.complete("s", "u")).text for _ in range(3)] == ["a", "b", "a"]
    assert f.calls == [("s", "u")] * 3


async def test_fake_llm_callable():
    f = FakeLLM(lambda s, u: u.upper())
    assert (await f.complete("s", "hi")).text == "HI"


class StubMessages:
    def __init__(self, resp):
        self.resp, self.kwargs = resp, None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return self.resp


def anthropic_resp(stop="end_turn", text='{"x":1}'):
    return SimpleNamespace(
        stop_reason=stop, stop_details=None,
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=10, cache_read_input_tokens=90,
                              cache_creation_input_tokens=0, output_tokens=7))


CLAUDE = ModelCfg("claude", "anthropic", None, "claude-opus-5-5", "ANTHROPIC_API_KEY",
                  4.0, 0.2, 20.0, 0, True)


async def test_anthropic_usage_and_text():
    stub = StubMessages(anthropic_resp())
    c = AnthropicClient(CLAUDE, timeout_s=5,
                        sdk=SimpleNamespace(beta=SimpleNamespace(messages=stub)))
    r = await c.complete("sys", "user")
    assert r.text == '{"x":1}'
    assert r.usage == Usage(100, 90, 7)
    assert stub.kwargs["fallbacks"] == "default"
    assert stub.kwargs["system"] == "sys"


async def test_anthropic_refusal_raises():
    stub = StubMessages(anthropic_resp(stop="refusal"))
    c = AnthropicClient(CLAUDE, timeout_s=5,
                        sdk=SimpleNamespace(beta=SimpleNamespace(messages=stub)))
    with pytest.raises(LLMError, match="refusal") as e:
        await c.complete("s", "u")
    assert e.value.billable


@respx.mock
async def test_timeout_error_is_billable():
    respx.post(URL).mock(side_effect=httpx.ReadTimeout("t"))
    with pytest.raises(LLMError) as e:
        await make_client(KIMI, timeout_s=5).complete("s", "u")
    assert e.value.billable


@respx.mock
async def test_http_error_not_billable():
    respx.post(URL).mock(return_value=httpx.Response(500))
    with pytest.raises(LLMError) as e:
        await make_client(KIMI, timeout_s=5).complete("s", "u")
    assert not e.value.billable
