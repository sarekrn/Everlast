"""Tiny .NET IL disassembler, used by extract_respawns.py to read EQTool's data tables."""
import os, struct, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from eqtool_native import dotnet_resources as D

def load(path):
    b = open(path, "rb").read()
    pe = D._u32(b, 0x3C); nsec = D._u16(b, pe+6); opt_size = D._u16(b, pe+20); opt = pe+24
    magic = D._u16(b, opt); dd = opt + (96 if magic == 0x10B else 112)
    secs=[]; so=opt+opt_size
    for i in range(nsec):
        vsize, va, rsize, rptr = struct.unpack_from("<IIII", b, so+i*40+8); secs.append((va, max(vsize,rsize), rptr))
    def off(rva):
        for va,size,rptr in secs:
            if va<=rva<va+size: return rva-va+rptr
    cli = off(D._u32(b, dd+14*8)); md = off(D._u32(b, cli+8))
    vlen = D._u32(b, md+12); p = md+16+vlen+2; n = D._u16(b,p); p+=2; streams={}
    for _ in range(n):
        o,s = D._u32(b,p), D._u32(b,p+4); p+=8; e=b.index(b"\0",p); streams[b[p:e].decode()]=(md+o,s); p=(e+4)&~3
    t = streams["#~"][0]; heap=b[t+6]; valid=struct.unpack_from("<Q",b,t+8)[0]; q=t+24; rows={}
    for i in range(64):
        if valid>>i&1: rows[i]=D._u32(b,q); q+=4
    sz={"s":4 if heap&1 else 2,"g":4 if heap&2 else 2,"b":4 if heap&4 else 2}
    def cs(c):
        if isinstance(c,int): return c
        if isinstance(c,str): return sz[c]
        if c[0]=="t": return 2 if rows.get(c[1],0)<0x10000 else 4
        bits,tabs=D._CODED[c[1]]; mx=max(rows.get(x,0) for x in tabs); return 2 if mx<(1<<(16-bits)) else 4
    T=dict(D._T)
    tstart={}
    for tid in range(0x2D):
        if tid in rows:
            tstart[tid]=q
            if tid not in T: break
            q+=rows[tid]*sum(cs(c) for c in T[tid])
    def row(tid, idx):
        cols=[cs(c) for c in T[tid]]; o=tstart[tid]+(idx-1)*sum(cols); out=[]
        for c in cols:
            out.append(D._u16(b,o) if c==2 else (D._u32(b,o) if c==4 else b[o:o+c])); o+=c
        return out
    sbase=streams["#Strings"][0]; us=streams["#US"][0]
    def st(i): return b[sbase+i:b.index(b"\0",sbase+i)].decode("utf-8","replace")
    return dict(b=b,off=off,rows=rows,row=row,st=st,us=us,cs=cs)

OPS = {  # opcode -> (name, operand size / kind)
 0x00:("nop",0),0x02:("ldarg.0",0),0x03:("ldarg.1",0),0x04:("ldarg.2",0),0x05:("ldarg.3",0),0x06:("ldloc.0",0),0x07:("ldloc.1",0),0x08:("ldloc.2",0),0x09:("ldloc.3",0),
 0x0F:("ldarga.s","u1"),0x0A:("stloc.0",0),0x0B:("stloc.1",0),0x0C:("stloc.2",0),0x0D:("stloc.3",0),0x0E:("ldarg.s","u1"),0x10:("starg.s","u1"),0x11:("ldloc.s","u1"),0x12:("ldloca.s","u1"),0x13:("stloc.s","u1"),
 0x14:("ldnull",0),0x15:("ldc.i4.m1",0),0x16:("ldc.i4.0",0),0x17:("ldc.i4.1",0),0x18:("ldc.i4.2",0),0x19:("ldc.i4.3",0),0x1A:("ldc.i4.4",0),0x1B:("ldc.i4.5",0),0x1C:("ldc.i4.6",0),
 0x1D:("ldc.i4.7",0),0x1E:("ldc.i4.8",0),0x1F:("ldc.i4.s","i1"),0x20:("ldc.i4","i4"),0x21:("ldc.i8",8),0x22:("ldc.r4","r4"),0x23:("ldc.r8","r8"),0x25:("dup",0),0x26:("pop",0),
 0x28:("call","tok"),0x2A:("ret",0),0x2B:("br.s","b1"),0x2C:("brfalse.s","b1"),0x2D:("brtrue.s","b1"),0x2E:("beq.s","b1"),0x2F:("bge.s","b1"),0x30:("bgt.s","b1"),0x31:("ble.s","b1"),
 0x32:("blt.s","b1"),0x33:("bne.un.s","b1"),0x34:("bge.un.s","b1"),0x35:("bgt.un.s","b1"),0x36:("ble.un.s","b1"),0x37:("blt.un.s","b1"),0x38:("br","b4"),0x39:("brfalse","b4"),0x3A:("brtrue","b4"),
 0x3B:("beq","b4"),0x3C:("bge","b4"),0x3D:("bgt","b4"),0x3E:("ble","b4"),0x3F:("blt","b4"),0x40:("bne.un","b4"),0x41:("bge.un","b4"),0x42:("bgt.un","b4"),0x43:("ble.un","b4"),0x44:("blt.un","b4"),
 0x45:("switch","sw"),0x46:("ldind.i1",0),0x4A:("ldind.i4",0),0x58:("add",0),0x59:("sub",0),0x5A:("mul",0),0x5B:("div",0),0x5D:("rem",0),0x5F:("and",0),0x60:("or",0),0x65:("neg",0),
 0x69:("conv.i4",0),0x6B:("conv.r4",0),0x6C:("conv.r8",0),0x6F:("callvirt","tok"),0x72:("ldstr","str"),0x73:("newobj","tok"),0x74:("castclass","tok"),0x75:("isinst","tok"),
 0x79:("unbox","tok"),0x7B:("ldfld","tok"),0x7C:("ldflda","tok"),0x7D:("stfld","tok"),0x7E:("ldsfld","tok"),0x80:("stsfld","tok"),0x8C:("box","tok"),0x8D:("newarr","tok"),0x8E:("ldlen",0),
 0x8F:("ldelema","tok"),0x94:("ldelem.i4",0),0x9A:("ldelem.ref",0),0x9E:("stelem.i4",0),0xA2:("stelem.ref",0),0xA5:("unbox.any","tok"),0xD0:("ldtoken","tok"),0xD1:("conv.u2",0),0xD2:("conv.u1",0),
 0xDD:("leave","b4"),0xDE:("leave.s","b1"),0xDC:("endfinally",0),0xD3:("conv.i",0),0x5C:("div.un",0),0x6D:("conv.u4",0),0x6E:("conv.u8",0),0x6A:("conv.i8",0),0x76:("conv.r.un",0),
}
FE = {0x01:("ceq",0),0x02:("cgt",0),0x03:("cgt.un",0),0x04:("clt",0),0x05:("clt.un",0),0x09:("ldarg","i2"),0x0C:("ldloc","i2"),0x0E:("stloc","i2"),0x16:("constrained.","tok"),0x15:("initobj","tok")}

