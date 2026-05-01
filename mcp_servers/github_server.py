import os
import subprocess
import json
import logging
import shlex
import time
import socket
import urllib.request
from datetime import datetime
from dotenv import load_dotenv
import threading
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("voxkage-github")


# Optional: PyGithub for API features
try:
    from github import Github
    from github import Auth
    HAS_PYGITHUB = True
except ImportError:
    HAS_PYGITHUB = False

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("github_server")

load_dotenv()
GITHUB_PAT = os.environ.get("GITHUB_PAT", "")
VOXKAGE_CLONE_ROOT = os.environ.get("VOXKAGE_CLONE_ROOT", r"C:\VoxKage\Clones")

# Ensure clone root exists
if not os.path.exists(VOXKAGE_CLONE_ROOT):
    try:
        os.makedirs(VOXKAGE_CLONE_ROOT)
    except Exception as e:
        logger.error(f"Failed to create clone root {VOXKAGE_CLONE_ROOT}: {e}")

# Track running projects
_RUNNING_PROJECTS = {}  # repo_path -> {"process": Popen, "port": int}

def _run_cmd(command, cwd=None, timeout=30):
    try:
        # Prevent Git from invoking pagers (like less) which hang in headless subprocesses
        env = os.environ.copy()
        env["GIT_PAGER"] = "cat"
        env["PAGER"] = "cat"
        env["GIT_TERMINAL_PROMPT"] = "0"  # Prevent interactive credential prompts hanging
        
        result = subprocess.run(
            command,
            cwd=cwd,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "exit_code": result.returncode
        }
    except subprocess.TimeoutExpired as e:
        return {"success": False, "error": "Command timed out", "stderr": str(e.stderr if e.stderr else "")}
    except Exception as e:
        return {"success": False, "error": str(e), "stderr": ""}

# ====== LOCAL GIT CLI OPERATIONS ======

def git_clone(kwargs):
    url = kwargs.get("url")
    dest_name = kwargs.get("dest_name")
    
    if not url:
        return {"error": "url is required"}
        
    if not dest_name:
        dest_name = url.split("/")[-1].replace(".git", "")
        
    dest_path = os.path.join(VOXKAGE_CLONE_ROOT, dest_name)
    
    if os.path.exists(dest_path):
        return {"error": f"Destination path already exists: {dest_path}", "path": dest_path}
        
    cmd = f"git clone {shlex.quote(url)} {shlex.quote(dest_path)}"
    
    res = _run_cmd(cmd)
    res["path"] = dest_path
    
    # Auto trigger RAG indexing (simulated here by returning instructions)
    res["message"] = f"Successfully cloned to {dest_path}. VoxKage, please run check_and_index or index_directory on this path now."
    return res

def git_status(kwargs):
    repo_path = kwargs.get("repo_path")
    if not repo_path or not os.path.exists(repo_path):
        return {"error": f"Invalid repo path: {repo_path}"}
        
    status = _run_cmd("git status --short", cwd=repo_path)
    log = _run_cmd("git log --oneline -5", cwd=repo_path)
    
    return {
        "status": status.get("stdout", ""),
        "recent_commits": log.get("stdout", ""),
        "repo": repo_path
    }

def git_diff_summary(kwargs):
    repo_path = kwargs.get("repo_path")
    if not repo_path or not os.path.exists(repo_path):
        return {"error": f"Invalid repo path: {repo_path}"}
        
    stat = _run_cmd("git diff HEAD --stat", cwd=repo_path)
    diff = _run_cmd("git diff HEAD", cwd=repo_path)
    
    diff_text = diff.get("stdout", "")
    # Truncate if too long
    if len(diff_text) > 10000:
        diff_text = diff_text[:10000] + "\n... (truncated)"
        
    return {
        "files_changed": stat.get("stdout", ""),
        "diff_preview": diff_text
    }

