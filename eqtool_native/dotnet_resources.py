"""Read embedded (manifest) resources from a .NET assembly, no .NET required.

Used to import the zone maps that ship inside EQTool.exe:
    python3 -m eqtool_native --import-eqtool-maps /path/to/EQTool.exe
"""
import os
import re
import struct


class DotNetError(Exception):
    pass


# Tables before ManifestResource (0x28), as column specs. Codes:
#   int: fixed byte width; "s" string heap; "g" guid heap; "b" blob heap;
#   ("t", table) simple index; ("c", name) coded index.
_CODED = {
    "ResolutionScope": (2, [0x00, 0x1A, 0x23, 0x01]),
    "TypeDefOrRef": (2, [0x02, 0x01, 0x1B]),
    "HasConstant": (2, [0x04, 0x08, 0x17]),
    "HasCustomAttribute": (5, [0x06, 0x04, 0x01, 0x02, 0x08, 0x09, 0x0A, 0x00, 0x0E, 0x17,
                               0x14, 0x11, 0x1A, 0x1B, 0x20, 0x23, 0x26, 0x27, 0x28, 0x2A,
                               0x2C, 0x2B]),
    "HasFieldMarshal": (1, [0x04, 0x08]),
    "HasDeclSecurity": (2, [0x02, 0x06, 0x20]),
    "MemberRefParent": (3, [0x02, 0x01, 0x1A, 0x06, 0x1B]),
    "HasSemantics": (1, [0x14, 0x17]),
    "MethodDefOrRef": (1, [0x06, 0x0A]),
    "MemberForwarded": (1, [0x04, 0x06]),
    "Implementation": (2, [0x26, 0x23, 0x27]),
    "CustomAttributeType": (3, [0x06, 0x0A]),
}
_T = {
    0x00: [2, "s", "g", "g", "g"],
    0x01: [("c", "ResolutionScope"), "s", "s"],
    0x02: [4, "s", "s", ("c", "TypeDefOrRef"), ("t", 0x04), ("t", 0x06)],
    0x03: [("t", 0x04)],
    0x04: [2, "s", "b"],
    0x05: [("t", 0x06)],
    0x06: [4, 2, 2, "s", "b", ("t", 0x08)],
    0x07: [("t", 0x08)],
    0x08: [2, 2, "s"],
    0x09: [("t", 0x02), ("c", "TypeDefOrRef")],
    0x0A: [("c", "MemberRefParent"), "s", "b"],
    0x0B: [2, ("c", "HasConstant"), "b"],
    0x0C: [("c", "HasCustomAttribute"), ("c", "CustomAttributeType"), "b"],
    0x0D: [("c", "HasFieldMarshal"), "b"],
    0x0E: [2, ("c", "HasDeclSecurity"), "b"],
    0x0F: [2, 4, ("t", 0x02)],
    0x10: [4, ("t", 0x04)],
    0x11: ["b"],
    0x12: [("t", 0x02), ("t", 0x14)],
    0x13: [("t", 0x14)],
    0x14: [2, "s", ("c", "TypeDefOrRef")],
    0x15: [("t", 0x02), ("t", 0x17)],
    0x16: [("t", 0x17)],
    0x17: [2, "s", "b"],
    0x18: [2, ("t", 0x06), ("c", "HasSemantics")],
    0x19: [("t", 0x02), ("c", "MethodDefOrRef"), ("c", "MethodDefOrRef")],
    0x1A: ["s"],
    0x1B: ["b"],
    0x1C: [2, ("c", "MemberForwarded"), "s", ("t", 0x1A)],
    0x1D: [4, ("t", 0x04)],
    0x1E: [4, 4],
    0x1F: [4],
    0x20: [4, 8, 4, "b", "s", "s"],
    0x21: [4],
    0x22: [12],
    0x23: [8, 4, "b", "s", "s", "b"],
    0x24: [4, ("t", 0x23)],
    0x25: [12, ("t", 0x23)],
    0x26: [4, "s", "b"],
    0x27: [4, 4, "s", "s", ("c", "Implementation")],
    0x28: [4, 4, "s", ("c", "Implementation")],
}


def _u16(b, o):
    return struct.unpack_from("<H", b, o)[0]


def _u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def _read_exe_bytes(path):
    """Bytes of an .exe, or of EQTool.exe inside a downloaded EQTool .zip."""
    import zipfile
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            exes = [n for n in z.namelist() if n.lower().endswith("eqtool.exe")]
            if not exes:
                raise DotNetError("no EQTool.exe inside the zip")
            return z.read(exes[0])
    with open(path, "rb") as f:
        return f.read()


