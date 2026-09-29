"""Agent utility helpers used across agent modules."""
import json


def serialize_agent_messages(messages) -> str:
    """Serialize agent chat history into plain-text format for LLM prompts."""
    serialized = []
    for message in messages or []:
        role = getattr(message, "role", None)
        content = getattr(message, "content", None)
        if not isinstance(role, str) or not isinstance(content, str):
            continue
        serialized.append(f"[{role}]: {content}")
    return "\n".join(serialized)


def json_input(value) -> str:
    """Serialize a value to JSON string, tolerating non-JSON-native values."""
    return json.dumps(value, default=str)


def drain_generator(gen):
    """Run a generator to completion, discarding what it yields; return its return value."""
    while True:
        try:
            next(gen)
        except StopIteration as stop:
            return stop.value