def git_smart_commit(kwargs):
    repo_path = kwargs.get("repo_path")
    message = kwargs.get("message")
    push = kwargs.get("push", True)
    
    if not repo_path or not message:
        return {"error": "repo_path and message are required"}
        
    # 1. Add all
    add_res = _run_cmd("git add .", cwd=repo_path)
    if not add_res["success"]:
        return {"error": "git add failed", "details": add_res["stderr"]}
        
    # 2. Commit
    commit_cmd = f'git commit -m {shlex.quote(message)}'
    commit_res = _run_cmd(commit_cmd, cwd=repo_path)
    
    # It's okay if commit fails because there's nothing to commit
    if not commit_res["success"] and "nothing to commit" not in commit_res["stdout"].lower():
        return {"error": "git commit failed", "details": commit_res["stderr"], "stdout": commit_res["stdout"]}
        
    res_obj = {"commit_output": commit_res["stdout"]}
        
    # 3. Push
    if push:
        push_res = _run_cmd("git push", cwd=repo_path)
        if push_res["success"]:
            res_obj["push_output"] = push_res["stderr"] or push_res["stdout"]
            res_obj["status"] = "Success"
        else:
            err = push_res["stderr"].lower()
            if "rejected" in err and "fetch first" in err:
                res_obj["status"] = "Rejected (Needs Pull)"
                res_obj["suggestion"] = "Please run git_pull first, then retry push."
            elif "could not read username" in err or "authentication failed" in err:
                res_obj["status"] = "Authentication Failed"
                res_obj["suggestion"] = "Git credentials not configured properly for HTTPS push."
            else:
                res_obj["status"] = "Push Failed"
            
            res_obj["push_error"] = push_res["stderr"]
            
    return res_obj

def git_pull(kwargs):
    repo_path = kwargs.get("repo_path")
    if not repo_path:
        return {"error": "repo_path is required"}
        
    pull_res = _run_cmd("git pull", cwd=repo_path)
    if pull_res["success"]:
        return {"status": "Success", "output": pull_res["stdout"]}
    else:
        return {"status": "Failed", "error": pull_res["stderr"]}

def fake_commit(kwargs):
    repo_path = kwargs.get("repo_path")
    message = kwargs.get("message", "Keepalive ping")
    
    if not repo_path:
        return {"error": "repo_path is required"}
        
    commit_cmd = f'git commit --allow-empty -m {shlex.quote(message)}'
    c_res = _run_cmd(commit_cmd, cwd=repo_path)
    
    if c_res["success"]:
        p_res = _run_cmd("git push", cwd=repo_path)
        return {"status": "Success", "push": p_res["success"], "details": p_res["stderr"]}
    return {"status": "Failed", "error": c_res["stderr"]}

def detect_and_install_deps(kwargs):
    repo_path = kwargs.get("repo_path")
    if not repo_path:
        return {"error": "repo_path is required"}
        
    results = []
    
    # Python
    if os.path.exists(os.path.join(repo_path, "requirements.txt")):
        res = _run_cmd("pip install -r requirements.txt", cwd=repo_path)
        if not res["success"]:
            # Fallback
            res = _run_cmd("pip install --user -r requirements.txt", cwd=repo_path)
        results.append({"type": "python pip", "success": res["success"], "output": res["stderr"] or res["stdout"][:500]})
        
    # Node
    if os.path.exists(os.path.join(repo_path, "package.json")):
        res = _run_cmd("npm install", cwd=repo_path)
        if not res["success"]:
            res = _run_cmd("npm install --legacy-peer-deps", cwd=repo_path)
        results.append({"type": "node npm", "success": res["success"], "output": res["stderr"] or res["stdout"][:500]})
        
    if not results:
        return {"status": "No standard dependencies detected (no requirements.txt or package.json)"}
        
    return {"status": "Completed", "details": results}

