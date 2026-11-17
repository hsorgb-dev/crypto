"""Prüfung gegen die offiziellen Regeln: XSD + Schematron (EN 16931, XRechnung).

Optional. Benötigt ``lxml`` und ``saxonche`` (pip install lxml saxonche).
Die Regelwerke werden beim ersten Aufruf von Maven Central geladen (Pakete
von phive/ph-ubl/ph-cii, die dieselben Schematron-Dateien enthalten wie der
KoSIT-Validator) und unter ~/.cache/erechnung-regeln abgelegt.

    python -m erechnung.validator rechnung.xml
"""
from __future__ import annotations

import os
import re
import sys
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

MAVEN = "https://repo1.maven.org/maven2"
PACKAGES = {
    "en16931": ("com/helger/phive/rules", "phive-rules-en16931", "4.7.0"),
    "xrechnung": ("com/helger/phive/rules", "phive-rules-xrechnung", "4.7.0"),
    "ubl": ("com/helger/ubl", "ph-ubl21", "10.3.0"),
    "cii": ("com/helger/cii", "ph-cii-d16b", "10.1.0"),
}
# Die UBL-Schemas importieren diese Namensräume ohne schemaLocation.
UBL_IMPORTS = {
    "urn:un:unece:uncefact:data:specification:CoreComponentTypeSchemaModule:2":
        ("ph-xsds-ccts-cct-schemamodule", "CCTS_CCT_SchemaModule.xsd"),
    "http://www.w3.org/2000/09/xmldsig#": ("ph-xsds-xmldsig", "xmldsig-core-schema.xsd"),
    "http://uri.etsi.org/01903/v1.3.2#": ("ph-xsds-xades132", "XAdES01903v132-201601.xsd"),
    "http://uri.etsi.org/01903/v1.4.1#": ("ph-xsds-xades141", "XAdES01903v141-201601.xsd"),
}
XSD_PACKAGE_VERSION = "4.1.2"
EN16931_VERSION = "1.3.13"
XRECHNUNG_VERSION = "3.0.2"

SVRL = "{http://purl.oclc.org/dsdl/svrl}"


@dataclass
class ValidationReport:
    syntax: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def rules_dir() -> Path:
    return Path(os.environ.get("ERECHNUNG_REGELN", Path.home() / ".cache" / "erechnung-regeln"))


def _download(group: str, artifact: str, version: str) -> Path:
    target = rules_dir() / f"{artifact}-{version}"
    if not (target / ".ok").exists():
        target.mkdir(parents=True, exist_ok=True)
        jar = target / "paket.jar"
        url = f"{MAVEN}/{group}/{artifact}/{version}/{artifact}-{version}.jar"
        with urllib.request.urlopen(url, timeout=120) as resp:
            jar.write_bytes(resp.read())
        if not zipfile.is_zipfile(jar):
            raise RuntimeError(f"Download von {url} fehlgeschlagen (kein JAR, evtl. Rate-Limit) "
                               "– später erneut versuchen")
        with zipfile.ZipFile(jar) as zf:
            for member in zf.namelist():
                if member.endswith((".xsd", ".xslt", ".xsl")):
                    zf.extract(member, target)
        jar.unlink()
        (target / ".ok").write_text("ok")
    return target


def _link_imports(directory: Path) -> None:
    """Ergänzt fehlende schemaLocation-Angaben, damit lxml die Schemas auflösen kann."""
    locations, files = {}, list(directory.rglob("*.xsd"))
    for ns, (artifact, filename) in UBL_IMPORTS.items():
        xsd = next(_download("com/helger/xsd", artifact, XSD_PACKAGE_VERSION).rglob(filename))
        locations[ns] = xsd.resolve().as_uri()
        files.append(xsd)  # XAdES importiert seinerseits XML-DSig ohne schemaLocation
    pattern = re.compile(r'<xsd?:import\s+namespace="([^"]+)"\s*/>')
    for xsd in files:
        text = xsd.read_text(encoding="utf-8")
        new = pattern.sub(lambda m: (m.group(0)[:-2].rstrip() + f' schemaLocation="{locations[m.group(1)]}"/>')
                          if m.group(1) in locations else m.group(0), text)
        if new != text:
            xsd.write_text(new, encoding="utf-8")


