"""What the texts on a house plan mean: room names and sizes, site items, roads, drawing titles,
door tags, compass letters. Indian drafting habits are the default (feet-inch sizes such as 12'0"X14'6",
names such as POOJA, SIT-OUT, M.BED ROOM, U.G.SUMP, OHT); edit the tables below for other offices.
"""
import re

# (regex on the normalised label, kind, type). First match wins, so specific patterns come first.
# kind: room (inside the building), site (on the plot), road, main_door, stair_hint, ignore.
VOCAB = [
    (r'\b(SAFTY|SAFETY|GRILL|S\.?S\.?)\b.*\bGATE\b', 'ignore', 'grill_gate'),
    (r'\b(STUDY|DINING|DINNING|COMPUTER|WRITING|DRESSING|CENTRE|CENTER|COFFEE|SIDE|DRAFTING|WORK)\s+TABLE\b', 'ignore', 'furniture'),
    (r'\b(BED|TV|T\.V|SHOE|CROCKERY|WALL|OVERHEAD|LOFT|PUJA|POOJA)\s*UNIT\b', 'ignore', 'furniture'),
    (r'\bMAIN\s*(DOOR|ENTRANCE|ENTRY)\b|^M\.?\s?D\.?$|^DM$|^MD\d*$|^D\s*M$', 'main_door', 'main_door'),
    (r'\b(POOJA|PUJA|POOJ|PRAYER|MANDIR|DEVA|DEVARA|GOD\'?S?\s*ROOM|DEITY)\b', 'room', 'puja'),
    (r'\b(KITCHEN|KIT\.?|COOKING|PANTRY|DRY\s*KITCHEN|WET\s*KITCHEN)\b', 'room', 'kitchen'),
    (r'\b(M\.?\s?BED|MASTER\s*BED|M\.?B\.?R|MBR|MASTER)\b', 'room', 'master_bedroom'),
    (r'\b(GUEST)\b', 'room', 'guest_bedroom'),
    (r'\b(KIDS?|CHILD|CHILDREN|CHILDRENS)\b', 'room', 'children_bedroom'),
    (r'\b(BED\s*-?\s*ROOM|BEDROOM|BED\s*RM|B\.?\s?R\.?\s*\d*$|^BED\s*\d*$|^B\.?ROOM)', 'room', 'bedroom'),
    (r'\b(DINING|DINNING|DINING\s*HALL)\b', 'room', 'dining'),
    (r'\b(HALL|LIVING|DRAWING\s*(ROOM|HALL|RM)|LOUNGE|FAMILY|SITTING)\b', 'room', 'living'),
    (r'\b(BATHING|SHOWER\s*ROOM)\b', 'room', 'bath'),
    (r'\b(TOILET|TOILETS|TOI|W\.?C\.?|BATH|BATHROOM|T\s*&\s*B|LAVATORY|POWDER)\b', 'room', 'toilet'),
    (r'\b(STAIR\s*CASE|STAIRCASE|STAIRS?|STAIR\s*WAY)\b', 'room', 'staircase'),
    (r'^(UP|DN|DOWN)$', 'stair_hint', 'staircase'),
    (r'\b(STORE|STORAGE|STORE\s*ROOM|LUMBER|S\.?R\.?)\b', 'room', 'store'),
    (r'\b(STUDY|LIBRARY|READING)\b', 'room', 'study'),
    (r'\b(UTILITY|WASH|WASHING|LAUNDRY|W\.?A\.?|DRY\s*AREA)\b', 'room', 'laundry'),
    (r'\b(CASH|SAFE|LOCKER|TREASURY|STRONG\s*ROOM)\b', 'room', 'cash_room'),
    (r'\b(SIT\s*-?\s*OUT|SITOUT|VERANDAH?|VERANDA|DECK)\b', 'room', 'verandah'),
    (r'\b(PORTICO|PORCH)\b', 'room', 'portico'),
    (r'\b(BALCONY|BALC|BLCNY|BAL\.?)\b', 'room', 'balcony'),
    (r'\b(OPEN\s*TERRACE|TERRACE|SUN\s*DECK)\b', 'room', 'terrace'),
    (r'\b(COURTYARD|COURT\s*YARD|OPEN\s*TO\s*SKY|O\.?T\.?S\.?|CUTOUT|CUT\s*OUT)\b', 'room', 'courtyard'),
    (r'\b(O\.?H\.?T\.?|OVER\s*HEAD\s*TANK|OVERHEAD\s*TANK|WATER\s*TANK|SINTEX)\b', 'room', 'overhead_tank'),
    (r'\b(FOYER|LOBBY|PASSAGE|CORRIDOR|ENTRANCE|ENTRY|VESTIBULE)\b', 'room', 'foyer'),
    (r'\b(LIFT|ELEVATOR)\b', 'room', 'lift'),
    (r'\b(DUCT|SHAFT|SHAFT\s*AREA)\b', 'room', 'duct'),
    (r'\b(DRESS|DRESSING|WARDROBE|WALK\s*-?\s*IN)\b', 'room', 'dress'),
    (r'\b(HOME\s*THEATRE|THEATER|THEATRE|GYM|OFFICE|WORK\s*STATION|MULTIPURPOSE|PLAY)\b', 'room', 'other'),
    (r'\b(BASEMENT|CELLAR)\b', 'room', 'basement'),
    (r'\b(CAR\s*PARK\w*|PARKING|CAR\s*PORCH|CAR\s*SHED|GARAGE|TWO\s*WHEELER|BIKE\s*PARK)', 'site', 'parking'),
    (r'\b(MAIN\s*GATE|GATE)\b', 'site', 'gate'),
    (r'\b(U\.?G\.?\s*SUMP|SUMP|U\.?G\.?T\.?|UNDER\s*GROUND\s*TANK|UNDERGROUND\s*TANK|RAIN\s*WATER\s*SUMP)\b', 'site', 'sump'),
    (r'\b(BORE\s*WELL|BOREWELL|BORE|B\.?W\.?)\b', 'site', 'borewell'),
    (r'\b(OPEN\s*WELL|WELL)\b', 'site', 'well'),
    (r'\b(SEPTIC|S\.?T\.?\s*TANK|SOAK\s*PIT|SOAKPIT|SOAK\s*AWAY)\b|^S\.\s?T\.?$', 'site', 'septic_tank'),
    (r'\b(GENERATOR|GENSET|D\.?G\.?\s*SET|TRANSFORMER)\b', 'site', 'generator'),
    (r'\b(SWIMMING\s*POOL|POOL)\b', 'site', 'pool'),
    (r'\b(FOUNTAIN|WATER\s*BODY|POND)\b', 'site', 'water_feature'),
    (r'\b(LAWN|GARDEN|LANDSCAPE|GREEN\s*AREA)\b', 'site', 'lawn'),
    (r'\b(TULSI|TULASI)\b', 'site', 'tulsi'),
    (r'\b(OUT\s*HOUSE|SERVANT|SECURITY|WATCHMAN|GUARD\s*ROOM)\b', 'site', 'outhouse'),
    (r'\b(ROAD|STREET|LANE|CROSS|MAIN\s*RD|\d+\s*\'?\s*-?\s*\d*\"?\s*W(IDE)?\s*RD)\b', 'road', 'road'),
]
_VOCAB = [(re.compile(p), k, t) for p, k, t in VOCAB]

