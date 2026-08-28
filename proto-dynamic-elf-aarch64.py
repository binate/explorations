import struct
BASE=0x400000
# ---- aarch64 instruction encoders ----
def adrp(rd, frm, to):
    d = (to & ~0xfff) - (frm & ~0xfff); imm = d >> 12
    immlo = imm & 3; immhi = (imm >> 2) & 0x7ffff
    return 0x90000000 | (immlo<<29) | (immhi<<5) | rd
def ldr64(rt, rn, off): return 0xF9400000 | ((off//8)<<10) | (rn<<5) | rt
def add64(rd, rn, imm): return 0x91000000 | (imm<<10) | (rn<<5) | rd
def br(rn): return 0xD61F0000 | (rn<<5)
def movz_w(rd, imm): return 0x52800000 | (imm<<5) | rd
def bl(frm, to): off=((to-frm)>>2) & 0x3ffffff; return 0x94000000 | off
def w(l): return b''.join(struct.pack('<I', x) for x in l)

interp = b"/lib/ld-linux-aarch64.so.1\x00"
# dynstr: index 0 = \0 ; then names
dynstr = b"\x00" + b"exit\x00" + b"libc.so.6\x00"
exit_stroff = 1
libc_stroff = 1 + len("exit") + 1
# dynsym: [0]=null, [1]=exit (UND, STT_FUNC|STB_GLOBAL)
def sym(name,info,shndx,val,size): return struct.pack('<IBBHQQ', name,info,0,shndx,val,size)
dynsym = sym(0,0,0,0,0) + sym(exit_stroff, 0x12, 0, 0, 0)
NSYM=2
# SysV hash over dynsym: nbucket=1, nchain=NSYM
def elf_hash(name):
    h=0
    for c in name:
        h=(h<<4)+c; g=h&0xf0000000
        if g: h^=g>>24
        h&=~g
    return h & 0xffffffff
# bucket[0] -> first sym idx whose hash%1==0 => the highest indexed? chain links.
# nbucket=1: bucket[0]=1 (sym 1 exit), chain[1]=0 (end); chain[0]=0
hashtab = struct.pack('<II',1,NSYM) + struct.pack('<I',1) + struct.pack('<II',0,0)

# ---- layout (choose offsets; file offset == vaddr-BASE within a LOAD) ----
ehdr_sz=64; nph=4; ph_sz=nph*56
off=ehdr_sz+ph_sz
def place(sz, align=8):
    global off
    if off % align: off += align-(off%align)
    a=off; off+=sz; return a
o_interp=place(len(interp),1)
o_hash=place(len(hashtab),8)
o_dynsym=place(len(dynsym),8)
o_dynstr=place(len(dynstr),1)
o_relaplt=place(24,8)   # one Rela
o_plt=place(16,4)       # one 4-instr stub
o_text=place(12,4)      # _start: movz + bl  (12 bytes, pad to 8 later ok)
# page 2 (rw) for .dynamic + .got.plt
page2 = ((off + 0xfff)//0x1000)*0x1000
o_dyn = page2
n_dyn = 13
o_gotplt = o_dyn + n_dyn*16
gotplt_sz = 4*8   # 3 reserved + 1 import
file_end = o_gotplt + gotplt_sz

va=lambda o: BASE+o
exit_gotslot = va(o_gotplt + 3*8)   # 4th slot

# _start at va(o_text): mov w0,#42 ; bl exit@plt(va(o_plt))
start_va=va(o_text)
text = w([ movz_w(0,42), bl(start_va + 4, va(o_plt)) ])
# exit@plt stub
plt_va=va(o_plt)
plt = w([ adrp(16, plt_va, exit_gotslot),
          ldr64(17,16, exit_gotslot & 0xfff),
          add64(16,16, exit_gotslot & 0xfff),
          br(17) ])
# .rela.plt: R_AARCH64_JUMP_SLOT(1026) for exit(sym idx 1) -> GOT slot
r_info = (1<<32) | 1026
relaplt = struct.pack('<QQQ', exit_gotslot, r_info, 0)
# .got.plt: [0]=&.dynamic, [1]=0,[2]=0, [3]=exit slot (init 0; ld.so fills)
gotplt = struct.pack('<QQQQ', va(o_dyn), 0, 0, 0)
# .dynamic
DT={'NULL':0,'NEEDED':1,'PLTRELSZ':2,'PLTGOT':3,'HASH':4,'STRTAB':5,'SYMTAB':6,
    'STRSZ':10,'SYMENT':11,'PLTREL':20,'JMPREL':23,'BIND_NOW':24,'FLAGS':30}
dyn=b''
def de(tag,val): 
    global dyn; dyn+=struct.pack('<QQ', DT[tag], val)
de('NEEDED', libc_stroff)
de('HASH', va(o_hash)); de('STRTAB', va(o_dynstr)); de('SYMTAB', va(o_dynsym))
de('STRSZ', len(dynstr)); de('SYMENT', 24)
de('PLTGOT', va(o_gotplt)); de('PLTRELSZ', 24); de('PLTREL', 7); de('JMPREL', va(o_relaplt))
de('FLAGS', 0x8); de('BIND_NOW', 0)   # DF_BIND_NOW
de('NULL', 0)
assert len(dyn)==n_dyn*16, (len(dyn), n_dyn*16)

# ---- assemble file ----
total=file_end
buf=bytearray(total)
def put(o,b): buf[o:o+len(b)]=b
put(o_interp, interp); put(o_hash, hashtab); put(o_dynsym, dynsym); put(o_dynstr, dynstr)
put(o_relaplt, relaplt); put(o_plt, plt); put(o_text, text)
put(o_dyn, dyn); put(o_gotplt, gotplt)
# program headers
ro_filesz = page2  # end of page1 content region (LOAD1 covers [0, page2))
rw_filesz = file_end - page2
phdrs=b''
def ph(t,fl,off_,va_,filesz,memsz,align): 
    return struct.pack('<IIQQQQQQ', t,fl,off_,va_,va_,filesz,memsz,align)
phdrs+=ph(3,4,o_interp,va(o_interp),len(interp),len(interp),1)          # PT_INTERP
phdrs+=ph(1,5,0,BASE,ro_filesz,ro_filesz,0x1000)                        # LOAD r-x
phdrs+=ph(1,6,page2,va(page2),rw_filesz,rw_filesz,0x1000)               # LOAD rw
phdrs+=ph(2,6,o_dyn,va(o_dyn),n_dyn*16,n_dyn*16,8)                      # PT_DYNAMIC
put(64, phdrs)
# ELF header
e=bytearray(64)
e[0:4]=b'\x7fELF'; e[4]=2; e[5]=1; e[6]=1
struct.pack_into('<HHIQQQIHHHHHH', e, 16, 2,183,1, start_va, 64, 0, 0, 64,56,nph, 64,0,0)
put(0, bytes(e))
open('/tmp/dynelf/proto','wb').write(buf)
import os; os.chmod('/tmp/dynelf/proto',0o755)
print("wrote proto, entry", hex(start_va), "exit_gotslot", hex(exit_gotslot), "size", total)
