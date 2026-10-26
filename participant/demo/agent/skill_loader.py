from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml


@dataclass
class LoadedSkill:
    name: str
    description: str
    handler: str
    path: Path
    instructions: str
    metadata: dict[str, Any]

    def load_handler(self) -> Callable[..., Any]:
        module_name, function_name = self.handler.split(":", 1)
        module = importlib.import_module(module_name)
        return getattr(module, function_name)


def discover_skills(skills_dir: str | Path | None) -> list[LoadedSkill]:
    if not skills_dir:
        return []
    root = Path(skills_dir)
    skills: list[LoadedSkill] = []
    for path in sorted(root.glob("*/SKILL.md")):
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---\n") or "\n---\n" not in text[4:]:
            continue
        frontmatter, instructions = text[4:].split("\n---\n", 1)
        metadata = yaml.safe_load(frontmatter) or {}
        if not all(metadata.get(key) for key in ("name", "description", "handler")):
            continue
        skills.append(
            LoadedSkill(
                name=str(metadata["name"]),
                description=str(metadata["description"]),
                handler=str(metadata["handler"]),
                path=path,
                instructions=instructions.strip(),
                metadata=metadata,
            )
        )
    return skills


def write_skill_usage(path: str | Path, payload: dict[str, Any]) -> None:
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

