#!/usr/bin/env python3
"""Zip the vastu-checker folder into dist/vastu-checker.skill, the file Claude takes as a skill."""
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / 'vastu-checker'
OUT = ROOT / 'dist' / 'vastu-checker.skill'
PARTS = ('SKILL.md', 'LICENSE.txt', 'scripts', 'references', 'assets')     # nothing else goes in the skill


def main():
    if not (SKILL / 'SKILL.md').exists():
        raise SystemExit('vastu-checker/SKILL.md is missing')
    OUT.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(OUT, 'w', zipfile.ZIP_DEFLATED) as z:
        for part in PARTS:
            base = SKILL / part
            files = [base] if base.is_file() else sorted(base.rglob('*'))
            for p in files:
                if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc':
                    z.write(p, Path('vastu-checker') / p.relative_to(SKILL))
    print(OUT)


if __name__ == '__main__':
    main()
