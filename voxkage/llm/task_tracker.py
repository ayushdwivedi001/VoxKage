from voxkage.paths import task_logs_dir
"""
Phase 4: Session Task Tracker
Manages task files in ~/.voxkage/data/tasks\ for agentic loop tracking.
Each task file stores: goal, steps, current_step, status, step_count, visited_urls.
Auto-cleanup on session end.
"""
import os
import json
import time
import glob
import logging

logger = logging.getLogger(__name__)

_TASKS_DIR = str(task_logs_dir())

def _ensure_dir():
    os.makedirs(_TASKS_DIR, exist_ok=True)

def create_task(goal: str, steps: list = None) -> str:
    """Create a new task file and return task_id."""
    _ensure_dir()
    task_id = f"task_{int(time.time())}"
    task_data = {
        "task_id": task_id,
        "goal": goal,
        "steps": steps or [],
        "current_step": 0,
        "status": "in_progress",
        "step_count": 0,
        "visited_urls": [],
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    task_path = os.path.join(_TASKS_DIR, f"{task_id}.json")
    try:
        with open(task_path, 'w', encoding='utf-8') as f:
            json.dump(task_data, f, ensure_ascii=False, indent=2)
        logger.info(f"[Phase4] Created task: {task_id} — goal: {goal[:80]}")
    except Exception as e:
        logger.error(f"[Phase4] Failed to create task file: {e}")
    return task_id

def update_task(task_id: str, step_completed: int = None, status: str = None, 
                url_visited: str = None, increment_step: bool = False) -> dict:
    """Update an existing task file with progress."""
    task_path = os.path.join(_TASKS_DIR, f"{task_id}.json")
    if not os.path.exists(task_path):
        logger.warning(f"[Phase4] Task file not found: {task_id}")
        return {}
    try:
        with open(task_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if step_completed is not None:
            data["current_step"] = step_completed
        if status is not None:
            data["status"] = status
        if url_visited:
            if "visited_urls" not in data:
                data["visited_urls"] = []
            data["visited_urls"].append(url_visited)
        if increment_step:
            data["step_count"] = data.get("step_count", 0) + 1
        with open(task_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return data
    except Exception as e:
        logger.error(f"[Phase4] Failed to update task: {e}")
        return {}

def get_task(task_id: str) -> dict:
    """Read task state from file."""
    task_path = os.path.join(_TASKS_DIR, f"{task_id}.json")
    if not os.path.exists(task_path):
        return {}
    try:
        with open(task_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}

def complete_task(task_id: str):
    """Mark task as completed and delete the file."""
    task_path = os.path.join(_TASKS_DIR, f"{task_id}.json")
    try:
        if os.path.exists(task_path):
            os.remove(task_path)
            logger.info(f"[Phase4] Completed and removed task: {task_id}")
    except Exception as e:
        logger.warning(f"[Phase4] Failed to cleanup task: {e}")

def check_loop_detected(task_id: str, current_url: str, threshold: int = 3) -> bool:
    """Check if the same URL has been visited threshold times (loop detection)."""
    task = get_task(task_id)
    if not task:
        return False
    visited = task.get("visited_urls", [])
    count = visited.count(current_url)
    return count >= threshold

def get_step_count(task_id: str) -> int:
    """Get current step count for a task."""
    task = get_task(task_id)
    return task.get("step_count", 0) if task else 0

def cleanup_all_tasks():
    """Remove all task JSON files from the tasks directory."""
    try:
        _ensure_dir()
        files = glob.glob(os.path.join(_TASKS_DIR, "*.json"))
        for f in files:
            os.remove(f)
        logger.info(f"[Phase4] Cleaned up {len(files)} task files")
    except Exception as e:
        logger.warning(f"[Phase4] Failed to cleanup tasks: {e}")
