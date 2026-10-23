"""Official validation with the Mustang validator (KoSIT schematron for EN 16931 / XRechnung, veraPDF for PDF/A).

Needs Java 11+ and the Mustang-CLI jar. `python -m e_rechnung validator-laden` downloads it once
from Maven Central and checks the SHA-1; E_RECHNUNG_VALIDATOR_JAR can point to an existing jar."""
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

MUSTANG_VERSION = '2.26.0'
MUSTANG_URL = ('https://repo.maven.apache.org/maven2/org/mustangproject/Mustang-CLI/'
               f'{MUSTANG_VERSION}/Mustang-CLI-{MUSTANG_VERSION}.jar')
CACHE = Path(os.environ.get('E_RECHNUNG_CACHE', Path.home() / '.cache' / 'e_rechnung'))


def jar_path():
    configured = os.environ.get('E_RECHNUNG_VALIDATOR_JAR')
    if configured:
        return Path(configured)
    return CACHE / f'Mustang-CLI-{MUSTANG_VERSION}.jar'


def available():
    return shutil.which('java') is not None and jar_path().is_file()


def download(progress=print):
    """Fetch the Mustang-CLI jar (about 60 MB) and verify it against the published SHA-1."""
    target = jar_path()
    if target.is_file():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    progress(f'Lade {MUSTANG_URL} …')
    expected = urllib.request.urlopen(MUSTANG_URL + '.sha1', timeout=60).read().decode().split()[0].strip()
    partial = target.with_suffix('.part')
    digest = hashlib.sha1()
    with urllib.request.urlopen(MUSTANG_URL, timeout=600) as response, open(partial, 'wb') as out:
        while chunk := response.read(1 << 20):
            digest.update(chunk)
            out.write(chunk)
    if digest.hexdigest() != expected:
        partial.unlink()
        raise RuntimeError('Prüfsumme des Validators stimmt nicht, Download verworfen.')
    partial.rename(target)
    progress(f'Validator gespeichert: {target}')
    return target


def validate(content, suffix):
    """Run the official check on XML or PDF bytes. Returns {'valid': bool, 'messages': [...], 'report': str}."""
    if shutil.which('java') is None:
        raise RuntimeError('Java ist nicht installiert (für den offiziellen Validator nötig).')
    if not jar_path().is_file():
        raise RuntimeError('Validator fehlt. Einmalig "python -m e_rechnung validator-laden" ausführen.')
    with tempfile.TemporaryDirectory() as folder:
        source = Path(folder) / f'rechnung{suffix}'
        source.write_bytes(content)
        run = subprocess.run(['java', '-jar', str(jar_path()), '--action', 'validate', '--source', str(source),
                              '--no-notices', '--disable-file-logging'],
                             capture_output=True, text=True, timeout=600, cwd=folder)
    output = run.stdout
    start = output.find('<validation')
    report = output[start:] if start >= 0 else output
    messages = []
    valid = False
    try:
        root = ET.fromstring(report)
        valid = root.find('summary') is not None and root.find('summary').get('status') == 'valid'
        for node in root.iter():
            if node.tag in ('error', 'warning', 'fatal'):
                text = re.sub(r'\s+', ' ', ''.join(node.itertext())).strip()
                text = re.sub(r' from /xslt/\S+\)?$', '', text)
                messages.append({'level': 'fehler' if node.tag in ('error', 'fatal') else 'hinweis',
                                 'message': text, 'location': node.get('location', '')})
        pdf = root.find('pdf')
        if pdf is not None and 'isCompliant=false' in (pdf.text or ''):
            messages.append({'level': 'fehler', 'message': 'PDF/A-3 ist nicht konform: ' + (pdf.text or '').strip(),
                             'location': ''})
    except ET.ParseError:
        messages.append({'level': 'fehler', 'message': 'Validator-Ausgabe nicht lesbar: '
                         + (run.stderr or output)[-500:], 'location': ''})
    return {'valid': valid, 'messages': messages, 'report': report}
