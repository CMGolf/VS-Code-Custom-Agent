#!/usr/bin/env python3
"""
Project Manager Tools MCP Server

Provides state management tools for the Project Manager Agent including:
- project_state_manager: Manages project state persistence and validation
- task_queue_manager: Handles task queuing and queue operations  
- dependency_resolver: Manages feature dependencies and circular detection
"""

import json
import os
import shutil
import uuid
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Tuple
from pathlib import Path
import asyncio
import tempfile
import platform

# Cross-platform file locking imports
if platform.system() == "Windows":
    import msvcrt
else:
    import fcntl

from mcp.server.fastmcp import FastMCP

# Initialize the MCP server
mcp = FastMCP("Project Manager Tools")

# Configuration
STATE_FILE = "ProjectManagerState.json"
DESIGN_DOC_FILE = "ProjectDesignDocument.json" 
BACKUP_DIR = "state_backups"
ANALYTICS_FILE = "DependencyAnalytics.json"
MAX_BACKUPS = 5
MAX_CONCURRENT_TASKS = 3

# Reverse Dependency Configuration
DEFAULT_MAX_CASCADE_DEPTH = 5
ENABLE_REVERSE_TRACKING = True
ENABLE_CASCADE_TESTING = True

# ID Management Configuration
ID_FORMATS = {
    "feature": r"^feature-\d+(?:-\d+)*$",
    "dependency": r"^dependency-\d+(?:-\d+)*$",
    "function": r"^function-\d+(?:-\d+)*$",
    "class": r"^class-\d+(?:-\d+)*$",
    "module": r"^module-\d+(?:-\d+)*$",
    "literal": r"^literal-\d+(?:-\d+)*$"
}
DEFAULT_ID_PREFIX = "feature"
MAX_BATCH_SIZE = 1000
MIGRATION_LOG_FILE = "id_migration.log"

