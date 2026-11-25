"""Read plan geometry from AutoCAD DWG files, in pure Python.

Supported: AutoCAD 2000 (AC1015), 2004 (AC1018), 2010 (AC1024), 2013 (AC1027) and 2018 (AC1032) formats,
which covers every file saved by AutoCAD 2018 and later in its default format. AutoCAD 2007 format (AC1021)
and anything older than 2000 are not supported: save as 2018 DWG or as DXF instead.

Only what a plan check needs is decoded: layers (name, frozen, off), block definitions, and the entities
LINE, ARC, CIRCLE, ELLIPSE, LWPOLYLINE, POLYLINE (2D), SOLID, TEXT, MTEXT, INSERT and ATTRIB. Dimensions,
hatches, leaders and everything else are skipped. The result has the same shape as cad_read.read_dxf().

    from dwg_read import read_dwg
    dwg = read_dwg('plan.dwg')   # {'version', 'units', 'layers', 'blocks', 'model'}
"""
import math
import struct
import zlib  # noqa: F401  (kept for parity with the writer; not needed to read)
from pathlib import Path


class DwgError(Exception):
    pass


VERSIONS = {'AC1015': 2000, 'AC1018': 2004, 'AC1024': 2010, 'AC1027': 2013, 'AC1032': 2018}
UNSUPPORTED = {'AC1021': 'AutoCAD 2007', 'AC1014': 'Release 14', 'AC1012': 'Release 13', 'AC1009': 'Release 11/12'}


# ---------------------------------------------------------------- bit reader

class Bits:
    """Bit reader over a byte buffer, most significant bit first (the DWG bit-stream convention)."""

    __slots__ = ('buf', 'pos', 'end')

    def __init__(self, buf, pos=0, end=None):
        self.buf = buf
        self.pos = pos
        self.end = len(buf) * 8 if end is None else end

    def bits(self, n):
        if n == 0:
            return 0
        p = self.pos
        i, o = p >> 3, p & 7
        k = (o + n + 7) >> 3
        if (p + n) > len(self.buf) * 8:
            raise DwgError('read past the end of an object')
        v = int.from_bytes(self.buf[i:i + k], 'big')
        self.pos = p + n
        return (v >> (k * 8 - o - n)) & ((1 << n) - 1)

    def B(self):
        return self.bits(1)

    def BB(self):
        return self.bits(2)

    def RC(self):
        return self.bits(8)

    def RS(self):
        lo = self.bits(8)
        return lo | (self.bits(8) << 8)

    def RL(self):
        lo = self.RS()
        return lo | (self.RS() << 16)

    def RD(self):
        return struct.unpack('<d', bytes(self.bits(8) for _ in range(8)))[0]

    def BS(self):
        c = self.BB()
        if c == 0:
            return self.RS()
        if c == 1:
            return self.RC()
        if c == 2:
            return 0
        return 256

    def BL(self):
        c = self.BB()
        if c == 0:
            return self.RL()
        if c == 1:
            return self.RC()
        if c == 2:
            return 0
        raise DwgError('bad BL code')

    def BLL(self):
        n = 0
        while n < 3 and self.B():
            n += 1
        v = 0
        for i in range(n):
            v |= self.RC() << (8 * i)
        return v

    def BD(self):
        c = self.BB()
        if c == 0:
            return self.RD()
        if c == 1:
            return 1.0
        if c == 2:
            return 0.0
        raise DwgError('bad BD code')

    def DD(self, dflt):
        c = self.BB()
        if c == 0:
            return dflt
        d = bytearray(struct.pack('<d', dflt))
        if c == 1:
            for i in range(4):
                d[i] = self.RC()
        elif c == 2:
            d[4] = self.RC()
            d[5] = self.RC()
            for i in range(4):
                d[i] = self.RC()
        else:
            return self.RD()
        return struct.unpack('<d', bytes(d))[0]

    def BT(self):
        return 0.0 if self.B() else self.BD()

    def BE(self):
        if self.B():
            return (0.0, 0.0, 1.0)
        return (self.BD(), self.BD(), self.BD())

    def threeBD(self):
        return (self.BD(), self.BD(), self.BD())

    def twoRD(self):
        return (self.RD(), self.RD())

    def H(self):
        code = self.bits(4)
        n = self.bits(4)
        v = 0
        for _ in range(n):
            v = (v << 8) | self.RC()
        return code, v

    def MS(self):
        v, shift = 0, 0
        while True:
            w = self.RS()
            v |= (w & 0x7FFF) << shift
            shift += 15
            if not w & 0x8000:
                return v

    def UMC(self):
        v, shift = 0, 0
        while True:
            b = self.RC()
            v |= (b & 0x7F) << shift
            shift += 7
            if not b & 0x80:
                return v

    def TV(self, codepage='cp1252'):
        n = self.BS()
        raw = bytes(self.RC() for _ in range(n))
        return raw.split(b'\0', 1)[0].decode(codepage, 'replace')

    def TU(self):
        n = self.BS()
        chars = [self.RS() for _ in range(n)]
        s = ''.join(chr(c) for c in chars if c)
        return s

    def CMC(self, version):
        """Colour of a table entry: the ACI number (negative when the layer is off, as in DXF)."""
        idx = self.BS()
        if idx >= 0x8000:
            idx -= 0x10000
        if version >= 2004:
            rgb = self.BL()
            cb = self.RC()
            if cb & 1:
                self.TV()
            if cb & 2:
                self.TV()
            if rgb >> 24 == 0xC3:
                return (rgb & 0xFF) * (-1 if idx < 0 else 1)
        return idx

    def ENC(self):
        flags = self.BS()
        if flags & 0x8000:
            self.BL()
        if flags & 0x2000:
            self.BL()
        return flags & 0x1FF, bool(flags & 0x4000)


