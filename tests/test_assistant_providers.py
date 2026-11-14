import io
import json
from unittest import mock

import anthropic
import httpx2
import pytest

from apps.assistant.models import EMBEDDING_DIMENSIONS
from apps.assistant.providers import ChatRequest, ProviderError, ToolSpec
from apps.assistant.providers.anthropic import FALLBACK_BETA, AnthropicLLM
from apps.assistant.providers.fake import FakeEmbeddings
from apps.assistant.providers.voyage import VoyageEmbeddings

# --- fake embeddings ---------------------------------------------------------------


def cosine(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_fake_embeddings_deterministic_normalised():
    emb = FakeEmbeddings()
    a, b = emb.embed_documents(["Manicure 35 GEL", "Manicure 35 GEL"])
    assert a == b
    assert len(a) == EMBEDDING_DIMENSIONS
    assert cosine(a, a) == pytest.approx(1.0)


def test_fake_embeddings_related_texts_closer():
    emb = FakeEmbeddings()
    query = emb.embed_query("how much is a manicure")
    manicure, haircut = emb.embed_documents(
        ["Service: Manicure\nPrice: 35 GEL", "Service: Haircut\nPrice: 40 GEL"]
    )
    assert cosine(query, manicure) > cosine(query, haircut)


def test_fake_embeddings_georgian():
    emb = FakeEmbeddings()
    query = emb.embed_query("მანიკური რა ღირს?")
    manicure, haircut = emb.embed_documents(["მანიკური 35 ლარი", "თმის შეჭრა 40 ლარი"])
    assert cosine(query, manicure) > cosine(query, haircut)


# --- Claude provider (real SDK tool runner, mocked HTTP) ---------------------------


def message(content, stop_reason):
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


class FakeAPI:
    """Replays canned Messages API responses and records the requests."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        status, body = self.responses.pop(0)
        return httpx2.Response(status, json=body)

    def body(self, i: int) -> dict:
        return json.loads(self.requests[i].content)

    def client(self) -> anthropic.Anthropic:
        return anthropic.Anthropic(
            api_key="test-key",
            max_retries=0,
            http_client=anthropic.DefaultHttpxClient(
                transport=httpx2.MockTransport(self)
            ),
        )


def chat_request(func=None) -> ChatRequest:
    schema = {
        "type": "object",
        "properties": {"service_id": {"type": "integer"}},
        "required": ["service_id"],
        "additionalProperties": False,
    }
    tools = (
        [ToolSpec("find_free_slots", "Find free times.", schema, func)] if func else []
    )
    return ChatRequest(
        system="You are the salon assistant.",
        messages=[{"role": "user", "content": "When can I get a haircut?"}],
        tools=tools,
    )


def test_claude_runs_tool_loop_with_expected_parameters(settings):
    settings.ASSISTANT_LLM_MODEL = "claude-opus-5-5"
    calls = []

    def find_free_slots(service_id: int) -> str:
        calls.append(service_id)
        return '{"free_times": ["Monday 10:00"]}'

    api = FakeAPI(
        (
            200,
            message(
                [
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "find_free_slots",
                        "input": {"service_id": 7},
                    }
                ],
                "tool_use",
            ),
        ),
        (
            200,
            message(
                [{"type": "text", "text": "Monday at 10:00 is free [1]."}], "end_turn"
            ),
        ),
    )
    result = AnthropicLLM(client=api.client()).answer(chat_request(find_free_slots))

    assert result.text == "Monday at 10:00 is free [1]."
    assert not result.refused
    assert calls == [7]
    first = api.body(0)
    assert first["model"] == "claude-opus-5-5"
    assert first["system"] == "You are the salon assistant."
    assert first["output_config"] == {"effort": "low"}
    assert first["fallbacks"] == "default"
    assert "thinking" not in first  # always on for this model; never disabled
    assert first["tools"][0]["strict"] is True
    assert first["tools"][0]["input_schema"]["additionalProperties"] is False
    assert FALLBACK_BETA in api.requests[0].headers["anthropic-beta"]
    tool_result = api.body(1)["messages"][-1]["content"][0]
    assert (
        tool_result["type"] == "tool_result"
        and "Monday 10:00" in tool_result["content"]
    )


def test_claude_refusal_reported():
    api = FakeAPI((200, message([], "refusal")))
    result = AnthropicLLM(client=api.client()).answer(chat_request())
    assert result.refused and result.text == ""


@pytest.mark.parametrize("status", [429, 500, 400])
def test_claude_api_errors_become_provider_error(status):
    api = FakeAPI(
        (status, {"type": "error", "error": {"type": "x", "message": "boom"}})
    )
    with pytest.raises(ProviderError):
        AnthropicLLM(client=api.client()).answer(chat_request())


def test_claude_connection_error():
    def broken(request):
        raise httpx2.ConnectError("down")

    client = anthropic.Anthropic(
        api_key="k",
        max_retries=0,
        http_client=anthropic.DefaultHttpxClient(
            transport=httpx2.MockTransport(broken)
        ),
    )
    with pytest.raises(ProviderError):
        AnthropicLLM(client=client).answer(chat_request())


# --- Voyage embeddings (mocked HTTP) -----------------------------------------------


def voyage_response(n, offset=0):
    data = [
        {"index": i, "embedding": [float(offset + i)] * 3} for i in reversed(range(n))
    ]
    return io.BytesIO(json.dumps({"data": data}).encode())


def test_voyage_payload_and_order():
    emb = VoyageEmbeddings(api_key="vk", model="voyage-3.5")
    with mock.patch("urllib.request.urlopen") as urlopen:
        urlopen.return_value.__enter__.return_value = voyage_response(2)
        vectors = emb.embed_documents(["a", "b"])
    request = urlopen.call_args.args[0]
    body = json.loads(request.data)
    assert body == {
        "input": ["a", "b"],
        "model": "voyage-3.5",
        "input_type": "document",
        "output_dimension": EMBEDDING_DIMENSIONS,
    }
    assert request.headers["Authorization"] == "Bearer vk"
    assert vectors == [[0.0] * 3, [1.0] * 3]  # sorted by index


def test_voyage_query_input_type_and_batching():
    emb = VoyageEmbeddings(api_key="vk")
    with mock.patch("urllib.request.urlopen") as urlopen:
        urlopen.return_value.__enter__.side_effect = [
            voyage_response(64),
            voyage_response(6),
        ]
        assert len(emb.embed_documents([str(i) for i in range(70)])) == 70
        assert urlopen.call_count == 2
    with mock.patch("urllib.request.urlopen") as urlopen:
        urlopen.return_value.__enter__.return_value = voyage_response(1)
        emb.embed_query("q")
    assert json.loads(urlopen.call_args.args[0].data)["input_type"] == "query"


def test_voyage_errors(settings):
    settings.VOYAGE_API_KEY = ""
    with pytest.raises(ProviderError, match="not configured"):
        VoyageEmbeddings()
    emb = VoyageEmbeddings(api_key="vk")
    with (
        mock.patch("urllib.request.urlopen", side_effect=TimeoutError),
        pytest.raises(ProviderError),
    ):
        emb.embed_query("q")
