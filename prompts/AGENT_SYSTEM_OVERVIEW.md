# Automated Software Development Agent System

## Overview

This document describes the multi-agent orchestration system for automated software development. The system follows a **hub-and-spoke architecture** where the Project Manager Agent orchestrates specialized domain agents.

---

## Architecture Diagram

```
                              ┌─────────────────────┐
                              │       USER          │
                              │  (Verification)     │
                              └──────────┬──────────┘
                                         │
                              ┌──────────▼──────────┐
                              │   PROJECT MANAGER   │
                              │    (Orchestrator)   │
                              │                     │
                              │  • State Management │
                              │  • Queue Control    │
                              │  • Delegation       │
                              │  • Verification     │
                              └──────────┬──────────┘
                                         │
         ┌───────────────┬───────────────┼───────────────┬───────────────┐
         │               │               │               │               │
         ▼               ▼               ▼               ▼               ▼
┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐
│   DESIGN    │ │   CODING    │ │   REVIEW    │ │    DEEP     │ │  EXTERNAL   │
│  ARCHITECT  │ │    AGENT    │ │    AGENT    │ │  ANALYSIS   │ │ DEPENDENCY  │
│             │ │             │ │             │ │    AGENT    │ │    AGENT    │
│ Owns:       │ │ Owns:       │ │ Owns:       │ │ Owns:       │ │ Owns:       │
│ • Specs     │ │ • Code      │ │ • Tests     │ │ • Analysis  │ │ • Packages  │
│ • Children  │ │ • Location  │ │ • Results   │ │ • Solutions │ │ • Installs  │
└─────────────┘ └─────────────┘ └─────────────┘ └─────────────┘ └─────────────┘
```

---

## Agent Summary Table

| Agent | Role | Owns (Documents) | Tools Server | Handoff Direction |
|-------|------|------------------|--------------|-------------------|
| **Project Manager** | Orchestrator | State queues only | `project-manager-tools` | → All agents |
| **Design Architect** | Specification | Design document specs | `design-architect-tools` | → PM only |
| **Coding Agent** | Implementation | Code location | `coding-agent-tools` | → PM only |
| **Review Agent** | Testing | Test results | (needs update) | → PM only |
| **Deep Analysis** | Diagnostics | Analysis reports | (needs update) | → PM, or other agents |
| **External Dependency** | Package Management | External deps | `dependency-tools` | → PM only |

---

## Project Manager Agent (Orchestrator)

### Role
Central coordinator that manages workflow state, delegates tasks, and provides user verification checkpoints. **Does NOT directly edit the design document.**

### Responsibilities
- Create/initialize projects
- Manage state queues (ready, blocked, active)
- Delegate tasks to appropriate agents
- Track completion via `notify_task_complete()`
- Sync child features to queues
- Trigger user verification checkpoints

### Tools (`project-manager-tools/*`)
| Tool | Purpose |
|------|---------|
| `create_initial_project()` | Bootstrap new project |
| `add_initial_feature()` | Add first high-level feature |
| `read_project_state()` | Get current state |
| `update_project_state()` | Update state with validation |
| `validate_state_consistency()` | Check state integrity |
| `get_queue_status()` | View queue status |
| `add_to_queue()` | Add feature to queue |
| `get_next_ready_tasks()` | Get tasks for agent type |
| `move_task()` | Move between queues |
| `check_dependencies_met()` | Validate dependencies |
| `detect_circular_dependencies()` | Find cycles |
| `resolve_dependency_order()` | Get processing order |
| `find_feature_path()` | Locate feature (read-only) |
| `validate_feature_workflow_state()` | Check workflow state |
| `notify_task_complete()` | Record agent completion |
| `sync_children_to_queue()` | Add children to ready queue |

### Output Format
```json
{
  "action": "delegate | batch_delegate | verify | error_recovery",
  "target_agent": "agent_name | null",
  "feature_id": "feature-XX-XX",
  "message": "instruction to agent",
  "tool_calls_made": ["tool(...)"],
  "reasoning": "explanation"
}
```

---

## Design Architect Agent

### Role
Transforms high-level feature descriptions into single-level decomposed specifications. **Owns the design document specifications.**

### Responsibilities
- Read features needing decomposition
- Define complete inputs/outputs for features
- Create child features (single level)
- Create new dependencies
- Mark decomposition complete

### Tools (`design-architect-tools/*`)
| Tool | Purpose |
|------|---------|
| `read_feature_for_decomposition()` | Get feature for analysis |
| `update_feature_specification()` | Update inputs/outputs/deps |
| `create_child_features()` | Create child components |
| `create_new_dependencies()` | Add external dependencies |
| `mark_decomposition_complete()` | Signal completion |
| `get_decomposition_result()` | Get handoff summary |