def umc_at(buf, i):
    v, shift = 0, 0
    while True:
        b = buf[i]
        i += 1
        v |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            return v, i


def smc_at(buf, i):
    v, shift = 0, 0
    while True:
        b = buf[i]
        i += 1
        if b & 0x80:
            v |= (b & 0x7F) << shift
            shift += 7
        else:
            v |= (b & 0x3F) << shift
            return (-v if b & 0x40 else v), i


# ---------------------------------------------------------------- R2004 container

def _lit_len(src, i):
    b = src[i]
    if b > 0x0F:
        return 0, i
    i += 1
    if b == 0:
        total = 0x0F
        b = src[i]
        i += 1
        while b == 0:
            total += 0xFF
            b = src[i]
            i += 1
        return total + b + 3, i
    return b + 3, i


def _long_offset(src, i):
    total = 0
    b = src[i]
    i += 1
    while b == 0:
        total += 0xFF
        b = src[i]
        i += 1
    return total + b, i


def _two_byte(src, i):
    a, b = src[i], src[i + 1]
    return (a >> 2) | (b << 6), a & 3, i + 2


def decompress(src, out_size):
    """The LZ77 variant used for DWG 2004 and later sections."""
    dst = bytearray()
    i = 0
    n = len(src)
    lit, i = _lit_len(src, i)
    dst += src[i:i + lit]
    i += lit
    while i < n and len(dst) < out_size:
        op = src[i]
        i += 1
        if op == 0x11:
            break
        if op == 0x10:
            nb, i = _long_offset(src, i)
            nb += 9
            off, lit, i = _two_byte(src, i)
            off += 0x3FFF
        elif 0x12 <= op <= 0x1F:
            nb = (op & 0x0F) + 2
            off, lit, i = _two_byte(src, i)
            off += 0x3FFF
        elif op == 0x20:
            nb, i = _long_offset(src, i)
            nb += 0x21
            off, lit, i = _two_byte(src, i)
        elif 0x21 <= op <= 0x3F:
            nb = op - 0x1E
            off, lit, i = _two_byte(src, i)
        elif op >= 0x40:
            nb = ((op & 0xF0) >> 4) - 1
            off = (src[i] << 2) | ((op & 0x0C) >> 2)
            i += 1
            lit = op & 3
        else:
            raise DwgError('corrupt compressed data (opcode 0x%02x)' % op)
        if lit == 0:
            lit, i = _lit_len(src, i)
        start = len(dst) - off - 1
        if start < 0:
            raise DwgError('corrupt compressed data (offset before start)')
        if off + 1 >= nb:
            dst += dst[start:start + nb]
        else:
            for k in range(nb):
                dst.append(dst[start + k])
        dst += src[i:i + lit]
        i += lit
    return bytes(dst)


