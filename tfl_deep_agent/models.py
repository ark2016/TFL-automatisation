"""Chat model factory for the deep agent.

``make_chat_model`` returns a ``langchain_anthropic.ChatAnthropic`` whose
outgoing ``/v1/messages`` request has the same shape as the pipelines'
requests built by ``agent_system.lib.llm_client.AnthropicClient``:

* thinking models (every 5.x model, Haiku 5.5 included): ``thinking =
  {"type": "adaptive"}`` and an explicit ``output_config.effort``; no
  ``temperature``;
* Opus 5.x with ``REFUSAL_FALLBACK``: header ``anthropic-beta:
  server-side-fallback-2026-07-01`` and body field ``fallbacks: "default"``;
* legacy models (Haiku 4.5): ``temperature`` in the body, no thinking;
* ``max_tokens`` clamped to the model's output cap.

The shape is taken from ``AnthropicClient.build_request_kwargs`` itself, not
re-implemented, so a change to the pipelines' request kwargs reaches the agent
too. ``tests/test_models_request_shape.py`` pins the result on the wire.

Calls are streamed (``streaming=True``), like ``AnthropicClient.call``: with a
64000-token ceiling the SDK refuses non-streaming requests.
"""

from __future__ import annotations

from typing import Any

from agent_system.lib.llm_client import AnthropicClient

from . import config


def request_kwargs(
    model: str, max_tokens: int, effort: str, *, temperature: float | None = None,
) -> dict[str, Any]:
    """The pipelines' request kwargs for one call (``system`` / ``messages``
    dropped: ChatAnthropic builds those from the messages it is given)."""
    client = AnthropicClient(
        default_effort=config.DEFAULT_EFFORT,
        refusal_fallback=config.REFUSAL_FALLBACK,
    )
    kwargs = client.build_request_kwargs(
        model, max_tokens, "", "", effort=effort,
        temperature=config.TEMPERATURE if temperature is None else temperature,
        cache_system=False,
    )
    kwargs.pop("system", None)
    kwargs.pop("messages", None)
    return kwargs


def chat_model_params(
    model_id: str | None = None,
    role: str | None = "main",
    max_tokens: int | None = None,
    effort: str | None = None,
    *,
    temperature: float | None = None,
) -> dict[str, Any]:
    """Constructor arguments for ``ChatAnthropic`` (no client is built).

    Resolution: model ``TFL_MODEL_OVERRIDE`` > ``model_id`` > role default;
    effort ``effort`` > role default; max_tokens ``max_tokens`` > role
    default, clamped. See ``config.resolve_*``.
    """
    model = config.resolve_model(role, model_id)
    eff = config.resolve_effort(role, effort)
    mt = config.resolve_max_tokens(role, model, max_tokens)
    kw = request_kwargs(model, mt, eff, temperature=temperature)

    params: dict[str, Any] = {
        "model": kw.pop("model"),
        "max_tokens": kw.pop("max_tokens"),
        "streaming": True,
    }
    thinking = kw.pop("thinking", None)
    if thinking is not None:
        # Passed explicitly so langchain-anthropic does not substitute its own
        # default ({"type": "adaptive", "display": "summarized"}).
        params["thinking"] = thinking
    output_config = kw.pop("output_config", None) or {}
    if "effort" in output_config:
        params["effort"] = output_config.pop("effort")
    if output_config:
        params["output_config"] = output_config
    # extra_headers (refusal-fallback beta) and extra_body (``fallbacks``, or the
    # legacy models' ``temperature``, which the 1.x SDK signatures no longer
    # take as a keyword) go through model_kwargs into messages.create(**payload).
    model_kwargs: dict[str, Any] = {}
    for key in ("extra_headers", "extra_body"):
        if key in kw:
            model_kwargs[key] = kw.pop(key)
    if kw:  # a new field in build_request_kwargs: forward it rather than drop it
        model_kwargs.update(kw)
    if model_kwargs:
        params["model_kwargs"] = model_kwargs
    return params


# ChatAnthropic fields that chat_model_params resolves (model, budget-relevant
# max_tokens, thinking / effort shape, refusal-fallback headers and body,
# streaming) plus the sampling parameters that thinking models reject. A
# caller kwarg for any of them, under its field name or its pydantic alias,
# would silently replace the resolved value -- e.g. ``model="claude-opus-5-5"``
# would defeat TFL_MODEL_OVERRIDE, ``max_tokens_to_sample=`` would skip the
# clamp and the budget's worst case. make_chat_model refuses them.
_RESOLVED_FIELDS = (
    "model", "max_tokens", "temperature", "top_k", "top_p", "thinking",
    "output_config", "reasoning_effort", "model_kwargs", "betas", "streaming",
    "disable_streaming",
)


def _reserved_kwargs() -> frozenset[str]:
    from langchain_anthropic import ChatAnthropic

    names: set[str] = {"effort"}
    fields = ChatAnthropic.model_fields
    for name in _RESOLVED_FIELDS:
        names.add(name)
        info = fields.get(name)
        if info is None:
            continue
        for alias in (info.alias, info.validation_alias):
            if isinstance(alias, str):
                names.add(alias)
    return frozenset(names)


def make_chat_model(
    model_id: str | None = None,
    role: str | None = "main",
    max_tokens: int | None = None,
    effort: str | None = None,
    *,
    api_key: str | None = None,
    temperature: float | None = None,
    **chat_kwargs: Any,
):
    """A ``ChatAnthropic`` for ``role`` with the pipelines' request shape.

    ``api_key`` defaults to ``ANTHROPIC_API_KEY`` from the environment (read by
    langchain-anthropic); this module never loads ``.env``. Extra
    ``chat_kwargs`` (``max_retries``, ``default_request_timeout``, ...) go to
    ``ChatAnthropic`` unchanged; a kwarg that would replace a resolved request
    field (model, max_tokens, effort, thinking, model_kwargs, betas, streaming,
    sampling parameters, or any alias of them) raises ``TypeError`` -- pass
    ``model_id`` / ``max_tokens`` / ``effort`` instead. Building the model
    makes no request.
    """
    from langchain_anthropic import ChatAnthropic

    clash = sorted(set(chat_kwargs) & _reserved_kwargs())
    if clash:
        raise TypeError(
            f"make_chat_model: {clash} would override the resolved request "
            "fields; use model_id= / max_tokens= / effort= (TFL_MODEL_OVERRIDE, "
            "the max_tokens clamp and the refusal fallback stay in force)"
        )
    params = chat_model_params(
        model_id, role, max_tokens, effort, temperature=temperature,
    )
    params.update(chat_kwargs)
    if api_key is not None:
        params["api_key"] = api_key
    return ChatAnthropic(**params)
