import json
import logging
import os
import time
import uuid
from typing import Any

import boto3
import requests
from bedrock_agentcore.runtime import serve_a2a
from model.load import load_model
from memory.session import get_memory_session_manager
from requests.auth import HTTPBasicAuth
from strands import Agent, tool
from strands.multiagent.a2a.executor import StrandsA2AExecutor


logger = logging.getLogger(__name__)


class CognitoTokenProvider:
    def __init__(self) -> None:
        self._access_token: str | None = None
        self._expires_at = 0.0

    def get_token(self) -> str:
        static_token = os.getenv("GATEWAY_ACCESS_TOKEN")
        if static_token:
            return static_token

        if self._access_token and time.time() < self._expires_at:
            return self._access_token

        token_url = os.getenv("COGNITO_TOKEN_URL")
        client_id = os.getenv("COGNITO_CLIENT_ID")
        client_secret = os.getenv("COGNITO_CLIENT_SECRET")
        client_secret_parameter = os.getenv("COGNITO_CLIENT_SECRET_PARAMETER")
        scope = os.getenv("COGNITO_SCOPE")

        if not token_url or not client_id:
            raise RuntimeError(
                "Gateway JWT auth is not configured. Set GATEWAY_ACCESS_TOKEN, "
                "or set COGNITO_TOKEN_URL and COGNITO_CLIENT_ID."
            )

        if not client_secret and client_secret_parameter:
            client_secret = _ssm.get_parameter(
                Name=client_secret_parameter,
                WithDecryption=True,
            )["Parameter"]["Value"]

        data = {"grant_type": "client_credentials"}
        if scope:
            data["scope"] = scope

        auth = HTTPBasicAuth(client_id, client_secret) if client_secret else None
        if not client_secret:
            data["client_id"] = client_id

        response = requests.post(token_url, data=data, auth=auth, timeout=15)
        response.raise_for_status()
        payload = response.json()

        self._access_token = payload["access_token"]
        self._expires_at = time.time() + int(payload.get("expires_in", 3600)) - 60
        return self._access_token


_token_provider = CognitoTokenProvider()
_tool_name_cache: dict[str, str] = {}
_agentcore_runtime = boto3.client("bedrock-agentcore")
_ssm = boto3.client("ssm")


def _gateway_url() -> str:
    gateway_url = os.getenv("AGENTCORE_GATEWAY_URL", "").strip()
    if not gateway_url or gateway_url.startswith("REPLACE_WITH_"):
        raise RuntimeError("Set AGENTCORE_GATEWAY_URL to your AgentCore Gateway MCP endpoint.")
    return gateway_url


def _gateway_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_token_provider.get_token()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _gateway_jsonrpc(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    request_id = uuid.uuid4().hex
    payload: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
    }
    if params is not None:
        payload["params"] = params

    try:
        response = requests.post(_gateway_url(), headers=_gateway_headers(), json=payload, timeout=60)
        response.raise_for_status()
    except Exception:
        logger.exception("Gateway JSON-RPC request failed: method=%s", method)
        raise

    body = response.json()
    if "error" in body:
        raise RuntimeError(body["error"].get("message", json.dumps(body["error"])))
    return body.get("result", {})


def _resolve_gateway_tool_name(preferred_name: str) -> str:
    cached_name = _tool_name_cache.get(preferred_name)
    if cached_name:
        return cached_name

    result = _gateway_jsonrpc("tools/list")
    tools_result = result.get("tools", [])
    tool_names = [item.get("name", "") for item in tools_result if isinstance(item, dict)]

    for name in tool_names:
        if name == preferred_name:
            _tool_name_cache[preferred_name] = name
            return name

    for name in tool_names:
        if name.endswith(preferred_name):
            _tool_name_cache[preferred_name] = name
            return name

    raise RuntimeError(f"Gateway tool {preferred_name!r} was not found. Available tools: {tool_names}")