IGNORE_WORDS = re.compile(
    r'^(D|DW|SD|FD|PD|DF|DP|DL|DB|DT|KD|W|V|KW|FW|FG|C|RF|WM|DW|OVEN|HOB|SINK|PUSH|STEP|STEPS|SHELF|RACK|'
    r'LOFT|SLAB|BEAM|COL|COLUMN|LEVEL|LVL|SECTION|ELEVATION|DETAIL|SCALE|NTS|N\s*T\s*S|DATE|DRAWN|DESIGN|'
    r'DRAWING|CHECKED|APPROVED|ORIENTATION|REV|REVISION|NOTE|NOTES|TITLE|CLIENT|OWNER|ARCHITECT|ENGINEER|'
    r'SHOWER|DIVERTER|SOFA|BED|TABLE|WARDROBE\s*UNIT|CUPBOARD|SHOE\s*RACK|FRIDGE|REFRIGERATOR|WASHBASIN|'
    r'W\.?B\.?|EWC|IWC|URINAL|TV|AC|A\.?C\.?)\s*-?\d*$')
DOOR_TAG = re.compile(r'^(D|MD|DM|D\s*M|M\.?D\.?|FD|SD|PD|DF|DP|DL|KD|TD|BD|DT|DB|SLD|SLIDING)\s*-?\d*$')

