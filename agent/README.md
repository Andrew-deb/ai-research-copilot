# agent/ — AI Research Copilot Agent Configuration

This directory contains the core configuration and prompt engineering assets for the AI Agent.

## Files

| File | Purpose |
|------|---------|
| `system_prompt.md` | The core agent instructions, engineered using the 5 prompt optimization pillars (Role, Tool Routing, Protocols, Anti-Hallucination, Output Templates). |
| `agent_config.yaml` | Agent runtime configuration specifying model provider, hyperparameters, context window limits, and MCP tool bindings. |

---

## Key Design & Implementation Decisions

### 1. The 5 Structural Pillars of the System Prompt
* **Persona:** Establishes academic rigor combined with pedagogical empathy to adapt responses to the user's expertise level.
* **Explicit Dispatch Rules:** Eliminates tool ambiguity by defining exact conditions for when to use `search_papers`, `get_similar_papers`, `compare_papers`, and `explain_topic`.
* **Execution Protocols (Chain-of-Thought):** Scaffolds complex multi-turn workflows into predictable steps (Prerequisite Check $\rightarrow$ Discovery $\rightarrow$ Collection $\rightarrow$ Sequenced Plan).
* **Anti-Hallucination Bounds:** Prohibits fabricating DOIs or paper IDs, demanding reliance solely on tool-returned metadata.
* **Deterministic Response Contracts:** Provides structured Markdown templates for curricula and paper comparison matrices.

### 2. Hyperparameter Selection (`agent_config.yaml`)
* **Temperature (`0.2`):** Low temperature minimizes creative hallucination and enforces strict adherence to academic citations and JSON tool calling schemas.
* **Max Consecutive Tool Calls (`8`):** Prevents infinite tool execution loops during multi-step research queries while allowing sufficient depth for multi-paper discovery and synthesis.
