"""Minimal, dependency-free ELF reader.

Reads only what CompatD needs: class/machine, interpreter, DT_NEEDED, RPATH/RUNPATH,
required symbol versions (verneed), provided versions (verdef) and .modinfo of kernel
modules. Files are never executed (unlike ``ldd``).
"""
from __future__ import annotations

import mmap
import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

ET_REL, ET_EXEC, ET_DYN, ET_CORE = 1, 2, 3, 4
PT_LOAD, PT_DYNAMIC, PT_INTERP = 1, 2, 3
DT_NULL, DT_NEEDED, DT_STRTAB, DT_STRSZ, DT_SONAME, DT_RPATH, DT_RUNPATH = 0, 1, 5, 10, 14, 15, 29
DT_VERDEF, DT_VERDEFNUM, DT_VERNEED, DT_VERNEEDNUM = 0x6FFFFFFC, 0x6FFFFFFD, 0x6FFFFFFE, 0x6FFFFFFF

MACHINES = {
    3: ("i386", "x86 (32-bit)"),
    8: ("mips", "MIPS"),
    20: ("powerpc", "PowerPC"),
    21: ("ppc64el", "PowerPC 64"),
    22: ("s390x", "IBM S/390"),
    40: ("armhf", "ARM (32-bit)"),
    62: ("amd64", "x86-64"),
    183: ("arm64", "AArch64"),
    243: ("riscv64", "RISC-V"),
    258: ("loong64", "LoongArch"),
}

# ldconfig -p tags per Debian architecture
LDCONFIG_TAGS = {
    "amd64": "libc6,x86-64",
    "i386": "libc6",
    "arm64": "libc6,AArch64",
    "armhf": "libc6,hard-float",
    "riscv64": "libc6,double-float",
    "ppc64el": "libc6,64bit",
    "s390x": "libc6,64bit",
}


class ElfError(Exception):
    pass


@dataclass
class ElfInfo:
    path: str
    bits: int
    little_endian: bool
    etype: int
    machine: int
    interp: Optional[str] = None
    needed: List[str] = field(default_factory=list)
    soname: Optional[str] = None
    rpath: List[str] = field(default_factory=list)
    runpath: List[str] = field(default_factory=list)
    # {library file: [version names]}
    verneed: Dict[str, List[str]] = field(default_factory=dict)
    verdef: List[str] = field(default_factory=list)
    modinfo: Dict[str, str] = field(default_factory=dict)
    is_appimage: bool = False
    is_static: bool = False
    truncated: bool = False

    @property
    def arch(self) -> str:
        return MACHINES.get(self.machine, ("unknown-%d" % self.machine, ""))[0]

    @property
    def arch_label(self) -> str:
        return MACHINES.get(self.machine, ("", "machine %d" % self.machine))[1]


def is_elf(header: bytes) -> bool:
    return header[:4] == b"\x7fELF"


def _cstr(buf, off: int, limit: int = 4096) -> str:
    if off < 0 or off >= len(buf):
        return ""
    end = buf.find(b"\x00", off, min(len(buf), off + limit))
    if end < 0:
        end = min(len(buf), off + limit)
    return bytes(buf[off:end]).decode("utf-8", "replace")


def parse(path: str) -> ElfInfo:
    with open(path, "rb") as fh:
        try:
            buf = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
        except (ValueError, OSError) as exc:
            raise ElfError(str(exc))
        try:
            return _parse(path, buf)
        except (struct.error, IndexError, ValueError, OverflowError) as exc:
            raise ElfError("malformed ELF: %s" % exc)
        finally:
            buf.close()


