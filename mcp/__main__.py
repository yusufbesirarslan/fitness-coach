"""Allow running as: python -m mcp"""
from mcp.server import mcp as app

if __name__ == "__main__":
    import sys
    if "--http" in sys.argv:
        app.run(transport="streamable-http", host="127.0.0.1", port=8100)
    else:
        app.run(transport="stdio")
