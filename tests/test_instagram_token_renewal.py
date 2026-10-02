"""Behavioral tests for the weekly Instagram token renewal (renew_instagram_tokens).

Driven through the function the loop calls, with an in-memory repository; the Meta
call and the alerter are mocked. No database, no network.

    python tests/test_instagram_token_renewal.py
"""

import asyncio
import io
import logging
import os
import sys
from collections.abc import Callable
from typing import Any
from unittest.mock import patch

sys.path.insert(0, ".")

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres",
)
os.environ.setdefault("API_KEY", "test-key")

import httpx  # noqa: E402
from sqlalchemy.exc import DBAPIError  # noqa: E402

from app.models.tables import Company  # noqa: E402
from app.repositories.company import CompanyRepository  # noqa: E402
from app.services.instagram_client import InstagramClient  # noqa: E402
from app.services.instagram_token_renewal import renew_instagram_tokens  # noqa: E402

Alerts = list[tuple[str, int | None]]

# named, never inline where a call may raise: a traceback quotes the source line
OLD_TOKEN = "old-token-5"
NEW_TOKEN = "new-token-5"


class FakeRepo:
    """In-memory stand-in for CompanyRepository."""

    def __init__(self, tokens: dict[int, str], failing_saves: tuple[int, ...] = ()) -> None:
        self.tokens = dict(tokens)
        self.failing_saves = failing_saves

    async def list_with_instagram(self) -> list[Company]:
        return [
            Company(id=cid, instagram_account_id=f"ig-{cid}", instagram_access_token=token)
            for cid, token in self.tokens.items()
        ]

    async def update_instagram_access_token(self, company_id: int, access_token: str) -> None:
        if company_id in self.failing_saves:
            raise RuntimeError("save boom")
        self.tokens[company_id] = access_token


async def _renew(repo: FakeRepo, refreshed: dict[str, str]) -> tuple[Alerts, list[str]]:
    """`refreshed` maps old token -> new token; a token missing from it fails at Meta."""
    alerts: Alerts = []
    meta_calls: list[str] = []
    tokens = {*repo.tokens.values(), *refreshed.values()}

    async def _refresh(self: Any, access_token: str) -> str:
        meta_calls.append(access_token)
        if access_token not in refreshed:
            raise httpx.ConnectError("refresh boom")
        return refreshed[access_token]

    def _alert(error_type: str, location: str, error: Any, **kwargs: Any) -> None:
        alert = f"{location} {error} {kwargs}"
        assert not [t for t in tokens if t in alert], "token leaked into the alert"
        alerts.append((error_type, kwargs.get("company_id")))

    with patch(
        "app.services.instagram_client.InstagramClient.refresh_access_token", _refresh
    ), patch("app.services.instagram_token_renewal.send_critical_alert", _alert):
        await renew_instagram_tokens(repo)  # type: ignore[arg-type]
    return alerts, meta_calls


async def case_success_saves_new_token() -> None:
    repo = FakeRepo({5: "old-token-5"})
    alerts, _ = await _renew(repo, {"old-token-5": "new-token-5"})
    assert repo.tokens == {5: "new-token-5"}, repo.tokens
    assert alerts == [], alerts
    print("OK success: the new token is saved and no alert is sent")


async def case_failure_keeps_old_token_and_alerts() -> None:
    repo = FakeRepo({5: "old-token-5"})
    alerts, _ = await _renew(repo, {})
    assert repo.tokens == {5: "old-token-5"}, repo.tokens
    assert alerts == [("INSTAGRAM_TOKEN_RENEWAL_FAILED", 5)], alerts
    print("OK failure: the old token stays and one alert names the Company")


async def case_isolation_one_failure_does_not_stop_the_next() -> None:
    repo = FakeRepo({5: "old-token-5", 6: "old-token-6"})
    alerts, _ = await _renew(repo, {"old-token-6": "new-token-6"})
    assert repo.tokens == {5: "old-token-5", 6: "new-token-6"}, repo.tokens
    assert alerts == [("INSTAGRAM_TOKEN_RENEWAL_FAILED", 5)], alerts
    print("OK isolation: the second Company is renewed and the alert names only the first")


async def case_save_failure_alerts_and_does_not_stop_the_next() -> None:
    repo = FakeRepo({5: "old-token-5", 6: "old-token-6"}, failing_saves=(5,))
    alerts, _ = await _renew(repo, {"old-token-5": "new-token-5", "old-token-6": "new-token-6"})
    assert repo.tokens == {5: "old-token-5", 6: "new-token-6"}, repo.tokens
    assert alerts == [("INSTAGRAM_TOKEN_RENEWAL_FAILED", 5)], alerts
    print("OK save failure: the old token stays, the alert names the Company, the next is renewed")


