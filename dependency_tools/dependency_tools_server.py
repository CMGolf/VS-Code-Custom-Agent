#!/usr/bin/env python3
"""
Dependency Tools MCP Server

Provides tools for the External Dependency Agent including:
- search_packages: Search for packages matching requirements
- get_package_info: Get detailed package information
- install_package: Install package to project
- add_dependency_to_project: Update config files
- verify_installation: Test package import
- record_external_dependency: Update design document
"""

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from pathlib import Path
import platform

# Cross-platform file locking imports
if platform.system() == "Windows":
    import msvcrt
else:
    import fcntl

import httpx
from mcp.server.fastmcp import FastMCP

# Initialize the MCP server
mcp = FastMCP("Dependency Tools")

# Configuration
DESIGN_DOC_FILE = "ProjectDesignDocument.json"
BACKUP_DIR = "dependency_backups"
MAX_BACKUPS = 5

# Package registry APIs
PYPI_API = "https://pypi.org/pypi"
NPM_API = "https://registry.npmjs.org"


class DependencyManager:
    """Handles dependency operations and design document updates"""
    
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
        backup_file = os.path.join(BACKUP_DIR, f"dependency_backup_{timestamp}.json")
        
        with open(backup_file, 'w') as f:
            json.dump(doc, f, indent=2)
        
        backups = sorted([f for f in os.listdir(BACKUP_DIR) if f.startswith("dependency_backup_")])
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
                            "feature": feature
                        }
                    
                    children = feature.get("children", [])
                    if children and isinstance(children[0], dict) if children else False:
                        child_result = search_features(children, current_path + ["children"])
                        if child_result["found"]:
                            return child_result
                
                return {"found": False}
            
            result = search_features(design_doc.get("features", []), ["features"])
            
            if result["found"]:
                return result
            else:
                return {"found": False, "message": f"Feature {feature_id} not found"}
                
        except Exception as e:
            return {"found": False, "message": str(e)}


