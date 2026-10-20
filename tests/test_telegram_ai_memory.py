import time

from logic import telegram_ai as T


def setup_function(_):
    T._chat_turns.clear()


def test_recent_turns_come_back_oldest_first_and_per_chat():
    T._remember(1, "bounce port 12 on sw", "<b>Bouncing</b> gi12")
    T._remember(2, "other chat", "x")
    got = T._recall(1)
    assert got == [{"role": "user", "text": "bounce port 12 on sw"},
                   {"role": "assistant", "text": "Bouncing gi12"}]
    assert T._recall(None) == []


def test_turns_expire_and_are_capped():
    for i in range(20):
        T._remember(1, f"q{i}", f"a{i}")
    assert len(T._recall(1)) == T._CONVO_MAX_TURNS
    T._chat_turns[1] = [(time.time() - T._CONVO_TTL_SECONDS - 5, "user", "old")]
    assert T._recall(1) == []


def test_promise_only_replies_are_recognised():
    assert T._PROMISE_RE.match("Let me check the recent operation history logs to see which port.")
    assert T._PROMISE_RE.match("I'll look at the logs for that switch.")
    assert not T._PROMISE_RE.match("Ziad's switch is on gi12.")
    assert not T._PROMISE_RE.match("There are 3 updates pending.")
