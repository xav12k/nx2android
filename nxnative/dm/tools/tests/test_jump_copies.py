"""Exercise the rewriter's copied-base recognition on real AArch64 instructions, without game files."""
import ast
from pathlib import Path
import re
import struct
import sys
import unittest

from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
from nso_asm import ADRP, ADDI, BLR, NOP

# patch_nso.py is a command-line program whose top level loads and rewrites an image.
# Compile its actual recognition functions without executing that driver.
tree = ast.parse((TOOLS / 'patch_nso.py').read_text())
IMG = bytearray(0x5000)
for i, off in enumerate((0x10, 0x20, 0x30, 0x40)): struct.pack_into('<i', IMG, 0x41c8 + 4 * i, off)
scope = {'re': re, 'struct': struct, 'IMG': IMG}
exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ('num', 'copied_base', 'table_base', 'check_before_copy', 'jump_offsets')], type_ignores=[]), str(TOOLS / 'patch_nso.py'), 'exec'), scope)


def mov(dst, src, wide=True): return (0xaa0003e0 if wide else 0x2a0003e0) | src << 16 | dst


class CopiedBase(unittest.TestCase):
    def follow(self, words, reg='x7'):
        md = Cs(CS_ARCH_ARM64, CS_MODE_ARM); md.detail = True
        ins = list(md.disasm(struct.pack('<' + 'I' * len(words), *words), 0))
        self.assertEqual(len(ins), len(words))
        return scope['copied_base'](ins, len(ins), reg)

    def base(self, reg=7): return [ADRP(reg, 0, 0x4000), ADDI(reg, reg, 0x40c)]

    def test_far_copy_through_lr(self):
        self.assertEqual(self.follow(self.base() + [mov(30, 7)] + [NOP] * 688 + [mov(7, 30)]), 0x440c)

    def test_32_bit_writers_destroy_base(self):
        for clobber in (mov(7, 0, False), 0x52800027, ADDI(7, 7, 1) & 0x7fffffff):
            with self.subTest(clobber=hex(clobber)):
                self.assertIsNone(self.follow(self.base() + [clobber, mov(30, 7), mov(7, 30)]))
        self.assertIsNone(self.follow(self.base() + [mov(30, 7), mov(30, 0, False), mov(7, 30)]))

    def test_calls_clobber_volatile_and_link_registers(self):
        for call in (0x94000000, BLR(0)):
            self.assertIsNone(self.follow(self.base() + [call]))
            self.assertIsNone(self.follow(self.base() + [mov(30, 7), call, mov(7, 30)]))
            self.assertEqual(self.follow(self.base(19) + [call, mov(7, 19)]), 0x440c)

    def test_aliases_and_unrelated_writes(self):
        self.assertEqual(self.follow(self.base(29) + [mov(7, 29)]), 0x440c)
        self.assertEqual(self.follow(self.base(30), 'lr'), 0x440c)
        self.assertEqual(self.follow(self.base() + [mov(8, 0, False)]), 0x440c)
        self.assertIsNone(self.follow(self.base() + [mov(7, 0)]))


class CopyBeforeCheckWithBaseSetup(unittest.TestCase):
    """WWE 2K18 main+0x16581b0: the index is copied before the bounds check, and the table base is set up between the b.hi and the load"""
    def sites(self, words):
        md = Cs(CS_ARCH_ARM64, CS_MODE_ARM); md.detail = True
        ins = list(md.disasm(struct.pack('<' + 'I' * len(words), *words), 0))
        self.assertEqual(len(ins), len(words))
        return ins

    def test_base_setup_between_bhi_and_load(self):
        words = [0x2a1b03f2, 0x71000f7f, 0x540004a8, ADRP(1, 0, 0x4000), ADDI(1, 1, 0x1c8), 0xf9402ffb, 0xb8b27832, 0x8b010252, 0xd61f0240]
        ins = self.sites(words)
        self.assertEqual(scope['jump_offsets'](ins, 8), None)   # the plain forms do not match
        self.assertEqual(scope['jump_offsets'](ins, 8, copy_first=True, far_base=True), [0x41c8 + 0x10, 0x41c8 + 0x20, 0x41c8 + 0x30, 0x41c8 + 0x40])

    def test_index_rewritten_after_the_check_is_refused(self):
        words = [0x2a1b03f2, 0x71000f7f, 0x540004a8, ADRP(1, 0, 0x4000), ADDI(1, 1, 0x1c8), 0x528000b2, 0xb8b27832, 0x8b010252, 0xd61f0240]
        self.assertIsNone(scope['jump_offsets'](self.sites(words), 8, copy_first=True, far_base=True))


if __name__ == '__main__': unittest.main()
