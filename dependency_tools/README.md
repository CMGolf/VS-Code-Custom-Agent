# Dependency Tools MCP Server

MCP server for the External Dependency Agent in the automated software development system.

## Purpose

Researches, installs, and manages external packages and tools for projects. Supports Python (pip/uv) and JavaScript (npm) ecosystems.

## Tools

### `search_packages(query, language, count)`
Search for packages matching requirements.
- Searches PyPI for Python packages
- Searches npm for JavaScript packages
- Returns candidates with metadata

### `get_package_info(package_name, language)`
Get detailed information about a specific package.
- Version, description, author
- License, dependencies
- Last updated, documentation URL

### `install_package(package_name, version, language)`
Install a package to the project environment.
- Uses uv/pip for Python
- Uses npm for JavaScript
- Optional version pinning

### `add_dependency_to_project(package_name, version, language, dev_dependency)`
Add dependency to project configuration.
- Updates pyproject.toml or requirements.txt for Python
- Updates package.json for JavaScript

### `verify_installation(package_name, language)`
Verify package is correctly installed.
- Tests import/require works
- Returns import statement to use

### `record_external_dependency(dependency_id, package_name, version, import_statement, purpose, language)`
Record dependency in design document.
- Updates feature status to "complete"
- Stores package metadata for coding agent

## Usage

```bash
cd dependency_tools
uv sync
uv run dependency_tools_server.py
```

## Workflow

1. Design Architect creates dependency feature: `dependency-01`
2. PM delegates to External Dependency Agent
3. Agent calls: `search_packages("password hashing", "python")`
4. Agent evaluates options with: `get_package_info("bcrypt", "python")`
5. Agent installs: `install_package("bcrypt", "4.1.2", "python")`
6. Agent updates config: `add_dependency_to_project("bcrypt", "4.1.2", "python")`
7. Agent verifies: `verify_installation("bcrypt", "python")`
8. Agent records: `record_external_dependency("dependency-01", "bcrypt", ...)`
9. Agent returns to PM with handoff data