def _xor_header(raw):
    out, seed = bytearray(), 1
    for b in raw:
        seed = (seed * 0x343FD + 0x269EC3) & 0xFFFFFFFF
        out.append(b ^ ((seed >> 16) & 0xFF))
    return bytes(out)


def _system_page(data, addr, want_type):
    ptype, dsize, csize, ctype, _chk = struct.unpack_from('<5I', data, addr)
    if ptype != want_type:
        raise DwgError('unexpected page type 0x%08x at 0x%x' % (ptype, addr))
    body = data[addr + 20:addr + 20 + csize]
    return decompress(body, dsize) if ctype == 2 else body[:dsize]


def read_sections_2004(data):
    hdr = _xor_header(data[0x80:0x80 + 0x6C])
    if not hdr.startswith(b'AcFssFcAJMB'):
        raise DwgError('the file header is not readable (not a 2004-2018 DWG?)')
    pm_addr = struct.unpack_from('<Q', hdr, 0x54)[0] + 0x100
    sm_id = struct.unpack_from('<I', hdr, 0x5C)[0]
    pm = _system_page(data, pm_addr, 0x41630E3B)
    pages, addr, i = {}, 0x100, 0
    while i + 8 <= len(pm):
        num, size = struct.unpack_from('<ii', pm, i)
        i += 8
        if num >= 0:
            pages[num] = addr
        else:
            i += 16  # gap: parent, left, right, 0
        addr += size
    if sm_id not in pages:
        raise DwgError('section map page missing')
    sm = _system_page(data, pages[sm_id], 0x4163003B)
    ndesc = struct.unpack_from('<I', sm, 0)[0]
    i = 20
    sections = {}
    for _ in range(ndesc):
        size, npages, maxsize, _unk, comp, sid, enc = struct.unpack_from('<QIIIIII', sm, i)
        i += 32
        name = sm[i:i + 64].split(b'\0', 1)[0].decode('latin1')
        i += 64
        plist = []
        for _ in range(npages):
            pnum, csize, start = struct.unpack_from('<IIQ', sm, i)
            i += 16
            plist.append((pnum, csize, start))
        sections[name] = dict(size=size, pages=plist, maxsize=maxsize, comp=comp, enc=enc)

    def load(name):
        s = sections.get(name)
        if not s:
            return None
        buf = bytearray(s['size'])
        for pnum, csize, start in s['pages']:
            a = pages.get(pnum)
            if a is None:
                continue
            mask = 0x4164536B ^ a
            ph = [v ^ mask for v in struct.unpack_from('<8I', data, a)]
            dsize = ph[2]
            body = data[a + 32:a + 32 + dsize]
            chunk = decompress(body, s['maxsize']) if s['comp'] == 2 else body
            end = min(len(buf), start + len(chunk))
            buf[start:end] = chunk[:end - start]
        return bytes(buf)

    return load


# ---------------------------------------------------------------- R2000 container

def read_sections_2000(data):
    n = struct.unpack_from('<I', data, 0x15)[0]
    recs = {}
    for k in range(n):
        num, seek, size = struct.unpack_from('<BII', data, 0x19 + 9 * k)
        recs[num] = (seek, size)
    return recs


# ---------------------------------------------------------------- handle map

def read_handle_map(buf, start=0):
    out = {}
    i = start
    while i + 2 <= len(buf):
        size = (buf[i] << 8) | buf[i + 1]
        if size <= 2:
            break
        end = i + size
        j = i + 2
        h = loc = 0
        while j < end:
            dh, j = umc_at(buf, j)
            dl, j = smc_at(buf, j)
            h += dh
            loc += dl
            out[h] = loc
        i = end + 2
    return out


# ---------------------------------------------------------------- objects