def run_project(kwargs):
    repo_path = kwargs.get("repo_path")
    command = kwargs.get("command")
    port = kwargs.get("port")
    
    if not repo_path or not command:
        return {"error": "repo_path and command required"}
        
    # Kill existing if any
    kill_project({"repo_path": repo_path})
    
    try:
        # We must use Popen to keep it running in background
        process = subprocess.Popen(
            command,
            cwd=repo_path,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        _RUNNING_PROJECTS[repo_path] = {"process": process, "port": port, "start_time": time.time()}
        
        # Give it a few seconds to fail early
        time.sleep(3)
        if process.poll() is not None:
            # It died immediately
            err = process.stderr.read()
            out = process.stdout.read()
            del _RUNNING_PROJECTS[repo_path]
            return {"status": "Failed to start", "exit_code": process.returncode, "stderr": err, "stdout": out}
            
        return {
            "status": "Running",
            "pid": process.pid,
            "message": f"Project started in background. If it binds to port {port}, check health next."
        }
    except Exception as e:
        return {"error": str(e)}

def kill_project(kwargs):
    repo_path = kwargs.get("repo_path")
    if not repo_path:
        return {"error": "repo_path required"}
        
    if repo_path in _RUNNING_PROJECTS:
        proc = _RUNNING_PROJECTS[repo_path]["process"]
        try:
            proc.terminate()
            proc.wait(timeout=5)
            del _RUNNING_PROJECTS[repo_path]
            return {"status": "Terminated successfully"}
        except subprocess.TimeoutExpired:
            proc.kill()
            del _RUNNING_PROJECTS[repo_path]
            return {"status": "Killed forcefully"}
        except Exception as e:
            return {"error": str(e)}
            
    return {"status": "Not running"}

def check_project_health(kwargs):
    port = kwargs.get("port")
    url_path = kwargs.get("url_path", "/")
    
    if not port:
        return {"error": "port required"}
        
    url = f"http://localhost:{port}{url_path}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as response:
            body = response.read().decode('utf-8')
            return {
                "status": "Up",
                "code": response.getcode(),
                "body_preview": body[:500]
            }
    except Exception as e:
        return {"status": "Down/Error", "error": str(e)}


# ====== GITHUB API OPERATIONS (PyGithub) ======

def get_github_client():
    if not HAS_PYGITHUB:
        raise Exception("PyGithub is not installed. Please pip install PyGithub")
    if not GITHUB_PAT:
        raise Exception("GITHUB_PAT environment variable is not set")
    auth = Auth.Token(GITHUB_PAT)
    return Github(auth=auth)

def get_github_profile(kwargs):
    try:
        g = get_github_client()
        username = kwargs.get("username")
        if username:
            user = g.get_user(username)
        else:
            user = g.get_user()
            
        return {
            "login": user.login,
            "name": user.name,
            "bio": user.bio,
            "public_repos": user.public_repos,
            "total_private_repos": user.total_private_repos,
            "followers": user.followers,
            "following": user.following,
            "created_at": str(user.created_at)
        }
    except Exception as e:
        return {"error": str(e)}

def list_my_repos(kwargs):
    try:
        g = get_github_client()
        user = g.get_user()
        
        limit = kwargs.get("limit", 20)
        
        repos = []
        for repo in user.get_repos(sort="updated", direction="desc")[:limit]:
            repos.append({
                "name": repo.name,
                "full_name": repo.full_name,
                "private": repo.private,
                "html_url": repo.html_url,
                "description": repo.description,
                "language": repo.language,
                "stargazers_count": repo.stargazers_count,
                "updated_at": str(repo.updated_at)
            })
            
        return {"repos": repos, "count": len(repos)}
    except Exception as e:
        return {"error": str(e)}

def create_repo_local(kwargs):
    name = kwargs.get("name")
    description = kwargs.get("description", "")
    private = kwargs.get("private", True)
    init_readme = kwargs.get("init_readme", True)
    
    if not name:
        return {"error": "name required"}
        
    try:
        # 1. Create remote repo
        g = get_github_client()
        user = g.get_user()
        
        repo = user.create_repo(
            name,
            description=description,
            private=private,
            auto_init=init_readme
        )
        
        remote_url = repo.clone_url
        
        # 2. Clone locally
        clone_res = git_clone({"url": remote_url, "dest_name": name})
        
        if "error" in clone_res:
            return {
                "status": "Created remote, but local clone failed",
                "remote_url": remote_url,
                "clone_error": clone_res["error"]
            }
            
        return {
            "status": "Success",
            "remote_url": remote_url,
            "local_path": clone_res.get("path"),
            "message": "Repository created remotely and cloned locally. Ready for work."
        }
    except Exception as e:
        return {"error": str(e)}

def actions_list(kwargs):
    try:
        g = get_github_client()
        repo_name = kwargs.get("repo")
        if not repo_name:
            return {"error": "repo parameter required (e.g. ayushdwivedi001/VoxKage)"}
            
        repo = g.get_repo(repo_name)
        runs = repo.get_workflow_runs()
        limit = kwargs.get("limit", 10)
        
        result = []
        for run in runs[:limit]:
            result.append({
                "id": run.id,
                "name": run.name,
                "status": run.status,
                "conclusion": run.conclusion,
                "head_branch": run.head_branch,
                "created_at": str(run.created_at)
            })
        return {"runs": result}
    except Exception as e:
        return {"error": str(e)}

def actions_get(kwargs):
    try:
        g = get_github_client()
        repo_name = kwargs.get("repo")
        run_id = kwargs.get("run_id")
        
        if not repo_name or not run_id:
            return {"error": "repo and run_id required"}
            
        repo = g.get_repo(repo_name)
        run = repo.get_workflow_run(int(run_id))
        
        jobs = []
        for job in run.jobs():
            jobs.append({
                "id": job.id,
                "name": job.name,
                "status": job.status,
                "conclusion": job.conclusion
            })
            
        return {
            "id": run.id,
            "status": run.status,
            "conclusion": run.conclusion,
            "jobs": jobs
        }
    except Exception as e:
        return {"error": str(e)}

def get_job_logs(kwargs):
    try:
        g = get_github_client()
        repo_name = kwargs.get("repo")
        job_id = kwargs.get("job_id")
        
        if not repo_name or not job_id:
            return {"error": "repo and job_id required"}
            
        # PyGithub doesn't have native get_job_logs that returns string, it returns redirect URL
        # But we can just use requests with the PAT to get it
        import requests
        headers = {
            "Authorization": f"Bearer {GITHUB_PAT}",
            "Accept": "application/vnd.github.v3+json"
        }
        # Get job download URL
        resp = requests.get(f"https://api.github.com/repos/{repo_name}/actions/jobs/{job_id}/logs", headers=headers, allow_redirects=True)
        if resp.status_code == 200:
            return {"logs": resp.text[-10000:]} # Last 10k chars
        else:
            return {"error": f"Failed to get logs: {resp.status_code} {resp.text}"}
    except Exception as e:
        return {"error": str(e)}

@mcp.tool()
def github_clone_repo(url: str, dest_name: str = "") -> str:
    """Clone a git repository locally"""
    return json.dumps(git_clone({"url": url, "dest_name": dest_name}), indent=2)

# Deprecated: use native shell command `git status` instead.
# @mcp.tool()
# def github_repo_status(repo_path: str) -> str:
#     """Get git status and recent commits for a local repo"""
#     return json.dumps(git_status({"repo_path": repo_path}), indent=2)

# Deprecated: use native shell command `git diff` instead.
# @mcp.tool()
# def github_diff_summary(repo_path: str) -> str:
#     """Get a summary of pending changes in a local repo"""
#     return json.dumps(git_diff_summary({"repo_path": repo_path}), indent=2)

@mcp.tool()
def github_smart_commit(repo_path: str, message: str = "", push: bool = False) -> str:
    """Commit all changes with an optional message (auto-generated if empty) and optionally push"""
    return json.dumps(git_smart_commit({"repo_path": repo_path, "message": message, "push": push}), indent=2)

@mcp.tool()
def github_pull(repo_path: str) -> str:
    """Pull latest changes from remote"""
    return json.dumps(git_pull({"repo_path": repo_path}), indent=2)

@mcp.tool()
def github_fake_commit(repo_path: str, message: str = "", count: int = 1) -> str:
    """Create empty fake commits (useful for activity)"""
    return json.dumps(fake_commit({"repo_path": repo_path, "message": message, "count": count}), indent=2)

@mcp.tool()
def github_detect_and_install_deps(repo_path: str) -> str:
    """Automatically detect package managers (npm, pip, cargo) and install dependencies"""
    return json.dumps(detect_and_install_deps({"repo_path": repo_path}), indent=2)

@mcp.tool()
def github_run_project(repo_path: str, command: str = "") -> str:
    """Run a project in the background (starts dev servers, etc)"""
    return json.dumps(run_project({"repo_path": repo_path, "command": command}), indent=2)

@mcp.tool()
def github_kill_project(repo_path: str) -> str:
    """Kill a background project started by github_run_project"""
    return json.dumps(kill_project({"repo_path": repo_path}), indent=2)

@mcp.tool()
def github_check_project_health(repo_path: str) -> str:
    """Check if a background project is still running"""
    return json.dumps(check_project_health({"repo_path": repo_path}), indent=2)

@mcp.tool()
def github_get_profile() -> str:
    """Get the authenticated GitHub user profile"""
    return json.dumps(get_github_profile({}), indent=2)

@mcp.tool()
def github_list_my_repos(limit: int = 10, sort: str = "updated") -> str:
    """List repositories owned by the authenticated user"""
    return json.dumps(list_my_repos({"limit": limit, "sort": sort}), indent=2)

@mcp.tool()
def github_create_repo_local(path: str, name: str, private: bool = True, push: bool = True) -> str:
    """Initialize a local directory, create a GitHub repo, and push it"""
    return json.dumps(create_repo_local({"path": path, "name": name, "private": private, "push": push}), indent=2)

@mcp.tool()
def github_actions_list(repo: str, status: str = "") -> str:
    """List recent GitHub Actions workflow runs"""
    return json.dumps(actions_list({"repo": repo, "status": status}), indent=2)

@mcp.tool()
def github_actions_get(repo: str, run_id: str) -> str:
    """Get details of a specific GitHub Actions workflow run"""
    return json.dumps(actions_get({"repo": repo, "run_id": run_id}), indent=2)

@mcp.tool()
def github_get_job_logs(repo: str, job_id: str) -> str:
    """Get logs for a specific GitHub Actions job"""
    return json.dumps(get_job_logs({"repo": repo, "job_id": job_id}), indent=2)

if __name__ == "__main__":
    mcp.run()