class StateManager:
    """Handles project state persistence and validation"""
    
    def __init__(self):
        self.lock_file = None
    
    def _parse_feature_id(self, feature_id: str) -> Dict[str, Any]:
        """Parse feature ID into prefix and numeric segments"""
        if not feature_id:
            return {"valid": False, "message": "Empty feature ID"}
        
        # Match any valid ID format
        for prefix, pattern in ID_FORMATS.items():
            if re.match(pattern, feature_id):
                parts = feature_id.split("-")
                if len(parts) >= 2:
                    prefix_part = parts[0]
                    try:
                        numeric_segments = [int(part) for part in parts[1:]]
                        return {
                            "valid": True,
                            "prefix": prefix_part,
                            "segments": numeric_segments,
                            "level": len(numeric_segments),
                            "type": prefix
                        }
                    except ValueError:
                        break
        
        return {"valid": False, "message": f"Invalid feature ID format: {feature_id}"}
    
    def _build_feature_id(self, prefix: str, segments: List[int]) -> str:
        """Build feature ID from prefix and numeric segments"""
        if not segments:
            return f"{prefix}-01"
        
        # Determine appropriate padding based on segment values
        padded_segments = []
        for segment in segments:
            padding = max(2, len(str(segment)))
            padded_segments.append(f"{segment:0{padding}d}")
        
        return f"{prefix}-" + "-".join(padded_segments)
    
    def _acquire_lock(self, file_path: str) -> None:
        """Acquire file lock for atomic operations - cross-platform"""
        lock_path = f"{file_path}.lock"
        self.lock_file = open(lock_path, 'w')
        
        if platform.system() == "Windows":
            # Windows file locking using msvcrt
            try:
                msvcrt.locking(self.lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                # If lock fails, close file and re-raise
                self.lock_file.close()
                self.lock_file = None
                raise
        else:
            # Unix/Linux file locking using fcntl
            fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_EX)
    
    def _release_lock(self) -> None:
        """Release file lock - cross-platform"""
        if self.lock_file:
            try:
                if platform.system() == "Windows":
                    # Windows unlock using msvcrt
                    msvcrt.locking(self.lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    # Unix/Linux unlock using fcntl
                    fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_UN)
            finally:
                self.lock_file.close()
                self.lock_file = None
    
    def _get_default_state(self) -> Dict[str, Any]:
        """Get default state structure"""
        return {
            "session_id": str(uuid.uuid4()),
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "verification_counters": {
                "completed_features": 0,
                "total_updates": 0
            },
            "active_tasks": {
                "design_architect": [],
                "coding": [],
                "review": [],
                "deep_analysis": []
            },
            "parallel_ready_queue": [],
            "dependency_blocked_queue": [],
            "task_history": [],
            "circular_dependency_check": {
                "dependency_graph": {},
                "last_check": datetime.now(timezone.utc).isoformat()
            },
            "error_tracking": {
                "feature_failures": {}
            },
            "reverse_dependency_cache": {
                "mappings": {},
                "last_updated": None,
                "invalidated_features": []
            },
            "dependency_config": {
                "max_cascade_depth": DEFAULT_MAX_CASCADE_DEPTH,
                "enable_reverse_tracking": ENABLE_REVERSE_TRACKING,
                "enable_cascade_testing": ENABLE_CASCADE_TESTING
            },
            "pending_retest_queue": [],
            "cascade_depth_exceeded": []
        }
    
    def _validate_state_schema(self, state: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """Validate state schema and return (is_valid, errors)"""
        errors = []
        required_keys = [
            "session_id", "last_updated", "verification_counters", 
            "active_tasks", "parallel_ready_queue", "dependency_blocked_queue"
        ]
        
        for key in required_keys:
            if key not in state:
                errors.append(f"Missing required key: {key}")
        
        if "active_tasks" in state:
            required_agents = ["design_architect", "coding", "review", "deep_analysis"]
            for agent in required_agents:
                if agent not in state["active_tasks"]:
                    errors.append(f"Missing agent in active_tasks: {agent}")
                elif not isinstance(state["active_tasks"][agent], list):
                    errors.append(f"active_tasks.{agent} must be a list")
        
        return len(errors) == 0, errors
    
    def _backup_state(self, state: Dict[str, Any]) -> None:
        """Create backup of current state"""
        os.makedirs(BACKUP_DIR, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = os.path.join(BACKUP_DIR, f"state_backup_{timestamp}.json")
        
        with open(backup_file, 'w') as f:
            json.dump(state, f, indent=2)
        
        # Clean up old backups
        backups = sorted([f for f in os.listdir(BACKUP_DIR) if f.startswith("state_backup_")])
        while len(backups) > MAX_BACKUPS:
            old_backup = backups.pop(0)
            os.remove(os.path.join(BACKUP_DIR, old_backup))

@mcp.tool()
def create_initial_project(project_name: str, project_description: str) -> Dict[str, Any]:
    """
    Create initial project design document from user description.
    
    Args:
        project_name: Name of the project
        project_description: High-level project description from user
        
    Returns:
        Dict with project creation status
    """
    state_manager = StateManager()
    
    try:
        state_manager._acquire_lock(DESIGN_DOC_FILE)
        
        if os.path.exists(DESIGN_DOC_FILE):
            return {"status": "error", "message": "Project design document already exists"}
        
        # Create initial design document structure
        design_doc = {
            "project": {
                "name": project_name,
                "description": project_description,
                "created": datetime.now(timezone.utc).isoformat(),
                "version": "1.0.0"
            },
            "features": []
        }
        
        # Write initial document
        with open(DESIGN_DOC_FILE, 'w') as f:
            json.dump(design_doc, f, indent=2)
        
        # Create initial project state
        initial_state = state_manager._get_default_state()
        with open(STATE_FILE, 'w') as f:
            json.dump(initial_state, f, indent=2)
        
        return {
            "status": "success",
            "message": f"Project '{project_name}' created successfully",
            "project_name": project_name,
            "design_doc_created": True,
            "state_initialized": True
        }
        
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        state_manager._release_lock()

@mcp.tool()
def add_initial_feature(feature_name: str, feature_description: str, feature_type: str = "feature") -> Dict[str, Any]:
    """
    Add the first high-level feature to the project from user requirements.
    
    Args:
        feature_name: Name of the initial feature
        feature_description: Description of what this feature should do
        feature_type: Type of feature (default: "feature")
        
    Returns:
        Dict with feature creation status and ID
    """
    state_manager = StateManager()
    
    try:
        state_manager._acquire_lock(DESIGN_DOC_FILE)
        
        if not os.path.exists(DESIGN_DOC_FILE):
            return {"status": "error", "message": "No project design document found. Create project first."}
        
        with open(DESIGN_DOC_FILE, 'r') as f:
            design_doc = json.load(f)
        
        # Generate first feature ID
        generation_result = generate_feature_id(feature_type)
        if generation_result["status"] != "success":
            return generation_result
        
        feature_id = generation_result["generated_id"]
        
        # Create initial feature
        initial_feature = {
            "id": feature_id,
            "name": feature_name,
            "description": feature_description,
            "type": feature_type,
            "status": "pending",
            "test_status": "not_tested",
            "inputs": [],
            "outputs": [],
            "dependencies": [],
            "children": [],
            "error_log": []
        }
        
        # Add to design document
        design_doc["features"].append(initial_feature)
        
        # Write updated document
        temp_file = f"{DESIGN_DOC_FILE}.tmp"
        with open(temp_file, 'w') as f:
            json.dump(design_doc, f, indent=2)
        
        shutil.move(temp_file, DESIGN_DOC_FILE)
        
        # Add to ready queue
        add_to_queue(feature_id, "parallel_ready_queue")
        
        return {
            "status": "success",
            "message": f"Initial feature '{feature_name}' added successfully",
            "feature_id": feature_id,
            "added_to_queue": True,
            "ready_for_design": True
        }
        
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        state_manager._release_lock()

@mcp.tool()
def read_project_state() -> Dict[str, Any]:
    """
    Read the current project state from file.
    
    Returns:
        Dict containing the full project state including queues, counters, and active tasks
    """
    state_manager = StateManager()
    
    try:
        state_manager._acquire_lock(STATE_FILE)
        
        if not os.path.exists(STATE_FILE):
            # Create new state file with defaults
            default_state = state_manager._get_default_state()
            with open(STATE_FILE, 'w') as f:
                json.dump(default_state, f, indent=2)
            return default_state
        
        with open(STATE_FILE, 'r') as f:
            state = json.load(f)
        
        # Validate state schema
        is_valid, errors = state_manager._validate_state_schema(state)
        if not is_valid:
            raise ValueError(f"Invalid state schema: {', '.join(errors)}")
        
        return state
        
    except Exception as e:
        # Return default state on any error
        return state_manager._get_default_state()
    finally:
        state_manager._release_lock()

@mcp.tool()
def update_project_state(changes: Dict[str, Any]) -> Dict[str, str]:
    """
    Update the project state with specified changes.
    
    Args:
        changes: Dictionary of changes to apply to the state
        
    Returns:
        Dict with operation status and any errors
    """
    state_manager = StateManager()
    
    try:
        state_manager._acquire_lock(STATE_FILE)
        
        # Read current state
        current_state = read_project_state()
        
        # Create backup
        state_manager._backup_state(current_state)
        
        # Apply changes
        for key, value in changes.items():
            if key in current_state:
                if isinstance(current_state[key], dict) and isinstance(value, dict):
                    current_state[key].update(value)
                else:
                    current_state[key] = value
        
        # Update timestamp
        current_state["last_updated"] = datetime.now(timezone.utc).isoformat()
        
        # Validate updated state
        is_valid, errors = state_manager._validate_state_schema(current_state)
        if not is_valid:
            raise ValueError(f"Updated state validation failed: {', '.join(errors)}")
        
        # Write to temporary file first, then atomic rename
        temp_file = f"{STATE_FILE}.tmp"
        with open(temp_file, 'w') as f:
            json.dump(current_state, f, indent=2)
        
        # Atomic rename
        shutil.move(temp_file, STATE_FILE)
        
        return {"status": "success", "message": "State updated successfully"}
        
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        state_manager._release_lock()

@mcp.tool()
def validate_state_consistency() -> Dict[str, Any]:
    """
    Validate the consistency of the current project state.
    
    Returns:
        Dict with validation results and any issues found
    """
    try:
        state = read_project_state()
        issues = []
        
        # Check for duplicate features across queues
        all_features = set()
        for queue_name in ["parallel_ready_queue", "dependency_blocked_queue"]:
            for feature_id in state.get(queue_name, []):
                if feature_id in all_features:
                    issues.append(f"Feature {feature_id} appears in multiple queues")
                all_features.add(feature_id)
        
        # Check active tasks
        for agent, tasks in state.get("active_tasks", {}).items():
            if len(tasks) > MAX_CONCURRENT_TASKS:
                issues.append(f"Agent {agent} exceeds max concurrent tasks: {len(tasks)}")
            for feature_id in tasks:
                if feature_id in all_features:
                    issues.append(f"Feature {feature_id} in both queue and active tasks")
                all_features.add(feature_id)
        
        # Check if features exist in design document
        if os.path.exists(DESIGN_DOC_FILE):
            with open(DESIGN_DOC_FILE, 'r') as f:
                design_doc = json.load(f)
            
            existing_features = {f["id"] for f in design_doc.get("features", [])}
            for feature_id in all_features:
                if feature_id not in existing_features:
                    issues.append(f"Feature {feature_id} in state but not in design document")
        
        return {
            "is_valid": len(issues) == 0,
            "issues": issues,
            "total_features_tracked": len(all_features)
        }
        
    except Exception as e:
        return {"is_valid": False, "issues": [str(e)], "total_features_tracked": 0}

@mcp.tool()
def backup_state() -> Dict[str, str]:
    """
    Create a manual backup of the current state.
    
    Returns:
        Dict with backup operation status
    """
    try:
        state = read_project_state()
        state_manager = StateManager()
        state_manager._backup_state(state)
        return {"status": "success", "message": "State backup created successfully"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@mcp.tool()
def add_to_queue(feature_id: str, queue_name: str) -> Dict[str, str]:
    """
    Add a feature to the specified queue.
    
    Args:
        feature_id: ID of the feature to add
        queue_name: Name of the queue ("parallel_ready_queue" or "dependency_blocked_queue")
        
    Returns:
        Dict with operation status
    """
    valid_queues = ["parallel_ready_queue", "dependency_blocked_queue"]
    if queue_name not in valid_queues:
        return {"status": "error", "message": f"Invalid queue name. Must be one of: {valid_queues}"}
    
    try:
        state = read_project_state()
        
        # Remove from other queue if present
        other_queue = "dependency_blocked_queue" if queue_name == "parallel_ready_queue" else "parallel_ready_queue"
        if feature_id in state.get(other_queue, []):
            state[other_queue].remove(feature_id)
        
        # Remove from active tasks if present
        for agent, tasks in state.get("active_tasks", {}).items():
            if feature_id in tasks:
                tasks.remove(feature_id)
        
        # Add to target queue if not already there
        if feature_id not in state.get(queue_name, []):
            state[queue_name].append(feature_id)
        
        # Update state
        result = update_project_state(state)
        return result
        
    except Exception as e:
        return {"status": "error", "message": str(e)}

@mcp.tool()
def get_next_ready_tasks(agent_type: str, max_count: int = 1) -> Dict[str, Any]:
    """
    Get the next ready tasks for a specific agent type.
    
    Args:
        agent_type: Type of agent ("design_architect", "coding", "review", "deep_analysis")
        max_count: Maximum number of tasks to return
        
    Returns:
        Dict with list of feature IDs ready for processing
    """
    valid_agents = ["design_architect", "coding", "review", "deep_analysis"]
    if agent_type not in valid_agents:
        return {"status": "error", "message": f"Invalid agent type. Must be one of: {valid_agents}"}
    
    try:
        state = read_project_state()
        
        # Check current active tasks for this agent
        current_tasks = len(state.get("active_tasks", {}).get(agent_type, []))
        available_slots = max(0, MAX_CONCURRENT_TASKS - current_tasks)
        
        # Limit by available slots and requested max
        actual_max = min(max_count, available_slots)
        
        # Get ready tasks from queue
        ready_queue = state.get("parallel_ready_queue", [])
        next_tasks = ready_queue[:actual_max]
        
        return {
            "status": "success",
            "ready_tasks": next_tasks,
            "available_slots": available_slots,
            "current_active": current_tasks
        }
        
    except Exception as e:
        return {"status": "error", "message": str(e)}

@mcp.tool()
def move_task(feature_id: str, from_location: str, to_location: str) -> Dict[str, str]:
    """
    Move a task between queues or to/from active tasks.
    
    Args:
        feature_id: ID of the feature to move
        from_location: Source location (queue name or "active:{agent}")
        to_location: Target location (queue name or "active:{agent}")
        
    Returns:
        Dict with operation status
    """
    try:
        state = read_project_state()
        
        # Remove from source location
        if from_location.startswith("active:"):
            agent = from_location.split(":", 1)[1]
            if agent in state.get("active_tasks", {}):
                if feature_id in state["active_tasks"][agent]:
                    state["active_tasks"][agent].remove(feature_id)
        elif from_location in ["parallel_ready_queue", "dependency_blocked_queue"]:
            if feature_id in state.get(from_location, []):
                state[from_location].remove(feature_id)
        else:
            return {"status": "error", "message": f"Invalid from_location: {from_location}"}
        
        # Add to target location
        if to_location.startswith("active:"):
            agent = to_location.split(":", 1)[1]
            if agent in state.get("active_tasks", {}):
                if feature_id not in state["active_tasks"][agent]:
                    state["active_tasks"][agent].append(feature_id)
        elif to_location in ["parallel_ready_queue", "dependency_blocked_queue"]:
            if feature_id not in state.get(to_location, []):
                state[to_location].append(feature_id)
        else:
            return {"status": "error", "message": f"Invalid to_location: {to_location}"}
        
        # Update state
        result = update_project_state(state)
        return result
        
    except Exception as e:
        return {"status": "error", "message": str(e)}

@mcp.tool()
def get_queue_status() -> Dict[str, Any]:
    """
    Get the current status of all queues and active tasks.
    
    Returns:
        Dict with comprehensive queue status information
    """
    try:
        state = read_project_state()
        
        return {
            "parallel_ready_queue": {
                "count": len(state.get("parallel_ready_queue", [])),
                "features": state.get("parallel_ready_queue", [])
            },
            "dependency_blocked_queue": {
                "count": len(state.get("dependency_blocked_queue", [])),
                "features": state.get("dependency_blocked_queue", [])
            },
            "active_tasks": {
                agent: {
                    "count": len(tasks),
                    "features": tasks,
                    "available_slots": MAX_CONCURRENT_TASKS - len(tasks)
                }
                for agent, tasks in state.get("active_tasks", {}).items()
            },
            "verification_counters": state.get("verification_counters", {}),
            "last_updated": state.get("last_updated")
        }
        
    except Exception as e:
        return {"status": "error", "message": str(e)}

@mcp.tool()
def check_dependencies_met(feature_id: str) -> Dict[str, Any]:
    """
    Check if all dependencies for a feature are met.
    
    Args:
        feature_id: ID of the feature to check
        
    Returns:
        Dict with dependency check results
    """
    try:
        if not os.path.exists(DESIGN_DOC_FILE):
            return {"dependencies_met": False, "message": "Design document not found"}
        
        with open(DESIGN_DOC_FILE, 'r') as f:
            design_doc = json.load(f)
        
        # Find the feature
        feature = None
        for f in design_doc.get("features", []):
            if f["id"] == feature_id:
                feature = f
                break
        
        if not feature:
            return {"dependencies_met": False, "message": f"Feature {feature_id} not found"}
        
        dependencies = feature.get("dependencies", [])
        unmet_dependencies = []
        
        # Check each dependency
        for dep_id in dependencies:
            dep_feature = None
            for f in design_doc.get("features", []):
                if f["id"] == dep_id:
                    dep_feature = f
                    break
            
            if not dep_feature:
                unmet_dependencies.append(f"{dep_id} (not found)")
            elif dep_feature.get("status") != "complete":
                unmet_dependencies.append(f"{dep_id} (status: {dep_feature.get('status', 'unknown')})")
            elif dep_feature.get("test_status") == "failing":
                unmet_dependencies.append(f"{dep_id} (failing tests)")
        
        return {
            "dependencies_met": len(unmet_dependencies) == 0,
            "total_dependencies": len(dependencies),
            "unmet_dependencies": unmet_dependencies
        }
        
    except Exception as e:
        return {"dependencies_met": False, "message": str(e)}

@mcp.tool()
def detect_circular_dependencies() -> Dict[str, Any]:
    """
    Detect circular dependencies in the feature dependency graph.
    
    Returns:
        Dict with circular dependency detection results
    """
    try:
        if not os.path.exists(DESIGN_DOC_FILE):
            return {"has_circular": False, "message": "Design document not found"}
        
        with open(DESIGN_DOC_FILE, 'r') as f:
            design_doc = json.load(f)
        
        # Build dependency graph
        graph = {}
        for feature in design_doc.get("features", []):
            feature_id = feature["id"]
            dependencies = feature.get("dependencies", [])
            graph[feature_id] = dependencies
        
        # Detect cycles using DFS
        visited = set()
        rec_stack = set()
        cycles = []
        
        def dfs(node, path):
            if node not in graph:
                return
            
            visited.add(node)
            rec_stack.add(node)
            path.append(node)
            
            for neighbor in graph[node]:
                if neighbor not in visited:
                    dfs(neighbor, path.copy())
                elif neighbor in rec_stack:
                    # Found a cycle
                    cycle_start = path.index(neighbor)
                    cycle = path[cycle_start:] + [neighbor]
                    cycles.append(cycle)
            
            rec_stack.remove(node)
        
        # Check all nodes
        for feature_id in graph:
            if feature_id not in visited:
                dfs(feature_id, [])
        
        # Update state with check timestamp
        update_project_state({
            "circular_dependency_check": {
                "dependency_graph": graph,
                "last_check": datetime.now(timezone.utc).isoformat()
            }
        })
        
        return {
            "has_circular": len(cycles) > 0,
            "cycles_found": cycles,
            "total_features": len(graph)
        }
        
    except Exception as e:
        return {"has_circular": False, "message": str(e)}

@mcp.tool()
def get_dependency_chain(feature_id: str) -> Dict[str, Any]:
    """
    Get the complete dependency chain for a feature.
    
    Args:
        feature_id: ID of the feature to analyze
        
    Returns:
        Dict with the dependency chain and levels
    """
    try:
        if not os.path.exists(DESIGN_DOC_FILE):
            return {"chain": [], "message": "Design document not found"}
        
        with open(DESIGN_DOC_FILE, 'r') as f:
            design_doc = json.load(f)
        
        # Build feature lookup
        features = {f["id"]: f for f in design_doc.get("features", [])}
        
        if feature_id not in features:
            return {"chain": [], "message": f"Feature {feature_id} not found"}
        
        # Build dependency chain using BFS
        chain = []
        visited = set()
        queue = [(feature_id, 0)]  # (feature_id, level)
        
        while queue:
            current_id, level = queue.pop(0)
            
            if current_id in visited:
                continue
            
            visited.add(current_id)
            
            if current_id in features:
                feature = features[current_id]
                chain.append({
                    "id": current_id,
                    "name": feature.get("name", "Unknown"),
                    "level": level,
                    "status": feature.get("status", "unknown"),
                    "dependencies": feature.get("dependencies", [])
                })
                
                # Add dependencies to queue
                for dep_id in feature.get("dependencies", []):
                    if dep_id not in visited:
                        queue.append((dep_id, level + 1))
        
        return {
            "chain": sorted(chain, key=lambda x: x["level"]),
            "total_levels": max([c["level"] for c in chain]) + 1 if chain else 0
        }
        
    except Exception as e:
        return {"chain": [], "message": str(e)}

@mcp.tool()
def resolve_dependency_order(cache_reverse_deps: bool = True) -> Dict[str, Any]:
    """
    Resolve the optimal processing order for all features based on dependencies.
    Optionally builds and caches reverse dependency mappings for change impact analysis.
    
    Args:
        cache_reverse_deps: Whether to build and cache reverse dependency mappings (default: True)
    
    Returns:
        Dict with ordered feature list, processing recommendations, and reverse mapping status
    """
    try:
        if not os.path.exists(DESIGN_DOC_FILE):
            return {"ordered_features": [], "message": "Design document not found"}
        
        with open(DESIGN_DOC_FILE, 'r') as f:
            design_doc = json.load(f)
        
        features = {f["id"]: f for f in design_doc.get("features", [])}
        
        # Topological sort using Kahn's algorithm
        in_degree = {}
        graph = {}  # forward graph: feature -> list of dependents
        reverse_graph = {}  # reverse graph: feature -> list of features that depend on it
        
        # Initialize
        for feature_id in features:
            in_degree[feature_id] = 0
            graph[feature_id] = []
            reverse_graph[feature_id] = []
        
        # Build graph and calculate in-degrees
        # Also build reverse dependency graph simultaneously
        for feature_id, feature in features.items():
            for dep_id in feature.get("dependencies", []):
                if dep_id in graph:
                    graph[dep_id].append(feature_id)
                    in_degree[feature_id] += 1
                    # Build reverse mapping: dep_id -> features that depend on it
                    reverse_graph[dep_id].append(feature_id)
        
        # Topological sort
        queue = [f_id for f_id, degree in in_degree.items() if degree == 0]
        ordered = []
        
        while queue:
            current = queue.pop(0)
            ordered.append(current)
            
            for neighbor in graph[current]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)
        
        # Check for cycles
        if len(ordered) != len(features):
            remaining = set(features.keys()) - set(ordered)
            return {
                "ordered_features": ordered,
                "has_cycles": True,
                "cyclic_features": list(remaining),
                "message": "Circular dependencies detected"
            }
        
        # Group by processing readiness
        ready_to_process = []
        needs_dependencies = []
        
        for feature_id in ordered:
            deps_check = check_dependencies_met(feature_id)
            if deps_check["dependencies_met"]:
                ready_to_process.append(feature_id)
            else:
                needs_dependencies.append(feature_id)
        
        # Cache reverse dependencies if enabled
        reverse_cache_status = "disabled"
        if cache_reverse_deps:
            try:
                state = read_project_state()
                state["reverse_dependency_cache"] = {
                    "mappings": reverse_graph,
                    "last_updated": datetime.now(timezone.utc).isoformat(),
                    "invalidated_features": []
                }
                update_project_state(state)
                reverse_cache_status = "cached"
                
                # Record analytics
                _record_dependency_analytics("cache_build", {
                    "total_features": len(features),
                    "total_reverse_mappings": sum(len(deps) for deps in reverse_graph.values()),
                    "features_with_dependents": sum(1 for deps in reverse_graph.values() if deps)
                })
            except Exception as cache_error:
                reverse_cache_status = f"cache_failed: {str(cache_error)}"
        
        return {
            "ordered_features": ordered,
            "has_cycles": False,
            "ready_to_process": ready_to_process,
            "needs_dependencies": needs_dependencies,
            "total_features": len(features),
            "reverse_cache_status": reverse_cache_status,
            "reverse_dependency_count": sum(len(deps) for deps in reverse_graph.values())
        }
        
    except Exception as e:
        return {"ordered_features": [], "message": str(e)}


# ============================================================================
# REVERSE DEPENDENCY TRACKING TOOLS
# ============================================================================

def _record_dependency_analytics(event_type: str, data: Dict[str, Any]) -> None:
    """
    Record dependency analytics event to historical analytics file.
    
    Args:
        event_type: Type of analytics event
        data: Event data to record
    """
    try:
        # Load or create analytics file
        if os.path.exists(ANALYTICS_FILE):
            with open(ANALYTICS_FILE, 'r') as f:
                analytics = json.load(f)
        else:
            analytics = {
                "created": datetime.now(timezone.utc).isoformat(),
                "events": [],
                "summary": {
                    "total_cache_builds": 0,
                    "total_cache_hits": 0,
                    "total_cache_misses": 0,
                    "total_subgraph_recomputes": 0,
                    "total_cascade_triggers": 0,
                    "max_cascade_depth_reached": 0,
                    "cascade_depth_exceeded_count": 0
                },
                "cascade_depth_history": [],
                "performance_trends": []
            }
        
        # Record event
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": event_type,
            "data": data
        }
        analytics["events"].append(event)
        
        # Update summary based on event type
        if event_type == "cache_build":
            analytics["summary"]["total_cache_builds"] += 1
        elif event_type == "cache_hit":
            analytics["summary"]["total_cache_hits"] += 1
        elif event_type == "cache_miss":
            analytics["summary"]["total_cache_misses"] += 1
        elif event_type == "subgraph_recompute":
            analytics["summary"]["total_subgraph_recomputes"] += 1
        elif event_type == "cascade_trigger":
            analytics["summary"]["total_cascade_triggers"] += 1
            cascade_depth = data.get("cascade_depth", 0)
            analytics["cascade_depth_history"].append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "depth": cascade_depth,
                "feature_id": data.get("source_feature")
            })
            if cascade_depth > analytics["summary"]["max_cascade_depth_reached"]:
                analytics["summary"]["max_cascade_depth_reached"] = cascade_depth
        elif event_type == "cascade_depth_exceeded":
            analytics["summary"]["cascade_depth_exceeded_count"] += 1
        
        # Keep only last 1000 events to manage file size
        if len(analytics["events"]) > 1000:
            analytics["events"] = analytics["events"][-1000:]
        
        # Write analytics file
        with open(ANALYTICS_FILE, 'w') as f:
            json.dump(analytics, f, indent=2)
            
    except Exception:
        pass  # Analytics recording should not fail the main operation


@mcp.tool()
def get_reverse_dependencies(feature_id: str, use_cache: bool = True) -> Dict[str, Any]:
    """
    Get all features that depend on a given feature (reverse dependency lookup).
    Uses cached mappings when available, falls back to on-demand computation.
    
    Args:
        feature_id: ID of the feature to find dependents for
        use_cache: Whether to use cached reverse mappings (default: True)
        
    Returns:
        Dict with list of dependent feature IDs and metadata
    """
    try:
        direct_dependents = []
        cache_used = False
        
        if use_cache:
            # Try to use cached reverse dependencies
            state = read_project_state()
            cache = state.get("reverse_dependency_cache", {})
            mappings = cache.get("mappings", {})
            invalidated = cache.get("invalidated_features", [])
            
            if mappings and feature_id not in invalidated:
                direct_dependents = mappings.get(feature_id, [])
                cache_used = True
                _record_dependency_analytics("cache_hit", {"feature_id": feature_id})
            else:
                _record_dependency_analytics("cache_miss", {"feature_id": feature_id, "reason": "invalidated" if feature_id in invalidated else "not_in_cache"})
        
        if not cache_used:
            # Compute on-demand by scanning design document
            if not os.path.exists(DESIGN_DOC_FILE):
                return {"dependents": [], "message": "Design document not found"}
            
            with open(DESIGN_DOC_FILE, 'r') as f:
                design_doc = json.load(f)
            
            for feature in design_doc.get("features", []):
                if feature_id in feature.get("dependencies", []):
                    direct_dependents.append(feature["id"])
        
        # Get additional metadata for dependents
        dependent_details = []
        if os.path.exists(DESIGN_DOC_FILE):
            with open(DESIGN_DOC_FILE, 'r') as f:
                design_doc = json.load(f)
            
            features = {f["id"]: f for f in design_doc.get("features", [])}
            for dep_id in direct_dependents:
                if dep_id in features:
                    dep_feature = features[dep_id]
                    dependent_details.append({
                        "id": dep_id,
                        "name": dep_feature.get("name", "Unknown"),
                        "status": dep_feature.get("status", "unknown"),
                        "test_status": dep_feature.get("test_status", "unknown")
                    })
        
        return {
            "feature_id": feature_id,
            "dependents": direct_dependents,
            "dependent_count": len(direct_dependents),
            "dependent_details": dependent_details,
            "cache_used": cache_used
        }
        
    except Exception as e:
        return {"dependents": [], "message": str(e)}


@mcp.tool()
def get_full_dependent_tree(feature_id: str, max_depth: int = None) -> Dict[str, Any]:
    """
    Get the complete tree of all features that depend on a feature, recursively.
    Respects cascade depth limits to prevent runaway traversals.
    
    Args:
        feature_id: ID of the root feature
        max_depth: Maximum depth to traverse (uses config default if not specified)
        
    Returns:
        Dict with full dependent tree and depth statistics
    """
    try:
        state = read_project_state()
        config = state.get("dependency_config", {})
        
        if max_depth is None:
            max_depth = config.get("max_cascade_depth", DEFAULT_MAX_CASCADE_DEPTH)
        
        dependent_tree = []
        visited = set()
        depth_exceeded = False
        max_depth_reached = 0
        
        def traverse_dependents(current_id: str, current_depth: int, path: List[str]):
            nonlocal depth_exceeded, max_depth_reached
            
            if current_id in visited:
                return  # Prevent circular traversal
            
            if current_depth > max_depth:
                depth_exceeded = True
                return
            
            visited.add(current_id)
            max_depth_reached = max(max_depth_reached, current_depth)
            
            # Get direct dependents
            result = get_reverse_dependencies(current_id, use_cache=True)
            direct_deps = result.get("dependents", [])
            
            for dep_id in direct_deps:
                node = {
                    "id": dep_id,
                    "depth": current_depth,
                    "path": path + [dep_id]
                }
                dependent_tree.append(node)
                
                # Recurse
                traverse_dependents(dep_id, current_depth + 1, path + [dep_id])
        
        # Start traversal from root
        traverse_dependents(feature_id, 1, [feature_id])
        
        # Group by depth level
        by_depth = {}
        for node in dependent_tree:
            depth = node["depth"]
            if depth not in by_depth:
                by_depth[depth] = []
            by_depth[depth].append(node["id"])
        
        result = {
            "root_feature": feature_id,
            "total_dependents": len(dependent_tree),
            "max_depth_reached": max_depth_reached,
            "depth_limit": max_depth,
            "depth_exceeded": depth_exceeded,
            "dependent_tree": dependent_tree,
            "by_depth_level": by_depth
        }
        
        if depth_exceeded:
            result["warning"] = f"Cascade depth limit ({max_depth}) exceeded. Some dependents may not be included."
            _record_dependency_analytics("cascade_depth_exceeded", {
                "feature_id": feature_id,
                "max_depth": max_depth,
                "total_found": len(dependent_tree)
            })
        
        return result
        
    except Exception as e:
        return {"dependent_tree": [], "message": str(e)}


@mcp.tool()
def update_dependency_subgraph(changed_feature_ids: List[str]) -> Dict[str, Any]:
    """
    Selectively recompute reverse dependency mappings for features whose dependency arrays changed.
    More efficient than full cache rebuild for incremental updates.
    
    Args:
        changed_feature_ids: List of feature IDs whose dependencies have changed
        
    Returns:
        Dict with recomputation results and affected features
    """
    try:
        if not os.path.exists(DESIGN_DOC_FILE):
            return {"status": "error", "message": "Design document not found"}
        
        with open(DESIGN_DOC_FILE, 'r') as f:
            design_doc = json.load(f)
        
        features = {f["id"]: f for f in design_doc.get("features", [])}
        state = read_project_state()
        cache = state.get("reverse_dependency_cache", {})
        current_mappings = cache.get("mappings", {})
        
        affected_features = set()
        updated_mappings = dict(current_mappings)
        
        for changed_id in changed_feature_ids:
            if changed_id not in features:
                continue
            
            feature = features[changed_id]
            new_dependencies = set(feature.get("dependencies", []))
            
            # Find what dependencies this feature previously had
            old_dependencies = set()
            for dep_id, dependents in current_mappings.items():
                if changed_id in dependents:
                    old_dependencies.add(dep_id)
            
            # Remove from old dependency mappings
            removed_deps = old_dependencies - new_dependencies
            for dep_id in removed_deps:
                if dep_id in updated_mappings and changed_id in updated_mappings[dep_id]:
                    updated_mappings[dep_id].remove(changed_id)
                    affected_features.add(dep_id)
            
            # Add to new dependency mappings
            added_deps = new_dependencies - old_dependencies
            for dep_id in added_deps:
                if dep_id not in updated_mappings:
                    updated_mappings[dep_id] = []
                if changed_id not in updated_mappings[dep_id]:
                    updated_mappings[dep_id].append(changed_id)
                    affected_features.add(dep_id)
            
            affected_features.add(changed_id)
        
        # Update the cache
        state["reverse_dependency_cache"] = {
            "mappings": updated_mappings,
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "invalidated_features": []  # Clear invalidations after recompute
        }
        update_project_state(state)
        
        _record_dependency_analytics("subgraph_recompute", {
            "changed_features": changed_feature_ids,
            "affected_features": list(affected_features)
        })
        
        return {
            "status": "success",
            "changed_features": changed_feature_ids,
            "affected_features": list(affected_features),
            "total_affected": len(affected_features),
            "cache_updated": True
        }
        
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
def invalidate_reverse_cache(feature_ids: List[str]) -> Dict[str, Any]:
    """
    Mark specific features as invalidated in the reverse dependency cache.
    Use this when dependency arrays change but full recomputation is deferred.
    
    Args:
        feature_ids: List of feature IDs to mark as invalidated
        
    Returns:
        Dict with invalidation status
    """
    try:
        state = read_project_state()
        cache = state.get("reverse_dependency_cache", {})
        invalidated = set(cache.get("invalidated_features", []))
        
        for feature_id in feature_ids:
            invalidated.add(feature_id)
        
        state["reverse_dependency_cache"]["invalidated_features"] = list(invalidated)
        update_project_state(state)
        
        return {
            "status": "success",
            "invalidated_features": list(invalidated),
            "total_invalidated": len(invalidated)
        }
        
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
def get_dependency_analytics() -> Dict[str, Any]:
    """
    Get historical dependency analytics and performance metrics.
    
    Returns:
        Dict with analytics summary, trends, and recommendations
    """
    try:
        if not os.path.exists(ANALYTICS_FILE):
            return {
                "status": "success",
                "message": "No analytics data collected yet",
                "summary": {},
                "recommendations": ["Run resolve_dependency_order to start collecting analytics"]
            }
        
        with open(ANALYTICS_FILE, 'r') as f:
            analytics = json.load(f)
        
        summary = analytics.get("summary", {})
        events = analytics.get("events", [])
        cascade_history = analytics.get("cascade_depth_history", [])
        
        # Calculate cache hit rate
        total_lookups = summary.get("total_cache_hits", 0) + summary.get("total_cache_misses", 0)
        cache_hit_rate = (summary.get("total_cache_hits", 0) / total_lookups * 100) if total_lookups > 0 else 0
        
        # Calculate average cascade depth
        avg_cascade_depth = 0
        if cascade_history:
            avg_cascade_depth = sum(h["depth"] for h in cascade_history) / len(cascade_history)
        
        # Generate recommendations
        recommendations = []
        
        if cache_hit_rate < 70 and total_lookups > 10:
            recommendations.append("Low cache hit rate detected. Consider running resolve_dependency_order more frequently.")
        
        if summary.get("cascade_depth_exceeded_count", 0) > 5:
            recommendations.append("Cascade depth limit exceeded multiple times. Consider increasing max_cascade_depth or reviewing deep dependency chains.")
        
        if summary.get("total_subgraph_recomputes", 0) > summary.get("total_cache_builds", 0) * 10:
            recommendations.append("High subgraph recompute rate. Consider full cache rebuilds more frequently for better performance.")
        
        # Recent events summary (last 24 hours)
        recent_cutoff = datetime.now(timezone.utc).isoformat()[:10]  # Today's date
        recent_events = [e for e in events if e["timestamp"][:10] == recent_cutoff]
        
        return {
            "status": "success",
            "summary": summary,
            "cache_hit_rate_percent": round(cache_hit_rate, 2),
            "average_cascade_depth": round(avg_cascade_depth, 2),
            "total_events_recorded": len(events),
            "recent_events_today": len(recent_events),
            "recommendations": recommendations,
            "analytics_since": analytics.get("created"),
            "cascade_depth_history_count": len(cascade_history)
        }
        
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ============================================================================
# CASCADE TRIGGER AND RE-TESTING WORKFLOW TOOLS
# ============================================================================

@mcp.tool()
def trigger_dependent_retest(feature_id: str, reason: str, cascade_depth: int = 0) -> Dict[str, Any]:
    """
    Trigger re-testing of features that depend on a changed/failed feature.
    Uses recursive cascading: tests direct dependents first, then cascades downstream
    only if those tests also fail.
    
    Args:
        feature_id: ID of the feature that changed/failed
        reason: Reason for triggering retest
        cascade_depth: Current depth in cascade (internal use)
        
    Returns:
        Dict with queued features and cascade information
    """
    try:
        state = read_project_state()
        config = state.get("dependency_config", {})
        max_depth = config.get("max_cascade_depth", DEFAULT_MAX_CASCADE_DEPTH)
        
        # Check cascade depth limit
        if cascade_depth >= max_depth:
            # Log warning and escalate to PM
            escalation = _create_cascade_escalation(feature_id, cascade_depth, max_depth, reason)
            
            _record_dependency_analytics("cascade_depth_exceeded", {
                "source_feature": feature_id,
                "cascade_depth": cascade_depth,
                "max_depth": max_depth,
                "reason": reason
            })
            
            return {
                "status": "depth_limit_reached",
                "feature_id": feature_id,
                "cascade_depth": cascade_depth,
                "max_depth": max_depth,
                "dependents_queued": 0,
                "warning": f"Cascade depth limit ({max_depth}) reached. Manual review required.",
                "escalation": escalation
            }
        
        # Get direct dependents
        reverse_result = get_reverse_dependencies(feature_id, use_cache=True)
        direct_dependents = reverse_result.get("dependents", [])
        
        if not direct_dependents:
            return {
                "status": "success",
                "feature_id": feature_id,
                "cascade_depth": cascade_depth,
                "dependents_queued": 0,
                "message": "No dependent features found"
            }
        
        # Queue direct dependents for re-testing
        queued_features = []
        retest_queue = state.get("pending_retest_queue", [])
        
        for dep_id in direct_dependents:
            # Check if already queued or in active testing
            if dep_id not in retest_queue:
                retest_entry = {
                    "feature_id": dep_id,
                    "triggered_by": feature_id,
                    "reason": reason,
                    "cascade_depth": cascade_depth + 1,
                    "queued_at": datetime.now(timezone.utc).isoformat()
                }
                retest_queue.append(dep_id)
                queued_features.append(retest_entry)
        
        # Update state with new retest queue
        state["pending_retest_queue"] = retest_queue
        update_project_state(state)
        
        _record_dependency_analytics("cascade_trigger", {
            "source_feature": feature_id,
            "cascade_depth": cascade_depth,
            "dependents_queued": len(queued_features),
            "reason": reason
        })
        
        return {
            "status": "success",
            "feature_id": feature_id,
            "cascade_depth": cascade_depth,
            "dependents_queued": len(queued_features),
            "queued_features": queued_features,
            "direct_dependents": direct_dependents
        }
        
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
def process_retest_queue() -> Dict[str, Any]:
    """
    Process the pending retest queue, moving features to the appropriate testing queue.
    Prioritizes by cascade depth (lower depth first) to enable proper cascade propagation.
    
    Returns:
        Dict with processing results and next features to test
    """
    try:
        state = read_project_state()
        retest_queue = state.get("pending_retest_queue", [])
        
        if not retest_queue:
            return {
                "status": "success",
                "message": "No features pending retest",
                "processed_count": 0,
                "remaining_count": 0
            }
        
        # Get feature details to sort by cascade depth
        features_with_depth = []
        for feature_id in retest_queue:
            # Default depth of 1 if not tracked elsewhere
            features_with_depth.append({"id": feature_id, "depth": 1})
        
        # Sort by depth (lower first for proper cascade)
        features_with_depth.sort(key=lambda x: x["depth"])
        
        # Move first batch to ready queue for testing
        batch_size = MAX_CONCURRENT_TASKS
        to_process = features_with_depth[:batch_size]
        processed_ids = [f["id"] for f in to_process]
        
        # Update queues
        for feature_id in processed_ids:
            if feature_id in retest_queue:
                retest_queue.remove(feature_id)
            # Add to ready queue for review agent
            add_to_queue(feature_id, "parallel_ready_queue")
        
        state["pending_retest_queue"] = retest_queue
        update_project_state(state)
        
        return {
            "status": "success",
            "processed_count": len(processed_ids),
            "processed_features": processed_ids,
            "remaining_count": len(retest_queue),
            "remaining_features": retest_queue
        }
        
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
def handle_retest_result(feature_id: str, test_passed: bool, original_trigger: str = None) -> Dict[str, Any]:
    """
    Handle the result of a re-test and determine whether to cascade further downstream.
    Only cascades to downstream dependents if the re-test fails.
    
    Args:
        feature_id: ID of the feature that was re-tested
        test_passed: Whether the re-test passed
        original_trigger: ID of the original feature that triggered the cascade
        
    Returns:
        Dict with cascade continuation decision and actions taken
    """
    try:
        state = read_project_state()
        
        if test_passed:
            # Test passed - no need to cascade further
            return {
                "status": "success",
                "feature_id": feature_id,
                "test_passed": True,
                "cascade_continued": False,
                "message": "Re-test passed. No further cascade needed."
            }
        
        # Test failed - cascade to downstream dependents
        # Determine current cascade depth from tracking
        cascade_info = None
        for entry in state.get("task_history", [])[-50:]:  # Check recent history
            if entry.get("feature_id") == feature_id and entry.get("action") == "retest_queued":
                cascade_info = entry
                break
        
        current_depth = cascade_info.get("cascade_depth", 1) if cascade_info else 1
        
        # Trigger cascade to downstream dependents
        cascade_result = trigger_dependent_retest(
            feature_id,
            reason=f"cascade_retest_failure:triggered_by={original_trigger or 'unknown'}",
            cascade_depth=current_depth
        )
        
        return {
            "status": "success",
            "feature_id": feature_id,
            "test_passed": False,
            "cascade_continued": True,
            "cascade_depth": current_depth,
            "cascade_result": cascade_result
        }
        
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _create_cascade_escalation(feature_id: str, cascade_depth: int, max_depth: int, reason: str) -> Dict[str, Any]:
    """
    Create a detailed escalation report for Project Manager when cascade depth is exceeded.
    Includes full dependency chain visualization and affected feature list.
    
    Args:
        feature_id: ID of the feature where depth limit was reached
        cascade_depth: Current cascade depth
        max_depth: Maximum allowed depth
        reason: Original reason for cascade
        
    Returns:
        Dict with escalation details for PM review
    """
    try:
        # Build full dependency chain visualization
        chain_result = get_dependency_chain(feature_id)
        dependent_tree = get_full_dependent_tree(feature_id, max_depth=max_depth + 2)
        
        # Get affected feature details
        affected_features = []
        if os.path.exists(DESIGN_DOC_FILE):
            with open(DESIGN_DOC_FILE, 'r') as f:
                design_doc = json.load(f)
            
            features = {f["id"]: f for f in design_doc.get("features", [])}
            
            for node in dependent_tree.get("dependent_tree", []):
                dep_id = node["id"]
                if dep_id in features:
                    f = features[dep_id]
                    affected_features.append({
                        "id": dep_id,
                        "name": f.get("name", "Unknown"),
                        "status": f.get("status", "unknown"),
                        "test_status": f.get("test_status", "unknown"),
                        "depth": node["depth"],
                        "path": node["path"]
                    })
        
        # Build text-based tree visualization
        tree_visualization = _build_tree_visualization(feature_id, dependent_tree)
        
        # Store escalation in state for PM review
        state = read_project_state()
        if "cascade_depth_exceeded" not in state:
            state["cascade_depth_exceeded"] = []
        
        escalation = {
            "id": str(uuid.uuid4()),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_feature": feature_id,
            "cascade_depth": cascade_depth,
            "max_depth": max_depth,
            "reason": reason,
            "affected_feature_count": len(affected_features),
            "affected_features": affected_features,
            "dependency_chain": chain_result.get("chain", []),
            "tree_visualization": tree_visualization,
            "status": "pending_review",
            "recommendations": [
                f"Review dependency chain depth starting from {feature_id}",
                "Consider increasing max_cascade_depth if chains are legitimately deep",
                "Evaluate if feature can be refactored to reduce dependency depth",
                "Manually trigger re-tests for remaining downstream features"
            ]
        }
        
        state["cascade_depth_exceeded"].append(escalation)
        update_project_state(state)
        
        return escalation
        
    except Exception as e:
        return {
            "id": str(uuid.uuid4()),
            "error": str(e),
            "source_feature": feature_id,
            "status": "escalation_creation_failed"
        }


def _build_tree_visualization(root_id: str, tree_data: Dict[str, Any]) -> str:
    """
    Build a text-based tree visualization of the dependency chain.
    
    Args:
        root_id: Root feature ID
        tree_data: Tree data from get_full_dependent_tree
        
    Returns:
        String with formatted tree visualization
    """
    lines = [f"Dependency Tree for: {root_id}", "=" * 50]
    
    by_depth = tree_data.get("by_depth_level", {})
    
    for depth in sorted(by_depth.keys()):
        features_at_depth = by_depth[depth]
        indent = "  " * depth
        prefix = "├── " if depth > 0 else ""
        
        lines.append(f"\nLevel {depth}:")
        for feature_id in features_at_depth:
            lines.append(f"{indent}{prefix}{feature_id}")
    
    if tree_data.get("depth_exceeded"):
        lines.append(f"\n⚠️  WARNING: Depth limit reached. Tree may be incomplete.")
    
    lines.append(f"\nTotal dependents: {tree_data.get('total_dependents', 0)}")
    lines.append(f"Max depth reached: {tree_data.get('max_depth_reached', 0)}")
    
    return "\n".join(lines)


@mcp.tool()
def get_cascade_escalations(status: str = None) -> Dict[str, Any]:
    """
    Get cascade depth escalations for Project Manager review.
    
    Args:
        status: Filter by status (pending_review, reviewed, resolved)
        
    Returns:
        Dict with escalation reports
    """
    try:
        state = read_project_state()
        escalations = state.get("cascade_depth_exceeded", [])
        
        if status:
            escalations = [e for e in escalations if e.get("status") == status]
        
        return {
            "status": "success",
            "total_escalations": len(escalations),
            "escalations": escalations,
            "pending_review_count": len([e for e in state.get("cascade_depth_exceeded", []) if e.get("status") == "pending_review"])
        }
        
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
def resolve_cascade_escalation(escalation_id: str, resolution: str, actions_taken: List[str] = None) -> Dict[str, Any]:
    """
    Mark a cascade escalation as resolved after PM review.
    
    Args:
        escalation_id: ID of the escalation to resolve
        resolution: Resolution description
        actions_taken: List of actions taken to resolve
        
    Returns:
        Dict with resolution status
    """
    try:
        state = read_project_state()
        escalations = state.get("cascade_depth_exceeded", [])
        
        found = False
        for escalation in escalations:
            if escalation.get("id") == escalation_id:
                escalation["status"] = "resolved"
                escalation["resolution"] = resolution
                escalation["actions_taken"] = actions_taken or []
                escalation["resolved_at"] = datetime.now(timezone.utc).isoformat()
                found = True
                break
        
        if not found:
            return {"status": "error", "message": f"Escalation {escalation_id} not found"}
        
        state["cascade_depth_exceeded"] = escalations
        update_project_state(state)
        
        return {
            "status": "success",
            "message": f"Escalation {escalation_id} resolved",
            "resolution": resolution
        }
        
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
def update_dependency_config(
    max_cascade_depth: int = None,
    enable_reverse_tracking: bool = None,
    enable_cascade_testing: bool = None
) -> Dict[str, Any]:
    """
    Update reverse dependency tracking configuration.
    
    Args:
        max_cascade_depth: Maximum cascade depth for re-testing (default: 5)
        enable_reverse_tracking: Enable/disable reverse dependency caching
        enable_cascade_testing: Enable/disable automatic cascade testing
        
    Returns:
        Dict with updated configuration
    """
    try:
        state = read_project_state()
        config = state.get("dependency_config", {
            "max_cascade_depth": DEFAULT_MAX_CASCADE_DEPTH,
            "enable_reverse_tracking": ENABLE_REVERSE_TRACKING,
            "enable_cascade_testing": ENABLE_CASCADE_TESTING
        })
        
        if max_cascade_depth is not None:
            config["max_cascade_depth"] = max_cascade_depth
        if enable_reverse_tracking is not None:
            config["enable_reverse_tracking"] = enable_reverse_tracking
        if enable_cascade_testing is not None:
            config["enable_cascade_testing"] = enable_cascade_testing
        
        state["dependency_config"] = config
        update_project_state(state)
        
        return {
            "status": "success",
            "config": config
        }
        
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
def find_feature_path(feature_id: str) -> Dict[str, Any]:
    """
    Find the path to a feature in the design document (handles nested children).
    
    Args:
        feature_id: ID of the feature to find
        
    Returns:
        Dict with the path to the feature and its current data
    """
    try:
        if not os.path.exists(DESIGN_DOC_FILE):
            return {"found": False, "message": "Design document not found"}
        
        with open(DESIGN_DOC_FILE, 'r') as f:
            design_doc = json.load(f)
        
        def search_features(features_list, path=[]):
            """Recursively search for feature in features list"""
            for i, feature in enumerate(features_list):
                current_path = path + [i]
                
                if feature.get("id") == feature_id:
                    return {
                        "found": True,
                        "path": current_path,
                        "feature": feature,
                        "parent_path": path[:-1] if path else None
                    }
                
                # Search in children if they exist
                children = feature.get("children", [])
                if children and isinstance(children[0], dict) if children else False:
                    # Children are feature objects, search recursively
                    child_result = search_features(children, current_path + ["children"])
                    if child_result["found"]:
                        return child_result
            
            return {"found": False}
        
        # Search in main features array
        features = design_doc.get("features", [])
        result = search_features(features, ["features"])
        
        if result["found"]:
            return {
                "found": True,
                "path": result["path"],
                "feature": result["feature"],
                "parent_path": result["parent_path"],
                "is_child": "children" in str(result["path"])
            }
        else:
            return {"found": False, "message": f"Feature {feature_id} not found"}
            
    except Exception as e:
        return {"found": False, "message": str(e)}

@mcp.tool()
def update_feature_by_id(feature_id: str, updates: Dict[str, Any]) -> Dict[str, str]:
    """
    Update a specific feature by ID, automatically finding its location in the JSON structure.
    
    Args:
        feature_id: ID of the feature to update
        updates: Dictionary of field updates to apply to the feature
        
    Returns:
        Dict with operation status and details
    """
    state_manager = StateManager()
    
    try:
        # Find the feature first
        path_result = find_feature_path(feature_id)
        if not path_result["found"]:
            return {"status": "error", "message": f"Feature {feature_id} not found"}
        
        # Acquire lock for atomic operation
        state_manager._acquire_lock(DESIGN_DOC_FILE)
        
        # Read current design document
        with open(DESIGN_DOC_FILE, 'r') as f:
            design_doc = json.load(f)
        
        # Create backup
        backup_path = f"{DESIGN_DOC_FILE}.backup"
        with open(backup_path, 'w') as f:
            json.dump(design_doc, f, indent=2)
        
        # Navigate to the feature using the path
        current_obj = design_doc
        feature_path = path_result["path"]
        
        # Navigate to the parent of the feature
        for path_element in feature_path[:-1]:
            current_obj = current_obj[path_element]
        
        # Get the feature object
        feature_index = feature_path[-1]
        feature = current_obj[feature_index]
        
        # Validate that this is the correct feature
        if feature.get("id") != feature_id:
            return {"status": "error", "message": "Feature ID mismatch during update"}
        
        # Apply updates
        original_values = {}
        for key, value in updates.items():
            # Store original value for rollback if needed
            original_values[key] = feature.get(key)
            
            # Apply the update
            if key in ["inputs", "outputs", "dependencies", "children"] and isinstance(value, list):
                # For arrays, replace entirely or merge based on update strategy
                if isinstance(feature.get(key), list):
                    # If it's an extend operation (indicated by special key)
                    if key.endswith("_extend"):
                        actual_key = key.replace("_extend", "")
                        if actual_key in feature:
                            feature[actual_key].extend(value)
                        else:
                            feature[actual_key] = value
                    else:
                        feature[key] = value
                else:
                    feature[key] = value
            elif key in ["error_log"] and isinstance(value, dict):
                # For error_log, append to the array
                if "error_log" not in feature:
                    feature["error_log"] = []
                feature["error_log"].append(value)
            else:
                # Simple field update
                feature[key] = value
        
        # Update the feature in the document
        current_obj[feature_index] = feature
        
        # Validate the updated document structure
        if "features" not in design_doc or not isinstance(design_doc["features"], list):
            raise ValueError("Invalid document structure after update")
        
        # Write to temporary file first
        temp_file = f"{DESIGN_DOC_FILE}.tmp"
        with open(temp_file, 'w') as f:
            json.dump(design_doc, f, indent=2)
        
        # Atomic rename
        shutil.move(temp_file, DESIGN_DOC_FILE)
        
        # Clean up backup on success
        os.remove(backup_path)
        
        # Invalidate reverse dependency cache if dependencies changed
        dependencies_changed = "dependencies" in updates
        if dependencies_changed:
            try:
                # Trigger selective subgraph recomputation
                update_dependency_subgraph([feature_id])
            except Exception:
                # Cache update failure should not fail the main operation
                # Just invalidate the cache entry instead
                try:
                    invalidate_reverse_cache([feature_id])
                except Exception:
                    pass
        
        return {
            "status": "success", 
            "message": f"Feature {feature_id} updated successfully",
            "updated_fields": list(updates.keys()),
            "path": str(path_result["path"]),
            "dependencies_cache_updated": dependencies_changed
        }
        
    except Exception as e:
        # Attempt to restore from backup if it exists
        backup_path = f"{DESIGN_DOC_FILE}.backup"
        if os.path.exists(backup_path):
            try:
                shutil.move(backup_path, DESIGN_DOC_FILE)
            except:
                pass  # Backup restore failed, but don't mask the original error
        
        return {"status": "error", "message": str(e)}
    finally:
        state_manager._release_lock()

@mcp.tool()
def add_feature_to_document(feature_data: Dict[str, Any], parent_id: Optional[str] = None) -> Dict[str, str]:
    """
    Add a new feature to the design document, either as a top-level feature or as a child.
    
    Args:
        feature_data: Complete feature object to add
        parent_id: Optional parent feature ID to add as child (if None, adds to top level)
        
    Returns:
        Dict with operation status and details
    """
    state_manager = StateManager()
    
    try:
        # Generate ID automatically if not provided or invalid
        feature_id = feature_data.get("id")
        if not feature_id:
            # Auto-generate ID
            feature_type = feature_data.get("type", "feature")
            generation_result = generate_feature_id(feature_type, parent_id)
            if generation_result["status"] != "success":
                return generation_result
            feature_id = generation_result["generated_id"]
            feature_data["id"] = feature_id
        else:
            # Validate provided ID
            validation_result = validate_feature_id_format(feature_id)
            if not validation_result["is_valid"]:
                # Auto-generate new ID to replace invalid one
                feature_type = feature_data.get("type", "feature")
                generation_result = generate_feature_id(feature_type, parent_id)
                if generation_result["status"] != "success":
                    return generation_result
                feature_id = generation_result["generated_id"]
                feature_data["id"] = feature_id
        
        # Check if feature already exists (after ID generation/validation)
        existing_check = find_feature_path(feature_id)
        if existing_check["found"]:
            return {"status": "error", "message": f"Feature {feature_id} already exists"}
        
        # Acquire lock for atomic operation
        state_manager._acquire_lock(DESIGN_DOC_FILE)
        
        # Read or create design document
        if os.path.exists(DESIGN_DOC_FILE):
            with open(DESIGN_DOC_FILE, 'r') as f:
                design_doc = json.load(f)
        else:
            design_doc = {"features": []}
        
        # Create backup
        backup_path = f"{DESIGN_DOC_FILE}.backup"
        with open(backup_path, 'w') as f:
            json.dump(design_doc, f, indent=2)
        
        # Set default values for required fields
        feature_defaults = {
            "name": feature_data.get("name", "Unnamed Feature"),
            "description": feature_data.get("description", ""),
            "type": feature_data.get("type", "feature"),
            "status": feature_data.get("status", "pending"),
            "test_status": feature_data.get("test_status", "not_tested"),
            "inputs": feature_data.get("inputs", []),
            "outputs": feature_data.get("outputs", []),
            "dependencies": feature_data.get("dependencies", []),
            "children": feature_data.get("children", []),
            "error_log": feature_data.get("error_log", [])
        }
        
        # Merge with provided data
        new_feature = {**feature_defaults, **feature_data}
        
        if parent_id:
            # Add as child feature
            parent_path = find_feature_path(parent_id)
            if not parent_path["found"]:
                return {"status": "error", "message": f"Parent feature {parent_id} not found"}
            
            # Navigate to parent feature
            current_obj = design_doc
            for path_element in parent_path["path"][:-1]:
                current_obj = current_obj[path_element]
            
            parent_feature = current_obj[parent_path["path"][-1]]
            
            # Initialize children array if it doesn't exist
            if "children" not in parent_feature:
                parent_feature["children"] = []
            
            # Add as child
            parent_feature["children"].append(new_feature)
            location = f"Child of {parent_id}"
        else:
            # Add as top-level feature
            if "features" not in design_doc:
                design_doc["features"] = []
            
            design_doc["features"].append(new_feature)
            location = "Top-level feature"
        
        # Write to temporary file first
        temp_file = f"{DESIGN_DOC_FILE}.tmp"
        with open(temp_file, 'w') as f:
            json.dump(design_doc, f, indent=2)
        
        # Atomic rename
        shutil.move(temp_file, DESIGN_DOC_FILE)
        
        # Clean up backup on success
        os.remove(backup_path)
        
        return {
            "status": "success",
            "message": f"Feature {feature_id} added successfully",
            "location": location,
            "feature_id": feature_id
        }
        
    except Exception as e:
        # Attempt to restore from backup if it exists
        backup_path = f"{DESIGN_DOC_FILE}.backup"
        if os.path.exists(backup_path):
            try:
                shutil.move(backup_path, DESIGN_DOC_FILE)
            except:
                pass
        
        return {"status": "error", "message": str(e)}
    finally:
        state_manager._release_lock()

@mcp.tool()
def remove_feature_from_document(feature_id: str) -> Dict[str, str]:
    """
    Remove a feature from the design document by ID.
    
    Args:
        feature_id: ID of the feature to remove
        
    Returns:
        Dict with operation status and details
    """
    state_manager = StateManager()
    
    try:
        # Find the feature first
        path_result = find_feature_path(feature_id)
        if not path_result["found"]:
            return {"status": "error", "message": f"Feature {feature_id} not found"}
        
        # Check if any other features depend on this one
        dependencies_check = []
        if os.path.exists(DESIGN_DOC_FILE):
            with open(DESIGN_DOC_FILE, 'r') as f:
                design_doc = json.load(f)
            
            def check_dependencies_recursive(features_list):
                dependents = []
                for feature in features_list:
                    if feature_id in feature.get("dependencies", []):
                        dependents.append(feature["id"])
                    # Check children
                    children = feature.get("children", [])
                    if children and isinstance(children[0], dict) if children else False:
                        dependents.extend(check_dependencies_recursive(children))
                return dependents
            
            dependencies_check = check_dependencies_recursive(design_doc.get("features", []))
        
        if dependencies_check:
            return {
                "status": "error", 
                "message": f"Cannot remove feature {feature_id}: depended on by {', '.join(dependencies_check)}"
            }
        
        # Acquire lock for atomic operation
        state_manager._acquire_lock(DESIGN_DOC_FILE)
        
        # Read design document again (in case it changed)
        with open(DESIGN_DOC_FILE, 'r') as f:
            design_doc = json.load(f)
        
        # Create backup
        backup_path = f"{DESIGN_DOC_FILE}.backup"
        with open(backup_path, 'w') as f:
            json.dump(design_doc, f, indent=2)
        
        # Navigate to the parent container
        current_obj = design_doc
        feature_path = path_result["path"]
        
        for path_element in feature_path[:-1]:
            current_obj = current_obj[path_element]
        
        # Remove the feature
        feature_index = feature_path[-1]
        removed_feature = current_obj.pop(feature_index)
        
        # Write to temporary file first
        temp_file = f"{DESIGN_DOC_FILE}.tmp"
        with open(temp_file, 'w') as f:
            json.dump(design_doc, f, indent=2)
        
        # Atomic rename
        shutil.move(temp_file, DESIGN_DOC_FILE)
        
        # Clean up backup on success
        os.remove(backup_path)
        
        return {
            "status": "success",
            "message": f"Feature {feature_id} removed successfully",
            "removed_feature": removed_feature.get("name", "Unknown")
        }
        
    except Exception as e:
        # Attempt to restore from backup if it exists
        backup_path = f"{DESIGN_DOC_FILE}.backup"
        if os.path.exists(backup_path):
            try:
                shutil.move(backup_path, DESIGN_DOC_FILE)
            except:
                pass
        
        return {"status": "error", "message": str(e)}
    finally:
        state_manager._release_lock()

@mcp.tool()
def promote_completed_dependencies(feature_id: str) -> Dict[str, str]:
    """
    Check new_dependencies and new_child_features for completed items and promote them
    to the main dependencies/children lists.
    
    Args:
        feature_id: ID of the feature to process
        
    Returns:
        Dict with operation status and promotion details
    """
    try:
        # Find the feature
        path_result = find_feature_path(feature_id)
        if not path_result["found"]:
            return {"status": "error", "message": f"Feature {feature_id} not found"}
        
        feature = path_result["feature"]
        new_dependencies = feature.get("new_dependencies", [])
        new_child_features = feature.get("new_child_features", [])
        
        promoted_deps = []
        promoted_children = []
        updates = {}
        
        # Check new_dependencies for completed items
        if new_dependencies:
            remaining_new_deps = []
            current_deps = feature.get("dependencies", [])
            
            for dep in new_dependencies:
                if isinstance(dep, dict):
                    dep_id = dep.get("id")
                    dep_status = dep.get("status")
                    
                    # Check if dependency is complete and tested
                    if dep_status == "complete":
                        dep_check = check_dependencies_met(dep_id)
                        if dep_check.get("dependencies_met", False):
                            # Promote to main dependencies
                            if dep_id not in current_deps:
                                current_deps.append(dep_id)
                                promoted_deps.append(dep_id)
                        else:
                            remaining_new_deps.append(dep)
                    else:
                        remaining_new_deps.append(dep)
                else:
                    # Handle string format
                    remaining_new_deps.append(dep)
            
            if promoted_deps:
                updates["dependencies"] = current_deps
                updates["new_dependencies"] = remaining_new_deps
        
        # Check new_child_features for completed items  
        if new_child_features:
            remaining_new_children = []
            current_children = feature.get("children", [])
            
            for child in new_child_features:
                if isinstance(child, dict):
                    child_id = child.get("id")
                    child_status = child.get("status")
                    
                    # Check if child is complete and tested
                    if child_status == "complete":
                        # Get full child feature data
                        child_path = find_feature_path(child_id)
                        if child_path["found"]:
                            child_feature = child_path["feature"]
                            test_status = child_feature.get("test_status")
                            
                            if test_status == "passing":
                                # Promote to main children
                                # Check if it's already in children (by ID)
                                existing_child_ids = []
                                for existing_child in current_children:
                                    if isinstance(existing_child, dict):
                                        existing_child_ids.append(existing_child.get("id"))
                                    else:
                                        existing_child_ids.append(existing_child)
                                
                                if child_id not in existing_child_ids:
                                    if isinstance(current_children[0], dict) if current_children else True:
                                        # Children are full objects
                                        current_children.append(child_feature)
                                    else:
                                        # Children are just IDs
                                        current_children.append(child_id)
                                    promoted_children.append(child_id)
                            else:
                                remaining_new_children.append(child)
                        else:
                            remaining_new_children.append(child)
                    else:
                        remaining_new_children.append(child)
                else:
                    # Handle string format
                    remaining_new_children.append(child)
            
            if promoted_children:
                updates["children"] = current_children
                updates["new_child_features"] = remaining_new_children
        
        # Apply updates if any promotions occurred
        if updates:
            result = update_feature_by_id(feature_id, updates)
            if result["status"] == "success":
                return {
                    "status": "success",
                    "message": f"Promoted items for feature {feature_id}",
                    "promoted_dependencies": promoted_deps,
                    "promoted_children": promoted_children,
                    "total_promotions": len(promoted_deps) + len(promoted_children)
                }
            else:
                return result
        else:
            return {
                "status": "success", 
                "message": "No items ready for promotion",
                "promoted_dependencies": [],
                "promoted_children": [],
                "total_promotions": 0
            }
    
    except Exception as e:
        return {"status": "error", "message": str(e)}

@mcp.tool()
def update_feature_status_with_workflow(feature_id: str, new_status: str) -> Dict[str, str]:
    """
    Update feature status and handle workflow transitions including dependency/children promotion.
    
    Args:
        feature_id: ID of the feature to update
        new_status: New status to set
        
    Returns:
        Dict with operation status and workflow details
    """
    try:
        # Valid status transitions
        valid_statuses = ["pending", "designing", "implementing", "testing", "failed", "complete"]
        if new_status not in valid_statuses:
            return {"status": "error", "message": f"Invalid status: {new_status}"}
        
        # Get current feature state
        path_result = find_feature_path(feature_id)
        if not path_result["found"]:
            return {"status": "error", "message": f"Feature {feature_id} not found"}
        
        current_feature = path_result["feature"]
        current_status = current_feature.get("status", "pending")
        
        # Prepare updates
        updates = {"status": new_status}
        workflow_actions = []
        
        # Handle status-specific workflow logic
        if new_status == "complete":
            # When feature completes, promote any completed dependencies/children
            promotion_result = promote_completed_dependencies(feature_id)
            if promotion_result["status"] == "success":
                workflow_actions.append(f"Promoted {promotion_result['total_promotions']} items")
            
            # Set test_status to passing if not already set
            if current_feature.get("test_status") != "passing":
                updates["test_status"] = "passing"
                workflow_actions.append("Set test_status to passing")
        
        elif new_status == "testing":
            # When entering testing phase, check if ready
            spec_complete = (
                current_feature.get("inputs") is not None and
                current_feature.get("outputs") is not None and
                current_feature.get("location") is not None
            )
            if not spec_complete:
                return {
                    "status": "error", 
                    "message": "Feature not ready for testing - missing inputs, outputs, or location"
                }
            
            updates["test_status"] = "not_tested"
            workflow_actions.append("Reset test_status to not_tested")
        
        elif new_status == "implementing":
            # When entering implementation phase, validate design is complete
            deps_check = check_dependencies_met(feature_id)
            if not deps_check["dependencies_met"]:
                return {
                    "status": "error",
                    "message": f"Cannot implement - unmet dependencies: {deps_check['unmet_dependencies']}"
                }
            
            workflow_actions.append("Dependencies validated for implementation")
        
        elif new_status == "failed":
            # Log failure in error_log
            error_entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "previous_status": current_status,
                "error_type": "status_failure",
                "message": f"Feature failed during {current_status} phase"
            }
            updates["error_log"] = error_entry  # Will be appended by update_feature_by_id
            workflow_actions.append("Added failure entry to error_log")
        
        # Apply the updates
        result = update_feature_by_id(feature_id, updates)
        
        if result["status"] == "success":
            # Trigger reverse dependency cascade for significant status changes
            cascade_result = None
            state = read_project_state()
            config = state.get("dependency_config", {})
            
            if config.get("enable_cascade_testing", True):
                # Trigger cascade when feature fails or changes in ways that might affect dependents
                if new_status in ["failed", "implementing"]:
                    cascade_result = trigger_dependent_retest(
                        feature_id,
                        reason=f"dependency_status_change:{current_status}->{new_status}"
                    )
                    if cascade_result.get("dependents_queued", 0) > 0:
                        workflow_actions.append(f"Triggered retest for {cascade_result['dependents_queued']} dependent features")
            
            return {
                "status": "success",
                "message": f"Feature {feature_id} status updated from {current_status} to {new_status}",
                "workflow_actions": workflow_actions,
                "status_transition": f"{current_status} -> {new_status}",
                "cascade_result": cascade_result
            }
        else:
            return result
    
    except Exception as e:
        return {"status": "error", "message": str(e)}

@mcp.tool()
def validate_feature_workflow_state(feature_id: str) -> Dict[str, Any]:
    """
    Validate that a feature's current state is consistent with workflow expectations.
    
    Args:
        feature_id: ID of the feature to validate
        
    Returns:
        Dict with validation results and recommendations
    """
    try:
        # Find the feature
        path_result = find_feature_path(feature_id)
        if not path_result["found"]:
            return {"valid": False, "message": f"Feature {feature_id} not found"}
        
        feature = path_result["feature"]
        status = feature.get("status", "pending")
        test_status = feature.get("test_status", "not_tested")
        issues = []
        recommendations = []
        
        # Validate based on current status
        if status == "pending":
            if not feature.get("description"):
                issues.append("Missing description for pending feature")
                recommendations.append("Add feature description before design phase")
        
        elif status == "designing":
            if not feature.get("inputs"):
                issues.append("Design phase but inputs not defined")
            if not feature.get("outputs"): 
                issues.append("Design phase but outputs not defined")
        
        elif status == "implementing":
            required_fields = ["inputs", "outputs"]
            for field in required_fields:
                if not feature.get(field):
                    issues.append(f"Implementation phase but {field} not defined")
                    recommendations.append(f"Complete {field} specification")
            
            # Check dependencies
            deps_check = check_dependencies_met(feature_id)
            if not deps_check["dependencies_met"]:
                issues.append(f"Implementation phase but dependencies not met: {deps_check['unmet_dependencies']}")
                recommendations.append("Complete dependent features first")
        
        elif status == "testing":
            if not feature.get("location"):
                issues.append("Testing phase but location not set")
                recommendations.append("Set implementation location")
            
            if test_status not in ["not_tested", "passing", "failing", "partial"]:
                issues.append(f"Invalid test_status: {test_status}")
        
        elif status == "complete":
            if test_status != "passing":
                issues.append(f"Complete status but test_status is {test_status}")
                recommendations.append("Ensure all tests pass before marking complete")
        
        # Check for promotable items
        new_deps = feature.get("new_dependencies", [])
        new_children = feature.get("new_child_features", [])
        
        promotable_deps = []
        promotable_children = []
        
        for dep in new_deps:
            if isinstance(dep, dict) and dep.get("status") == "complete":
                promotable_deps.append(dep.get("id"))
        
        for child in new_children:
            if isinstance(child, dict) and child.get("status") == "complete":
                child_id = child.get("id") 
                child_path = find_feature_path(child_id)
                if child_path["found"]:
                    child_feature = child_path["feature"]
                    if child_feature.get("test_status") == "passing":
                        promotable_children.append(child_id)
        
        if promotable_deps:
            recommendations.append(f"Promote completed dependencies: {', '.join(promotable_deps)}")
        
        if promotable_children:
            recommendations.append(f"Promote completed children: {', '.join(promotable_children)}")
        
        return {
            "valid": len(issues) == 0,
            "feature_id": feature_id,
            "current_status": status,
            "test_status": test_status,
            "issues": issues,
            "recommendations": recommendations,
            "promotable_dependencies": promotable_deps,
            "promotable_children": promotable_children
        }
    
    except Exception as e:
        return {"valid": False, "message": str(e)}

