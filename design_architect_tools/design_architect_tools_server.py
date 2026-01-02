#!/usr/bin/env python3
"""
Design Architect Tools MCP Server

Provides specialized tools for the Design Architect Agent including:
- read_feature_for_decomposition: Read features needing design work
- update_feature_specification: Update feature with inputs/outputs/dependencies  
- create_child_features: Create child feature specifications
- mark_decomposition_complete: Mark feature as ready for implementation
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
import platform

# Cross-platform file locking imports
if platform.system() == "Windows":
    import msvcrt
else:
    import fcntl

from mcp.server.fastmcp import FastMCP

# Initialize the MCP server
mcp = FastMCP("Design Architect Tools")

# Configuration
DESIGN_DOC_FILE = "ProjectDesignDocument.json"
BACKUP_DIR = "design_backups"
MAX_BACKUPS = 5

# ID Management Configuration  
ID_FORMATS = {
    "feature": r"^feature-\d+(?:-\d+)*$",
    "dependency": r"^dependency-\d+(?:-\d+)*$",
    "function": r"^function-\d+(?:-\d+)*$",
    "class": r"^class-\d+(?:-\d+)*$",
    "module": r"^module-\d+(?:-\d+)*$",
    "literal": r"^literal-\d+(?:-\d+)*$"
}

class DesignManager:
    """Handles design document operations with file locking"""
    
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
        backup_file = os.path.join(BACKUP_DIR, f"design_backup_{timestamp}.json")
        
        with open(backup_file, 'w') as f:
            json.dump(doc, f, indent=2)
        
        # Clean up old backups
        backups = sorted([f for f in os.listdir(BACKUP_DIR) if f.startswith("design_backup_")])
        while len(backups) > MAX_BACKUPS:
            old_backup = backups.pop(0)
            os.remove(os.path.join(BACKUP_DIR, old_backup))

    def _parse_feature_id(self, feature_id: str) -> Dict[str, Any]:
        """Parse feature ID into prefix and numeric segments"""
        if not feature_id:
            return {"valid": False, "message": "Empty feature ID"}
        
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
        
        padded_segments = []
        for segment in segments:
            padding = max(2, len(str(segment)))
            padded_segments.append(f"{segment:0{padding}d}")
        
        return f"{prefix}-" + "-".join(padded_segments)

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
                    "parent_path": result["parent_path"],
                    "is_child": "children" in str(result["path"])
                }
            else:
                return {"found": False, "message": f"Feature {feature_id} not found"}
                
        except Exception as e:
            return {"found": False, "message": str(e)}

    def _generate_child_id(self, parent_id: str, child_prefix: str) -> Dict[str, Any]:
        """Generate next available child ID for a parent"""
        try:
            with open(DESIGN_DOC_FILE, 'r') as f:
                design_doc = json.load(f)
            
            existing_ids = set()
            def collect_ids(features_list):
                for feature in features_list:
                    existing_ids.add(feature.get("id", ""))
                    children = feature.get("children", [])
                    if children and isinstance(children[0], dict) if children else False:
                        collect_ids(children)
            
            collect_ids(design_doc.get("features", []))
            
            # Parse parent ID
            parent_parse = self._parse_feature_id(parent_id)
            if not parent_parse["valid"]:
                return {"status": "error", "message": f"Parent ID has invalid format: {parent_id}"}
            
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
            new_id = self._build_feature_id(child_prefix, new_segments)
            
            return {
                "status": "success",
                "generated_id": new_id,
                "level": len(new_segments),
                "segments": new_segments,
                "parent_id": parent_id
            }
            
        except Exception as e:
            return {"status": "error", "message": str(e)}

@mcp.tool()
def read_feature_for_decomposition(feature_id: str) -> Dict[str, Any]:
    """
    Read a feature that needs decomposition work.
    
    Args:
        feature_id: ID of the feature to decompose
        
    Returns:
        Dict with feature details and current state
    """
    design_manager = DesignManager()
    
    try:
        path_result = design_manager._find_feature_path(feature_id)
        if not path_result["found"]:
            return {"status": "error", "message": f"Feature {feature_id} not found"}
        
        feature = path_result["feature"]
        
        # Check if feature needs decomposition
        status = feature.get("status", "pending")
        if status not in ["pending", "designing"]:
            return {
                "status": "warning", 
                "message": f"Feature {feature_id} has status '{status}' - may not need decomposition"
            }
        
        return {
            "status": "success",
            "feature": feature,
            "ready_for_decomposition": True,
            "current_status": status,
            "has_children": len(feature.get("children", [])) > 0,
            "has_inputs_outputs": bool(feature.get("inputs")) and bool(feature.get("outputs")),
            "dependencies": feature.get("dependencies", [])
        }
        
    except Exception as e:
        return {"status": "error", "message": str(e)}

@mcp.tool()
def update_feature_specification(
    feature_id: str, 
    inputs: Optional[List[Dict]] = None,
    outputs: Optional[List[Dict]] = None, 
    dependencies: Optional[List[str]] = None,
    feature_type: Optional[str] = None,
    description: Optional[str] = None
) -> Dict[str, str]:
    """
    Update a feature's specification with inputs, outputs, and dependencies.
    
    Args:
        feature_id: ID of the feature to update
        inputs: List of input specifications
        outputs: List of output specifications  
        dependencies: List of dependency feature IDs
        feature_type: Type of feature (feature, function, class, etc.)
        description: Updated description
        
    Returns:
        Dict with operation status
    """
    design_manager = DesignManager()
    
    try:
        design_manager._acquire_lock(DESIGN_DOC_FILE)
        
        if not os.path.exists(DESIGN_DOC_FILE):
            return {"status": "error", "message": "Design document not found"}
        
        with open(DESIGN_DOC_FILE, 'r') as f:
            design_doc = json.load(f)
        
        # Create backup
        design_manager._backup_document(design_doc)
        
        # Find the feature
        path_result = design_manager._find_feature_path(feature_id)
        if not path_result["found"]:
            return {"status": "error", "message": f"Feature {feature_id} not found"}
        
        # Navigate to feature
        current_obj = design_doc
        feature_path = path_result["path"]
        
        for path_element in feature_path[:-1]:
            current_obj = current_obj[path_element]
        
        feature = current_obj[feature_path[-1]]
        
        # Update fields
        updates_made = []
        if inputs is not None:
            feature["inputs"] = inputs
            updates_made.append("inputs")
        
        if outputs is not None:
            feature["outputs"] = outputs
            updates_made.append("outputs")
        
        if dependencies is not None:
            feature["dependencies"] = dependencies
            updates_made.append("dependencies")
            
        if feature_type is not None:
            feature["type"] = feature_type
            updates_made.append("type")
            
        if description is not None:
            feature["description"] = description
            updates_made.append("description")
        
        # Update status to designing
        if feature.get("status") == "pending":
            feature["status"] = "designing"
            updates_made.append("status")
        
        # Write to temporary file first
        temp_file = f"{DESIGN_DOC_FILE}.tmp"
        with open(temp_file, 'w') as f:
            json.dump(design_doc, f, indent=2)
        
        # Atomic rename
        shutil.move(temp_file, DESIGN_DOC_FILE)
        
        return {
            "status": "success",
            "message": f"Feature {feature_id} specification updated",
            "updates_made": updates_made
        }
        
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        design_manager._release_lock()

@mcp.tool()
def create_child_features(parent_id: str, children_specs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Create child features for a parent feature.
    
    Args:
        parent_id: ID of the parent feature
        children_specs: List of child feature specifications
        
    Returns:
        Dict with creation results and generated IDs
    """
    design_manager = DesignManager()
    
    try:
        design_manager._acquire_lock(DESIGN_DOC_FILE)
        
        if not os.path.exists(DESIGN_DOC_FILE):
            return {"status": "error", "message": "Design document not found"}
        
        with open(DESIGN_DOC_FILE, 'r') as f:
            design_doc = json.load(f)
        
        design_manager._backup_document(design_doc)
        
        # Find parent feature
        path_result = design_manager._find_feature_path(parent_id)
        if not path_result["found"]:
            return {"status": "error", "message": f"Parent feature {parent_id} not found"}
        
        # Navigate to parent feature
        current_obj = design_doc
        feature_path = path_result["path"]
        
        for path_element in feature_path[:-1]:
            current_obj = current_obj[path_element]
        
        parent_feature = current_obj[feature_path[-1]]
        
        # Initialize children array
        if "children" not in parent_feature:
            parent_feature["children"] = []
        
        created_children = []
        
        for child_spec in children_specs:
            # Generate child ID
            child_type = child_spec.get("type", "function")
            id_result = design_manager._generate_child_id(parent_id, child_type)
            
            if id_result["status"] != "success":
                continue
            
            child_id = id_result["generated_id"]
            
            # Create child feature
            child_feature = {
                "id": child_id,
                "name": child_spec.get("name", f"Child_{len(created_children) + 1}"),
                "description": child_spec.get("description", ""),
                "type": child_type,
                "status": "pending",
                "test_status": "not_tested",
                "inputs": child_spec.get("inputs", []),
                "outputs": child_spec.get("outputs", []),
                "dependencies": child_spec.get("dependencies", []),
                "children": [],
                "error_log": []
            }
            
            parent_feature["children"].append(child_feature)
            created_children.append(child_id)
        
        # Write updated document
        temp_file = f"{DESIGN_DOC_FILE}.tmp"
        with open(temp_file, 'w') as f:
            json.dump(design_doc, f, indent=2)
        
        shutil.move(temp_file, DESIGN_DOC_FILE)
        
        return {
            "status": "success",
            "message": f"Created {len(created_children)} child features for {parent_id}",
            "created_children": created_children,
            "parent_id": parent_id
        }
        
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        design_manager._release_lock()