def _call_gateway_tool(preferred_name: str, arguments: dict[str, Any]) -> Any:
    tool_name = _resolve_gateway_tool_name(preferred_name)
    result = _gateway_jsonrpc(
        "tools/call",
        {
            "name": tool_name,
            "arguments": arguments,
        },
    )

    if result.get("isError"):
        content = result.get("content", [])
        text = content[0].get("text") if content and isinstance(content[0], dict) else result
        raise RuntimeError(str(text))

    content = result.get("content")
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict) and "text" in first:
            return first["text"]

    return result


def _extract_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if hasattr(value, "read"):
        return _extract_text(value.read())
    if isinstance(value, list):
        return "\n".join(part for part in (_extract_text(item) for item in value) if part)
    if isinstance(value, dict):
        text_values = []
        for key in ("text", "content", "result", "message", "artifact", "artifacts", "parts"):
            if key in value:
                text = _extract_text(value[key])
                if text:
                    text_values.append(text)
        if text_values:
            return "\n".join(text_values)
        return json.dumps(value)
    return str(value)


def _invoke_a2a_runtime(runtime_arn_env: str, prompt: str) -> str:
    runtime_arn = os.getenv(runtime_arn_env, "").strip()
    if not runtime_arn or runtime_arn.startswith("REPLACE_WITH_"):
        raise RuntimeError(f"Set {runtime_arn_env} to the collaborator AgentCore Runtime ARN.")

    message_id = str(uuid.uuid4())
    payload = {
        "jsonrpc": "2.0",
        "id": message_id,
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": prompt}],
                "messageId": message_id,
            }
        },
    }

    try:
        response = _agentcore_runtime.invoke_agent_runtime(
            agentRuntimeArn=runtime_arn,
            runtimeSessionId=message_id,
            qualifier=os.getenv("COLLABORATOR_RUNTIME_QUALIFIER", "DEFAULT"),
            payload=json.dumps(payload).encode("utf-8"),
        )
    except Exception:
        logger.exception("Collaborator runtime invocation failed: env=%s arn=%s", runtime_arn_env, runtime_arn)
        raise

    return _extract_text(response.get("response"))


@tool
def searchInWeb(query: str, max_results: int = 5) -> str:
    """Search the public web through the AgentCore Gateway."""
    return str(_call_gateway_tool("searchInWeb", {"query": query, "max_results": max_results}))


@tool
def turnScriptToAudio(script_json: str) -> str:
    """Convert a podcast transcript JSON array to MP3 through the AgentCore Gateway."""
    result = _call_gateway_tool("turnScriptToAudio", {"script_json": script_json})
    if isinstance(result, str):
        return result
    return json.dumps(result)


@tool
def ask_blog_writer(topic: str, research_context: str, constraints: str = "") -> str:
    """Ask the blog writer A2A agent to write a Markdown blog post from supervisor search results."""
    prompt = (
        f"Topic: {topic}\n\n"
        "Research context from supervisor search:\n"
        f"{research_context}\n\n"
        "Constraints:\n"
        f"{constraints or 'No extra constraints.'}"
    )
    return _invoke_a2a_runtime("BLOG_WRITER_RUNTIME_ARN", prompt)


@tool
def ask_speaker_a(
    persona: str,
    topic: str,
    context: str = "",
    previous_turn: str = "",
    closing_turn: bool = False,
) -> str:
    """Ask the Speaker A A2A agent to write one spoken podcast turn."""
    prompt = (
        f"Persona: {persona}\n"
        f"Topic: {topic}\n"
        f"Context: {context or 'No extra context.'}\n"
        f"Previous turn: {previous_turn or ''}\n"
        f"Closing turn: {str(closing_turn).lower()}"
    )
    return _invoke_a2a_runtime("SPEAKER_A_RUNTIME_ARN", prompt)


@tool
def ask_speaker_b(
    persona: str,
    topic: str,
    context: str = "",
    previous_turn: str = "",
    closing_turn: bool = False,
) -> str:
    """Ask the Speaker B A2A agent to write one spoken podcast turn."""
    prompt = (
        f"Persona: {persona}\n"
        f"Topic: {topic}\n"
        f"Context: {context or 'No extra context.'}\n"
        f"Previous turn: {previous_turn or ''}\n"
        f"Closing turn: {str(closing_turn).lower()}"
    )
    return _invoke_a2a_runtime("SPEAKER_B_RUNTIME_ARN", prompt)