T_TEXT, T_ATTRIB, T_ATTDEF, T_BLOCK, T_ENDBLK, T_SEQEND, T_INSERT, T_MINSERT = 1, 2, 3, 4, 5, 6, 7, 8
T_VERTEX2D, T_POLY2D, T_ARC, T_CIRCLE, T_LINE, T_SOLID, T_ELLIPSE = 10, 15, 17, 18, 19, 31, 35
T_MTEXT, T_BLOCK_HEADER, T_LAYER, T_LWPOLY = 44, 49, 51, 77
ENTITY_TYPES = set(range(1, 48)) | {77, 78, 101}
WANTED = {T_TEXT, T_ATTRIB, T_INSERT, T_MINSERT, T_VERTEX2D, T_POLY2D, T_ARC, T_CIRCLE, T_LINE, T_SOLID,
          T_ELLIPSE, T_MTEXT, T_BLOCK_HEADER, T_LAYER, T_LWPOLY}


class Obj:
    __slots__ = ('type', 'handle', 'data', 'hdl', 'strs', 'version', 'entmode', 'owner', 'refs', 'cp')

    def T(self):
        if self.version >= 2007:
            return self.strs.TU()
        return self.data.TV(self.cp)

    def ref(self):
        """Next handle reference from the handle stream, resolved to an absolute handle."""
        code, v = self.hdl.H()
        if code <= 5:
            return v
        if code == 6:
            return self.handle + 1
        if code == 8:
            return self.handle - 1
        if code == 0xA:
            return self.handle + v
        if code == 0xC:
            return self.handle - v
        return v


def _open_object(buf, loc, version, cp):
    r = Bits(buf, loc * 8)
    size = r.MS()
    hsize = r.UMC() if version >= 2010 else None
    start = r.pos
    end_bits = start + size * 8
    r.end = end_bits
    o = Obj()
    o.version, o.cp = version, cp
    if version >= 2010:
        c = r.BB()
        o.type = r.RC() if c == 0 else (r.RC() + 0x1F0 if c == 1 else r.RS())
        bitsize = size * 8 - hsize
    else:
        o.type = r.BS()
        bitsize = r.RL()
    o.data = r
    o.hdl = Bits(buf, start + bitsize, end_bits) if version >= 2004 else None
    if version < 2004:
        o.hdl = Bits(buf, start + bitsize, end_bits)
    o.strs = None
    if version >= 2007:
        s = Bits(buf, start + bitsize - 1, end_bits)
        if s.B():
            p = start + bitsize - 1 - 16
            s.pos = p
            n = s.RS()
            if n & 0x8000:
                p -= 16
                s.pos = p
                hi = s.RS()
                n = (n & 0x7FFF) | (hi << 15)
            s.pos = p - n
            o.strs = s
        else:
            o.strs = Bits(b'')
    _code, o.handle = r.H()
    # extended entity data
    n = r.BS()
    while n:
        r.H()
        r.pos += n * 8
        n = r.BS()
    return o


def _entity_common(o, version):
    r = o.data
    if r.B():  # graphic image
        n = r.BLL() if version >= 2010 else r.RL()
        r.pos += n * 8
    o.entmode = r.BB()
    nreact = r.BL()
    xdic_missing = r.B() if version >= 2004 else 0
    if version >= 2013:
        r.B()
    nolinks = r.B() if version < 2004 else 1
    if version >= 2004:
        _c, has_book = r.ENC()
    else:
        r.BS()
        has_book = False
    r.BD()  # linetype scale
    ltf = r.BB()
    psf = r.BB()
    matf = 0
    if version >= 2007:
        matf = r.BB()
        r.RC()
    if version >= 2010:
        r.B(); r.B(); r.B()
    r.BS()  # invisible
    r.RC()  # lineweight
    # handles
    h = o.hdl
    o.owner = o.ref() if o.entmode == 0 else None
    for _ in range(nreact):
        o.ref()
    if not xdic_missing:
        o.ref()
    refs = []
    # layer, then optional linetype / links / material / plot style, then type-specific handles
    nextra = (1 if ltf == 3 else 0) + (0 if nolinks else 2) + (1 if matf == 3 else 0) + (1 if psf == 3 else 0)
    try:
        for _ in range(1 + nextra + (1 if has_book else 0)):
            refs.append(o.ref())
    except DwgError:
        pass
    o.refs = refs
    return h