@mcp.tool()
def create_new_dependencies(dependency_specs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Create new dependency features in the design document.
    
    Args:
        dependency_specs: List of dependency specifications
        
    Returns:
        Dict with creation results
    """
    design_manager = DesignManager()
    
    try:
        design_manager._acquire_lock(DESIGN_DOC_FILE)
        
        if not os.path.exists(DESIGN_DOC_FILE):
            # Create new document
            design_doc = {"features": []}
        else:
            with open(DESIGN_DOC_FILE, 'r') as f:
                design_doc = json.load(f)
        
        design_manager._backup_document(design_doc)
        
        created_dependencies = []
        
        for dep_spec in dependency_specs:
            # Generate dependency ID
            dep_type = dep_spec.get("type", "dependency")
            
            # Find next available dependency number
            existing_ids = set()
            def collect_ids(features_list):
                for feature in features_list:
                    existing_ids.add(feature.get("id", ""))
                    children = feature.get("children", [])
                    if children and isinstance(children[0], dict) if children else False:
                        collect_ids(children)
            
            collect_ids(design_doc.get("features", []))
            
            # Generate ID
            dep_pattern = f"{dep_type}-(\\d+)"
            dep_numbers = []
            for existing_id in existing_ids:
                dep_match = re.match(dep_pattern, existing_id)
                if dep_match:
                    dep_numbers.append(int(dep_match.group(1)))
            
            next_dep_num = max(dep_numbers) + 1 if dep_numbers else 1
            dep_id = f"{dep_type}-{next_dep_num:02d}"
            
            # Create dependency feature
            dependency = {
                "id": dep_id,
                "name": dep_spec.get("name", f"Dependency_{next_dep_num}"),
                "description": dep_spec.get("description", ""),
                "type": dep_type,
                "status": "pending",
                "test_status": "not_tested",
                "inputs": dep_spec.get("inputs", []),
                "outputs": dep_spec.get("outputs", []),
                "dependencies": [],
                "children": [],
                "error_log": []
            }
            
            design_doc["features"].append(dependency)
            created_dependencies.append(dep_id)
        
        # Write updated document
        temp_file = f"{DESIGN_DOC_FILE}.tmp"
        with open(temp_file, 'w') as f:
            json.dump(design_doc, f, indent=2)
        
        shutil.move(temp_file, DESIGN_DOC_FILE)
        
        return {
            "status": "success",
            "message": f"Created {len(created_dependencies)} new dependencies",
            "created_dependencies": created_dependencies
        }
        
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        design_manager._release_lock()

@mcp.tool()
def mark_decomposition_complete(feature_id: str) -> Dict[str, str]:
    """
    Mark a feature as having completed decomposition and ready for next phase.
    
    Args:
        feature_id: ID of the feature that was decomposed
        
    Returns:
        Dict with completion status
    """
    design_manager = DesignManager()
    
    try:
        design_manager._acquire_lock(DESIGN_DOC_FILE)
        
        if not os.path.exists(DESIGN_DOC_FILE):
            return {"status": "error", "message": "Design document not found"}
        
        with open(DESIGN_DOC_FILE, 'r') as f:
            design_doc = json.load(f)
        
        design_manager._backup_document(design_doc)
        
        # Find the feature
        path_result = design_manager._find_feature_path(feature_id)
        if not path_result["found"]:
            return {"status": "error", "message": f"Feature {feature_id} not found"}
        
        # Navigate to feature
        current_obj = design_doc
        feature_path = path_result["path"]
        
        for path_element in feature_path[:-1]:
            current_obj = current_obj[path_element]
        
        feature = current_obj[feature_path[-1]]
        
        # Determine next status based on decomposition
        has_children = len(feature.get("children", [])) > 0
        has_complete_spec = bool(feature.get("inputs")) and bool(feature.get("outputs"))
        
        if has_children:
            # Feature was decomposed into children
            feature["status"] = "pending"  # Parent waits for children
        elif has_complete_spec:
            # Feature is ready for implementation
            feature["status"] = "pending"
        else:
            # Incomplete decomposition
            return {"status": "error", "message": "Feature decomposition incomplete - missing inputs/outputs or children"}
        
        # Write updated document
        temp_file = f"{DESIGN_DOC_FILE}.tmp"
        with open(temp_file, 'w') as f:
            json.dump(design_doc, f, indent=2)
        
        shutil.move(temp_file, DESIGN_DOC_FILE)
        
        # Collect child IDs for state queue sync
        child_ids = [child.get("id") for child in feature.get("children", []) if child.get("id")]
        
        return {
            "status": "success",
            "message": f"Feature {feature_id} decomposition marked complete",
            "next_phase": "implementation" if has_complete_spec else "child_processing",
            "ready_for_implementation": has_complete_spec and not has_children,
            "child_ids": child_ids,
            "requires_queue_sync": len(child_ids) > 0
        }
        
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        design_manager._release_lock()

@mcp.tool()
def get_decomposition_result(feature_id: str) -> Dict[str, Any]:
    """
    Get the decomposition result for a feature to report back to Project Manager.
    
    Args:
        feature_id: ID of the feature that was decomposed
        
    Returns:
        Dict with complete decomposition results for PM handoff
    """
    design_manager = DesignManager()
    
    try:
        if not os.path.exists(DESIGN_DOC_FILE):
            return {"status": "error", "message": "Design document not found"}
        
        with open(DESIGN_DOC_FILE, 'r') as f:
            design_doc = json.load(f)
        
        # Find the feature
        path_result = design_manager._find_feature_path(feature_id)
        if not path_result["found"]:
            return {"status": "error", "message": f"Feature {feature_id} not found"}
        
        feature = path_result["feature"]
        
        # Collect all child IDs (for queue sync)
        child_ids = []
        def collect_child_ids(children):
            for child in children:
                if child.get("id"):
                    child_ids.append(child["id"])
                    collect_child_ids(child.get("children", []))
        
        collect_child_ids(feature.get("children", []))
        
        # Determine readiness
        has_children = len(feature.get("children", [])) > 0
        has_complete_spec = bool(feature.get("inputs")) and bool(feature.get("outputs"))
        
        return {
            "status": "success",
            "feature_id": feature_id,
            "feature_name": feature.get("name", "Unknown"),
            "decomposition_result": {
                "has_children": has_children,
                "child_count": len(feature.get("children", [])),
                "child_ids": child_ids,
                "has_complete_specification": has_complete_spec,
                "inputs_defined": len(feature.get("inputs", [])),
                "outputs_defined": len(feature.get("outputs", [])),
                "dependencies": feature.get("dependencies", [])
            },
            "next_actions": {
                "requires_queue_sync": len(child_ids) > 0,
                "children_to_add_to_queue": child_ids,
                "ready_for_implementation": has_complete_spec and not has_children,
                "needs_further_decomposition": has_children
            }
        }
        
    except Exception as e:
        return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    mcp.run()