def _parse(path: str, buf) -> ElfInfo:
    if len(buf) < 52 or not is_elf(buf[:4]):
        raise ElfError("not an ELF file")
    ei_class, ei_data = buf[4], buf[5]
    if ei_class not in (1, 2) or ei_data not in (1, 2):
        raise ElfError("unknown ELF class/encoding")
    bits = 64 if ei_class == 2 else 32
    e = "<" if ei_data == 1 else ">"
    if bits == 64:
        etype, machine = struct.unpack_from(e + "HH", buf, 16)
        phoff, shoff = struct.unpack_from(e + "QQ", buf, 32)
        phentsize, phnum, shentsize, shnum, shstrndx = struct.unpack_from(e + "HHHHH", buf, 54)
    else:
        etype, machine = struct.unpack_from(e + "HH", buf, 16)
        phoff, shoff = struct.unpack_from(e + "II", buf, 28)
        phentsize, phnum, shentsize, shnum, shstrndx = struct.unpack_from(e + "HHHHH", buf, 42)

    info = ElfInfo(path=path, bits=bits, little_endian=(ei_data == 1), etype=etype, machine=machine)
    info.is_appimage = bytes(buf[8:10]) == b"AI" and buf[10] in (1, 2)

    try:
        # --- program headers -------------------------------------------------------
        loads: List[Tuple[int, int, int]] = []  # (vaddr, offset, filesz)
        dyn_off = dyn_size = None
        for i in range(min(phnum, 512)):
            base = phoff + i * phentsize
            if base + phentsize > len(buf):
                break
            if bits == 64:
                p_type, _flags, p_offset, p_vaddr, _paddr, p_filesz = struct.unpack_from(e + "IIQQQQ", buf, base)
            else:
                p_type, p_offset, p_vaddr, _paddr, p_filesz = struct.unpack_from(e + "IIIII", buf, base)
            if p_type == PT_LOAD:
                loads.append((p_vaddr, p_offset, p_filesz))
            elif p_type == PT_DYNAMIC:
                dyn_off, dyn_size = p_offset, p_filesz
            elif p_type == PT_INTERP:
                info.interp = _cstr(buf, p_offset, p_filesz)

        def v2o(addr: int) -> Optional[int]:
            for vaddr, off, size in loads:
                if vaddr <= addr < vaddr + size:
                    return addr - vaddr + off
            return None

        # --- dynamic section --------------------------------------------------------
        if dyn_off is not None:
            entsz = 16 if bits == 64 else 8
            fmt = e + ("qQ" if bits == 64 else "iI")
            tags: List[Tuple[int, int]] = []
            for i in range(min(dyn_size // entsz, 4096)):
                tag, val = struct.unpack_from(fmt, buf, dyn_off + i * entsz)
                if tag == DT_NULL:
                    break
                tags.append((tag, val))
            strtab_addr = next((v for t, v in tags if t == DT_STRTAB), None)
            strtab = v2o(strtab_addr) if strtab_addr is not None else None
            if strtab is None and strtab_addr is not None and strtab_addr < len(buf):
                strtab = strtab_addr  # some objects store file offsets
            if strtab is not None:
                for tag, val in tags:
                    if tag == DT_NEEDED:
                        info.needed.append(_cstr(buf, strtab + val))
                    elif tag == DT_SONAME:
                        info.soname = _cstr(buf, strtab + val)
                    elif tag == DT_RPATH:
                        info.rpath += [p for p in _cstr(buf, strtab + val).split(":") if p]
                    elif tag == DT_RUNPATH:
                        info.runpath += [p for p in _cstr(buf, strtab + val).split(":") if p]
                vn = dict(tags).get(DT_VERNEED)
                vn_num = dict(tags).get(DT_VERNEEDNUM, 0)
                if vn is not None and v2o(vn) is not None:
                    info.verneed = _verneed(buf, e, v2o(vn), vn_num, strtab)
                vd = dict(tags).get(DT_VERDEF)
                vd_num = dict(tags).get(DT_VERDEFNUM, 0)
                if vd is not None and v2o(vd) is not None:
                    info.verdef = _verdef(buf, e, v2o(vd), vd_num, strtab)
        elif etype == ET_EXEC:
            info.is_static = True
        elif etype == ET_DYN and info.interp is None and not info.needed:
            info.is_static = True  # static-pie

        # --- sections: only .modinfo (kernel modules) ---------------------------------
        if etype == ET_REL and shoff and shnum and shstrndx < shnum:
            info.modinfo = _modinfo(buf, e, bits, shoff, shentsize, shnum, shstrndx)
    except (struct.error, IndexError, ValueError, OverflowError):
        # Truncated/odd file: keep the header data (class, machine) we already have.
        info.truncated = True
    return info


def _verneed(buf, e: str, off: int, count: int, strtab: int) -> Dict[str, List[str]]:
    result: Dict[str, List[str]] = {}
    for _ in range(min(count, 256)):
        _ver, cnt, vn_file, vn_aux, vn_next = struct.unpack_from(e + "HHIII", buf, off)
        lib = _cstr(buf, strtab + vn_file)
        names: List[str] = []
        aux = off + vn_aux
        for _ in range(min(cnt, 512)):
            _hash, _flags, _other, vna_name, vna_next = struct.unpack_from(e + "IHHII", buf, aux)
            names.append(_cstr(buf, strtab + vna_name))
            if not vna_next:
                break
            aux += vna_next
        result.setdefault(lib, []).extend(names)
        if not vn_next:
            break
        off += vn_next
    return result


def _verdef(buf, e: str, off: int, count: int, strtab: int) -> List[str]:
    names: List[str] = []
    for _ in range(min(count, 4096)):
        _ver, _flags, _ndx, cnt, _hash, vd_aux, vd_next = struct.unpack_from(e + "HHHHIII", buf, off)
        if cnt:
            (vda_name,) = struct.unpack_from(e + "I", buf, off + vd_aux)
            names.append(_cstr(buf, strtab + vda_name))
        if not vd_next:
            break
        off += vd_next
    return names


def _modinfo(buf, e: str, bits: int, shoff: int, shentsize: int, shnum: int, shstrndx: int) -> Dict[str, str]:
    def sect(i: int):
        base = shoff + i * shentsize
        if bits == 64:
            name, typ, _fl, _addr, off, size = struct.unpack_from(e + "IIQQQQ", buf, base)
        else:
            name, typ, _fl, _addr, off, size = struct.unpack_from(e + "IIIIII", buf, base)
        return name, off, size

    _n, str_off, _s = sect(shstrndx)
    for i in range(min(shnum, 4096)):
        name, off, size = sect(i)
        if _cstr(buf, str_off + name) == ".modinfo":
            data = bytes(buf[off:off + size]).decode("utf-8", "replace")
            out: Dict[str, str] = {}
            for item in data.split("\x00"):
                if "=" in item:
                    k, _, v = item.partition("=")
                    out.setdefault(k, v)
            return out
    return {}