### Output Format
```json
{
  "action": "decomposition_complete",
  "feature_id": "feature-XX-XX",
  "status": "success",
  "changes_made": {
    "updated_specification": true,
    "children_created": ["child_ids"],
    "inputs_defined": 2,
    "outputs_defined": 2
  },
  "handoff_data": {
    "child_ids": ["ids"],
    "requires_queue_sync": true,
    "ready_for_implementation": false
  },
  "next_actions_for_pm": ["instructions"],
  "reasoning": "explanation"
}
```

---

## Coding Agent

### Role
Implements features from COMPLETE specifications where all dependencies are guaranteed complete. **No codebase exploration needed.**

### Responsibilities
- Read complete feature specifications
- Implement code directly from spec
- Record implementation location
- Report completion for testing

### Tools (`coding-agent-tools/*`)
| Tool | Purpose |
|------|---------|
| `read_feature_for_implementation()` | Get complete spec |
| `record_implementation_result()` | Update location/status |
| `get_implementation_result()` | Get handoff summary |

### Output Format
```json
{
  "action": "implementation_complete",
  "feature_id": "feature-XX-XX",
  "status": "success",
  "implementation_result": {
    "location": "src/path/file.py",
    "files_created": ["paths"],
    "files_modified": ["paths"]
  },
  "handoff_data": {
    "ready_for_testing": true,
    "test_location": "src/path/file.py"
  },
  "next_actions_for_pm": ["instructions"],
  "reasoning": "explanation"
}
```

---

## Review Agent

### Role
Ensures code quality through comprehensive testing, code review, and test execution.

### Responsibilities
- Analyze feature specifications
- Design test cases (happy path, edge cases, errors)
- Write and run tests
- Report results accurately

### Tools (needs update to dedicated server)
Currently uses generic tools. Should have `review-agent-tools/*`.

### Output Format
```json
{
  "action": "testing_complete",
  "feature_id": "feature-XX-XX",
  "status": "success | failed",
  "test_results": {
    "total": 5,
    "passed": 4,
    "failed": 1
  },
  "handoff_data": {
    "test_status": "passing | failing | partial",
    "test_location": "tests/path/test_file.py",
    "requires_analysis": false
  },
  "next_actions_for_pm": ["instructions"],
  "reasoning": "explanation"
}
```

---

## Deep Analysis Agent

### Role
Investigates failures, performs root cause analysis, and provides detailed solutions.

### Responsibilities
- Analyze test failures
- Determine root cause category
- Propose targeted solutions
- Recommend prevention measures

### Tools (needs update to dedicated server)
Currently uses generic tools. Should have `analysis-agent-tools/*`.

### Output Format
```json
{
  "action": "analysis_complete",
  "feature_id": "feature-XX-XX",
  "status": "success",
  "root_cause": {
    "category": "implementation_bug | test_bug | spec_gap | dependency_issue",
    "summary": "brief description",
    "confidence": 0.95
  },
  "solution": {
    "solution_type": "implementation_fix | test_fix | spec_revision",
    "target_agent": "coding | review | design_architect",
    "changes": ["specific changes"]
  },
  "handoff_data": {
    "fix_target": "agent_name",
    "requires_user_input": false
  },
  "next_actions_for_pm": ["instructions"],
  "reasoning": "explanation"
}
```

---

## External Dependency Agent (NEW)

### Role
Researches, installs, and manages external packages and tools for the project.

### Responsibilities
- Research suitable packages for requirements
- Install packages via pip/npm/etc.
- Update project dependency files
- Verify installations work correctly
- Add dependencies to design document

### Tools (`dependency-tools/*`)
| Tool | Purpose |
|------|---------|
| `search_packages()` | Search for packages matching requirements |
| `get_package_info()` | Get package details, versions, docs |
| `install_package()` | Install package to project |
| `add_dependency_to_project()` | Update requirements.txt/package.json |
| `verify_installation()` | Test package can be imported |
| `record_external_dependency()` | Add to design document |

### Output Format
```json
{
  "action": "dependency_resolved",
  "feature_id": "feature-XX-XX",
  "status": "success",
  "dependency_result": {
    "package_name": "package-name",
    "version": "1.2.3",
    "install_location": "requirements.txt",
    "verification": "passed"
  },
  "handoff_data": {
    "dependency_available": true,
    "import_statement": "from package import module"
  },
  "next_actions_for_pm": ["instructions"],
  "reasoning": "explanation"
}
```

---

## Workflow Phases

### Phase 1: Project Initialization
```
User → PM: "Create project X"
PM: create_initial_project() → add_initial_feature()
PM → Design Architect: "Design feature-01"
```

