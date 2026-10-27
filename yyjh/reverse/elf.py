# -*- coding: utf-8 -*-
"""ELF(.so) 读取与 AArch64 反汇编 —— 游戏更新后"重新找解密结构"用。

依赖（可选）:  pyelftools + capstone
    pip install pyelftools capstone

典型用法:
    1) 找所有调用 xxtea_decrypt 的地方   -> find_call_sites()
    2) 看某函数在干什么                  -> disasm()
    3) 某个字符串常量被谁引用            -> refs_to_string()
"""
import struct

from ..config.constants import KNOWN_SYMS

try:
    from elftools.elf.elffile import ELFFile
    HAS_ELF = True
except ImportError:                                   # pragma: no cover
    HAS_ELF = False
try:
    import capstone
    HAS_CAPSTONE = True
except ImportError:                                   # pragma: no cover
    HAS_CAPSTONE = False


def require_deps():
    missing = []
    if not HAS_ELF:
        missing.append('pyelftools')
    if not HAS_CAPSTONE:
        missing.append('capstone')
    if missing:
        raise RuntimeError('缺少依赖: ' + ', '.join(missing) +
                           '  ->  pip install ' + ' '.join(missing))


class Elf:
    def __init__(self, path):
        require_deps()
        self.path = path
        self.f = open(path, 'rb')
        self.elf = ELFFile(self.f)
        self._syms = None

    def close(self):
        try:
            self.f.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    # ── 结构 ──
    def sections(self):
        return [{'name': s.name, 'addr': s['sh_addr'], 'size': s['sh_size'],
                 'offset': s['sh_offset'], 'flags': s['sh_flags'],
                 'type': s['sh_type']}
                for s in self.elf.iter_sections() if s['sh_addr']]

    def symbols(self):
        """.dynsym + .symtab（去重）。返回 {name: (addr, size, type)}"""
        if self._syms is not None:
            return self._syms
        out = {}
        for secname in ('.dynsym', '.symtab'):
            sec = self.elf.get_section_by_name(secname)
            if sec is None:
                continue
            try:
                for s in sec.iter_symbols():
                    if s.name and s['st_value'] and s.name not in out:
                        out[s.name] = (s['st_value'], s['st_size'],
                                       s['st_info']['type'])
            except Exception:
                continue
        self._syms = out
        return out

    # ── 读 ──
    def read_at(self, vaddr, n):
        for s in self.elf.iter_sections():
            if not s['sh_addr'] or s['sh_type'] == 'SHT_NOBITS':
                continue
            if s['sh_addr'] <= vaddr < s['sh_addr'] + s['sh_size']:
                self.f.seek(s['sh_offset'] + (vaddr - s['sh_addr']))
                return self.f.read(n)
        return None

    def cstr(self, vaddr, n=64):
        b = self.read_at(vaddr, n)
        return b.split(b'\x00')[0] if b is not None else None

    def read_u32(self, vaddr):
        b = self.read_at(vaddr, 4)
        return struct.unpack('<I', b)[0] if b else None

    def symbol_at(self, addr, span=0x8000):
        """把地址归到最近的符号: 'sym+0x12'"""
        best = None
        for name, (a, size, _t) in self.symbols().items():
            if a <= addr and (size and addr < a + size):
                return name
            if a <= addr and addr - a < span:
                if best is None or a > best[1]:
                    best = (name, a)
        if best:
            return f'{best[0]}+0x{addr-best[1]:x}'
        return hex(addr)

    # ── 反汇编 ──
    def _text_sections(self):
        out = []
        for s in self.elf.iter_sections():
            if not s['sh_addr'] or s['sh_type'] == 'SHT_NOBITS':
                continue
            if s['sh_flags'] & 0x4:            # SHF_EXECINSTR
                out.append(s)
        return out

    def disasm(self, vaddr, count=40):
        """从 vaddr 反汇编 count 条指令，返回文本行列表。"""
        require_deps()
        md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
        data = self.read_at(vaddr, count * 4 + 16)
        if data is None:
            return []
        out = []
        for i in md.disasm(data, vaddr):
            out.append(f'0x{i.address:08x}  {i.mnemonic:8} {i.op_str}')
            if len(out) >= count:
                break
        return out

    def disasm_str(self, vaddr, count=40):
        return '\n'.join(self.disasm(vaddr, count))

    # ── 调用点扫描（AArch64 BL） ──
    def find_call_sites(self, targets=None):
        """找出所有 bl 到 targets 的指令位置。

        targets: {vaddr: 名字}，默认用 constants.KNOWN_SYMS
        返回 [(call_addr, target_vaddr, target_name)]
        """
        require_deps()
        targets = dict(targets or KNOWN_SYMS)
        out = []
        for s in self._text_sections():
            base, size = s['sh_addr'], s['sh_size']
            self.f.seek(s['sh_offset'])
            buf = self.f.read(size)
            for i in range(0, len(buf) - 3, 4):
                w = struct.unpack_from('<I', buf, i)[0]
                if (w >> 26) != 0x25:          # BL opcode
                    continue
                imm = w & 0x03FFFFFF
                if imm & 0x02000000:
                    imm -= 0x04000000
                a = base + i
                t = a + (imm << 2)
                if t in targets:
                    out.append((a, t, targets[t]))
        return sorted(out)

    # ── 字符串引用 ──
    def find_string_addr(self, s):
        """在只读数据段里找字符串的虚拟地址。"""
        needle = s.encode() if isinstance(s, str) else s
        hits = []
        for sec in self.elf.iter_sections():
            if not sec['sh_addr'] or sec['sh_type'] == 'SHT_NOBITS':
                continue
            if sec['sh_flags'] & 0x4:          # 跳过代码段
                continue
            self.f.seek(sec['sh_offset'])
            buf = self.f.read(sec['sh_size'])
            i = 0
            while True:
                i = buf.find(needle, i)
                if i < 0:
                    break
                hits.append(sec['sh_addr'] + i)
                i += 1
                if len(hits) > 8:
                    return hits
        return hits

    def refs_to_string(self, s, span=2):
        """找出引用了某个字符串常量的 adrp/add 指令对。

        返回 [(adrp_addr, add_addr, resolved_vaddr)] —— 只保留解析结果等于
        该字符串地址的。
        """
        require_deps()
        addrs = set(self.find_string_addr(s))
        if not addrs:
            return []
        out = []
        for sec in self._text_sections():
            base, size = sec['sh_addr'], sec['sh_size']
            self.f.seek(sec['sh_offset'])
            buf = self.f.read(size)
            page = None
            reg = None
            for i in range(0, len(buf) - 3, 4):
                w = struct.unpack_from('<I', buf, i)[0]
                a = base + i
                if (w & 0x9F000000) == 0x90000000:          # ADRP
                    rd = w & 0x1F
                    immhi = (w >> 5) & 0x7FFFF
                    immlo = (w >> 29) & 0x3
                    imm = ((immhi << 2) | immlo)
                    if imm & (1 << 20):
                        imm -= (1 << 21)
                    page = (a & ~0xFFF) + (imm << 12)
                    reg = rd
                elif (w & 0xFF800000) == 0x91000000 and reg is not None:   # ADD imm
                    rn = (w >> 5) & 0x1F
                    rd = w & 0x1F
                    imm12 = (w >> 10) & 0xFFF
                    if rn == reg:
                        val = page + imm12
                        if val in addrs:
                            out.append((a - 4, a, val))
                    if rd == reg:
                        reg = None
                elif (w & 0xFFC00000) == 0xF9400000:      # LDR x, [x, #imm]
                    rn = (w >> 5) & 0x1F
                    imm12 = (w >> 10) & 0xFFF
                    if rn == reg:
                        val = page + imm12 * 8
                        if val in addrs:
                            out.append((a - 4, a, val))
        return out
