# Contributing

The reader gets better with every drawing it meets. The most useful contributions are drawings it reads wrongly and names it doesn't know.

## A drawing that reads wrongly

Open an issue with:
- the summary `cad_plan.py` printed and the `<name>_read.png` it wrote;
- what it got wrong (a room's type or outline, north, the main door, the plot);
- if you can, a minimal DXF that shows the problem.

Only share drawings you have the right to share. Delete the title block, client and owner names, site address and anything else that identifies the project first; the reader only needs the walls, doors and room labels. A synthetic drawing that reproduces the problem is even better, and can join the tests.

## Names it doesn't know

Room, site and door names live in `vastu-checker/scripts/cad_labels.py` (`VOCAB`, `IGNORE_WORDS`, `DOOR_TAG`, `LEVEL_WORDS`). Regional names and other scripts (Kannada, Tamil, Telugu, Hindi, Marathi labels) are very welcome. Add the pattern before more general ones, since the first match wins, and add a line to the tests or a test drawing that uses it.

## Code

- Keep the scripts dependency-light: the standard library, `numpy` and `matplotlib`, with `scipy` optional. The DWG reader stays pure Python so the skill runs where nothing can be installed.
- Run `python3 tests/test_skill.py` before sending a change, and add a test for what you fixed.
- The rules (`references/vastu-rules.md`) and their machine copy (`scripts/zone_rules.json`) change together. Cite the source for a rule, and mark it contested when the sources disagree.
