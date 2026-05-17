import json
import re
from typing import Any, Callable

import anthropic
import os

client = anthropic.Anthropic(
    base_url=os.environ['ANTHROPIC_BASE_URL'],
    api_key=os.environ['ANTHROPIC_API_KEY'],
)


def get_response(messages, tools=None, system=None):
    """
    Send messages and tools to MiniMax-M2 and return the response.

    Args:
        messages: list of message dicts with 'role' and 'content'
        tools: list of tool definitions (optional)
        system: system prompt string (optional)

    Returns:
        response object from the model
    """
    kwargs = {
        "model": "MiniMax-M2.7",
        "max_tokens": 4096,
        "messages": messages,
    }
    # FIX 1: Only pass tools if non-empty — empty list can confuse some models
    if tools:
        kwargs["tools"] = tools
    # FIX 2: system must be a top-level param, not a message with role="system"
    if system:
        kwargs["system"] = system

    return client.messages.create(**kwargs)


def _extract_json_from_response(response: Any) -> dict:
    """Extract JSON from response content."""
    text_blocks = [block for block in response.content if block.type == "text"]
    tool_blocks = [block for block in response.content if block.type == "tool_use"]

    if tool_blocks:
        return tool_blocks[0].input

    if text_blocks:
        try:
            return json.loads(text_blocks[0].text)
        except json.JSONDecodeError:
            return {"text": text_blocks[0].text}

    return {}


def _handle_tool_calls(messages: list, response: Any, tools: list, execute_tool: Callable | None) -> Any:
    """Execute tool calls and return final response.

    FIX 3: Accept the full response object and append response.content as-is
    to preserve thinking blocks and avoid context loss.
    """
    tool_blocks = [block for block in response.content if block.type == "tool_use"]

    # Append the full assistant response (preserves thinking blocks too)
    messages.append({"role": "assistant", "content": response.content})

    tool_results = [
        {
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": execute_tool(block.input) if execute_tool else json.dumps(block.input)
        }
        for block in tool_blocks
    ]

    messages.append({"role": "user", "content": tool_results})
    return get_response(messages, tools=tools)


def structured_llm(
    prompt: str,
    output_schema: dict,
    system_prompt: str | None = None,
    tools: list | None = None,           # FIX 4: expose tools so callers can pass them
    _tool_name: str = "extract",
    execute_tool: Callable[[dict], str] | None = None
) -> dict:
    """
    Send a prompt to MiniMax-M2 with a required output schema and get structured JSON back.

    Args:
        prompt: The user prompt/question
        output_schema: JSON schema for the desired output structure
        system_prompt: Optional system prompt to add context
        tools: Optional list of tool definitions to enable tool calling
        execute_tool: Optional callback to execute tool calls and continue conversation.

    Returns:
        dict: The structured JSON output from the model
    """
    schema_str = json.dumps(output_schema)

    base_system = (
        f"You must respond ONLY with valid JSON that matches this schema:\n{schema_str}\n"
        "Do not include any text before or after the JSON. No markdown, no explanation."
    )
    system = f"{base_system}\n\n{system_prompt}" if system_prompt else base_system

    # FIX 2: Don't put system in messages — pass it as a top-level param
    messages = [{"role": "user", "content": prompt}]

    response = get_response(messages, tools=tools, system=system)

    tool_blocks = [block for block in response.content if block.type == "tool_use"]
    if tool_blocks and execute_tool:
        # FIX 3: pass the full response, not just tool_blocks
        response = _handle_tool_calls(messages, response, tools=tools or [], execute_tool=execute_tool)

    result = _extract_json_from_response(response)

    # Fallback: if result is {"text": "..."}, try to pull a JSON object out of it
    if isinstance(result, dict) and "text" in result and len(result) == 1:
        text = result["text"]
        json_match = re.search(r'\{[\s\S]*\}|\[[\s\S]*\]', text)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass

    return result


def process_response(response):
    thinking_blocks = []
    text_blocks = []
    tool_use_blocks = []

    for block in response.content:
        if block.type == "thinking":
            thinking_blocks.append(block)
            print(f"💭 Thinking>\n{block.thinking}\n")
        elif block.type == "text":
            text_blocks.append(block)
            print(f"💬 Model>\t{block.text}")
        elif block.type == "tool_use":
            tool_use_blocks.append(block)
            print(f"🔧 Tool>\t{block.name}({json.dumps(block.input, ensure_ascii=False)})")

    return thinking_blocks, text_blocks, tool_use_blocks


def llm():
    messages = [{"role": "user", "content": "How's the weather in San Francisco?"}]
    tools = [
        {
            "name": "get_weather",
            "description": "Get weather of a location, the user should supply a location first.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "The city and state, e.g. San Francisco, US",
                    }
                },
                "required": ["location"]
            }
        }
    ]
    print(f"\n👤 User>\t {messages[0]['content']}")
    response = get_response(messages, tools=tools)

    thinking_blocks, text_blocks, tool_use_blocks = process_response(response)

    if tool_use_blocks:
        # FIX 3: append response.content as-is to preserve all blocks
        messages.append({"role": "assistant", "content": response.content})

        print(f"\n🔨 Executing tool: {tool_use_blocks[0].name}")
        tool_result = "24℃, sunny"
        print(f"📊 Tool result: {tool_result}")

        messages.append({
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_blocks[0].id,
                    "content": tool_result
                }
            ]
        })

        final_response = get_response(messages, tools=tools)
        # FIX 5: process final_response, not the original response
        thinking_blocks, text_blocks, tool_use_blocks = process_response(final_response)

    print(text_blocks)