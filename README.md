# Tutorial: self-learning for AI agent

Self-learning materials for building an agentic AI loop from scratch — no framework, just an LLM API, Python, and a growing list of messages.

## Structure

| Folder / file | What it is |
|---|---|
| `tuto/` | One notebook or script per concept, numbered in learning order (see below) |
| `tuto/notes.md` | Condensed revision notes for all modules |
| `agent_loop_guide.html` | Visual guide: the base loop flowchart, plug-in patterns, message evolution (open in a browser) |
| `agent_harness/` | The pieces refactored into a small reusable package (loop, tools, prompts, cache, cost) |
| `H4 resilience paper query/` | Applied example: an agent that builds literature queries for a resilience paper |
| `models.yaml` | Model/provider configuration (keys come from environment variables, never from this file) |

## Learning path (`tuto/`)

0. Baseline LLM call
1. Minimal agent loop (messages + stop condition)
2. First tool call (schema, registry, JSON arguments)
3. Tool decorator + error handling
4. In-session context management (summary + recent steps)
5. Persistent memory (JSON file)
6. Planning & task decomposition
7. Error recovery & self-correction (retry limit, critic)
8. Parallel tool calling (asyncio)
9. Guardrails & safety (validate, confirm, audit)
10. Observability & tracing
11. Multi-agent orchestration
12. Using libraries
13. MCP (Model Context Protocol)

## Setup

Create a `.env` with your API keys (see `models.yaml` for the expected variable names). The `.env` file is git-ignored.

Based on following along with the [agent-harness-course](https://github.com/jiminelee/agent-harness-course) tutorial, with personal notes, experiments, and extensions.
