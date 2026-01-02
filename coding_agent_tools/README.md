# Coding Agent Tools MCP Server

Minimal MCP server for the Coding Agent in the automated software development system.

## Philosophy

The Coding Agent receives **fully-specified features** from the Project Manager. All dependencies and child features are guaranteed to be complete before implementation is delegated. Therefore, this server provides only **minimal tools**:

1. **No codebase exploration** - The feature spec contains everything needed
2. **No dependency analysis** - Dependencies are already complete
3. **Direct implementation** - Read spec → Write code → Record result

## Tools

### `read_feature_for_implementation(feature_id)`
Reads complete feature specification including:
- Name, description, type
- Inputs/outputs with types
- Dependency interfaces (for imports)
- Project context

### `record_implementation_result(feature_id, location, status, notes)`
Records where code was implemented:
- Updates feature location in design document
- Updates feature status
- Optional implementation notes

### `get_implementation_result(feature_id)`
Gets implementation summary for Project Manager handoff:
- Location of implemented code
- Status
- Ready for testing flag

## Usage

```bash
cd coding_agent_tools
uv sync
uv run coding_agent_tools_server.py
```

## Workflow

1. PM delegates: "Implement feature-01-01. All deps complete."
2. Agent calls: `read_feature_for_implementation("feature-01-01")`
3. Agent implements code based on spec
4. Agent calls: `record_implementation_result("feature-01-01", "src/auth.py", "implementing")`
5. Agent calls: `get_implementation_result("feature-01-01")`
6. Agent returns to PM with handoff data