LEVEL_WORDS = [
    (r'\b(BASEMENT|CELLAR|LOWER\s*GROUND)\b', -1, 'Basement'),
    (r'\b(STILT|PARKING\s*FLOOR|PARKING\s*LEVEL)\b', 0, 'Stilt'),
    (r'\b(TERRACE|ROOF)\b', 90, 'Terrace'),
    (r'\b(GROUND|G\.?\s?F\.?|GF)\b', 0, 'Ground floor'),
    (r'\b(FIRST|1ST|F\.?\s?F\.?|FF)\b', 1, 'First floor'),
    (r'\b(SECOND|2ND|S\.?\s?F\.?|SF)\b', 2, 'Second floor'),
    (r'\b(THIRD|3RD|T\.?\s?F\.?|TF)\b', 3, 'Third floor'),
    (r'\b(FOURTH|4TH)\b', 4, 'Fourth floor'),
    (r'\b(FIFTH|5TH)\b', 5, 'Fifth floor'),
]
_LEVELS = [(re.compile(p), n, name) for p, n, name in LEVEL_WORDS]

COMPASS = {'N': 0, 'NORTH': 0, 'E': 90, 'EAST': 90, 'S': 180, 'SOUTH': 180, 'W': 270, 'WEST': 270,
           'NE': 45, 'SE': 135, 'SW': 225, 'NW': 315}

# feet and inches: 12'0", 12'-6", 12', 12 '6", 9'9"; metric: 3.05, 3050, 3.05M
_FTIN = r"(\d{1,3})\s*['’′]\s*-?\s*(?:(\d{1,2}(?:\.\d+)?)\s*(?:\"|''|”|″)?)?"
_DIMS_FT = re.compile(_FTIN + r"\s*[xX×*]\s*" + _FTIN)
_DIMS_M = re.compile(r"(\d+(?:\.\d+)?)\s*(MM|M|CM)?\s*[xX×*]\s*(\d+(?:\.\d+)?)\s*(MM|M|CM)?\b")
_WIDE = re.compile(_FTIN + r"\s*(WIDE|WD|W)\b")


def normalise(s):
    s = s.upper().replace('\t', ' ')
    s = re.sub(r'[–—_]', '-', s)
    lines = [ln.strip() for ln in s.split('\n') if ln.strip()]
    # letters stacked one per line (B / E / D / U / N / I / T) read as one word
    if len(lines) > 2 and all(len(ln) <= 3 for ln in lines):
        lines = [''.join(lines)]
    out = []
    for ln in lines:
        if re.fullmatch(r'(?:[A-Z]\s){2,}[A-Z]', ln):     # R O A D -> ROAD
            ln = ln.replace(' ', '')
        out.append(ln)
    return out


def ft(a, b):
    return int(a) + (float(b) / 12.0 if b else 0.0)


