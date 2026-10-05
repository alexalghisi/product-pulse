"""pulse-mcp: run the MCP server.

Locally over stdio, with a personal API key in the environment:

    PULSE_API_KEY=ppk_... pulse-mcp

Or over streamable HTTP, where every request carries a key as a bearer token:

    pulse-mcp --transport http --port 8150
"""

from __future__ import annotations

import argparse
import os


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="pulse-mcp", description="product-pulse MCP server")
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8150)
    args = parser.parse_args(argv)

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "product_pulse.settings")
    # Expected tool errors (unknown names and the like) are logged at INFO; over stdio the
    # client shows the server's stderr, so keep it to real problems.
    os.environ.setdefault("PULSE_LOG_LEVEL", "WARNING")
    import django

    django.setup()
    # Imported after setup: the server uses the Django ORM.
    from mcp.server.auth.settings import AuthSettings

    from product_pulse.clickhouse.client import default_client
    from product_pulse.mcp_server.server import (
        PersonalKeyVerifier,
        build_server,
        project_from_access_token,
        project_from_key,
    )

    client = default_client()
    if args.transport == "stdio":
        project_id = project_from_key(os.environ.get("PULSE_API_KEY", ""))
        build_server(lambda: project_id, lambda: client).run("stdio")
        return

    base = f"http://{args.host}:{args.port}"
    auth = AuthSettings(
        issuer_url=base,
        resource_server_url=f"{base}/mcp",
        required_scopes=["analytics"],
    )
    server = build_server(
        project_from_access_token, lambda: client, auth=(auth, PersonalKeyVerifier())
    )
    server.run("streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
