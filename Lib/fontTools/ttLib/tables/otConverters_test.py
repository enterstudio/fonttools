from __future__ import print_function, division, absolute_import
from fontTools.misc.py23 import *
from fontTools.misc.testTools import FakeFont
from fontTools.misc.textTools import deHexStr
import fontTools.ttLib.tables.otConverters as otConverters
from fontTools.ttLib.tables.otBase import OTTableReader, OTTableWriter
import unittest


class GlyphIDTest(unittest.TestCase):
    font = FakeFont(".notdef A B C".split())
    converter = otConverters.GlyphID('GlyphID', 0, None, None)

    def test_readArray(self):
        reader = OTTableReader(deHexStr("0002 0001 DEAD 0002"))
        self.assertEqual(self.converter.readArray(reader, self.font, {}, 4),
                         ["B", "A", "glyph57005", "B"])
        self.assertEqual(reader.pos, 8)

    def test_read(self):
        reader = OTTableReader(deHexStr("0003"))
        self.assertEqual(self.converter.read(reader, self.font, {}), "C")
        self.assertEqual(reader.pos, 2)

    def test_write(self):
        writer = OTTableWriter(globalState={})
        self.converter.write(writer, self.font, {}, "B")
        self.assertEqual(writer.getData(), deHexStr("0002"))


class AATLookupTest(unittest.TestCase):
    font = FakeFont(".notdef A B C D E F G H".split())
    converter = otConverters.AATLookup("AATLookup", 0, None, None)

    def __init__(self, methodName):
        unittest.TestCase.__init__(self, methodName)
        # Python 3 renamed assertRaisesRegexp to assertRaisesRegex,
        # and fires deprecation warnings if a program uses the old name.
        if not hasattr(self, "assertRaisesRegex"):
            self.assertRaisesRegex = self.assertRaisesRegexp

    def test_readFormat0(self):
        reader = OTTableReader(deHexStr("0000 0000 0001 0002 0000 7D00 0001"))
        self.assertEqual(self.converter.read(reader, self.font, None), {
            "C": ".notdef",
            "D": "glyph32000",
            "E": "A"
        })

    def test_readFormat2(self):
        reader = OTTableReader(deHexStr(
            "0002 0006 0003 000C 0001 0006 "
            "0002 0001 0003 "   # glyph A..B: map to C
            "0007 0005 0008 "   # glyph E..G: map to H
            "FFFF FFFF FFFF"))  # end of search table
        self.assertEqual(self.converter.read(reader, self.font, None), {
            "A": "C",
            "B": "C",
            "E": "H",
            "F": "H",
            "G": "H",
        })

    def test_readFormat4(self):
        reader = OTTableReader(deHexStr(
            "0004 0006 0003 000C 0001 0006 "
            "0002 0001 001E "  # glyph 1..2: mapping at offset 0x1E
            "0005 0004 001E "  # glyph 4..5: mapping at offset 0x1E
            "FFFF FFFF FFFF "  # end of search table
            "0007 0008"))      # offset 0x18: glyphs [7, 8] = [G, H]
        self.assertEqual(self.converter.read(reader, self.font, None), {
            "A": "G",
            "B": "H",
            "D": "G",
            "E": "H",
        })

    def test_readFormat6(self):
        reader = OTTableReader(deHexStr(
            "0006 0004 0003 0008 0001 0004 "
            "0003 0001 "   # C --> A
            "0005 0002 "   # E --> B
            "FFFF FFFF"))  # end of search table
        self.assertEqual(self.converter.read(reader, self.font, None), {
            "C": "A",
            "E": "B",
        })

    def test_readFormat8(self):
        reader = OTTableReader(deHexStr(
            "0008 "
            "0003 0003 "        # first: C, count: 3
            "0007 0001 0002"))  # [G, A, B]
        self.assertEqual(self.converter.read(reader, self.font, None), {
            "C": "G",
            "D": "A",
            "E": "B",
        })

    def test_readUnknownFormat(self):
        reader = OTTableReader(deHexStr("0009"))
        self.assertRaisesRegex(
            AssertionError,
            "unsupported lookup format: 9",
            self.converter.read, reader, self.font, None)


if __name__ == "__main__":
    unittest.main()
