"""A 429 is a wait, not a failure.

An instance throttles its upload route — the shipped nginx is `rate=5r/m` with `burst=3` — and
pushing a group of layers is exactly the burst that trips it. Before this, the fourth layer of a
fourteen-layer push raised `HTTP 429: HTTP 429`, the push stopped there, and the user had to press
the button again to get the rest. That is the client asking a person to do a computer's job.

**Retrying a POST is safe HERE and would not be in general**, which is the point worth pinning: the
rejection happens at the front door, before the request is proxied to the application at all, so
nothing was created and nothing was half-done. These tests are as much about that boundary — what
is and is not retried — as about the waiting.
"""
import pytest

from geodeploy.client import Client
from geodeploy.errors import RateLimited
from geodeploy.transport import Response


class FakeTransport:
    """Answers with a scripted list of statuses, recording what it was asked."""

    def __init__(self, statuses, headers=None):
        self.statuses = list(statuses)
        # The content type matters: without it the client hands back raw text, which is correct
        # behaviour and would quietly make these assertions test the wrong thing.
        self.headers = dict({"Content-Type": "application/json"}, **(headers or {}))
        self.sent = []

    def send(self, request):
        self.sent.append(request)
        status = self.statuses.pop(0) if self.statuses else 200
        body = b'{"ok": true}' if status < 400 else b'{"detail": "slow down"}'
        return Response(status, dict(self.headers), body, request.url)


@pytest.fixture(autouse=True)
def no_sleeping(monkeypatch):
    """The waits are real seconds; the tests are not going to spend them."""
    slept = []
    monkeypatch.setattr("geodeploy.client._time.sleep", lambda s: slept.append(s))
    return slept


def client(transport, **kw):
    return Client("https://example.invalid", token="t", transport=transport, **kw)


class TestItWaitsAndTriesAgain:

    def test_a_429_then_a_200_is_a_success(self, no_sleeping):
        transport = FakeTransport([429, 200])
        assert client(transport).post("/data/vector/upload", {"a": 1}) == {"ok": True}
        assert len(transport.sent) == 2
        assert no_sleeping, "it should have waited before trying again"

    def test_several_429s_are_all_waited_out(self, no_sleeping):
        transport = FakeTransport([429, 429, 429, 200])
        assert client(transport).post("/x") == {"ok": True}
        assert len(transport.sent) == 4

    def test_the_waits_grow_and_are_capped(self, no_sleeping):
        transport = FakeTransport([429] * 6 + [200])
        client(transport, rate_limit_retries=6).post("/x")
        assert no_sleeping == sorted(no_sleeping), "a backoff that shrinks is not a backoff"
        assert max(no_sleeping) <= 60, "a wait nobody can see the end of reads as a hang"

    def test_the_first_wait_clears_the_shipped_limit(self, no_sleeping):
        """`rate=5r/m` is one request every twelve seconds, so a shorter first wait just earns
        another 429."""
        client(FakeTransport([429, 200])).post("/x")
        assert no_sleeping[0] >= 12


class TestItGivesUpEventually:

    def test_it_raises_RateLimited_rather_than_a_bare_APIError(self, no_sleeping):
        """The user saw "HTTP 429: HTTP 429" — a status echoed twice and no idea what to do."""
        with pytest.raises(RateLimited):
            client(FakeTransport([429] * 10), rate_limit_retries=2).post("/x")

    def test_it_stops_after_the_configured_number_of_tries(self, no_sleeping):
        transport = FakeTransport([429] * 10)
        with pytest.raises(RateLimited):
            client(transport, rate_limit_retries=3).post("/x")
        assert len(transport.sent) == 4, "three retries after the first attempt"

    def test_retrying_can_be_switched_off(self, no_sleeping):
        transport = FakeTransport([429, 200])
        with pytest.raises(RateLimited):
            client(transport, rate_limit_retries=0).post("/x")
        assert len(transport.sent) == 1
        assert not no_sleeping


class TestItObeysTheServer:

    def test_retry_after_beats_the_backoff(self, no_sleeping):
        client(FakeTransport([429, 200], {"Retry-After": "7"})).post("/x")
        assert no_sleeping == [7.0]

    def test_a_nonsense_retry_after_falls_back_to_the_backoff(self, no_sleeping):
        client(FakeTransport([429, 200], {"Retry-After": "next tuesday"})).post("/x")
        assert no_sleeping and no_sleeping[0] >= 12

    def test_an_absurd_retry_after_is_capped(self, no_sleeping):
        """A misconfigured limiter must not park the client for an hour."""
        client(FakeTransport([429, 200], {"Retry-After": "99999"})).post("/x")
        assert no_sleeping == [300.0]

    def test_the_error_carries_the_wait_for_a_caller_that_wants_it(self, no_sleeping):
        with pytest.raises(RateLimited) as raised:
            client(FakeTransport([429], {"Retry-After": "42"}), rate_limit_retries=0).post("/x")
        assert raised.value.retry_after == 42.0


class TestEverythingElseIsUnchanged:

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422, 500, 503])
    def test_no_other_status_is_retried(self, status, no_sleeping):
        """A 500 might be retried safely; a 409 certainly must not. Neither is this change's
        business — widening it silently would be a much bigger promise than "waiting works"."""
        transport = FakeTransport([status, 200])
        with pytest.raises(Exception):
            client(transport).post("/x")
        assert len(transport.sent) == 1
        assert not no_sleeping

    def test_a_plain_success_sends_once(self, no_sleeping):
        transport = FakeTransport([200])
        assert client(transport).get("/x") == {"ok": True}
        assert len(transport.sent) == 1


class TestABodyThatCannotBeResent:

    def test_a_rewindable_body_is_rewound(self, no_sleeping):
        """A file-like body is CONSUMED by the first attempt. Sending it again without rewinding
        would upload zero bytes — a silently truncated file is far worse than a 429."""
        import io as _io
        body = _io.BytesIO(b"some data")
        body.read(4)                                  # as a real attempt would leave it
        transport = FakeTransport([429, 200])
        client(transport).request("PUT", "/x", body=body)
        assert len(transport.sent) == 2
        assert body.tell() == 0, "the body was not rewound before the retry"

    def test_a_body_that_cannot_rewind_is_not_retried(self, no_sleeping):
        class OneShot:
            def read(self, *_a):
                return b""
        transport = FakeTransport([429, 200])
        with pytest.raises(RateLimited):
            client(transport).request("PUT", "/x", body=OneShot())
        assert len(transport.sent) == 1, "better a clear 429 than a truncated upload"

    def test_bytes_are_always_resendable(self, no_sleeping):
        transport = FakeTransport([429, 200])
        client(transport).request("POST", "/x", body=b"literal bytes")
        assert len(transport.sent) == 2
