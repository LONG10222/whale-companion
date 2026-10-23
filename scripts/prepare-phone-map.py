"""Generate operator-local phoneme metadata from a supplied pronunciation table.

No upstream voicebank component is redistributed in the application release.
"""
import argparse
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile

SOURCE = 'https://github.com/yqzhishen/qixuan-diffsinger/blob/main/multilingual_usage_guidance/Pronunciation_Tables.xlsx'
NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


def make(source):
    source = Path(source)
    if source.stat().st_size > 8 * 1024 ** 2:
        raise ValueError('The supplied workbook is too large.')
    with zipfile.ZipFile(source) as book:
        def read(name):
            entry = book.getinfo(name)
            if entry.file_size > 8 * 1024 ** 2:
                raise ValueError('Workbook XML exceeds the local conversion budget.')
            return ET.fromstring(book.read(name))
        strings = []
        if 'xl/sharedStrings.xml' in book.namelist():
            strings = [''.join(node.itertext()) for node in read('xl/sharedStrings.xml').findall('s:si', NS)]
        sheet = read('xl/worksheets/sheet1.xml')
        rows = []
        for row in sheet.findall('.//s:row', NS):
            if len(rows) >= 1000:
                raise ValueError('Unexpected pronunciation table size.')
            values = {}
            for cell in row.findall('s:c', NS):
                column = re.match(r'[A-Z]+', cell.get('r', '')).group()
                value = cell.find('s:v', NS)
                text = value.text if value is not None else ''.join(cell.itertext())
                values[column] = strings[int(text)] if cell.get('t') == 's' else text or ''
            rows.append(values)
        if len(rows) < 3 or '汉语拼音表' not in ''.join(rows[0].values()):
            raise ValueError('Select the upstream Mandarin pronunciation workbook.')
        header = rows[1]
        mapping = {}
        for row in rows[2:]:
            final = row.get('A', '').strip()
            if not re.fullmatch(r'[a-z0-9_]+', final):
                continue
            for column, text in row.items():
                if column == 'A':
                    continue
                initial = header.get(column, '').strip()
                if initial != '(none)' and not re.fullmatch(r'[a-z0-9_]+', initial):
                    continue
                for syllable in re.findall(r'[a-züv]+', text.lower()):
                    mapping[syllable.replace('ü', 'v')] = ([] if initial == '(none)' else [initial]) + [final]
    if len(mapping) < 100:
        raise ValueError('The supplied table did not produce a complete mapping.')
    return {'voice': 'Qixuan · operator-local metadata', 'source': SOURCE, 'mapping': mapping}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parent.parent / 'configs/qixuan-phones.local.json')
    args = parser.parse_args()
    if '.local.' not in args.output.name:
        parser.error('Use a private *.local.json output; do not put upstream metadata in a release.')
    if args.output.exists():
        parser.error('The output already exists; existing operator metadata was kept.')
    result = make(args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Operator-local mapping created:', len(result['mapping']), 'entries. No model or recording was copied.')
