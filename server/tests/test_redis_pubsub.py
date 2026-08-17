import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.redis_pubsub import consume_pubsub


class ConsumePubsubTests(unittest.IsolatedAsyncioTestCase):
    async def test_resubscribes_after_connection_drop(self) -> None:
        stop = asyncio.Event()
        handled: list[str] = []

        first_pubsub = AsyncMock()
        first_pubsub.get_message = AsyncMock(side_effect=ConnectionError("drop"))
        second_pubsub = AsyncMock()

        async def second_get_message(**_kwargs):
            if handled:
                stop.set()
                return None
            return {"type": "message", "data": "ok"}

        second_pubsub.get_message = AsyncMock(side_effect=second_get_message)

        redis = AsyncMock()
        redis.pubsub = MagicPubsub([first_pubsub, second_pubsub])

        async def handler(message: dict) -> None:
            handled.append(str(message.get("data")))

        with patch(
            "app.redis_pubsub.emit_pubsub_disconnect_event",
            new_callable=AsyncMock,
        ) as emit:
            await consume_pubsub(
                redis,
                ["ticks"],
                component="test",
                handler=handler,
                should_stop=stop.is_set,
            )

        self.assertEqual(handled, ["ok"])
        emit.assert_not_awaited()
        first_pubsub.subscribe.assert_awaited()
        second_pubsub.subscribe.assert_awaited()

    async def test_emits_critical_after_repeated_failures(self) -> None:
        stop = asyncio.Event()
        attempts = 0

        class FailingPubsub:
            async def subscribe(self, *_channels) -> None:
                nonlocal attempts
                attempts += 1
                if attempts >= 3:
                    stop.set()
                raise ConnectionError("down")

            async def unsubscribe(self, *_channels) -> None:
                return None

            async def close(self) -> None:
                return None

            async def get_message(self, **_kwargs):
                return None

        redis = AsyncMock()
        redis.pubsub = lambda: FailingPubsub()

        with patch(
            "app.redis_pubsub.emit_pubsub_disconnect_event",
            new_callable=AsyncMock,
        ) as emit:
            with patch("app.redis_pubsub._backoff_sleep", new_callable=AsyncMock):
                await consume_pubsub(
                    redis,
                    ["ticks"],
                    component="position_monitor",
                    handler=AsyncMock(),
                    should_stop=stop.is_set,
                )

        emit.assert_awaited()
        self.assertGreaterEqual(attempts, 3)


class MagicPubsub:
    def __init__(self, clients: list[AsyncMock]) -> None:
        self._clients = list(clients)

    def __call__(self) -> AsyncMock:
        return self._clients.pop(0)


if __name__ == "__main__":
    unittest.main()
