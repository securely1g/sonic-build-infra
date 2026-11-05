"""Verify installed DWARF compression, content and runtime/debug pairing."""
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tarfile
import tempfile
import zlib

readelf, *archives = sys.argv[1:]


def section(data, name):
    assert data[:6] == b'\x7fELF\x02\x01', 'expected native AMD64/AArch64 ELF64'
    offset = struct.unpack_from('<Q', data, 40)[0]
    size, count, names = struct.unpack_from('<HHH', data, 58)
    entries = [struct.unpack_from('<IIQQQQIIQQ', data, offset + i * size) for i in range(count)]
    strings = entries[names]
    table = data[strings[4]:strings[4] + strings[5]]
    for entry in entries:
        end = table.index(b'\0', entry[0])
        if table[entry[0]:end].decode() == name:
            return entry[2], data[entry[4]:entry[4] + entry[5]]
    raise AssertionError('missing ELF section: ' + name)


results = []
with tempfile.TemporaryDirectory(dir=os.environ['TEST_TMPDIR']) as temporary:
    root = Path(temporary)
    for index in range(2):
        with tarfile.open(archives[index * 2]) as runtime:
            data = runtime.extractfile('usr/bin/hello').read()
        binary = root / ('hello' + str(index))
        binary.write_bytes(data)
        binary.chmod(0o755)
        notes = subprocess.check_output([readelf, '-n', str(binary)], text=True)
        build_id = re.search(r'Build ID: ([0-9a-f]+)', notes).group(1)
        name = 'usr/lib/debug/.build-id/' + build_id[:2] + '/' + build_id[2:] + '.debug'
        with tarfile.open(archives[index * 2 + 1]) as symbols:
            matches = [m for m in symbols if m.name.removeprefix('./') == name]
            assert len(matches) == 1 and matches[0].isfile(), (name, matches)
            debug = symbols.extractfile(matches[0]).read()
        path = root / ('symbols' + str(index))
        path.write_bytes(debug)
        assert 'Build ID: ' + build_id in subprocess.check_output([readelf, '-n', str(path)], text=True)
        assert 'DW_TAG_compile_unit' in subprocess.check_output([readelf, '--debug-dump=info', str(path)], text=True)
        _, link = section(data, '.gnu_debuglink')
        end = link.index(b'\0')
        assert link[:end].decode() == Path(name).name
        assert struct.unpack_from('<I', link, (end + 4) & ~3)[0] == zlib.crc32(debug)
        assert subprocess.check_output([str(binary)], text=True) == 'hello from c++\n'
        flags, info = section(debug, '.debug_info')
        # ELF ABI SHF_COMPRESSED is bit 11. GNU objcopy uses ELF ZLIB type 1.
        assert bool(flags & (1 << 11)) == bool(index), (index, flags)
        if index:
            compression, reserved, uncompressed_size, alignment = struct.unpack_from('<IIQQ', info)
            assert compression == 1 and reserved == 0 and alignment > 0
            info = zlib.decompress(info[24:])
            assert len(info) == uncompressed_size
        results.append((build_id, section(data, '.text')[1], info))
assert results[0] == results[1], 'compression changed executable code, identity or DWARF'
