import json
from html.parser import HTMLParser
from typing import Any


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._parts.append(data)

    def get_text(self) -> str:
        return " ".join(" ".join(self._parts).split())


def searchInWeb(query: str, max_results: int = 5) -> str:
    """Search the web for real-time information."""
    if not query.strip():
        raise ValueError("query must not be empty")
    if max_results < 1:
        raise ValueError("max_results must be at least 1")

    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS
        except ImportError as exc:
            raise ImportError("ddgs is required. Install it with: pip install ddgs") from exc

    try:
        import requests
    except ImportError as exc:
        raise ImportError("requests is required. Install it with: pip install requests") from exc

    with DDGS() as ddgs:
        results = ddgs.text(query, max_results=max_results)

    search_blocks = []
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
        )
    }

    for index, result in enumerate(results, start=1):
        title = result.get("title", "")
        url = result.get("href", "")
        snippet = result.get("body", "")
        page_text = ""

        if url:
            try:
                response = requests.get(url, headers=headers, timeout=10)
                response.raise_for_status()
                extractor = TextExtractor()
                extractor.feed(response.text)
                page_text = extractor.get_text()
            except requests.RequestException:
                page_text = snippet

        search_blocks.append(
            "\n".join(
                [
                    f"Search {index}:",
                    f"Title: {title}",
                    f"URL: {url}",
                    f"Snippet: {snippet}",
                    f"Content: {page_text[:3000]}",
                ]
            )
        )

    return "\n\n".join(search_blocks)


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any] | None:
    """Handle AgentCore Lambda target events and local MCP JSON-RPC tests."""
    if "query" in event:
        try:
            return {
                "result": searchInWeb(
                    query=event["query"],
                    max_results=int(event.get("max_results", 5)),
                )
            }
        except Exception as exc:
            return {"result": f"Error searching web: {exc}"}

    payload, wrap_http_response = _decode_event(event)
    response = _handle_jsonrpc(payload)

    if response is None:
        return None

    if wrap_http_response:
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(response),
        }

    return response


def _decode_event(event: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    body = event.get("body")
    if body is None:
        return event, False
    if isinstance(body, str):
        return json.loads(body), True
    return body, True


def _handle_jsonrpc(payload: dict[str, Any]) -> dict[str, Any] | None:
    request_id = payload.get("id")
    method = payload.get("method")

    if request_id is None:
        return None

    try:
        if method == "initialize":
            params = payload.get("params", {})
            return _jsonrpc_result(
                request_id,
                {
                    "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "Demo", "version": "1.0.0"},
                },
            )

        if method == "tools/list":
            return _jsonrpc_result(request_id, {"tools": [_search_tool_schema()]})

        if method == "tools/call":
            params = payload.get("params", {})
            if params.get("name") != "searchInWeb":
                raise ValueError(f"unknown tool: {params.get('name')}")

            arguments = params.get("arguments", {})
            text = searchInWeb(
                query=arguments.get("query", ""),
                max_results=arguments.get("max_results", 5),
            )
            return _jsonrpc_result(
                request_id,
                {"content": [{"type": "text", "text": text}], "isError": False},
            )

        return _jsonrpc_error(request_id, -32601, f"method not found: {method}")
    except Exception as exc:
        return _jsonrpc_error(request_id, -32000, str(exc))


def _search_tool_schema() -> dict[str, Any]:
    return {
        "name": "searchInWeb",
        "description": "Search the web for real-time information.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query."},
                "max_results": {
                    "type": "integer",
                    "description": "The maximum number of results to return.",
                    "default": 5,
                    "minimum": 1,
                },
            },
            "required": ["query"],
        },
    }


def _jsonrpc_result(request_id: str | int, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _jsonrpc_error(request_id: str | int, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }
