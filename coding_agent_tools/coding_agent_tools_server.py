#!/usr/bin/env python3
"""
Coding Agent Tools MCP Server

Provides MINIMAL tools for the Coding Agent:
- read_feature_for_implementation: Get complete feature spec (all context needed)
- record_implementation_result: Record where code was implemented
- get_dependency_interfaces: Get interfaces of completed dependencies (if needed)

Philosophy: The feature specification should contain ALL information needed.
No codebase exploration tools - the agent implements directly from spec.
"""

import json
import os
import shutil
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from pathlib import Path
import platform

# Cross-platform file locking imports
if platform.system() == "Windows":
    import msvcrt
else:
    import fcntl

from mcp.server.fastmcp import FastMCP

# Initialize the MCP server
mcp = FastMCP("Coding Agent Tools")

# Configuration
DESIGN_DOC_FILE = "ProjectDesignDocument.json"
BACKUP_DIR = "coding_backups"
MAX_BACKUPS = 5


class CodingManager:
    """Handles design document read operations and implementation recording"""
    
    def __init__(self):
        self.lock_file = None
    
    def _acquire_lock(self, file_path: str) -> None:
        """Acquire file lock for atomic operations"""
        lock_path = f"{file_path}.lock"
        self.lock_file = open(lock_path, 'w')
        
        if platform.system() == "Windows":
            try:
                msvcrt.locking(self.lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                self.lock_file.close()
                self.lock_file = None
                raise
        else:
            fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_EX)
    
    def _release_lock(self) -> None:
        """Release file lock"""
        if self.lock_file:
            try:
                if platform.system() == "Windows":
                    msvcrt.locking(self.lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_UN)
            finally:
                self.lock_file.close()
                self.lock_file = None
    
    def _backup_document(self, doc: Dict[str, Any]) -> None:
        """Create backup of design document"""
        os.makedirs(BACKUP_DIR, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = os.path.join(BACKUP_DIR, f"coding_backup_{timestamp}.json")
        
        with open(backup_file, 'w') as f:
            json.dump(doc, f, indent=2)
        
        # Clean up old backups
        backups = sorted([f for f in os.listdir(BACKUP_DIR) if f.startswith("coding_backup_")])
        while len(backups) > MAX_BACKUPS:
            old_backup = backups.pop(0)
            os.remove(os.path.join(BACKUP_DIR, old_backup))

    def _find_feature_path(self, feature_id: str) -> Dict[str, Any]:
        """Find path to feature in design document"""
        try:
            if not os.path.exists(DESIGN_DOC_FILE):
                return {"found": False, "message": "Design document not found"}
            
            with open(DESIGN_DOC_FILE, 'r') as f:
                design_doc = json.load(f)
            
            def search_features(features_list, path=[]):
                for i, feature in enumerate(features_list):
                    current_path = path + [i]
                    
                    if feature.get("id") == feature_id:
                        return {
                            "found": True,
                            "path": current_path,
                            "feature": feature,
                            "parent_path": path[:-1] if path else None
                        }
                    
                    children = feature.get("children", [])
                    if children and isinstance(children[0], dict) if children else False:
                        child_result = search_features(children, current_path + ["children"])
                        if child_result["found"]:
                            return child_result
                
                return {"found": False}
            
            result = search_features(design_doc.get("features", []), ["features"])
            
            if result["found"]:
                return {
                    "found": True,
                    "path": result["path"],
                    "feature": result["feature"],
                    "parent_path": result["parent_path"]
                }
            else:
                return {"found": False, "message": f"Feature {feature_id} not found"}
                
        except Exception as e:
            return {"found": False, "message": str(e)}


@mcp.tool()
def read_feature_for_implementation(feature_id: str) -> Dict[str, Any]:
    """
    Read complete feature specification for implementation.
    
    This returns ALL information needed to implement the feature:
    - Full specification with inputs/outputs
    - Type information (function, class, module)
    - Dependencies (already implemented - just for reference)
    - Project context (name, target location)
    
    The coding agent should implement DIRECTLY from this spec without
    exploring the codebase. All dependencies are guaranteed complete.
    
    Args:
        feature_id: ID of the feature to implement
        
    Returns:
        Dict with complete implementation context
    """
    coding_manager = CodingManager()
    
    try:
        if not os.path.exists(DESIGN_DOC_FILE):
            return {"status": "error", "message": "Design document not found"}
        
        with open(DESIGN_DOC_FILE, 'r') as f:
            design_doc = json.load(f)
        
        # Find the feature
        path_result = coding_manager._find_feature_path(feature_id)
        if not path_result["found"]:
            return {"status": "error", "message": f"Feature {feature_id} not found"}
        
        feature = path_result["feature"]
        
        # Validate feature is ready for implementation
        if feature.get("type") == "feature":
            return {
                "status": "error",
                "message": f"Feature {feature_id} is type 'feature' - needs decomposition first, not implementation"
            }
        
        # Get project context
        project_info = design_doc.get("project", {})
        
        # Get dependency interfaces (for import statements)
        dependency_interfaces = []
        for dep_id in feature.get("dependencies", []):
            dep_result = coding_manager._find_feature_path(dep_id)
            if dep_result["found"]:
                dep_feature = dep_result["feature"]
                dependency_interfaces.append({
                    "id": dep_id,
                    "name": dep_feature.get("name"),
                    "type": dep_feature.get("type"),
                    "location": dep_feature.get("location"),
                    "outputs": dep_feature.get("outputs", [])  # What this dep provides
                })
        
        return {
            "status": "success",
            "ready_to_implement": True,
            "feature": {
                "id": feature.get("id"),
                "name": feature.get("name"),
                "description": feature.get("description"),
                "type": feature.get("type"),
                "inputs": feature.get("inputs", []),
                "outputs": feature.get("outputs", []),
                "dependencies": feature.get("dependencies", [])
            },
            "dependency_interfaces": dependency_interfaces,
            "project_context": {
                "name": project_info.get("name", "Unknown"),
                "description": project_info.get("description", "")
            },
            "implementation_guidance": {
                "all_dependencies_complete": True,
                "implement_from_spec": True,
                "no_codebase_exploration_needed": True
            }
        }
        
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
def record_implementation_result(
    feature_id: str, 
    location: str,
    status: str = "implementing",
    notes: Optional[str] = None
) -> Dict[str, str]:
    """
    Record where a feature was implemented and update its status.
    
    Args:
        feature_id: ID of the feature that was implemented
        location: File path where the code was written
        status: New status (implementing, complete, failed)
        notes: Optional implementation notes
        
    Returns:
        Dict with operation status
    """
    coding_manager = CodingManager()
    
    try:
        coding_manager._acquire_lock(DESIGN_DOC_FILE)
        
        if not os.path.exists(DESIGN_DOC_FILE):
            return {"status": "error", "message": "Design document not found"}
        
        with open(DESIGN_DOC_FILE, 'r') as f:
            design_doc = json.load(f)
        
        # Backup before changes
        coding_manager._backup_document(design_doc)
        
        # Find the feature
        path_result = coding_manager._find_feature_path(feature_id)
        if not path_result["found"]:
            return {"status": "error", "message": f"Feature {feature_id} not found"}
        
        # Navigate to feature
        current_obj = design_doc
        feature_path = path_result["path"]
        
        for path_element in feature_path[:-1]:
            current_obj = current_obj[path_element]
        
        feature = current_obj[feature_path[-1]]
        
        # Update feature
        feature["location"] = location
        feature["status"] = status
        
        if notes:
            if "implementation_notes" not in feature:
                feature["implementation_notes"] = []
            feature["implementation_notes"].append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "note": notes
            })
        
        # Write updated document
        temp_file = f"{DESIGN_DOC_FILE}.tmp"
        with open(temp_file, 'w') as f:
            json.dump(design_doc, f, indent=2)
        
        shutil.move(temp_file, DESIGN_DOC_FILE)
        
        return {
            "status": "success",
            "message": f"Implementation recorded for {feature_id}",
            "feature_id": feature_id,
            "location": location,
            "new_status": status
        }
        
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        coding_manager._release_lock()


@mcp.tool()
def get_implementation_result(feature_id: str) -> Dict[str, Any]:
    """
    Get implementation result summary for handoff back to Project Manager.
    
    Args:
        feature_id: ID of the feature that was implemented
        
    Returns:
        Dict with implementation results for PM handoff
    """
    coding_manager = CodingManager()
    
    try:
        if not os.path.exists(DESIGN_DOC_FILE):
            return {"status": "error", "message": "Design document not found"}
        
        with open(DESIGN_DOC_FILE, 'r') as f:
            design_doc = json.load(f)
        
        # Find the feature
        path_result = coding_manager._find_feature_path(feature_id)
        if not path_result["found"]:
            return {"status": "error", "message": f"Feature {feature_id} not found"}
        
        feature = path_result["feature"]
        
        return {
            "status": "success",
            "feature_id": feature_id,
            "feature_name": feature.get("name"),
            "implementation_result": {
                "location": feature.get("location"),
                "feature_status": feature.get("status"),
                "is_complete": feature.get("status") == "implementing"
            },
            "handoff_data": {
                "ready_for_testing": feature.get("status") == "implementing" and feature.get("location"),
                "test_location": feature.get("location")
            },
            "next_actions_for_pm": [
                f"Call notify_task_complete('coding', '{feature_id}', this_result)",
                "Queue for testing if implementation complete"
            ]
        }
        
    except Exception as e:
        return {"status": "error", "message": str(e)}


if __name__ == "__main__":
    mcp.run()