def parse_dims(lines):
    """Clear room size from a label: ((first, second) in feet, the text) or (None, None)."""
    for ln in lines:
        m = _DIMS_FT.search(ln)
        if m:
            return (ft(m.group(1), m.group(2)), ft(m.group(3), m.group(4))), m.group(0)
    for ln in lines:
        m = _DIMS_M.search(ln)
        if m and not re.search(r"['\"]", ln):
            a, ua, b, ub = float(m.group(1)), m.group(2), float(m.group(3)), m.group(4)
            u = ua or ub
            if u == 'MM' or (u is None and a > 500 and b > 500):
                a, b = a / 304.8, b / 304.8
            elif u == 'CM':
                a, b = a / 30.48, b / 30.48
            elif u == 'M' or (u is None and 1.0 <= a <= 15 and 1.0 <= b <= 15):
                a, b = a / 0.3048, b / 0.3048
            else:
                continue
            return (a, b), m.group(0)
    return None, None


def classify(text):
    """{'kind', 'type', 'name', 'dims', 'dims_text', 'lines'} for one text on the drawing."""
    lines = normalise(text)
    if not lines:
        return {'kind': 'ignore', 'type': None, 'name': '', 'dims': None, 'lines': []}
    dims, dims_text = parse_dims(lines)
    name_lines = [ln for ln in lines if not _DIMS_FT.search(ln) and not _DIMS_M.fullmatch(ln.strip())]
    name = ' '.join(name_lines).strip(' -:,') or (lines[0] if not dims else '')
    joined = ' '.join(lines)
    res = {'kind': 'unknown', 'type': None, 'name': name, 'dims': dims, 'dims_text': dims_text, 'lines': lines}
    if (re.search(r'\bPLAN\b', joined) or (re.search(r'FLOORS?\b', joined) and (
            'TYPICAL' in joined or title_level(joined)[0] is not None))) and len(joined) > 6 and not dims:
        res['kind'] = 'title'
        res['level'], res['level_name'] = title_level(joined)
        return res
    stripped = re.sub(r'[^A-Z0-9]', '', joined)
    if stripped in COMPASS and len(joined) <= 6:
        res.update(kind='compass', type=stripped)
        return res
    if not name and dims:
        res['kind'] = 'dims'
        return res
    if re.fullmatch(r"[\d'\"\s.\-/X]+", joined):
        res['kind'] = 'dims'
        return res
    if DOOR_TAG.match(name):
        res['kind'] = 'main_door' if re.fullmatch(r'(MD|DM|D\s*M|M\.?D\.?)\s*-?\d*', name) else 'door_tag'
        res['type'] = 'main_door' if res['kind'] == 'main_door' else None
        return res
    for rx, kind, typ in _VOCAB:
        if rx.search(name if name else joined):
            res.update(kind=kind, type=typ)
            if kind == 'room' and typ == 'staircase':
                w = _WIDE.search(joined)
                if w:
                    res['width'] = ft(w.group(1), w.group(2))
            w = _WIDE.search(res['name'])
            if w:
                res['name'] = (res['name'][:w.start()] + res['name'][w.end():]).strip(' -,') or res['name']
            return res
    if IGNORE_WORDS.match(name):
        res['kind'] = 'ignore'
    return res


def title_level(s):
    s = s.upper()
    typical = 'TYPICAL' in s
    found = []
    for rx, n, name in _LEVELS:
        if rx.search(s):
            found.append((n, name))
    if not found:
        return None, None
    if typical:
        m = re.search(r'(G\.?\s?F|GF|GROUND)\s*\+\s*(\d+)', s)
        lo = min(n for n, _ in found)
        if m:
            return lo, f'Typical floor (GF+{int(m.group(2))})'
        return lo, 'Typical floor'
    n, name = sorted(found, key=lambda t: (t[0] == 0 and t[1] == 'Ground floor', t[0]))[0]
    if any(t[1] == 'Stilt' for t in found):
        return 0, 'Stilt'
    return n, name