def _object_common(o, version):
    r = o.data
    nreact = r.BL()
    xdic_missing = r.B() if version >= 2004 else 0
    if version >= 2013:
        r.B()
    return nreact, xdic_missing


def _rest_refs(o, limit=64):
    out = []
    try:
        for _ in range(limit):
            if o.hdl.pos + 8 > o.hdl.end:
                break
            out.append(o.ref())
    except DwgError:
        pass
    return out


def read_dwg(path):
    data = Path(path).read_bytes()
    ver = data[:6].decode('latin1', 'replace')
    if ver in UNSUPPORTED:
        raise DwgError(f'{ver} ({UNSUPPORTED[ver]} format) is not supported: save the drawing as AutoCAD 2018 DWG '
                       'or as DXF (SAVEAS or DXFOUT) and try again.')
    if ver not in VERSIONS:
        raise DwgError(f'not a DWG file this reader knows (version string {ver!r}).')
    version = VERSIONS[ver]
    cp_byte = struct.unpack_from('<H', data, 0x13)[0]
    cp = {30: 'cp1252', 29: 'cp1251', 31: 'cp1253', 38: 'cp932'}.get(cp_byte, 'cp1252')
    if version >= 2004:
        load = read_sections_2004(data)
        objbuf = load('AcDb:AcDbObjects')
        hm = load('AcDb:Handles')
        if objbuf is None or hm is None:
            raise DwgError('objects or handle map section missing')
        handles = read_handle_map(hm)
    else:
        recs = read_sections_2000(data)
        if 2 not in recs:
            raise DwgError('object map missing')
        seek, size = recs[2]
        handles = read_handle_map(data[seek:seek + size])
        objbuf = data
    return decode_objects(objbuf, handles, version, cp, ver)


def decode_objects(buf, handles, version, cp, ver):
    layers, block_names, block_base, block_owned = {}, {}, {}, {}
    ents, problems = [], 0
    types = {}
    for h, loc in handles.items():
        if loc < 0 or loc >= len(buf):
            continue
        try:
            o = _open_object(buf, loc, version, cp)
        except (DwgError, IndexError, struct.error):
            problems += 1
            continue
        types[o.handle] = o.type
        if o.type not in WANTED:
            continue
        try:
            if o.type == T_LAYER:
                _parse_layer(o, version, layers)
            elif o.type == T_BLOCK_HEADER:
                _parse_block_header(o, version, block_names, block_base, block_owned)
            else:
                e = _parse_entity(o, version)
                if e is not None:
                    ents.append(e)
        except (DwgError, IndexError, struct.error, OverflowError, ValueError):
            problems += 1
    return assemble(ents, layers, block_names, block_base, types, problems, ver)


def _parse_layer(o, version, layers):
    nreact, xmiss = _object_common(o, version)
    r = o.data
    name = o.T()
    r.B()                       # 64-flag
    if version < 2007:
        r.BS()                  # xref index + 1
    r.B()                       # xref dependent
    flags = r.BS()
    color = r.CMC(version)
    layers[o.handle] = {'name': name, 'frozen': bool(flags & 1), 'off': bool(flags & 2) or color < 0,
                        'color': abs(color)}


def _parse_block_header(o, version, names, bases, owned):
    nreact, xmiss = _object_common(o, version)
    r = o.data
    name = o.T()
    r.B()
    if version < 2007:
        r.BS()
    r.B()                       # xdep
    r.B()                       # anonymous
    r.B()                       # has attribs
    isxref = r.B()
    overlaid = r.B()
    r.B()                       # loaded
    if version >= 2004 and not isxref and not overlaid:
        r.BL()
    base = r.threeBD()
    names[o.handle] = name
    bases[o.handle] = (base[0], base[1])


def _ocs(ext):
    return ext[2] < -0.5


