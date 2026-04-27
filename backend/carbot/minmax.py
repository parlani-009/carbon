import json
from typing import Any, Callable

import anthropic
import os

client = anthropic.Anthropic(
    base_url=os.environ['ANTHROPIC_BASE_URL'],
    api_key=os.environ['ANTHROPIC_API_KEY'],
)

def get_response(messages, tools=None):
    """
    Send messages and tools to MiniMax-M2 and return the response.

    Args:
        messages: list of message dicts with 'role' and 'content'
        tools: list of tool definitions (optional)

    Returns:
        response object from the model
    """
    return client.messages.create(
        model="MiniMax-M2",
        max_tokens=4096,
        messages=messages,
        tools=tools or []
    )


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


def _handle_tool_calls(messages: list, tool_blocks: list, execute_tool: Callable | None) -> Any:
    """Execute tool calls and return final response."""
    messages.append({"role": "assistant", "content": [
        {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
        for b in tool_blocks
    ]})

    tool_results = [
        {
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": execute_tool(block.input) if execute_tool else json.dumps(block.input)
        }
        for block in tool_blocks
    ]

    messages.append({"role": "user", "content": tool_results})
    return get_response(messages)


def structured_llm(
    prompt: str,
    output_schema: dict,
    system_prompt: str | None = None,
    _tool_name: str = "extract",  # kept for backward compat, unused
    execute_tool: Callable[[dict], str] | None = None
) -> dict:
    """
    Send a prompt to MiniMax-M2 with a required output schema and get structured JSON back.
    Relies on a system prompt instructing the model to return JSON matching the schema.
    Falls back to regex extraction if model returns plain text.

    Args:
        prompt: The user prompt/question
        output_schema: JSON schema for the desired output structure
        system_prompt: Optional system prompt to add context
        tool_name: Name for the tool (default: "extract") — unused, kept for compat
        execute_tool: Optional callback to execute tool calls and continue conversation.

    Returns:
        dict: The structured JSON output from the model
    """
    schema_str = json.dumps(output_schema)

    base_system = (
        f"You must respond ONLY with valid JSON that matches this schema:\n{schema_str}\n"
        "Do not include any text before or after the JSON. No markdown, no explanation."
    )
    if system_prompt:
        system = f"{base_system}\n\n{system_prompt}"
    else:
        system = base_system

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt}
    ]

    response = get_response(messages)

    tool_blocks = [block for block in response.content if block.type == "tool_use"]
    if tool_blocks and execute_tool:
        response = _handle_tool_calls(messages, tool_blocks, execute_tool)

    result = _extract_json_from_response(response)

    # Fallback: if result is just {"text": "long text"}, try to extract JSON from it
    if isinstance(result, dict) and "text" in result and len(result) == 1:
        text = result["text"]
        # Try to find a JSON object/array inside the text
        import re
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

    # Iterate through all content blocks
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
    response = get_response(messages,tools)

    thinking_blocks, text_blocks, tool_use_blocks = process_response(response)

    # 3. If tool calls exist, execute tools and continue conversation
    if tool_use_blocks:
        # ⚠️ Critical: Append the assistant's complete response to message history
        # response.content contains a list of all blocks: [thinking block, text block, tool_use block]
        # Must be fully preserved, otherwise subsequent conversation will lose context
        messages.append({
            "role": "assistant",
            "content": response.content
        })

        # Execute tool and return result (simulating weather API call)
        print(f"\n🔨 Executing tool: {tool_use_blocks[0].name}")
        tool_result = "24℃, sunny"
        print(f"📊 Tool result: {tool_result}")

        # Add tool execution result
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

        # 4. Get final response
        final_response = get_response(messages)
        # process_response(final_response)
        thinking_blocks, text_blocks, tool_use_blocks = process_response(response)

    
    print(text_blocks)