@mcp.tool()
def generate_feature_id(prefix: str = DEFAULT_ID_PREFIX, parent_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Generate unique hierarchical feature ID with unlimited nesting levels.
    
    Args:
        prefix: ID prefix type (feature, dependency, function, class, module, literal)
        parent_id: Parent feature ID for child features (creates next nesting level)
        
    Returns:
        Dict with generated ID and metadata
    """
    try:
        state_manager = StateManager()
        
        if not os.path.exists(DESIGN_DOC_FILE):
            # First feature
            if parent_id:
                return {"status": "error", "message": "Cannot create child feature when no design document exists"}
            new_id = f"{prefix}-01"
            return {
                "status": "success", 
                "generated_id": new_id,
                "level": 1,
                "segments": [1],
                "is_root": True
            }
        
        with open(DESIGN_DOC_FILE, 'r') as f:
            design_doc = json.load(f)
        
        existing_ids = set()
        
        def collect_ids(features_list):
            """Recursively collect all feature IDs"""
            for feature in features_list:
                existing_ids.add(feature.get("id", ""))
                children = feature.get("children", [])
                if children and isinstance(children[0], dict) if children else False:
                    collect_ids(children)
        
        collect_ids(design_doc.get("features", []))
        
        if parent_id:
            # Generate child ID (add one more level)
            if parent_id not in existing_ids:
                return {"status": "error", "message": f"Parent feature {parent_id} does not exist"}
            
            # Parse parent ID
            parent_parse = state_manager._parse_feature_id(parent_id)
            if not parent_parse["valid"]:
                return {"status": "error", "message": f"Parent ID has invalid format: {parent_id}"}
            
            parent_prefix = parent_parse["prefix"]
            parent_segments = parent_parse["segments"]
            
            # Find all child IDs that start with parent pattern
            child_pattern = re.escape(parent_id) + r"-(\d+)(?:-\d+)*$"
            child_numbers = []
            
            for existing_id in existing_ids:
                child_match = re.match(child_pattern, existing_id)
                if child_match:
                    child_numbers.append(int(child_match.group(1)))
            
            # Get next available child number
            next_child_num = max(child_numbers) + 1 if child_numbers else 1
            
            # Build new child ID
            new_segments = parent_segments + [next_child_num]
            new_id = state_manager._build_feature_id(parent_prefix, new_segments)
            
            return {
                "status": "success",
                "generated_id": new_id,
                "level": len(new_segments),
                "segments": new_segments,
                "parent_id": parent_id,
                "is_root": False
            }
        
        else:
            # Generate root-level ID
            root_pattern = rf"{prefix}-(\d+)(?:-\d+)*$"
            root_numbers = []
            
            for existing_id in existing_ids:
                root_match = re.match(root_pattern, existing_id)
                if root_match:
                    root_numbers.append(int(root_match.group(1)))
            
            # Get next available root number
            next_root_num = max(root_numbers) + 1 if root_numbers else 1
            
            # Build new root ID
            new_segments = [next_root_num]
            new_id = state_manager._build_feature_id(prefix, new_segments)
            
            return {
                "status": "success",
                "generated_id": new_id,
                "level": 1,
                "segments": new_segments,
                "is_root": True
            }
    
    except Exception as e:
        return {"status": "error", "message": str(e)}

@mcp.tool()
def validate_feature_id_format(feature_id: str, expected_prefix: Optional[str] = None) -> Dict[str, Any]:
    """
    Validate feature ID format and uniqueness with unlimited nesting support.
    
    Args:
        feature_id: ID to validate
        expected_prefix: Expected prefix type (feature, dependency, etc.)
        
    Returns:
        Dict with validation results and issues
    """
    try:
        state_manager = StateManager()
        issues = []
        
        # Basic format validation
        if not feature_id or not isinstance(feature_id, str):
            return {
                "is_valid": False,
                "issues": ["Feature ID must be a non-empty string"],
                "format_valid": False,
                "unique": False
            }
        
        # Parse the ID
        parse_result = state_manager._parse_feature_id(feature_id)
        format_valid = parse_result["valid"]
        
        if not format_valid:
            issues.append(parse_result["message"])
        
        detected_prefix = parse_result.get("prefix") if format_valid else None
        detected_type = parse_result.get("type") if format_valid else None
        nesting_level = parse_result.get("level", 0) if format_valid else 0
        
        # Check expected prefix
        if expected_prefix and detected_type != expected_prefix:
            issues.append(f"Expected prefix '{expected_prefix}' but detected '{detected_type}'")
        
        # Check uniqueness
        uniqueness_check = find_feature_path(feature_id)
        is_unique = not uniqueness_check["found"]
        
        if not is_unique:
            issues.append(f"Feature ID '{feature_id}' already exists")
        
        return {
            "is_valid": len(issues) == 0,
            "issues": issues,
            "format_valid": format_valid,
            "detected_prefix": detected_prefix,
            "detected_type": detected_type,
            "nesting_level": nesting_level,
            "segments": parse_result.get("segments", []) if format_valid else [],
            "unique": is_unique,
            "feature_id": feature_id
        }
    
    except Exception as e:
        return {
            "is_valid": False,
            "issues": [str(e)],
            "format_valid": False,
            "unique": False
        }

def migrate_legacy_feature_ids() -> Dict[str, Any]:
    """
    Automatically migrate all non-conforming feature IDs to new hierarchical format.
    Runs on startup to ensure all features have proper IDs.
    
    Returns:
        Dict with migration results and statistics
    """
    try:
        if not os.path.exists(DESIGN_DOC_FILE):
            return {
                "status": "success",
                "message": "No design document found, no migration needed",
                "migrated_count": 0
            }
        
        with open(DESIGN_DOC_FILE, 'r') as f:
            design_doc = json.load(f)
        
        migration_log = []
        migrated_count = 0
        id_mapping = {}  # old_id -> new_id
        
        def migrate_features_recursive(features_list, parent_id=None):
            nonlocal migrated_count
            
            for i, feature in enumerate(features_list):
                old_id = feature.get("id", "")
                
                # Check if ID needs migration
                validation = validate_feature_id_format(old_id)
                if not validation["format_valid"]:
                    # Generate new ID
                    feature_type = feature.get("type", "feature")
                    generation_result = generate_feature_id(feature_type, parent_id)
                    
                    if generation_result["status"] == "success":
                        new_id = generation_result["generated_id"]
                        id_mapping[old_id] = new_id
                        feature["id"] = new_id
                        migrated_count += 1
                        
                        migration_entry = {
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "old_id": old_id,
                            "new_id": new_id,
                            "feature_name": feature.get("name", "Unknown"),
                            "feature_type": feature.get("type", "feature"),
                            "nesting_level": generation_result.get("level", 1),
                            "parent_id": parent_id
                        }
                        migration_log.append(migration_entry)
                
                # Migrate children recursively
                current_id = feature.get("id")
                children = feature.get("children", [])
                if children and isinstance(children[0], dict) if children else False:
                    migrate_features_recursive(children, current_id)
        
        # Migrate all features
        migrate_features_recursive(design_doc.get("features", []))
        
        # Update all dependency references
        def update_dependencies_recursive(features_list):
            for feature in features_list:
                # Update dependencies array
                dependencies = feature.get("dependencies", [])
                updated_deps = []
                for dep_id in dependencies:
                    new_dep_id = id_mapping.get(dep_id, dep_id)
                    updated_deps.append(new_dep_id)
                feature["dependencies"] = updated_deps
                
                # Update children
                children = feature.get("children", [])
                if children and isinstance(children[0], dict) if children else False:
                    update_dependencies_recursive(children)
        
        update_dependencies_recursive(design_doc.get("features", []))
        
        # Update state file if it exists
        state_updated = False
        if os.path.exists(STATE_FILE):
            state = read_project_state()
            
            # Update queues
            for queue_name in ["parallel_ready_queue", "dependency_blocked_queue"]:
                if queue_name in state:
                    updated_queue = []
                    for feature_id in state[queue_name]:
                        new_id = id_mapping.get(feature_id, feature_id)
                        updated_queue.append(new_id)
                    state[queue_name] = updated_queue
            
            # Update active tasks
            if "active_tasks" in state:
                for agent, tasks in state["active_tasks"].items():
                    updated_tasks = []
                    for feature_id in tasks:
                        new_id = id_mapping.get(feature_id, feature_id)
                        updated_tasks.append(new_id)
                    state["active_tasks"][agent] = updated_tasks
            
            update_project_state(state)
            state_updated = True
        
        # Save updated design document
        if migrated_count > 0:
            backup_path = f"{DESIGN_DOC_FILE}.pre_migration_backup"
            shutil.copy2(DESIGN_DOC_FILE, backup_path)
            
            with open(DESIGN_DOC_FILE, 'w') as f:
                json.dump(design_doc, f, indent=2)
        
        # Save migration log
        if migration_log:
            with open(MIGRATION_LOG_FILE, 'w') as f:
                json.dump(migration_log, f, indent=2)
        
        return {
            "status": "success",
            "message": f"Migration completed successfully",
            "migrated_count": migrated_count,
            "id_mappings": len(id_mapping),
            "state_updated": state_updated,
            "migration_log_created": len(migration_log) > 0
        }
    
    except Exception as e:
        return {"status": "error", "message": str(e)}

def validate_feature_id_batch(feature_ids: List[str], expected_prefix: Optional[str] = None) -> Dict[str, Any]:
    """
    Validate multiple feature IDs efficiently. Internal utility function.
    
    Args:
        feature_ids: List of IDs to validate
        expected_prefix: Expected prefix for all IDs
        
    Returns:
        Dict with aggregated validation results
    """
    try:
        if len(feature_ids) > MAX_BATCH_SIZE:
            return {
                "status": "error",
                "message": f"Batch size {len(feature_ids)} exceeds maximum {MAX_BATCH_SIZE}"
            }
        
        valid_count = 0
        invalid_ids = []
        duplicate_ids = []
        format_invalid_ids = []
        detailed_results = {}
        
        # Check for duplicates within batch
        id_counts = defaultdict(int)
        for feature_id in feature_ids:
            id_counts[feature_id] += 1
            if id_counts[feature_id] > 1:
                duplicate_ids.append(feature_id)
        
        # Validate each ID
        for feature_id in feature_ids:
            validation = validate_feature_id_format(feature_id, expected_prefix)
            detailed_results[feature_id] = validation
            
            if validation["is_valid"]:
                valid_count += 1
            else:
                invalid_ids.append(feature_id)
                if not validation["format_valid"]:
                    format_invalid_ids.append(feature_id)
        
        return {
            "status": "success",
            "total_ids": len(feature_ids),
            "valid_count": valid_count,
            "invalid_count": len(invalid_ids),
            "invalid_ids": invalid_ids,
            "duplicate_ids": list(set(duplicate_ids)),
            "format_invalid_ids": format_invalid_ids,
            "all_valid": valid_count == len(feature_ids) and len(duplicate_ids) == 0,
            "detailed_results": detailed_results
        }
    
    except Exception as e:
        return {"status": "error", "message": str(e)}

@mcp.tool()
def notify_task_complete(agent_type: str, feature_id: str, result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Record completion of an agent task. Called when an agent reports back to Project Manager.
    
    Args:
        agent_type: Type of agent that completed (design_architect, coding, review, deep_analysis)
        feature_id: ID of the feature that was worked on
        result: Result data from the agent including status and any updates
        
    Returns:
        Dict with acknowledgment and next actions
    """
    state_manager = StateManager()
    
    try:
        state_manager._acquire_lock(STATE_FILE)
        
        # Read current state
        if not os.path.exists(STATE_FILE):
            return {"status": "error", "message": "State file not found"}
        
        with open(STATE_FILE, 'r') as f:
            state = json.load(f)
        
        # Create backup
        state_manager._backup_state(state)
        
        # Remove from active tasks
        if agent_type in state["active_tasks"]:
            if feature_id in state["active_tasks"][agent_type]:
                state["active_tasks"][agent_type].remove(feature_id)
        
        # Update counters based on result
        result_status = result.get("status", "unknown")
        if result_status == "success":
            state["verification_counters"]["completed_features"] += 1
        state["verification_counters"]["total_updates"] += 1
        
        # Add to task history
        if "task_history" not in state:
            state["task_history"] = []
        
        state["task_history"].append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent_type": agent_type,
            "feature_id": feature_id,
            "result_status": result_status,
            "action": result.get("action", "task_complete")
        })
        
        # Determine next actions based on agent type and result
        next_actions = []
        
        if agent_type == "design_architect":
            # Check if children were created that need queueing
            child_ids = result.get("child_ids", [])
            if child_ids:
                next_actions.append({
                    "action": "sync_children_to_queue",
                    "parent_id": feature_id,
                    "child_ids": child_ids
                })
            
            if result.get("ready_for_implementation"):
                next_actions.append({
                    "action": "queue_for_implementation",
                    "feature_id": feature_id
                })
        
        elif agent_type == "coding":
            if result_status == "success":
                next_actions.append({
                    "action": "queue_for_testing",
                    "feature_id": feature_id
                })
        
        elif agent_type == "review":
            if result.get("test_status") == "failing":
                next_actions.append({
                    "action": "queue_for_analysis",
                    "feature_id": feature_id
                })
                
                # Trigger cascade retest for dependent features when tests fail
                config = state.get("dependency_config", {})
                if config.get("enable_cascade_testing", True):
                    # Queue dependent features for re-testing
                    # This is done after releasing the lock to avoid deadlocks
                    next_actions.append({
                        "action": "trigger_dependent_retest",
                        "feature_id": feature_id,
                        "reason": "test_failure"
                    })
        
        # Check verification threshold
        verification_needed = (
            state["verification_counters"]["completed_features"] % 5 == 0 or
            state["verification_counters"]["total_updates"] % 10 == 0
        )
        
        # Update timestamp
        state["last_updated"] = datetime.now(timezone.utc).isoformat()
        
        # Write updated state
        temp_file = f"{STATE_FILE}.tmp"
        with open(temp_file, 'w') as f:
            json.dump(state, f, indent=2)
        
        shutil.move(temp_file, STATE_FILE)
        
        return {
            "status": "success",
            "message": f"Task completion recorded for {feature_id} from {agent_type}",
            "feature_id": feature_id,
            "agent_type": agent_type,
            "result_status": result_status,
            "next_actions": next_actions,
            "verification_checkpoint_needed": verification_needed,
            "counters": state["verification_counters"]
        }
        
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        state_manager._release_lock()

@mcp.tool()
def sync_children_to_queue(parent_id: str, child_ids: List[str] = None) -> Dict[str, Any]:
    """
    Add child features to the ready queue after Design Architect creates them.
    
    Args:
        parent_id: ID of the parent feature
        child_ids: Optional list of child IDs to add (if not provided, reads from design doc)
        
    Returns:
        Dict with sync results
    """
    state_manager = StateManager()
    
    try:
        # If child_ids not provided, read from design document
        if child_ids is None:
            if not os.path.exists(DESIGN_DOC_FILE):
                return {"status": "error", "message": "Design document not found"}
            
            with open(DESIGN_DOC_FILE, 'r') as f:
                design_doc = json.load(f)
            
            # Find parent feature and collect child IDs
            def find_children(features_list):
                for feature in features_list:
                    if feature.get("id") == parent_id:
                        return [child.get("id") for child in feature.get("children", []) if child.get("id")]
                    
                    children = feature.get("children", [])
                    if children and isinstance(children[0], dict) if children else False:
                        result = find_children(children)
                        if result is not None:
                            return result
                return None
            
            child_ids = find_children(design_doc.get("features", []))
            
            if child_ids is None:
                return {"status": "error", "message": f"Parent feature {parent_id} not found"}
        
        if not child_ids:
            return {
                "status": "success",
                "message": "No children to sync",
                "synced_count": 0,
                "child_ids": []
            }
        
        # Add each child to the ready queue
        state_manager._acquire_lock(STATE_FILE)
        
        if not os.path.exists(STATE_FILE):
            return {"status": "error", "message": "State file not found"}
        
        with open(STATE_FILE, 'r') as f:
            state = json.load(f)
        
        state_manager._backup_state(state)
        
        synced = []
        already_queued = []
        
        for child_id in child_ids:
            # Check if already in any queue
            in_ready = child_id in state.get("parallel_ready_queue", [])
            in_blocked = child_id in state.get("dependency_blocked_queue", [])
            in_active = any(
                child_id in state["active_tasks"].get(agent, [])
                for agent in state.get("active_tasks", {})
            )
            
            if not (in_ready or in_blocked or in_active):
                if "parallel_ready_queue" not in state:
                    state["parallel_ready_queue"] = []
                state["parallel_ready_queue"].append(child_id)
                synced.append(child_id)
            else:
                already_queued.append(child_id)
        
        # Update timestamp
        state["last_updated"] = datetime.now(timezone.utc).isoformat()
        
        # Write updated state
        temp_file = f"{STATE_FILE}.tmp"
        with open(temp_file, 'w') as f:
            json.dump(state, f, indent=2)
        
        shutil.move(temp_file, STATE_FILE)
        
        return {
            "status": "success",
            "message": f"Synced {len(synced)} children to ready queue",
            "parent_id": parent_id,
            "synced_count": len(synced),
            "synced_ids": synced,
            "already_queued": already_queued
        }
        
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        state_manager._release_lock()

if __name__ == "__main__":
    # Run automatic migration on startup
    print("Project Manager Tools MCP Server starting...")
    migration_result = migrate_legacy_feature_ids()
    if migration_result["status"] == "success" and migration_result["migrated_count"] > 0:
        print(f"Migration completed: {migration_result['migrated_count']} features migrated")
    
    mcp.run()