def _parse_entity(o, version):
    r = o.data
    t = o.type
    _entity_common(o, version)
    layer_h = o.refs[0] if o.refs else None
    e = {'hd': o.handle, 'layer_h': layer_h, 'mode': o.entmode, 'owner': o.owner, 'refs': o.refs}
    if t == T_LINE:
        z = r.B()
        x1 = r.RD(); x2 = r.DD(x1); y1 = r.RD(); y2 = r.DD(y1)
        if not z:
            z1 = r.RD(); r.DD(z1)
        r.BT()
        ext = r.BE()
        e.update(type='LINE', p1=(x1, y1), p2=(x2, y2))
        return e
    if t in (T_CIRCLE, T_ARC):
        c = r.threeBD(); rad = r.BD(); r.BT(); ext = r.BE()
        e.update(type='CIRCLE', c=(c[0], c[1]), r=rad, ocs=_ocs(ext))
        if t == T_ARC:
            e.update(type='ARC', a0=r.BD(), a1=r.BD())
        return e
    if t == T_ELLIPSE:
        c = r.threeBD(); ax = r.threeBD(); ext = r.threeBD(); ratio = r.BD(); a0 = r.BD(); a1 = r.BD()
        e.update(type='ELLIPSE', c=(c[0], c[1]), major=(ax[0], ax[1]), ratio=ratio, a0=a0, a1=a1)
        return e
    if t == T_SOLID:
        r.BT(); elev = r.BD()
        pts = [r.twoRD() for _ in range(4)]
        ext = r.BE()
        e.update(type='SOLID', pts=[pts[0], pts[1], pts[3], pts[2]], ocs=_ocs(ext))
        return e
    if t == T_LWPOLY:
        flag = r.BS()
        if flag & 4:
            r.BD()
        if flag & 8:
            r.BD()
        if flag & 2:
            r.BD()
        ext = (0.0, 0.0, 1.0)
        if flag & 1:
            ext = r.threeBD()
        n = r.BL()
        nb = r.BL() if flag & 16 else 0
        nid = r.BL() if (version >= 2010 and flag & 1024) else 0
        nw = r.BL() if flag & 32 else 0
        if n > 100000:
            raise DwgError('implausible polyline')
        pts = []
        if n:
            x, y = r.RD(), r.RD()
            pts.append((x, y))
            for _ in range(n - 1):
                x = r.DD(x)
                y = r.DD(y)
                pts.append((x, y))
        bulges = [r.BD() for _ in range(nb)]
        e.update(type='LWPOLYLINE', pts=pts, bulges=bulges, closed=bool(flag & 512), ocs=_ocs(ext))
        return e
    if t == T_POLY2D:
        flags = r.BS(); r.BS(); r.BD(); r.BD(); r.BT(); elev = r.BD(); ext = r.BE()
        e.update(type='POLY2D', closed=bool(flags & 1), ocs=_ocs(ext), verts=[])
        return e
    if t == T_VERTEX2D:
        flags = r.RC()
        p = r.threeBD()
        sw = r.BD()
        if sw < 0:
            sw = -sw
        else:
            r.BD()
        bulge = r.BD()
        e.update(type='VERTEX', p=(p[0], p[1]), bulge=bulge, vflags=flags)
        return e
    if t in (T_TEXT, T_ATTRIB):
        df = r.RC()
        if not df & 1:
            r.RD()
        p = r.twoRD()
        ap = p
        if not df & 2:
            ap = (r.DD(p[0]), r.DD(p[1]))
        ext = r.BE(); r.BT()
        if not df & 4:
            r.RD()
        rot = 0.0 if df & 8 else r.RD()
        hgt = r.RD()
        wf = 1.0 if df & 16 else r.RD()
        text = o.T()
        if not df & 32:
            r.BS()
        ha = 0 if df & 64 else r.BS()
        va = 0 if df & 128 else r.BS()
        e.update(type='TEXT' if t == T_TEXT else 'ATTRIB', text=text, p=p, ap=ap, h=hgt, rot=rot, ha=ha, va=va,
                 wf=wf, ocs=_ocs(ext))
        return e
    if t == T_MTEXT:
        p = r.threeBD(); ext = r.threeBD(); xd = r.threeBD()
        width = r.BD()
        if version >= 2007:
            r.BD()
        hgt = r.BD()
        attach = r.BS()
        r.BS()
        r.BD(); r.BD()
        text = o.T()
        e.update(type='MTEXT', text=text, p=(p[0], p[1]), h=hgt, rot=math.atan2(xd[1], xd[0]), attach=attach,
                 width=width, ocs=_ocs(ext))
        return e
    if t in (T_INSERT, T_MINSERT):
        p = r.threeBD()
        if version >= 2000:
            sf = r.BB()
            if sf == 3:
                sx = sy = 1.0
            elif sf == 1:
                sx = 1.0
                sy = r.DD(1.0); r.DD(1.0)
            elif sf == 2:
                sx = sy = r.RD()
            else:
                sx = r.RD(); sy = r.DD(sx); r.DD(sx)
        else:
            sx, sy, _ = r.threeBD()
        rot = r.BD()
        ext = r.threeBD()
        has_att = r.B()
        nown = r.BL() if (version >= 2004 and has_att) else 0
        if t == T_MINSERT:
            r.BS(); r.BS(); r.BD(); r.BD()
        rest = _rest_refs(o)
        e.update(type='INSERT', p=(p[0], p[1]), sx=sx, sy=sy, rot=rot, ocs=_ocs(ext), cand=o.refs[1:] + rest,
                 has_att=has_att)
        return e
    return None


