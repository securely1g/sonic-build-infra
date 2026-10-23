"""Read ELF dynamic dependencies and symbol versions without executing target code."""
import struct


class Elf:
    def __init__(self, data):
        if data[:4] != b'\x7fELF':
            raise ValueError('expected ELF')
        self.data = data
        self.bits = {1: 32, 2: 64}[data[4]]
        self.order = {1: '<', 2: '>'}[data[5]]
        header = self.unpack('HHIQQQIHHHHHH' if self.bits == 64 else 'HHIIIIIHHHHHH', 16)
        self.machine = header[1]
        layout = 'IIQQQQIIQQ' if self.bits == 64 else 'IIIIIIIIII'
        self.sections = [self.unpack(layout, header[5] + n * header[10]) for n in range(header[11])]
        if not self.sections:
            raise ValueError('ELF section table is required for dependency analysis')
        names = self.section_data(self.sections[header[12]])
        self.by_name = {self.string(names, section[0]): section for section in self.sections}
        self.build_id = None
        for section in self.sections:
            if section[1] != 7:
                continue
            notes = self.section_data(section)
            offset = 0
            while offset + 12 <= len(notes):
                namesz, descsz, kind = struct.unpack_from(self.order + 'III', notes, offset)
                name = notes[offset + 12:offset + 12 + namesz]
                descriptor = offset + 12 + ((namesz + 3) & ~3)
                if name.rstrip(b'\0') == b'GNU' and kind == 3:
                    self.build_id = notes[descriptor:descriptor + descsz].hex()
                offset = descriptor + ((descsz + 3) & ~3)
        self.debuglink = None
        self.debuglink_crc = None
        if '.gnu_debuglink' in self.by_name:
            data = self.section_data(self.by_name['.gnu_debuglink'])
            length = data.index(b'\0')
            self.debuglink = data[:length].decode()
            self.debuglink_crc = struct.unpack_from(self.order + 'I', data, (length + 4) & ~3)[0]
        self.needed = []
        self.soname = None
        dynamic = self.by_name.get('.dynamic')
        if dynamic and dynamic[1] != 8:
            strings = self.section_data(self.sections[dynamic[6]])
            for offset in range(dynamic[4], dynamic[4] + dynamic[5], dynamic[9]):
                tag, value = self.unpack('qQ' if self.bits == 64 else 'iI', offset)
                if tag == 1:
                    self.needed.append(self.string(strings, value))
                elif tag == 14:
                    self.soname = self.string(strings, value)
        versions = {}
        version_section = self.by_name.get('.gnu.version_r')
        if version_section and version_section[1] != 8:
            strings = self.section_data(self.sections[version_section[6]])
            offset = version_section[4]
            while True:
                _, count, file_name, auxiliary, next_record = self.unpack('HHIII', offset)
                cursor = offset + auxiliary
                for _ in range(count):
                    _, _, other, name, next_aux = self.unpack('IHHII', cursor)
                    versions[other & 0x7fff] = (self.string(strings, file_name), self.string(strings, name))
                    cursor += next_aux
                if not next_record:
                    break
                offset += next_record
        definitions = {}
        definition_section = self.by_name.get('.gnu.version_d')
        if definition_section and definition_section[1] != 8:
            strings = self.section_data(self.sections[definition_section[6]])
            offset = definition_section[4]
            while True:
                _, _, index, _, _, auxiliary, next_record = self.unpack('HHHHIII', offset)
                name, _ = self.unpack('II', offset + auxiliary)
                definitions[index & 0x7fff] = self.string(strings, name)
                if not next_record:
                    break
                offset += next_record
        self.required_versions = list(versions.values())
        self.undefined = []
        self.exports = set()
        self.export_versions = set()
        symbols = self.by_name.get('.dynsym')
        version_indexes = self.by_name.get('.gnu.version')
        if version_indexes and version_indexes[1] == 8:
            version_indexes = None
        if symbols and symbols[1] != 8:
            strings = self.section_data(self.sections[symbols[6]])
            for index, offset in enumerate(range(symbols[4], symbols[4] + symbols[5], symbols[9])):
                symbol = self.unpack('IBBHQQ' if self.bits == 64 else 'IIIBBH', offset)
                name = self.string(strings, symbol[0])
                binding = (symbol[1] if self.bits == 64 else symbol[3]) >> 4
                defined = symbol[3] if self.bits == 64 else symbol[5]
                if not name or binding not in (1, 2, 10):
                    continue
                if defined:
                    self.exports.add(name)
                    version = self.unpack('H', version_indexes[4] + index * 2)[0] & 0x7fff if version_indexes else 0
                    self.export_versions.add((name, definitions.get(version)))
                elif binding != 2:
                    version = self.unpack('H', version_indexes[4] + index * 2)[0] & 0x7fff if version_indexes else 0
                    self.undefined.append((name, versions.get(version)))

    def unpack(self, layout, offset):
        return struct.unpack_from(self.order + layout, self.data, offset)

    def section_data(self, section):
        return self.data[section[4]:section[4] + section[5]]

    @staticmethod
    def string(data, offset):
        return data[offset:data.index(b'\0', offset)].decode()
