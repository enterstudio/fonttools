from __future__ import print_function, division, absolute_import
from fontTools.misc.py23 import *
from fontTools.misc.fixedTools import fixedToFloat, floatToFixed
from fontTools.misc.textTools import safeEval
import array
import io
import logging
import struct
import sys


# https://www.microsoft.com/typography/otspec/otvarcommonformats.htm

EMBEDDED_PEAK_TUPLE = 0x8000
INTERMEDIATE_REGION = 0x4000
PRIVATE_POINT_NUMBERS = 0x2000

DELTAS_ARE_ZERO = 0x80
DELTAS_ARE_WORDS = 0x40
DELTA_RUN_COUNT_MASK = 0x3f

POINTS_ARE_WORDS = 0x80
POINT_RUN_COUNT_MASK = 0x7f

log = logging.getLogger(__name__)


class TupleVariation(object):
	def __init__(self, axes, coordinates):
		self.axes = axes
		self.coordinates = coordinates

	def __repr__(self):
		axes = ",".join(sorted(["%s=%s" % (name, value) for (name, value) in self.axes.items()]))
		return "<TupleVariation %s %s>" % (axes, self.coordinates)

	def __eq__(self, other):
		return self.coordinates == other.coordinates and self.axes == other.axes

	def getUsedPoints(self):
		result = set()
		for i, point in enumerate(self.coordinates):
			if point is not None:
				result.add(i)
		return result

	def hasImpact(self):
		"""Returns True if this TupleVariation has any visible impact.

		If the result is False, the TupleVariation can be omitted from the font
		without making any visible difference.
		"""
		for c in self.coordinates:
			if c is not None:
				return True
		return False

	def toXML(self, writer, axisTags):
		writer.begintag("tuple")
		writer.newline()
		for axis in axisTags:
			value = self.axes.get(axis)
			if value is not None:
				minValue, value, maxValue = value
				defaultMinValue = min(value, 0.0)  # -0.3 --> -0.3; 0.7 --> 0.0
				defaultMaxValue = max(value, 0.0)  # -0.3 -->  0.0; 0.7 --> 0.7
				if minValue == defaultMinValue and maxValue == defaultMaxValue:
					writer.simpletag("coord", axis=axis, value=value)
				else:
					writer.simpletag("coord", axis=axis, value=value, min=minValue, max=maxValue)
				writer.newline()
		wrote_any_deltas = False
		for i, delta in enumerate(self.coordinates):
			if type(delta) == tuple and len(delta) == 2:
				writer.simpletag("delta", pt=i, x=delta[0], y=delta[1])
				writer.newline()
				wrote_any_deltas = True
			elif type(delta) == int:
				writer.simpletag("delta", cvt=i, value=delta)
				writer.newline()
				wrote_any_deltas = True
			elif delta is not None:
				log.error("bad delta format")
				writer.comment("bad delta #%d" % i)
				writer.newline()
				wrote_any_deltas = True
		if not wrote_any_deltas:
			writer.comment("no deltas")
			writer.newline()
		writer.endtag("tuple")
		writer.newline()

	def fromXML(self, name, attrs, _content):
		if name == "coord":
			axis = attrs["axis"]
			value = float(attrs["value"])
			defaultMinValue = min(value, 0.0)  # -0.3 --> -0.3; 0.7 --> 0.0
			defaultMaxValue = max(value, 0.0)  # -0.3 -->  0.0; 0.7 --> 0.7
			minValue = float(attrs.get("min", defaultMinValue))
			maxValue = float(attrs.get("max", defaultMaxValue))
			self.axes[axis] = (minValue, value, maxValue)
		elif name == "delta":
			if "pt" in attrs:
				point = safeEval(attrs["pt"])
				x = safeEval(attrs["x"])
				y = safeEval(attrs["y"])
				self.coordinates[point] = (x, y)
			elif "cvt" in attrs:
				cvt = safeEval(attrs["cvt"])
				value = safeEval(attrs["value"])
				self.coordinates[cvt] = value
			else:
				log.warning("bad delta format: %s" %
				            ", ".join(sorted(attrs.keys())))

	def compile(self, axisTags, sharedCoordIndices, sharedPoints):
		tupleData = []

		assert all(tag in axisTags for tag in self.axes.keys()), ("Unknown axis tag found.", self.axes.keys(), axisTags)

		coord = self.compileCoord(axisTags)
		if coord in sharedCoordIndices:
			flags = sharedCoordIndices[coord]
		else:
			flags = EMBEDDED_PEAK_TUPLE
			tupleData.append(coord)

		intermediateCoord = self.compileIntermediateCoord(axisTags)
		if intermediateCoord is not None:
			flags |= INTERMEDIATE_REGION
			tupleData.append(intermediateCoord)

		if sharedPoints is not None:
			auxData = self.compileDeltas(sharedPoints)
		else:
			flags |= PRIVATE_POINT_NUMBERS
			points = self.getUsedPoints()
			numPointsInGlyph = len(self.coordinates)
			auxData = self.compilePoints(points, numPointsInGlyph) + self.compileDeltas(points)

		tupleData = struct.pack('>HH', len(auxData), flags) + bytesjoin(tupleData)
		return (tupleData, auxData)

	def compileCoord(self, axisTags):
		result = []
		for axis in axisTags:
			_minValue, value, _maxValue = self.axes.get(axis, (0.0, 0.0, 0.0))
			result.append(struct.pack(">h", floatToFixed(value, 14)))
		return bytesjoin(result)

	def compileIntermediateCoord(self, axisTags):
		needed = False
		for axis in axisTags:
			minValue, value, maxValue = self.axes.get(axis, (0.0, 0.0, 0.0))
			defaultMinValue = min(value, 0.0)  # -0.3 --> -0.3; 0.7 --> 0.0
			defaultMaxValue = max(value, 0.0)  # -0.3 -->  0.0; 0.7 --> 0.7
			if (minValue != defaultMinValue) or (maxValue != defaultMaxValue):
				needed = True
				break
		if not needed:
			return None
		minCoords = []
		maxCoords = []
		for axis in axisTags:
			minValue, value, maxValue = self.axes.get(axis, (0.0, 0.0, 0.0))
			minCoords.append(struct.pack(">h", floatToFixed(minValue, 14)))
			maxCoords.append(struct.pack(">h", floatToFixed(maxValue, 14)))
		return bytesjoin(minCoords + maxCoords)

	@staticmethod
	def decompileCoord_(axisTags, data, offset):
		coord = {}
		pos = offset
		for axis in axisTags:
			coord[axis] = fixedToFloat(struct.unpack(">h", data[pos:pos+2])[0], 14)
			pos += 2
		return coord, pos

	@staticmethod
	def decompileCoords_(axisTags, numCoords, data, offset):
		result = []
		pos = offset
		for _ in range(numCoords):
			coord, pos = TupleVariation.decompileCoord_(axisTags, data, pos)
			result.append(coord)
		return result, pos

	@staticmethod
	def compilePoints(points, numPointsInGlyph):
		# If the set consists of all points in the glyph, it gets encoded with
		# a special encoding: a single zero byte.
		if len(points) == numPointsInGlyph:
			return b"\0"

		# In the 'gvar' table, the packing of point numbers is a little surprising.
		# It consists of multiple runs, each being a delta-encoded list of integers.
		# For example, the point set {17, 18, 19, 20, 21, 22, 23} gets encoded as
		# [6, 17, 1, 1, 1, 1, 1, 1]. The first value (6) is the run length minus 1.
		# There are two types of runs, with values being either 8 or 16 bit unsigned
		# integers.
		points = list(points)
		points.sort()
		numPoints = len(points)

		# The binary representation starts with the total number of points in the set,
		# encoded into one or two bytes depending on the value.
		if numPoints < 0x80:
			result = [bytechr(numPoints)]
		else:
			result = [bytechr((numPoints >> 8) | 0x80) + bytechr(numPoints & 0xff)]

		MAX_RUN_LENGTH = 127
		pos = 0
		lastValue = 0
		while pos < numPoints:
			run = io.BytesIO()
			runLength = 0
			useByteEncoding = None
			while pos < numPoints and runLength <= MAX_RUN_LENGTH:
				curValue = points[pos]
				delta = curValue - lastValue
				if useByteEncoding is None:
					useByteEncoding = 0 <= delta <= 0xff
				if useByteEncoding and (delta > 0xff or delta < 0):
					# we need to start a new run (which will not use byte encoding)
					break
				# TODO This never switches back to a byte-encoding from a short-encoding.
				# That's suboptimal.
				if useByteEncoding:
					run.write(bytechr(delta))
				else:
					run.write(bytechr(delta >> 8))
					run.write(bytechr(delta & 0xff))
				lastValue = curValue
				pos += 1
				runLength += 1
			if useByteEncoding:
				runHeader = bytechr(runLength - 1)
			else:
				runHeader = bytechr((runLength - 1) | POINTS_ARE_WORDS)
			result.append(runHeader)
			result.append(run.getvalue())

		return bytesjoin(result)

	@staticmethod
	def decompilePoints_(numPoints, data, offset, tableTag):
		"""(numPoints, data, offset, tableTag) --> ([point1, point2, ...], newOffset)"""
		assert tableTag in ('cvar', 'gvar')
		pos = offset
		numPointsInData = byteord(data[pos])
		pos += 1
		if (numPointsInData & POINTS_ARE_WORDS) != 0:
			numPointsInData = (numPointsInData & POINT_RUN_COUNT_MASK) << 8 | byteord(data[pos])
			pos += 1
		if numPointsInData == 0:
			return (range(numPoints), pos)

		result = []
		while len(result) < numPointsInData:
			runHeader = byteord(data[pos])
			pos += 1
			numPointsInRun = (runHeader & POINT_RUN_COUNT_MASK) + 1
			point = 0
			if (runHeader & POINTS_ARE_WORDS) != 0:
				points = array.array("H")
				pointsSize = numPointsInRun * 2
			else:
				points = array.array("B")
				pointsSize = numPointsInRun
			points.fromstring(data[pos:pos+pointsSize])
			if sys.byteorder != "big":
				points.byteswap()

			assert len(points) == numPointsInRun
			pos += pointsSize

			result.extend(points)

		# Convert relative to absolute
		absolute = []
		current = 0
		for delta in result:
			current += delta
			absolute.append(current)
		result = absolute
		del absolute

		badPoints = {str(p) for p in result if p < 0 or p >= numPoints}
		if badPoints:
			log.warning("point %s out of range in '%s' table" %
			            (",".join(sorted(badPoints)), tableTag))
		return (result, pos)

	def compileDeltas(self, points):
		deltaX = []
		deltaY = []
		for p in sorted(list(points)):
			c = self.coordinates[p]
			if c is not None:
				deltaX.append(c[0])
				deltaY.append(c[1])
		return self.compileDeltaValues_(deltaX) + self.compileDeltaValues_(deltaY)

	@staticmethod
	def compileDeltaValues_(deltas):
		"""[value1, value2, value3, ...] --> bytestring

		Emits a sequence of runs. Each run starts with a
		byte-sized header whose 6 least significant bits
		(header & 0x3F) indicate how many values are encoded
		in this run. The stored length is the actual length
		minus one; run lengths are thus in the range [1..64].
		If the header byte has its most significant bit (0x80)
		set, all values in this run are zero, and no data
		follows. Otherwise, the header byte is followed by
		((header & 0x3F) + 1) signed values.  If (header &
		0x40) is clear, the delta values are stored as signed
		bytes; if (header & 0x40) is set, the delta values are
		signed 16-bit integers.
		"""  # Explaining the format because the 'gvar' spec is hard to understand.
		stream = io.BytesIO()
		pos = 0
		while pos < len(deltas):
			value = deltas[pos]
			if value == 0:
				pos = TupleVariation.encodeDeltaRunAsZeroes_(deltas, pos, stream)
			elif value >= -128 and value <= 127:
				pos = TupleVariation.encodeDeltaRunAsBytes_(deltas, pos, stream)
			else:
				pos = TupleVariation.encodeDeltaRunAsWords_(deltas, pos, stream)
		return stream.getvalue()

	@staticmethod
	def encodeDeltaRunAsZeroes_(deltas, offset, stream):
		runLength = 0
		pos = offset
		numDeltas = len(deltas)
		while pos < numDeltas and runLength < 64 and deltas[pos] == 0:
			pos += 1
			runLength += 1
		assert runLength >= 1 and runLength <= 64
		stream.write(bytechr(DELTAS_ARE_ZERO | (runLength - 1)))
		return pos

	@staticmethod
	def encodeDeltaRunAsBytes_(deltas, offset, stream):
		runLength = 0
		pos = offset
		numDeltas = len(deltas)
		while pos < numDeltas and runLength < 64:
			value = deltas[pos]
			if value < -128 or value > 127:
				break
			# Within a byte-encoded run of deltas, a single zero
			# is best stored literally as 0x00 value. However,
			# if are two or more zeroes in a sequence, it is
			# better to start a new run. For example, the sequence
			# of deltas [15, 15, 0, 15, 15] becomes 6 bytes
			# (04 0F 0F 00 0F 0F) when storing the zero value
			# literally, but 7 bytes (01 0F 0F 80 01 0F 0F)
			# when starting a new run.
			if value == 0 and pos+1 < numDeltas and deltas[pos+1] == 0:
				break
			pos += 1
			runLength += 1
		assert runLength >= 1 and runLength <= 64
		stream.write(bytechr(runLength - 1))
		for i in range(offset, pos):
			stream.write(struct.pack('b', deltas[i]))
		return pos

	@staticmethod
	def encodeDeltaRunAsWords_(deltas, offset, stream):
		runLength = 0
		pos = offset
		numDeltas = len(deltas)
		while pos < numDeltas and runLength < 64:
			value = deltas[pos]
			# Within a word-encoded run of deltas, it is easiest
			# to start a new run (with a different encoding)
			# whenever we encounter a zero value. For example,
			# the sequence [0x6666, 0, 0x7777] needs 7 bytes when
			# storing the zero literally (42 66 66 00 00 77 77),
			# and equally 7 bytes when starting a new run
			# (40 66 66 80 40 77 77).
			if value == 0:
				break

			# Within a word-encoded run of deltas, a single value
			# in the range (-128..127) should be encoded literally
			# because it is more compact. For example, the sequence
			# [0x6666, 2, 0x7777] becomes 7 bytes when storing
			# the value literally (42 66 66 00 02 77 77), but 8 bytes
			# when starting a new run (40 66 66 00 02 40 77 77).
			isByteEncodable = lambda value: value >= -128 and value <= 127
			if isByteEncodable(value) and pos+1 < numDeltas and isByteEncodable(deltas[pos+1]):
				break
			pos += 1
			runLength += 1
		assert runLength >= 1 and runLength <= 64
		stream.write(bytechr(DELTAS_ARE_WORDS | (runLength - 1)))
		for i in range(offset, pos):
			stream.write(struct.pack('>h', deltas[i]))
		return pos

	@staticmethod
	def decompileDeltas_(numDeltas, data, offset):
		"""(numDeltas, data, offset) --> ([delta, delta, ...], newOffset)"""
		result = []
		pos = offset
		while len(result) < numDeltas:
			runHeader = byteord(data[pos])
			pos += 1
			numDeltasInRun = (runHeader & DELTA_RUN_COUNT_MASK) + 1
			if (runHeader & DELTAS_ARE_ZERO) != 0:
				result.extend([0] * numDeltasInRun)
			else:
				if (runHeader & DELTAS_ARE_WORDS) != 0:
					deltas = array.array("h")
					deltasSize = numDeltasInRun * 2
				else:
					deltas = array.array("b")
					deltasSize = numDeltasInRun
				deltas.fromstring(data[pos:pos+deltasSize])
				if sys.byteorder != "big":
					deltas.byteswap()
				assert len(deltas) == numDeltasInRun
				pos += deltasSize
				result.extend(deltas)
		assert len(result) == numDeltas
		return (result, pos)

	@staticmethod
	def getTupleSize_(flags, axisCount):
		size = 4
		if (flags & EMBEDDED_PEAK_TUPLE) != 0:
			size += axisCount * 2
		if (flags & INTERMEDIATE_REGION) != 0:
			size += axisCount * 4
		return size