def tokname(M, tok):
    tab, idx = tok >> 24, tok & 0xFFFFFF
    try:
        if tab == 0x06: return "M:" + M["st"](M["row"](0x06, idx)[3])
        if tab == 0x04: return "F:" + M["st"](M["row"](0x04, idx)[1])
        if tab == 0x0A: return "MR:" + M["st"](M["row"](0x0A, idx)[1])
        if tab == 0x01: return "T:" + M["st"](M["row"](0x01, idx)[1])
        if tab == 0x02: return "T:" + M["st"](M["row"](0x02, idx)[1])
        if tab == 0x70:
            o = M["us"] + idx; n = M["b"][o]; o += 1
            if n & 0x80: n = ((n & 0x3F) << 8) | M["b"][o]; o += 1
            return repr(M["b"][o:o+n-1].decode("utf-16-le", "replace"))
    except Exception as e:
        return f"?{tok:#x}"
    return f"tok{tok:#x}"

def methods(M, needle):
    out=[]
    for i in range(1, M["rows"][6]+1):
        r = M["row"](6, i); name = M["st"](r[3])
        if needle.lower() in name.lower(): out.append((i, name, r[0]))
    return out

def disasm(M, rva):
    b = M["b"]; o = M["off"](rva); h = b[o]
    if h & 3 == 2: size = h >> 2; o += 1
    else:
        size = D._u32(b, o+4); o += (D._u16(b, o) >> 12) * 4
    code = b[o:o+size]; i = 0; lines=[]
    while i < len(code):
        pc = i; op = code[i]; i += 1
        if op == 0xFE: op2 = code[i]; i += 1; name, k = FE.get(op2, (f"fe{op2:02x}", 0))
        else: name, k = OPS.get(op, (f"op{op:02x}", 0))
        arg = ""
        if k == "u1": arg = str(code[i]); i += 1
        elif k == "i1": arg = str(struct.unpack_from("<b", code, i)[0]); i += 1
        elif k == "i2": arg = str(struct.unpack_from("<H", code, i)[0]); i += 2
        elif k == "i4": arg = str(struct.unpack_from("<i", code, i)[0]); i += 4
        elif k == "r4": arg = str(struct.unpack_from("<f", code, i)[0]); i += 4
        elif k == "r8": arg = str(struct.unpack_from("<d", code, i)[0]); i += 8
        elif k == "b1": arg = f"-> {i+1+struct.unpack_from('<b', code, i)[0]}"; i += 1
        elif k == "b4": arg = f"-> {i+4+struct.unpack_from('<i', code, i)[0]}"; i += 4
        elif k in ("tok", "str"): arg = tokname(M, D._u32(code, i)); i += 4
        elif k == "sw":
            n = D._u32(code, i); i += 4; tg = [i + 4*n + struct.unpack_from("<i", code, i+4*j)[0] for j in range(n)]; i += 4*n; arg = str(tg)
        elif isinstance(k, int): i += k
        lines.append(f"{pc:5}: {name} {arg}")
    return lines

if __name__ == "__main__":
    M = load(sys.argv[1])
    for idx, name, rva in methods(M, sys.argv[2]):
        print(f"==== {name} (#{idx}) rva={rva:#x}")
        if rva and (len(sys.argv) < 4 or sys.argv[3] == "dis"):
            print("\n".join(disasm(M, rva)))
