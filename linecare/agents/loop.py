"""The shared tool-calling loop: model -> tool calls -> results -> model, until it answers or hits the cap."""
import time
from collections.abc import Callable

from openai import APIConnectionError, APITimeoutError, InternalServerError, OpenAI, RateLimitError

from linecare import config

# One retry at most (the SDK default is two, each with the full timeout): a stalled model must not hold a turn for
# minutes. The per-turn budget below caps all rounds together.
llm_client = OpenAI(base_url=config.LLM_BASE_URL, api_key="lm-studio", timeout=config.LLM_TIMEOUT_SECONDS,
                    max_retries=1)


class TurnTimeout(Exception):
    """The turn used up TURN_BUDGET_SECONDS; the caller answers with its "technical problem" reply."""


def assistant_message_to_dict(msg) -> dict:
    """An SDK message as a plain dict that can be re-sent."""
    entry = {"role": "assistant", "content": msg.content or ""}
    if msg.tool_calls:
        entry["tool_calls"] = [
            {"id": tc.id, "type": "function",
             "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"}}
            for tc in msg.tool_calls
        ]
    return entry


RETRYABLE = (APIConnectionError, InternalServerError, RateLimitError)   # APITimeoutError is an APIConnectionError


def _complete(deadline: float, **request):
    """One completion within the turn's budget: each attempt gets at most the time left, and one retry (the client's
    max_retries) only while time is left. A timeout at the end of the budget is a TurnTimeout."""
    for attempt in range(llm_client.max_retries + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TurnTimeout(f"no answer within {config.TURN_BUDGET_SECONDS:.0f} s")
        try:
            return llm_client.with_options(timeout=min(config.LLM_TIMEOUT_SECONDS, remaining),
                                           max_retries=0).chat.completions.create(**request)
        except RETRYABLE as exc:
            out_of_time = time.monotonic() >= deadline - 0.05
            if out_of_time and isinstance(exc, APITimeoutError):
                raise TurnTimeout(f"no answer within {config.TURN_BUDGET_SECONDS:.0f} s") from exc
            if out_of_time or attempt == llm_client.max_retries:
                raise


def run_tool_loop(*, model: str, system_prompt: Callable[[], str], tools: list, history: list,
                  execute: Callable[[str, str], str], after_round: Callable[[], None] = None,
                  max_rounds: int = None) -> tuple:
    """Run one user turn. Mutates `history`. Returns (final message, rounds, llm_ms).

    `system_prompt` is called on every round, so state changes from tools (e.g. a verified identity) reach the
    model at once. `after_round` runs after each round of tool calls (used for redaction).
    """
    max_rounds = config.MAX_TOOL_ROUNDS if max_rounds is None else max_rounds
    extra = {"reasoning_effort": config.LLM_REASONING_EFFORT} if config.LLM_REASONING_EFFORT else {}
    msg, rounds, llm_ms = None, 0, 0
    deadline = time.monotonic() + config.TURN_BUDGET_SECONDS
    for _ in range(max_rounds + 1):
        started = time.perf_counter()
        response = _complete(deadline, model=model, tools=tools, temperature=0.2, **extra,
                             messages=[{"role": "system", "content": system_prompt()}] + history)
        llm_ms += int((time.perf_counter() - started) * 1000)
        msg = response.choices[0].message
        if not msg.tool_calls:
            break
        rounds += 1
        history.append(assistant_message_to_dict(msg))
        for tc in msg.tool_calls:
            history.append({"role": "tool", "tool_call_id": tc.id,
                            "content": execute(tc.function.name, tc.function.arguments or "{}")})
        if after_round:
            after_round()
    return msg, rounds, llm_ms


def trim_history(history: list, max_turns: int = config.MAX_HISTORY_TURNS) -> list:
    """Keep the last `max_turns` user turns; cut only before a user message so tool-call pairs stay complete."""
    user_indexes = [i for i, m in enumerate(history) if m.get("role") == "user"]
    if len(user_indexes) <= max_turns:
        return history
    return history[user_indexes[-max_turns]:]