def _package(name: str) -> Path:
    target = _download(*PACKAGES[name])
    if name == "ubl" and not (target / ".linked").exists():
        _link_imports(target)
        (target / ".linked").write_text("ok")
    return target / "external"


def _xsd(syntax: str, root_name: str) -> Path:
    if syntax == "ubl":
        return _package("ubl") / "schemas" / "ubl21" / "maindoc" / f"UBL-{root_name}-2.1.xsd"
    matches = sorted(_package("cii").rglob("CrossIndustryInvoice_100pD16B.xsd"))
    if not matches:
        raise RuntimeError("CII-D16B-Schema nicht im Paket gefunden")
    return matches[0]


def _schematrons(syntax: str, xrechnung: bool) -> list[tuple[str, Path]]:
    base = _package("en16931") / "schematron" / EN16931_VERSION / syntax
    result = [(f"EN 16931 {EN16931_VERSION}", next(base.rglob("*.xslt")))]
    if xrechnung:
        name = "XRechnung-UBL-validation.xslt" if syntax == "ubl" else "XRechnung-CII-validation.xslt"
        result.append((f"XRechnung {XRECHNUNG_VERSION}", _package("xrechnung") / "schematron" / XRECHNUNG_VERSION / name))
    return result


_compiled: dict[Path, object] = {}
_processor = None


def _run_schematron(xslt: Path, xml: bytes):
    global _processor
    from saxonche import PySaxonProcessor
    if _processor is None:
        _processor = PySaxonProcessor(license=False)
    if xslt not in _compiled:
        _compiled[xslt] = _processor.new_xslt30_processor().compile_stylesheet(stylesheet_file=str(xslt))
    node = _processor.parse_xml(xml_text=xml.decode("utf-8"))
    return _compiled[xslt].transform_to_string(xdm_node=node)


def validate(xml: bytes) -> ValidationReport:
    from lxml import etree

    doc = etree.fromstring(xml)
    root_name = etree.QName(doc).localname
    syntax = "ubl" if root_name in ("Invoice", "CreditNote") else "cii"
    report = ValidationReport(syntax=syntax)
    xrechnung = b"xeinkauf.de:kosit:xrechnung" in xml

    schema = etree.XMLSchema(etree.parse(str(_xsd(syntax, root_name))))
    if schema.validate(doc):
        report.steps.append("XSD: gültig")
    else:
        for e in schema.error_log:
            report.errors.append(f"[XSD] Zeile {e.line}: {e.message}")
        report.steps.append("XSD: Fehler")
        return report

    for label, xslt in _schematrons(syntax, xrechnung):
        svrl = etree.fromstring(_run_schematron(xslt, xml).encode("utf-8"))
        n_err = 0
        for fa in svrl.iter(f"{SVRL}failed-assert", f"{SVRL}successful-report"):
            flag = (fa.get("flag") or "fatal").lower()
            text = " ".join("".join(fa.find(f"{SVRL}text").itertext()).split())
            msg = f"[{fa.get('id')}] {text} ({fa.get('location')})"
            if flag in ("warning", "information"):
                report.warnings.append(msg)
            else:
                report.errors.append(msg)
                n_err += 1
        report.steps.append(f"Schematron {label}: {'gültig' if n_err == 0 else f'{n_err} Fehler'}")
    return report


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("Aufruf: python -m erechnung.validator datei.xml|datei.pdf [...]")
        return 2
    rc = 0
    for name in argv:
        data = Path(name).read_bytes()
        if data[:4] == b"%PDF":
            from facturx import get_xml_from_pdf
            _, data = get_xml_from_pdf(data, check_xsd=False)
        rep = validate(data)
        print(f"{name}: {'GÜLTIG' if rep.ok else 'UNGÜLTIG'} ({rep.syntax.upper()})")
        for s in rep.steps:
            print(f"  {s}")
        for e in rep.errors:
            print(f"  FEHLER  {e}")
        for w in rep.warnings:
            print(f"  WARNUNG {w}")
        rc |= 0 if rep.ok else 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