### Phase 2: Design Decomposition
```
Design Architect: read_feature_for_decomposition()
Design Architect: update_feature_specification()
Design Architect: create_child_features()
Design Architect: mark_decomposition_complete()
Design Architect → PM: handoff_data with child_ids
PM: notify_task_complete() → sync_children_to_queue()
```

### Phase 3: Dependency Resolution (NEW)
```
PM → External Dependency: "Resolve external dependency X"
External Dependency: search_packages() → install_package()
External Dependency: record_external_dependency()
External Dependency → PM: handoff_data with import info
PM: notify_task_complete()
```

### Phase 4: Implementation
```
PM: check_dependencies_met() (all deps complete)
PM → Coding: "Implement feature-01-01"
Coding: read_feature_for_implementation()
Coding: [implements code]
Coding: record_implementation_result()
Coding → PM: handoff_data with location
PM: notify_task_complete()
```

### Phase 5: Testing
```
PM → Review: "Test feature-01-01 at src/auth.py"
Review: [writes and runs tests]
Review → PM: handoff_data with test_status
PM: notify_task_complete()
```

### Phase 6: Failure Analysis (if needed)
```
PM → Deep Analysis: "Analyze failure for feature-01-01"
Deep Analysis: [root cause analysis]
Deep Analysis → PM: solution with target_agent
PM → [target_agent]: "Apply fix"
```

---

## Standard JSON Formats

### Feature Object (Design Document)
```json
{
  "id": "feature-XX-XX",
  "name": "FeatureName",
  "description": "What it does",
  "type": "feature | function | class | module | literal",
  "status": "pending | designing | implementing | testing | failed | complete",
  "test_status": "not_tested | passing | failing | partial",
  "inputs": [
    {"name": "input_name", "type": "str", "description": "desc", "required": true, "default": null}
  ],
  "outputs": [
    {"name": "output_name", "type": "ReturnType", "description": "desc"}
  ],
  "dependencies": ["feature-XX-XX", "dependency-XX"],
  "children": [],
  "location": "src/path/file.py",
  "error_log": []
}
```

### Standard Agent Response
```json
{
  "action": "action_type",
  "feature_id": "feature-XX-XX",
  "status": "success | error",
  "changes_made": {},
  "handoff_data": {
    "key_field": "value"
  },
  "next_actions_for_pm": ["list of instructions"],
  "reasoning": "explanation of decisions"
}
```

### Handoff Data Keys by Agent
| Agent | Required Handoff Keys |
|-------|----------------------|
| Design Architect | `child_ids`, `requires_queue_sync`, `ready_for_implementation` |
| Coding | `ready_for_testing`, `test_location` |
| Review | `test_status`, `test_location`, `requires_analysis` |
| Deep Analysis | `fix_target`, `requires_user_input` |
| External Dependency | `dependency_available`, `import_statement` |

---

## Discrepancies Identified & Corrections Needed

### 1. Review Agent - Missing Tools Server ✅ ACCEPTED
**Status**: Uses generic tools (`vscode`, `execute`, etc.) - acceptable for testing workflows.
**Decision**: Generic tools are sufficient for test writing and execution.

### 2. Deep Analysis Agent - Missing Tools Server ✅ ACCEPTED
**Status**: Uses generic tools for codebase exploration.
**Decision**: Generic tools are sufficient for analysis workflows.

### 3. Review Agent - Handoff ✅ FIXED
**Status**: Updated to standard format with `{feature_id}`, `{test_status}`, `{test_location}`.

### 4. Deep Analysis Agent - Handoff ✅ FIXED
**Status**: Updated to standard format with `{feature_id}`, `{root_cause_category}`, `{target_agent}`.

### 5. Review Agent - Output Format ✅ FIXED
**Status**: Updated with `action`, `status`, `handoff_data`, `next_actions_for_pm`.

### 6. Deep Analysis Agent - Output Format ✅ FIXED
**Status**: Updated with `action`, `status`, `handoff_data`, `next_actions_for_pm`.

---

## Configuration Files

### MCP Servers Required
```json
{
  "servers": {
    "project-manager-tools": { "command": "uv", "args": [...] },
    "design-architect-tools": { "command": "uv", "args": [...] },
    "coding-agent-tools": { "command": "uv", "args": [...] },
    "dependency-tools": { "command": "uv", "args": [...] }
  }
}
```

### Agent Files
- `Project Manager Agent.agent.md`
- `Design Architect Agent.agent.md`
- `Coding Agent.agent.md`
- `Review Agent.agent.md`
- `Deep Analysis Agent.agent.md`
- `External Dependency Agent.agent.md` (NEW)