def read_resources(path):
    """Return {resource_name: bytes} for every embedded manifest resource."""
    b = _read_exe_bytes(path)
    if b[:2] != b"MZ":
        raise DotNetError("not a Windows executable")
    pe = _u32(b, 0x3C)
    if b[pe:pe + 4] != b"PE\0\0":
        raise DotNetError("bad PE header")
    nsec = _u16(b, pe + 6)
    opt_size = _u16(b, pe + 20)
    opt = pe + 24
    magic = _u16(b, opt)
    dd = opt + (96 if magic == 0x10B else 112)
    clr_rva = _u32(b, dd + 14 * 8)
    if not clr_rva:
        raise DotNetError("not a .NET assembly")
    secs = []
    so = opt + opt_size
    for i in range(nsec):
        o = so + i * 40
        vsize, va, rsize, rptr = struct.unpack_from("<IIII", b, o + 8)
        secs.append((va, max(vsize, rsize), rptr))

    def off(rva):
        for va, size, rptr in secs:
            if va <= rva < va + size:
                return rva - va + rptr
        raise DotNetError(f"RVA {rva:#x} not in any section")

    cli = off(clr_rva)
    md = off(_u32(b, cli + 8))
    res_base = off(_u32(b, cli + 24)) if _u32(b, cli + 24) else None
    if _u32(b, md) != 0x424A5342:
        raise DotNetError("bad metadata signature")
    vlen = _u32(b, md + 12)
    p = md + 16 + vlen + 2
    nstreams = _u16(b, p)
    p += 2
    streams = {}
    for _ in range(nstreams):
        so_, ss = _u32(b, p), _u32(b, p + 4)
        p += 8
        end = b.index(b"\0", p)
        name = b[p:end].decode("ascii")
        p = (end + 4) & ~3
        streams[name] = (md + so_, ss)
    tbl = streams.get("#~") or streams.get("#-")
    strings = streams.get("#Strings")
    if not tbl or not strings:
        raise DotNetError("metadata streams missing")
    t = tbl[0]
    heap = b[t + 6]
    valid = struct.unpack_from("<Q", b, t + 8)[0]
    q = t + 24
    rows = {}
    for i in range(64):
        if valid >> i & 1:
            rows[i] = _u32(b, q)
            q += 4
    if heap & 0x40:
        q += 4
    sz = {"s": 4 if heap & 1 else 2, "g": 4 if heap & 2 else 2, "b": 4 if heap & 4 else 2}

    def colsize(c):
        if isinstance(c, int):
            return c
        if isinstance(c, str):
            return sz[c]
        if c[0] == "t":
            return 2 if rows.get(c[1], 0) < 0x10000 else 4
        bits, tables = _CODED[c[1]]
        mx = max(rows.get(x, 0) for x in tables)
        return 2 if mx < (1 << (16 - bits)) else 4

    for tid in range(0x28):
        if tid in rows:
            q += rows[tid] * sum(colsize(c) for c in _T[tid])
    if 0x28 not in rows:
        return {}

    def rd(o, n):
        return _u16(b, o) if n == 2 else _u32(b, o)

    def string(idx):
        s0 = strings[0] + idx
        return b[s0:b.index(b"\0", s0)].decode("utf-8", errors="replace")

    cols = [colsize(c) for c in _T[0x28]]
    out = {}
    for _ in range(rows[0x28]):
        offset = rd(q, cols[0])
        name = string(rd(q + cols[0] + cols[1], cols[2]))
        impl = rd(q + sum(cols[:3]), cols[3])
        q += sum(cols)
        if impl == 0 and res_base is not None:  # stored in this file
            start = res_base + offset
            n = _u32(b, start)
            out[name] = b[start + 4:start + 4 + n]
    return out


def extract_eqtool_maps(exe_path, dest):
    """Write EQTool's embedded map_files/*.txt into dest (lowercased). Returns count."""
    res = read_resources(exe_path)
    os.makedirs(dest, exist_ok=True)
    n = 0
    for name, data in res.items():
        m = re.search(r"map_files\.(.+\.txt)$", name, re.I)
        if not m:
            continue
        with open(os.path.join(dest, m.group(1).lower()), "wb") as f:
            f.write(data)
        n += 1
    return n
