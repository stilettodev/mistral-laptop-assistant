"""Skill management: list, install, and uninstall agent skills.

Skills are Markdown files stored under `.agents/skills/` that provide
persistent context for OpenHands-style agent sessions. They can be
installed from any public GitHub URL pointing to a `SKILL.md` file.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx

from .config import settings


SKILLS_DIR = Path(settings.workspace_dir) / ".agents" / "skills"
SKILLS_INDEX = SKILLS_DIR / ".index.json"


# ---------------------------------------------------------------------------
# Index helpers
# ---------------------------------------------------------------------------


def _load_index() -> dict[str, Any]:
    if SKILLS_INDEX.exists():
        try:
            return json.loads(SKILLS_INDEX.read_text())
        except Exception:
            pass
    return {}


def _save_index(index: dict[str, Any]) -> None:
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    SKILLS_INDEX.write_text(json.dumps(index, indent=2))


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def list_skills() -> dict[str, Any]:
    """List all installed skills (name, source_url, description, installed_at)."""
    try:
        index = _load_index()
        skills = []
        for name, info in index.items():
            path = SKILLS_DIR / name
            skills.append(
                {
                    "name": name,
                    "description": info.get("description", ""),
                    "source_url": info.get("source_url", ""),
                    "installed_at": info.get("installed_at", ""),
                    "file_size": path.stat().st_size if path.exists() else 0,
                }
            )
        return {"ok": True, "count": len(skills), "skills": skills}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def list_available_skills(query: str = "") -> dict[str, Any]:
    """Search GitHub for public OpenHands skills matching `query`."""
    try:
        q = (query or "OpenHands skill").strip()
        import os as _os
        token = _os.environ.get("GITHUB_TOKEN", "")
        # Percent-encode query for URL
        encoded_q = "+".join(
            "".join(c if c.isalnum() else f"%{ord(c):02X}" for c in part)
            for part in q.split()
        )
        url = (
            f"https://api.github.com/search/code"
            f"?q={encoded_q}+filename:SKILL.md+in:path"
            f"&per_page=10&type=code"
        )
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        with httpx.Client(timeout=15.0) as client:
            resp = client.get(url, headers=headers)

        if resp.status_code == 200:
            data = resp.json()
            items = []
            for item in data.get("items", []):
                path = item.get("path", "")
                parts = path.split("/")
                if len(parts) >= 2 and parts[-1] == "SKILL.md":
                    skill_name = parts[1]
                    repo = item.get("repository", {}) or {}
                    items.append(
                        {
                            "name": skill_name,
                            "repo": repo.get("full_name", ""),
                            "path": path,
                            "url": (
                                f"https://github.com/{repo.get('full_name', '')}"
                                f"/blob/master/{path}"
                            ),
                        }
                    )
            return {"ok": True, "count": len(items), "skills": items}
        else:
            return {
                "ok": False,
                "error": f"GitHub search failed: {resp.status_code} {resp.reason}",
            }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def install_skill(url: str) -> dict[str, Any]:
    """Install a skill from a GitHub URL pointing to a SKILL.md file.

    Accepts:
      https://github.com/OWNER/REPO/tree/BRANCH/skills/NAME
      https://github.com/OWNER/REPO/blob/BRANCH/skills/NAME/SKILL.md
      https://raw.githubusercontent.com/OWNER/REPO/BRANCH/skills/NAME/SKILL.md
    """
    try:
        raw_url, name = _resolve_github_url(url)
        if not raw_url:
            return {
                "ok": False,
                "error": (
                    "Could not resolve SKILL.md from URL. "
                    "Provide a GitHub URL to a skill directory or raw SKILL.md file."
                ),
            }

        with httpx.Client(timeout=15.0) as client:
            resp = client.get(raw_url)

        if resp.status_code != 200:
            return {"ok": False, "error": f"Failed to fetch skill: {resp.status_code}"}

        content = resp.text
        if len(content) < 50:
            return {"ok": False, "error": "Skill file is too short to be valid."}

        # Extract description from first # heading or first paragraph
        desc = ""
        for line in content.splitlines()[:10]:
            m = re.match(r"^#\s+(.+)", line)
            if m:
                desc = m.group(1).strip()
                break
        if not desc:
            desc = content[:100].split("\n")[0].strip()

        SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        skill_file = SKILLS_DIR / f"{name}.md"
        skill_file.write_text(content)

        import os as _os
        index = _load_index()
        index[name] = {
            "description": desc,
            "source_url": url,
            "installed_at": str(_os.stat(__file__).st_mtime),
            "file": str(skill_file),
        }
        _save_index(index)

        return {
            "ok": True,
            "name": name,
            "description": desc,
            "size_bytes": len(content),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def uninstall_skill(name: str) -> dict[str, Any]:
    """Remove a previously installed skill by name."""
    try:
        index = _load_index()
        if name not in index:
            return {"ok": False, "error": f"No skill named '{name}' is installed."}

        skill_file = Path(index[name].get("file", ""))
        if skill_file.exists():
            skill_file.unlink()

        del index[name]
        _save_index(index)
        return {"ok": True, "name": name, "message": f"Skill '{name}' uninstalled."}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_github_url(url: str) -> tuple[str, str]:
    """Convert a GitHub URL to raw content URL and return skill name."""
    # raw.githubusercontent.com
    m = re.match(
        r"https://raw\.githubusercontent\.com/([^/]+)/([^/]+)/([^/]+)/(.+\.md)$",
        url,
    )
    if m:
        owner, repo, branch, path = m.groups()
        return (
            f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}",
            path.split("/")[-1].replace(".md", ""),
        )

    # tree view or blob view
    m = re.match(
        r"https://github\.com/([^/]+)/([^/]+)/(?:tree|blob)/([^/]+)/(.+)$",
        url,
    )
    if m:
        owner, repo, branch, path = m.groups()
        if not path.endswith("/SKILL.md"):
            path = path.rstrip("/") + "/SKILL.md"
        name = path.split("/")[-2]
        raw = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
        return raw, name

    return "", ""