tools = [searchInWeb, turnScriptToAudio, ask_blog_writer, ask_speaker_a, ask_speaker_b]


SYSTEM_PROMPT = """
You are the supervisor agent for a conversational podcast and blog creation app. The user now sends natural-language chat messages, not a fixed workflow form.

Available collaborators and tools:

blog-writer-agent: writes researched Markdown blog posts.
podcast-speaker-a-agent: writes dialogue turns for Speaker A.
podcast-speaker-b-agent: writes dialogue turns for Speaker B.
searchInWeb: search the public web for current information. Input: query, optional max_results.
turnScriptToAudio: convert a podcast transcript JSON array into an MP3 file. Input: script_json.
ask_blog_writer: send the topic and supervisor search results to the blog writer.
ask_speaker_a: ask Speaker A for exactly one spoken podcast turn.
ask_speaker_b: ask Speaker B for exactly one spoken podcast turn.

Conversation rules:

If the user greets you, asks a normal question, asks for help, or says something unrelated to generating content, answer normally without calling tools.
Only create a podcast/audio file when the user clearly asks to create, generate, produce, record, or turn something into a podcast/audio.
Only create a blog/article/post when the user clearly asks to create, write, draft, or generate a blog/article/post.
If the user's intent is ambiguous, answer normally and ask a short clarifying question.
If the user asks to revise, summarize, explain, or continue previous content, use conversation context and answer normally unless they explicitly ask to regenerate audio or write a new blog.
Use searchInWeb only when current facts, biographies, recent events, or external context would improve the answer.
Do not invent tool outputs.
If searchInWeb fails, continue with the best available information and mention that web search was unavailable only if it matters.
If ask_blog_writer, ask_speaker_a, ask_speaker_b, or turnScriptToAudio fails, do not create fake output. Return one line beginning with ERROR: and include the failed tool name and short reason.

Podcast workflow:

Identify Speaker A and Speaker B from the user's message.
If two personas are provided, assign the first to Speaker A and the second to Speaker B.
If one persona is provided, use it for Speaker A and choose a contrasting relevant persona for Speaker B.
If no personas are provided, choose two relevant speaker roles for the topic.
Use searchInWeb to gather concise context for the topic and personas when useful.
Create exactly eight dialogue turns: Speaker A, Speaker B, Speaker A, Speaker B, Speaker A, Speaker B, Speaker A, Speaker B.
Call ask_speaker_a exactly four times and ask_speaker_b exactly four times.
Each speaker-agent call must request one turn of about 120 to 180 spoken words.
Convert the final transcript into one JSON array string. Each item must contain speaker and text.
Call turnScriptToAudio exactly once after the full transcript is ready.
After turnScriptToAudio succeeds, stop calling tools and return only a short user-facing confirmation plus the audio_url, s3_bucket, and s3_key if available.

Podcast transcript JSON format:

[
  {
    "speaker": "Speaker A",
    "text": "Spoken dialogue only."
  },
  {
    "speaker": "Speaker B",
    "text": "Spoken dialogue only."
  }
]

Blog workflow:

Use searchInWeb to gather current information about the topic when useful.
Use ask_blog_writer to send the topic and research context to blog-writer-agent.
Ask for a focused Markdown article of 600 to 900 words unless the user asks for a different length.
Return the collaborator's final Markdown blog post.
Output only the final blog content.
"""

def agent_factory():
    cache = {}
    def get_or_create_agent(session_id):
        user_id = "default-user"
        key = f"{session_id}/{user_id}"
        if key not in cache:
            cache[key] = Agent(
                model=load_model(),
                session_manager=get_memory_session_manager(session_id, user_id),
                system_prompt=SYSTEM_PROMPT,
                tools=tools,
            )
        return cache[key]
    return get_or_create_agent

if __name__ == "__main__":
    serve_a2a(StrandsA2AExecutor(agent_factory=agent_factory()))
