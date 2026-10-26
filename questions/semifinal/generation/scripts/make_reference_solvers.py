"""Create one small, independently executable solver entry point per question."""

from pathlib import Path

from question_specs import QUESTIONS, PRACTICE


ROOT = Path('/root/ops-competition-2026/semifinal-qa-benchmark-v1')


def main():
    destination = ROOT / 'validation/reference-solvers'
    destination.mkdir(parents=True, exist_ok=True)
    for spec in QUESTIONS + PRACTICE:
        path = destination / f"{spec['id']}.py"
        if path.exists():
            raise FileExistsError(f'Reference solver already exists: {path.name}')
        path.write_text('#!/usr/bin/env python3\n'
            '"""Public-first solver for one registered semifinal question."""\n'
            'import sys\n'
            'from pathlib import Path\n'
            'sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))\n'
            'from solver_runtime import run\n'
            f'run("{spec["id"]}")\n')
    print(f'CREATED_REFERENCE_SOLVERS={len(QUESTIONS) + len(PRACTICE)}')


if __name__ == '__main__':
    main()
