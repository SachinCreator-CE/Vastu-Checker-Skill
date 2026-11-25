"""Tests for the CAD reader, the check and the overlay. Run with `python3 tests/test_skill.py` or with pytest.

The drawings are synthetic (tests/drawings.py): the same small house written as a metric DXF with a north
arrow block, a feet-and-inch DXF with compass letters and a gate gap, and a metric DWG in AutoCAD 2010 format.
Whatever way it is drawn, the house should read the same: kitchen SE, puja NE, master bedroom SW, main door
facing east onto the road, septic tank in the SW (against the source).
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'vastu-checker' / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))

import drawings  # noqa: E402
import cad_plan  # noqa: E402
import vastu_check  # noqa: E402
import vastu_overlay  # noqa: E402
from dwg_read import read_dwg  # noqa: E402
from dwg_write import DwgWriter  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix='vastu-tests-'))
FILES = {p.name: p for p in drawings.write_all(TMP / 'drawings')}
EXPECTED_ZONES = {'KITCHEN': 'SE', 'POOJA': 'NE', 'MASTER BED ROOM': 'SW', 'GUEST BED ROOM': 'NW', 'DINING': 'E',
                  'LIVING': 'NE', 'TOILET': 'W', 'SEPTIC TANK': 'SW', 'SUMP': 'NE', 'BORE WELL': 'N'}
CASES = {
    'house_mm_block_north.dxf': {'units': 'mm', 'up': 'E', 'plot': (12.0, 9.0), 'building': (10.0, 7.0)},
    'house_in_compass_north.dxf': {'units': 'in', 'up': 'N', 'plot': (29.53, 39.37), 'building': (22.97, 32.81)},
    'house_mm_letters.dwg': {'units': 'mm', 'up': 'E', 'plot': (12.0, 9.0), 'building': (10.0, 7.0)},
}
_cache = {}


def read(name):
    if name not in _cache:
        out = TMP / name.replace('.', '_')
        plan, summary = cad_plan.build(FILES[name], out, show_all=False)
        results = {}
        vastu_check.check(plan, False, results)
        _cache[name] = (plan, summary, results)
    return _cache[name]


def close(a, b, tol):
    return abs(a - b) <= tol


def check_case(name):
    plan, summary, results = read(name)
    exp = CASES[name]
    assert plan['cad']['units'] == exp['units'], (name, plan['cad']['units'])
    assert plan['up'] == exp['up'], (name, plan['up'])
    tol = 0.12 if plan['units'] == 'm' else 0.4
    assert close(plan['plot']['width'], exp['plot'][0], tol) and close(plan['plot']['depth'], exp['plot'][1], tol), \
        (name, plan['plot'])
    b = plan['building']
    assert close(b['width'], exp['building'][0], tol) and close(b['depth'], exp['building'][1], tol), (name, b)
    zones = {it['name']: it['zone'] for it in results['items']}
    for room, z in EXPECTED_ZONES.items():
        assert zones.get(room) == z, (name, room, zones.get(room), z)
    status = {it['name']: it['status'] for it in results['items']}
    assert status['SEPTIC TANK'] == 'Against' and status['KITCHEN'] == 'Follows', (name, status)
    md = plan['main_door']
    assert md['facing'] == 'E' and md['into'] == 'LIVING', (name, md)
    assert plan.get('roads') == ['E'], (name, plan.get('roads'))


def test_mm_dxf_with_north_block():
    check_case('house_mm_block_north.dxf')


def test_inch_dxf_with_compass_letters_and_gate_gap():
    check_case('house_in_compass_north.dxf')


def test_mm_dwg_2010_format():
    check_case('house_mm_letters.dwg')


def test_separate_size_texts_are_attached():
    plan, _, _ = read('house_mm_block_north.dxf')
    sizes = {r['name']: r.get('label_size') for r in plan['rooms']}
    assert sizes['LIVING'] == '2770 X 3655' and sizes['MASTER BED ROOM'] == '3155 X 2770', sizes


def test_overlay_files():
    plan, _, _ = read('house_mm_block_north.dxf')
    p = TMP / 'overlay_plan.json'
    p.write_text(json.dumps(plan))
    results = {}
    vastu_check.check(plan, False, results)
    items = vastu_overlay.build_items(plan, results)
    vastu_overlay.write_dxf(items, TMP / 'o.dxf')
    vastu_overlay.write_dwg(items, TMP / 'o.dwg')
    back = read_dwg(TMP / 'o.dwg')
    layers = {e['layer'] for e in back['model']}
    assert {'VASTU-GRID', 'VASTU-STATUS', 'VASTU-ZONE', 'VASTU-DOOR', 'VASTU-NORTH'} <= layers, layers
    assert back['problems'] == 0
    assert 'VASTU-STATUS' in (TMP / 'o.dxf').read_text()


def test_dwg_round_trip_and_honest_app_info():
    w = DwgWriter()
    w.layer('T', 3)
    w.line('T', (0, 0), (120, 0))
    w.arc('T', (0, 0), 36, 0, 90)
    w.pline('T', [(0, 0), (10, 0), (10, 10)], True)
    w.text('T', (5, 5), 6, 'KITCHEN\n8\'6"X7\'2"', 'center', 'middle')
    out = TMP / 'rt.dwg'
    w.save(out)
    d = read_dwg(out)
    types = sorted(e['type'] for e in d['model'])
    assert types == ['ARC', 'LINE', 'LWPOLYLINE', 'MTEXT'], types
    raw = out.read_bytes()
    assert 'Teigha'.encode('utf-16-le') not in raw and 'ODA application'.encode('utf-16-le') not in raw


def test_check_on_the_documented_example():
    plan = {"units": "ft", "up": "N", "plot": {"width": 30, "depth": 40},
            "building": {"x": 0, "y": 6, "width": 26, "depth": 32},
            "rooms": [{"name": "Kitchen", "type": "kitchen", "level": 0, "x": 17.5, "y": 28, "width": 8.5, "depth": 10}],
            "main_door": {"level": 0, "x": 26, "y": 12}}
    report = vastu_check.check(plan, False)
    assert '| Kitchen | kitchen | 0 | SE | Follows |' in report
    assert '| Main door wall | E wall | Follows |' in report


def test_door_facing_overrides_nearest_wall():
    plan = {"units": "ft", "up": "N", "plot": {"width": 30, "depth": 40},
            "building": {"x": 0, "y": 0, "width": 30, "depth": 40}, "rooms": [],
            "main_door": {"level": 0, "x": 2, "y": 20, "facing": "N"}}
    report = vastu_check.check(plan, False)
    assert '| Main door wall | faces N | Follows |' in report


def test_road_verdicts():
    assert 'best' in vastu_check.road_line(['N', 'E', 'S', 'W'])
    assert 'good' in vastu_check.road_line(['E', 'N'])
    assert 'bad' in vastu_check.road_line(['E', 'S', 'W'])


if __name__ == '__main__':
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith('test_') and callable(f)]
    failed = 0
    for n, f in tests:
        try:
            f()
            print(f'ok    {n}')
        except AssertionError as ex:
            failed += 1
            print(f'FAIL  {n}: {ex}')
    print(f'{len(tests) - failed} passed, {failed} failed')
    sys.exit(1 if failed else 0)
