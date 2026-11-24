"""The "wants a person" gate: on every customer turn, in parallel with the agent, a calibrated check whether the
customer asks for a human colleague. Inverted (p(yes) >= 0.5 is enough): when the agent did not hand over by itself,
the code does. It can only add a handover, never prevent one."""
import json
import math

import httpx
import pytest

from linecare import config, db, decisions
from linecare.agents.customer import HANDOVER_REPLY, run_customer_turn
from linecare.decide import Calibration, Decider
from linecare.session import HANDED_OVER


class GateModel:
    """Answers the gate's requests with queued first-token probabilities, or an HTTP status code."""

    def __init__(self):
        self.answers = []
        self.requests = []

    def handle(self, request):
        body = json.loads(request.content)
        self.requests.append(body)
        answer = self.answers.pop(0) if self.answers else {"N": 0.99, "Y": 0.01}
        if isinstance(answer, int):
            return httpx.Response(answer)
        top = [{"token": t, "logprob": math.log(p)} for t, p in answer.items()]
        return httpx.Response(200, json={"choices": [{"message": {"content": top[0]["token"]},
                                                       "logprobs": {"content": [{"top_logprobs": top}]}}]})


@pytest.fixture
def gate(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DECISION_GATE", True)
    question = decisions.WANTS_HUMAN
    Calibration(question.id, config.MODEL_NAME, question.prompt_hash(), 1.0, 30, "2026-11-24", {}, {}).save(tmp_path)
    model = GateModel()
    monkeypatch.setattr(decisions, "decider", lambda model_name=None: Decider(
        config.LLM_BASE_URL, model_name or config.MODEL_NAME, calibration_dir=tmp_path,
        transport=httpx.MockTransport(model.handle)))
    return model


def handover_count():
    with db.connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM handovers").fetchone()[0]


def test_the_code_hands_over_when_the_agent_did_not(new_session, fake_lm, gate):
    session = new_session()
    fake_lm.script(reply="I can help with that myself. What is your account number?")
    gate.answers.append({"Y": 0.97, "N": 0.03})
    before = handover_count()

    result = run_customer_turn(session, "I want to talk to a real person.", config.MODEL_NAME)

    assert session.state == HANDED_OVER and handover_count() == before + 1
    assert result.reply == HANDOVER_REPLY["en"].format(position=result.tool_calls[-1]["result"]["queue_position"])
    assert result.tool_calls[-1]["name"] == "handover_to_human"
    assert result.tool_calls[-1]["by"] == "wants_human_gate"
    assert result.tool_calls[-1]["arguments"]["reason"] == "customer_request"   # not a repeat caller
    assert session.decisions[-1]["question_id"] == "linecare.wants_human"
    assert session.decisions[-1]["policy_action"] == "handover"
    assert session.history[-1] == {"role": "assistant", "content": result.reply}


def test_a_repeat_caller_handed_over_by_the_gate_keeps_the_high_priority(verified, fake_lm, gate):
    # E2E (Gemma, s9-repeat-escalates): the gate handed over with reason customer_request -> normal priority,
    # where the agent would have used repeat_contact.
    session = verified("QN-100009")
    assert session.repeat_categories
    fake_lm.script(reply="Would you like me to run some diagnostics first?")
    gate.answers.append({"Y": 0.8, "N": 0.2})
    result = run_customer_turn(session, "Yes, please.", config.MODEL_NAME)
    handover = result.tool_calls[-1]
    assert handover["arguments"]["reason"] == "repeat_contact" and handover["result"]["priority"] == "high"


def test_the_handover_reply_follows_the_message_language(new_session, fake_lm, gate):
    session = new_session()
    fake_lm.script(reply="Мога да помогна и аз.")
    gate.answers.append({"Y": 0.9, "N": 0.1})
    result = run_customer_turn(session, "Искам да говоря с човек.", config.MODEL_NAME)
    assert result.reply.startswith("Свързвам ви с колега.")


def test_no_second_handover_when_the_agent_already_handed_over(new_session, fake_lm, gate):
    session = new_session()
    fake_lm.script(("handover_to_human", {"reason": "customer_request"}), reply="A colleague will take over.")
    gate.answers.append({"Y": 0.99, "N": 0.01})
    before = handover_count()

    result = run_customer_turn(session, "Operator, please.", config.MODEL_NAME)

    assert handover_count() == before + 1
    assert result.reply == "A colleague will take over."
    assert [c.get("by") for c in result.tool_calls] == [None]
    assert session.decisions[-1]["policy_action"] == "none"


@pytest.mark.parametrize("answer, handed_over", [({"Y": 0.5, "N": 0.5}, True), ({"Y": 0.49, "N": 0.51}, False)])
def test_the_threshold_is_one_half(new_session, fake_lm, gate, answer, handed_over):
    session = new_session()
    fake_lm.script(reply="Sure.")
    gate.answers.append(answer)
    run_customer_turn(session, "hm, a person maybe?", config.MODEL_NAME)
    assert (session.state == HANDED_OVER) is handed_over


def test_a_no_leaves_the_turn_as_it_was(new_session, fake_lm, gate):
    session = new_session()
    fake_lm.script(reply="Please tell me your account number.")
    result = run_customer_turn(session, "My internet is down.", config.MODEL_NAME)
    assert result.reply == "Please tell me your account number."
    assert session.state != HANDED_OVER and not result.tool_calls
    assert session.decisions[-1]["policy_action"] == "none"


def test_no_usable_answer_never_hands_over(new_session, fake_lm, gate):
    session = new_session()
    fake_lm.script(reply="Please tell me your account number.")
    gate.answers.append(500)
    result = run_customer_turn(session, "I want a human.", config.MODEL_NAME)
    assert session.state != HANDED_OVER and result.reply == "Please tell me your account number."
    assert session.decisions[-1]["chosen"] is None


def test_the_question_sees_the_agents_previous_message(new_session, fake_lm, gate):
    session = new_session()
    fake_lm.script(reply="Shall I connect you with a colleague?")
    run_customer_turn(session, "This is taking too long.", config.MODEL_NAME)
    fake_lm.script(reply="OK.")
    gate.answers.append({"Y": 0.95, "N": 0.05})
    run_customer_turn(session, "yes please", config.MODEL_NAME)

    first, second = (r["messages"][1]["content"] for r in gate.requests)
    assert "(none: this is the customer's first message)" in first
    assert "The agent's previous message: Shall I connect you with a colleague?" in second
    assert second.endswith("<<input>>\nyes please\n<</input>>")
    assert session.state == HANDED_OVER


def test_not_asked_when_switched_off_or_already_handed_over(new_session, fake_lm, gate, monkeypatch):
    session = new_session()
    session.state = HANDED_OVER
    fake_lm.script(reply="A colleague is on the way.")
    run_customer_turn(session, "Hello?", config.MODEL_NAME)
    monkeypatch.setattr(config, "DECISION_GATE", False)
    fake_lm.script(reply="Hi.")
    run_customer_turn(new_session(), "I want a person.", config.MODEL_NAME)
    assert gate.requests == []


def test_previous_agent_message_is_shortened_and_skips_tool_rounds():
    history = [{"role": "assistant", "content": "x " * 300},
               {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
               {"role": "tool", "content": "{}"}]
    text = decisions.previous_agent_message(history)
    assert len(text) == 403 and text.endswith("...")
    assert decisions.previous_agent_message([]) == "(none: this is the customer's first message)"
