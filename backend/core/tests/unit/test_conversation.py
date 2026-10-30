"""Offline tests for LangGraph turn streaming and checkpointed conversation state."""

import unittest

from langgraph.checkpoint.memory import InMemorySaver

from backend.core.agent.conversation import ConversationOrchestrator, build_conversation_graph
from backend.core.llm.protocol import ChatMessage


class FakeChatModel:
    def __init__(self) -> None:
        self.received: list[ChatMessage] = []

    async def stream_reply(self, messages):
        self.received = list(messages)
        yield "Hello"
        yield " there."

    async def aclose(self) -> None:
        return None


class FakeLongTermMemory:
    def __init__(self) -> None:
        self.searches: list[tuple[str, str]] = []
        self.writes: list[tuple[str, list[ChatMessage]]] = []

    async def search(self, *, user_id: str, query: str, limit: int = 5) -> list[str]:
        self.searches.append((user_id, query))
        return ["The user prefers concise replies."]

    async def add(self, *, user_id: str, messages: list[ChatMessage]) -> None:
        self.writes.append((user_id, messages))

    async def aclose(self) -> None:
        return None


class ConversationGraphTests(unittest.IsolatedAsyncioTestCase):
    async def test_detected_input_language_does_not_override_requested_output_language(self) -> None:
        model = FakeChatModel()
        graph = build_conversation_graph(InMemorySaver(), model)
        orchestrator = ConversationOrchestrator(graph, model_configured=True)

        _ = [
            event
            async for event in orchestrator.stream_user_turn(
                thread_id="conversation-language-test",
                user_id="user-language-test",
                text="Please answer in Hindi.",
                language="en-US",
            )
        ]

        language_context = next(
            message.content
            for message in model.received
            if message.role == "system" and "Speech recognition detected" in message.content
        )
        self.assertIn("input-language evidence", language_context)
        self.assertIn("explicitly asks you to answer or speak", language_context)
        self.assertIn("If no response language is explicitly requested", language_context)

    async def test_turn_streaming_saves_state_and_scopes_memory_by_user(self) -> None:
        model = FakeChatModel()
        memory = FakeLongTermMemory()
        graph = build_conversation_graph(InMemorySaver(), model, memory)
        orchestrator = ConversationOrchestrator(graph, model_configured=True)

        events = [
            event
            async for event in orchestrator.stream_user_turn(
                thread_id="conversation-test-1",
                user_id="user-test-1",
                text="Give me an update.",
            )
        ]

        self.assertEqual(
            [event for event in events if event["type"] == "assistant.delta"],
            [
                {"type": "assistant.delta", "text": "Hello"},
                {"type": "assistant.delta", "text": " there."},
            ],
        )
        self.assertIn(
            {"type": "conversation.turn_saved", "message_count": 2},
            events,
        )
        self.assertIn({"type": "assistant.completed"}, events)
        self.assertEqual(memory.searches, [("user-test-1", "Give me an update.")])
        self.assertEqual(memory.writes[0][0], "user-test-1")
        self.assertEqual(len(memory.writes[0][1]), 2)
        self.assertIn("Relevant long-term memories", model.received[0].content)

        second_turn = [
            event
            async for event in orchestrator.stream_user_turn(
                thread_id="conversation-test-1",
                user_id="user-test-1",
                text="Thanks.",
            )
        ]

        self.assertIn(
            {"type": "conversation.turn_saved", "message_count": 4},
            second_turn,
        )

    async def test_missing_model_saves_user_turn_and_reports_unavailable(self) -> None:
        graph = build_conversation_graph(InMemorySaver(), model=None)
        orchestrator = ConversationOrchestrator(graph, model_configured=False)

        events = [
            event
            async for event in orchestrator.stream_user_turn(
                thread_id="conversation-test-2",
                user_id="user-test-2",
                text="Hello.",
            )
        ]

        self.assertIn({"type": "conversation.turn_saved", "message_count": 1}, events)
        self.assertIn({"type": "assistant.unavailable", "code": "provider_not_configured"}, events)


if __name__ == "__main__":
    unittest.main()