# ---------------------------------------------------------------- assembly

def assemble(ents, layers, block_names, block_base, types, problems, ver):
    def layer_of(e):
        for h in [e['layer_h']] + list(e['refs'][1:]):
            if h in layers:
                return layers[h]['name']
        return '0'

    by_owner = {}
    verts = {}
    polys = {}
    for e in ents:
        e['layer'] = layer_of(e)
        if e['type'] == 'VERTEX':
            verts.setdefault(e['owner'], []).append(e)
            continue
        if e['type'] == 'POLY2D':
            polys[e['hd']] = e
        if e['type'] == 'INSERT':
            name = None
            for h in e['cand']:
                if h in block_names:
                    name = block_names[h]
                    break
            e['name'] = name
        key = 'model' if e['mode'] == 2 else ('paper' if e['mode'] == 1 else e['owner'])
        by_owner.setdefault(key, []).append(e)
    for h, p in polys.items():
        vs = sorted(verts.get(h, []), key=lambda v: v['hd'])
        p['pts'] = [v['p'] for v in vs if not v['vflags'] & 16]
        p['bulges'] = [v['bulge'] for v in vs if not v['vflags'] & 16]
        p['type'] = 'LWPOLYLINE'
    # attributes belong to their insert
    inserts = {e['hd']: e for e in ents if e['type'] == 'INSERT'}
    for e in ents:
        if e['type'] == 'ATTRIB' and e['owner'] in inserts:
            inserts[e['owner']].setdefault('attribs', []).append(e)
    blocks = {}
    for h, name in block_names.items():
        lst = [x for x in by_owner.get(h, []) if x['type'] != 'ATTRIB']
        blocks[name] = {'base': block_base.get(h, (0.0, 0.0)), 'ents': [_clean(x) for x in lst]}
    model = [_clean(x) for x in by_owner.get('model', []) if x['type'] != 'ATTRIB']
    lay = {v['name']: {'frozen': v['frozen'], 'off': v['off'], 'color': v['color']} for v in layers.values()}
    return {'version': ver, 'units': None, 'layers': lay, 'blocks': blocks, 'model': model, 'problems': problems}


def _clean(e):
    out = {k: v for k, v in e.items() if k not in ('refs', 'cand', 'layer_h', 'mode', 'owner', 'hd')}
    if 'attribs' in e:
        out['attribs'] = [_clean(a) for a in e['attribs']]
    return out


if __name__ == '__main__':
    import collections
    import sys
    d = read_dwg(sys.argv[1])
    print(d['version'], 'problems:', d['problems'])
    print('layers:', len(d['layers']), 'blocks:', len(d['blocks']), 'model entities:', len(d['model']))
    c = collections.Counter((e['type'], e['layer']) for e in d['model'])
    for (t, l), n in sorted(c.items(), key=lambda kv: -kv[1])[:60]:
        print(f'{n:6d}  {t:12s} {l}')
