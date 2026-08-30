# proto-dynamic-macho-arm64.py — VALIDATED minimal dynamic Mach-O for macOS arm64.
#
# Emits a from-scratch, non-lazy, dynamically-linked arm64 Mach-O whose _start does
# `mov w0,#42 ; bl <stub>` where the stub jumps through a __got slot that dyld binds
# to libSystem's `_exit` at load.  Ad-hoc sign with `codesign --no-strict -f -s -
# proto` (the prototype has no LC_CODE_SIGNATURE, so codesign must ADD one, which its
# default strict mode refuses — bnld will EMIT LC_CODE_SIGNATURE itself + sign via the
# R35 signer, so this is a prototype-only shortcut).  Then `./proto` exits 42.
# dyld_info confirms one fixup: __DATA_CONST/__got bind -> libSystem/_exit.
#
# This is the Mach-O analogue of proto-dynamic-elf-aarch64.py: it validates that dyld
# accepts a hand-rolled classic (LC_DYLD_INFO_ONLY) dynamic Mach-O with a non-lazy
# GOT bind — the recipe bnld's Mach-O dynamic writer ports.  See plan-dynamic-linking.md
# ("M2 recipe").
import struct
PAGE=0x4000; BASE=0x100000000
def uleb(n):
    o=bytearray()
    while True:
        b=n&0x7f; n>>=7
        o.append(b|0x80 if n else b)
        if not n: break
    return bytes(o)
def al(n,a): return (n+a-1)&~(a-1)

SEG=0x19; DINFO=0x80000022; SYMTAB=0x2; DYSYM=0xb; LDYLINKER=0xe; LDYLIB=0xc; MAIN=0x80000028
dylinker=b"/usr/lib/dyld\0"; dylib=b"/usr/lib/libSystem.B.dylib\0"
sz_dylinker=al(12+len(dylinker),8); sz_dylib=al(24+len(dylib),8)
ncmds=12
sizeofcmds=72+(72+80)+(72+80)+72+48+24+80+24+24+sz_dylinker+sz_dylib+24
hdr_end=32+sizeofcmds

# __TEXT: single __text section (_start 8 + stub 12 = 20 bytes)
text_off=al(hdr_end+0x100,16); text_va=BASE+text_off
start_off=text_off; stub_off=start_off+8; stub_va=BASE+stub_off
text_size=20

# __DATA_CONST/__got
got_off=PAGE; got_va=BASE+got_off; got_size=8

# __LINKEDIT
le_off=2*PAGE; le_va=BASE+le_off
bind=bytes([0x10|1,0x40|0])+b"_exit\0"+bytes([0x50|1,0x70|2])+uleb(0)+bytes([0x90,0x00])
bind_off=le_off; bind_end=bind_off+len(bind)
sym_off=al(bind_end,8)
nlist=struct.pack("<IBBHQ",1,0x01,0,0x0100,0)   # n_strx=1,N_EXT undef,desc=ord1<<8
indirect_off=sym_off+16; indirect=struct.pack("<I",0)
str_off=indirect_off+4; strtab=b"\0_exit\0"; str_end=str_off+len(strtab)
le_filesize=str_end-le_off

def adrp(rd,frm,to):
    imm=((to&~0xfff)-(frm&~0xfff))>>12
    return 0x90000000|((imm&3)<<29)|(((imm>>2)&0x7ffff)<<5)|rd
mov=0x52800000|(42<<5)
i_bl=0x94000000|((((stub_va)-(start_off+4+BASE))>>2)&0x3ffffff)
i_adrp=adrp(16,stub_va,got_va)
i_ldr=0xf9400000|(((got_va&0xfff)//8)<<10)|(16<<5)|16
i_br=0xd61f0000|(16<<5)
code=struct.pack("<IIIII",mov,i_bl,i_adrp,i_ldr,i_br)

buf=bytearray(al(str_end,16))
struct.pack_into("<IIIIIIII",buf,0,0xfeedfacf,0x0100000c,0,2,ncmds,sizeofcmds,0x00200084,0)
lc=32
def seg(name,va,vs,fo,fs,mx,ini,ns,flags=0):
    global lc
    struct.pack_into("<II16sQQQQiiII",buf,lc,SEG,72+80*ns,name,va,vs,fo,fs,mx,ini,ns,flags); lc+=72
def sect(nm,sg,addr,size,off,a2,flags,r1,r2):
    global lc
    struct.pack_into("<16s16sQQIIIIIII",buf,lc,nm,sg,addr,size,off,a2,0,0,flags,r1,r2); lc+=80

seg(b"__PAGEZERO",0,BASE,0,0,0,0,0)
seg(b"__TEXT",text_va-text_off,PAGE,0,PAGE,5,5,1)
sect(b"__text",b"__TEXT",text_va,text_size,text_off,2,0x80000400,0,0)
seg(b"__DATA_CONST",got_va,PAGE,got_off,PAGE,3,3,1,0x10)
sect(b"__got",b"__DATA_CONST",got_va,got_size,got_off,3,0x06,0,0)
seg(b"__LINKEDIT",le_va,al(le_filesize,PAGE),le_off,le_filesize,1,1,0)
struct.pack_into("<12I",buf,lc,DINFO,48,0,0,bind_off,len(bind),0,0,0,0,0,0); lc+=48
struct.pack_into("<6I",buf,lc,SYMTAB,24,sym_off,1,str_off,len(strtab)); lc+=24
struct.pack_into("<20I",buf,lc,DYSYM,80, 0,0, 0,0, 0,1, 0,0, 0,0, 0,0, indirect_off,1, 0,0, 0,0); lc+=80
struct.pack_into("<IIIIII",buf,lc,0x32,24,1,0x1a0000,0x1a0000,0); lc+=24  # LC_BUILD_VERSION macOS 26.0
struct.pack_into("<II16s",buf,lc,0x1b,24,b"BINATEPROTO01234"); lc+=24  # LC_UUID
struct.pack_into("<III",buf,lc,LDYLINKER,sz_dylinker,12); buf[lc+12:lc+12+len(dylinker)]=dylinker; lc+=sz_dylinker
struct.pack_into("<IIIIII",buf,lc,LDYLIB,sz_dylib,24,0,0x10000,0x10000); buf[lc+24:lc+24+len(dylib)]=dylib; lc+=sz_dylib
struct.pack_into("<IIQQ",buf,lc,MAIN,24,start_off,0); lc+=24

buf[text_off:text_off+len(code)]=code
buf[bind_off:bind_off+len(bind)]=bind
buf[sym_off:sym_off+16]=nlist
buf[indirect_off:indirect_off+4]=indirect
buf[str_off:str_end]=strtab
open("proto","wb").write(buf)
print("wrote",len(buf),"entry_off",hex(start_off),"got_va",hex(got_va))