async def case_nothing_configured_is_a_noop() -> None:
    alerts, meta_calls = await _renew(FakeRepo({}), {})
    assert meta_calls == [], meta_calls
    assert alerts == [], alerts
    print("OK nothing configured: no call to Meta and no alert")


async def _refresh_over_http(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[str | Exception, str]:
    """Run the real client call on a fake transport; return (token or error, all log output)."""
    real_client = httpx.AsyncClient
    output = io.StringIO()
    capture = logging.StreamHandler(output)
    root = logging.getLogger()
    level = root.level
    root.addHandler(capture)
    root.setLevel(logging.DEBUG)
    result: str | Exception
    try:
        with patch(
            "app.services.instagram_client.httpx.AsyncClient",
            lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
        ):
            try:
                result = await InstagramClient().refresh_access_token(OLD_TOKEN)
            except Exception as err:
                logging.exception("logged the way the renewal logs a failure")
                result = err
    finally:
        root.removeHandler(capture)
        root.setLevel(level)
    return result, output.getvalue()


def _answers(status: int, body: dict[str, Any]) -> Callable[[httpx.Request], httpx.Response]:
    return lambda request: httpx.Response(status, json=body)


async def case_client_exchanges_the_token_without_logging_it() -> None:
    requests: list[httpx.Request] = []

    def _meta(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"access_token": "new-token-5", "token_type": "bearer", "expires_in": 5183944},
        )

    result, logged = await _refresh_over_http(_meta)
    assert result == "new-token-5", result
    assert len(requests) == 1, requests
    assert requests[0].method == "GET"
    assert str(requests[0].url) == (
        "https://graph.instagram.com/refresh_access_token"
        "?grant_type=ig_refresh_token&access_token=old-token-5"
    ), requests[0].url
    assert "refresh_access_token" in logged, logged
    assert OLD_TOKEN not in logged and "new-token-5" not in logged, logged
    print("OK client: the token is exchanged at Meta and neither token reaches a log line")


async def case_client_error_response_does_not_leak_the_token() -> None:
    result, logged = await _refresh_over_http(_answers(400, {}))
    assert isinstance(result, httpx.HTTPStatusError), result
    assert result.response.status_code == 400
    assert OLD_TOKEN not in str(result), str(result)
    assert OLD_TOKEN not in logged, logged
    print("OK client: an error response raises HTTPStatusError without the token in it")


async def case_client_timeout_does_not_leak_the_token() -> None:
    def _timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    result, logged = await _refresh_over_http(_timeout)
    assert isinstance(result, httpx.ReadTimeout), result
    assert OLD_TOKEN not in logged, logged
    print("OK client: a timeout propagates without the token in the log")


async def case_client_response_without_token_fails() -> None:
    bodies: list[dict[str, Any]] = [{}, {"access_token": ""}]
    for body in bodies:
        result, _ = await _refresh_over_http(_answers(200, body))
        assert isinstance(result, Exception), result
    print("OK client: a response without a token raises instead of returning an empty value")


class BrokenSession:
    """A session whose write fails the way SQLAlchemy fails: quoting the parameters."""

    rolled_back = False

    async def execute(self, statement: Any) -> None:
        raise DBAPIError("UPDATE companies ...", (NEW_TOKEN, 5), Exception("db boom"))

    async def rollback(self) -> None:
        self.rolled_back = True


async def case_repository_save_failure_rolls_back_without_leaking_the_token() -> None:
    session = BrokenSession()
    repo = CompanyRepository(session)  # type: ignore[arg-type]
    output = io.StringIO()
    capture = logging.StreamHandler(output)
    logging.getLogger().addHandler(capture)
    try:
        await repo.update_instagram_access_token(5, NEW_TOKEN)
    except Exception as err:
        logging.exception("logged the way the renewal logs a failure")
        raised = str(err)
    else:
        raise AssertionError("the failed save did not raise")
    finally:
        logging.getLogger().removeHandler(capture)
    assert session.rolled_back
    assert NEW_TOKEN not in raised, raised
    assert NEW_TOKEN not in output.getvalue(), output.getvalue()
    print("OK repository: a failed save rolls back and raises without the token in the error")


async def main() -> None:
    await case_success_saves_new_token()
    await case_failure_keeps_old_token_and_alerts()
    await case_isolation_one_failure_does_not_stop_the_next()
    await case_save_failure_alerts_and_does_not_stop_the_next()
    await case_nothing_configured_is_a_noop()
    await case_client_exchanges_the_token_without_logging_it()
    await case_client_error_response_does_not_leak_the_token()
    await case_client_timeout_does_not_leak_the_token()
    await case_client_response_without_token_fails()
    await case_repository_save_failure_rolls_back_without_leaking_the_token()


if __name__ == "__main__":
    asyncio.run(main())
