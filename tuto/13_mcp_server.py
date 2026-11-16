from mcp.server.mcpserver import MCPServer

mcp = MCPServer("demo-tools")


@mcp.tool()
def add(a: float, b: float) -> float:
    """Add two numbers and return the sum."""
    return a + b


@mcp.tool()
def word_count(text: str) -> int:
    """Count the words in a piece of text."""
    return len(text.split())


if __name__ == "__main__":
    mcp.run()  # default transport: stdio