@mcp.tool()
async def search_packages(
    query: str, 
    language: str = "python", 
    count: int = 5
) -> Dict[str, Any]:
    """
    Search for packages matching a query.
    
    Args:
        query: Search terms (e.g., "password hashing", "jwt")
        language: Programming language (python, javascript)
        count: Maximum number of results
        
    Returns:
        Dict with list of matching packages
    """
    try:
        packages = []
        
        if language.lower() == "python":
            # Search PyPI
            async with httpx.AsyncClient() as client:
                # PyPI doesn't have a great search API, use simple search
                response = await client.get(
                    f"https://pypi.org/search/",
                    params={"q": query},
                    headers={"Accept": "application/json"},
                    follow_redirects=True,
                    timeout=30.0
                )
                
                # Since PyPI search returns HTML, we'll use a different approach
                # Try to get info on common packages related to query
                search_terms = query.lower().split()
                common_packages = {
                    "password": ["bcrypt", "argon2-cffi", "passlib", "hashlib"],
                    "hashing": ["bcrypt", "argon2-cffi", "hashlib", "cryptography"],
                    "hash": ["bcrypt", "hashlib", "cryptography"],
                    "jwt": ["pyjwt", "python-jose", "authlib"],
                    "http": ["httpx", "requests", "aiohttp", "urllib3"],
                    "database": ["sqlalchemy", "psycopg2", "pymongo", "redis"],
                    "api": ["fastapi", "flask", "django", "starlette"],
                    "json": ["orjson", "ujson", "simplejson"],
                    "validation": ["pydantic", "marshmallow", "cerberus"],
                    "testing": ["pytest", "unittest", "nose2"],
                    "async": ["asyncio", "aiohttp", "trio"],
                    "crypto": ["cryptography", "pycryptodome", "nacl"],
                    "email": ["email-validator", "validate-email", "flanker"],
                    "date": ["python-dateutil", "arrow", "pendulum"],
                    "yaml": ["pyyaml", "ruamel.yaml"],
                    "xml": ["lxml", "beautifulsoup4", "xmltodict"],
                    "logging": ["loguru", "structlog", "python-json-logger"],
                }
                
                candidate_packages = set()
                for term in search_terms:
                    if term in common_packages:
                        candidate_packages.update(common_packages[term])
                
                # If no matches, add the query itself as a package name
                if not candidate_packages:
                    candidate_packages.add(query.replace(" ", "-"))
                
                for pkg_name in list(candidate_packages)[:count]:
                    try:
                        pkg_response = await client.get(
                            f"{PYPI_API}/{pkg_name}/json",
                            timeout=10.0
                        )
                        if pkg_response.status_code == 200:
                            data = pkg_response.json()
                            info = data.get("info", {})
                            packages.append({
                                "name": pkg_name,
                                "version": info.get("version", "unknown"),
                                "description": info.get("summary", "")[:200],
                                "license": info.get("license", "unknown"),
                                "home_page": info.get("home_page", info.get("project_url", "")),
                                "language": "python"
                            })
                    except Exception:
                        continue
                        
        elif language.lower() in ["javascript", "typescript", "js", "ts"]:
            # Search npm
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{NPM_API}/-/v1/search",
                    params={"text": query, "size": count},
                    timeout=30.0
                )
                
                if response.status_code == 200:
                    data = response.json()
                    for obj in data.get("objects", []):
                        pkg = obj.get("package", {})
                        packages.append({
                            "name": pkg.get("name", ""),
                            "version": pkg.get("version", "unknown"),
                            "description": pkg.get("description", "")[:200],
                            "license": pkg.get("license", "unknown"),
                            "home_page": pkg.get("links", {}).get("homepage", ""),
                            "language": "javascript"
                        })
        
        return {
            "status": "success",
            "query": query,
            "language": language,
            "packages": packages,
            "count": len(packages)
        }
        
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def get_package_info(package_name: str, language: str = "python") -> Dict[str, Any]:
    """
    Get detailed information about a specific package.
    
    Args:
        package_name: Name of the package
        language: Programming language (python, javascript)
        
    Returns:
        Dict with detailed package information
    """
    try:
        async with httpx.AsyncClient() as client:
            if language.lower() == "python":
                response = await client.get(
                    f"{PYPI_API}/{package_name}/json",
                    timeout=30.0
                )
                
                if response.status_code != 200:
                    return {"status": "error", "message": f"Package {package_name} not found"}
                
                data = response.json()
                info = data.get("info", {})
                releases = data.get("releases", {})
                
                # Get release dates
                latest_version = info.get("version", "")
                release_info = releases.get(latest_version, [{}])
                upload_time = release_info[0].get("upload_time", "") if release_info else ""
                
                return {
                    "status": "success",
                    "package": {
                        "name": package_name,
                        "version": latest_version,
                        "description": info.get("summary", ""),
                        "long_description": info.get("description", "")[:500],
                        "author": info.get("author", ""),
                        "license": info.get("license", "unknown"),
                        "home_page": info.get("home_page", info.get("project_url", "")),
                        "documentation_url": info.get("docs_url", ""),
                        "requires_python": info.get("requires_python", ""),
                        "last_updated": upload_time,
                        "keywords": info.get("keywords", ""),
                        "classifiers": info.get("classifiers", [])[:10],
                        "dependencies": info.get("requires_dist", [])[:20],
                        "language": "python"
                    }
                }
                
            elif language.lower() in ["javascript", "typescript", "js", "ts"]:
                response = await client.get(
                    f"{NPM_API}/{package_name}",
                    timeout=30.0
                )
                
                if response.status_code != 200:
                    return {"status": "error", "message": f"Package {package_name} not found"}
                
                data = response.json()
                latest = data.get("dist-tags", {}).get("latest", "")
                version_info = data.get("versions", {}).get(latest, {})
                
                return {
                    "status": "success",
                    "package": {
                        "name": package_name,
                        "version": latest,
                        "description": data.get("description", ""),
                        "author": data.get("author", {}).get("name", "") if isinstance(data.get("author"), dict) else str(data.get("author", "")),
                        "license": data.get("license", "unknown"),
                        "home_page": data.get("homepage", ""),
                        "repository": data.get("repository", {}).get("url", "") if isinstance(data.get("repository"), dict) else "",
                        "last_updated": data.get("time", {}).get(latest, ""),
                        "keywords": data.get("keywords", []),
                        "dependencies": list(version_info.get("dependencies", {}).keys())[:20],
                        "language": "javascript"
                    }
                }
        
        return {"status": "error", "message": f"Unsupported language: {language}"}
        
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
def install_package(
    package_name: str, 
    version: Optional[str] = None,
    language: str = "python"
) -> Dict[str, str]:
    """
    Install a package to the project environment.
    
    Args:
        package_name: Name of the package to install
        version: Optional specific version (e.g., "1.2.3")
        language: Programming language (python, javascript)
        
    Returns:
        Dict with installation status
    """
    try:
        if language.lower() == "python":
            # Build package spec
            pkg_spec = f"{package_name}=={version}" if version else package_name
            
            # Try uv first, fall back to pip
            try:
                result = subprocess.run(
                    ["uv", "pip", "install", pkg_spec],
                    capture_output=True,
                    text=True,
                    timeout=120
                )
            except FileNotFoundError:
                result = subprocess.run(
                    [sys.executable, "-m", "pip", "install", pkg_spec],
                    capture_output=True,
                    text=True,
                    timeout=120
                )
            
            if result.returncode == 0:
                return {
                    "status": "success",
                    "message": f"Installed {pkg_spec}",
                    "package": package_name,
                    "version": version or "latest",
                    "language": "python"
                }
            else:
                return {
                    "status": "error",
                    "message": f"Installation failed: {result.stderr}"
                }
                
        elif language.lower() in ["javascript", "typescript", "js", "ts"]:
            pkg_spec = f"{package_name}@{version}" if version else package_name
            
            result = subprocess.run(
                ["npm", "install", pkg_spec],
                capture_output=True,
                text=True,
                timeout=120
            )
            
            if result.returncode == 0:
                return {
                    "status": "success",
                    "message": f"Installed {pkg_spec}",
                    "package": package_name,
                    "version": version or "latest",
                    "language": "javascript"
                }
            else:
                return {
                    "status": "error",
                    "message": f"Installation failed: {result.stderr}"
                }
        
        return {"status": "error", "message": f"Unsupported language: {language}"}
        
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "Installation timed out"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
def add_dependency_to_project(
    package_name: str,
    version: str,
    language: str = "python",
    dev_dependency: bool = False
) -> Dict[str, str]:
    """
    Add a dependency to the project configuration file.
    
    Args:
        package_name: Name of the package
        version: Version to pin
        language: Programming language
        dev_dependency: Whether this is a dev-only dependency
        
    Returns:
        Dict with update status
    """
    try:
        if language.lower() == "python":
            # Check for pyproject.toml first
            if os.path.exists("pyproject.toml"):
                config_file = "pyproject.toml"
                # Read and update pyproject.toml
                with open(config_file, 'r') as f:
                    content = f.read()
                
                dep_line = f'    "{package_name}>={version}",\n'
                
                # Simple append to dependencies section
                if "dependencies = [" in content:
                    content = content.replace(
                        "dependencies = [",
                        f"dependencies = [\n{dep_line}",
                        1
                    )
                    with open(config_file, 'w') as f:
                        f.write(content)
                else:
                    # Fall back to requirements.txt
                    config_file = "requirements.txt"
                    with open(config_file, 'a') as f:
                        f.write(f"{package_name}>={version}\n")
            else:
                # Use requirements.txt
                config_file = "requirements.txt"
                with open(config_file, 'a') as f:
                    f.write(f"{package_name}>={version}\n")
            
            return {
                "status": "success",
                "message": f"Added {package_name} to {config_file}",
                "config_file": config_file,
                "package": package_name,
                "version": version
            }
            
        elif language.lower() in ["javascript", "typescript", "js", "ts"]:
            # Update package.json
            config_file = "package.json"
            
            if os.path.exists(config_file):
                with open(config_file, 'r') as f:
                    pkg_json = json.load(f)
                
                dep_key = "devDependencies" if dev_dependency else "dependencies"
                if dep_key not in pkg_json:
                    pkg_json[dep_key] = {}
                
                pkg_json[dep_key][package_name] = f"^{version}"
                
                with open(config_file, 'w') as f:
                    json.dump(pkg_json, f, indent=2)
                
                return {
                    "status": "success",
                    "message": f"Added {package_name} to {config_file}",
                    "config_file": config_file,
                    "package": package_name,
                    "version": version
                }
            else:
                return {"status": "error", "message": "package.json not found"}
        
        return {"status": "error", "message": f"Unsupported language: {language}"}
        
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
def verify_installation(package_name: str, language: str = "python") -> Dict[str, Any]:
    """
    Verify that a package is correctly installed and can be imported.
    
    Args:
        package_name: Name of the package to verify
        language: Programming language
        
    Returns:
        Dict with verification status
    """
    try:
        if language.lower() == "python":
            # Try to import the package
            import_name = package_name.replace("-", "_").lower()
            
            # Some packages have different import names
            import_mappings = {
                "pyjwt": "jwt",
                "python-jose": "jose",
                "pillow": "PIL",
                "beautifulsoup4": "bs4",
                "scikit-learn": "sklearn",
                "opencv-python": "cv2",
                "pyyaml": "yaml",
                "python-dateutil": "dateutil",
            }
            
            import_name = import_mappings.get(package_name.lower(), import_name)
            
            result = subprocess.run(
                [sys.executable, "-c", f"import {import_name}; print({import_name}.__version__ if hasattr({import_name}, '__version__') else 'installed')"],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                version = result.stdout.strip()
                return {
                    "status": "success",
                    "verified": True,
                    "package": package_name,
                    "import_name": import_name,
                    "import_statement": f"import {import_name}",
                    "version_found": version
                }
            else:
                return {
                    "status": "error",
                    "verified": False,
                    "package": package_name,
                    "error": result.stderr
                }
                
        elif language.lower() in ["javascript", "typescript", "js", "ts"]:
            result = subprocess.run(
                ["node", "-e", f"console.log(require('{package_name}'))"],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                return {
                    "status": "success",
                    "verified": True,
                    "package": package_name,
                    "import_statement": f"const {package_name.replace('-', '_')} = require('{package_name}')"
                }
            else:
                return {
                    "status": "error",
                    "verified": False,
                    "package": package_name,
                    "error": result.stderr
                }
        
        return {"status": "error", "message": f"Unsupported language: {language}"}
        
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "Verification timed out"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
def record_external_dependency(
    dependency_id: str,
    package_name: str,
    version: str,
    import_statement: str,
    purpose: str,
    language: str = "python"
) -> Dict[str, str]:
    """
    Record an external dependency in the design document.
    
    Args:
        dependency_id: ID of the dependency feature
        package_name: Name of the installed package
        version: Installed version
        import_statement: How to import the package
        purpose: What this dependency is used for
        language: Programming language
        
    Returns:
        Dict with recording status
    """
    dep_manager = DependencyManager()
    
    try:
        dep_manager._acquire_lock(DESIGN_DOC_FILE)
        
        if not os.path.exists(DESIGN_DOC_FILE):
            return {"status": "error", "message": "Design document not found"}
        
        with open(DESIGN_DOC_FILE, 'r') as f:
            design_doc = json.load(f)
        
        # Backup
        dep_manager._backup_document(design_doc)
        
        # Find the dependency feature
        path_result = dep_manager._find_feature_path(dependency_id)
        if not path_result["found"]:
            return {"status": "error", "message": f"Dependency {dependency_id} not found"}
        
        # Navigate to feature
        current_obj = design_doc
        feature_path = path_result["path"]
        
        for path_element in feature_path[:-1]:
            current_obj = current_obj[path_element]
        
        feature = current_obj[feature_path[-1]]
        
        # Update dependency feature
        feature["status"] = "complete"
        feature["external_package"] = {
            "name": package_name,
            "version": version,
            "import_statement": import_statement,
            "language": language,
            "purpose": purpose,
            "resolved_at": datetime.now(timezone.utc).isoformat()
        }
        
        # Write updated document
        temp_file = f"{DESIGN_DOC_FILE}.tmp"
        with open(temp_file, 'w') as f:
            json.dump(design_doc, f, indent=2)
        
        shutil.move(temp_file, DESIGN_DOC_FILE)
        
        return {
            "status": "success",
            "message": f"Recorded external dependency {package_name} for {dependency_id}",
            "dependency_id": dependency_id,
            "package": package_name,
            "version": version
        }
        
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        dep_manager._release_lock()


if __name__ == "__main__":
    mcp.run()
