"""Writes the sample CVs next to this file: the layouts people upload, as Word and PDF files.

    CHROME_BIN=… .venv/bin/python tests/fixtures/cvs/make.py

Each is built the way the tools people use build them: a Word template with the name in the page
header, in a layout table or in a text box, links that are hyperlinks behind the word "LinkedIn"; a
designed PDF with the name in big letters over two lines, letter-spaced, beside the job title, or under
a sidebar. `expected.json` says what onboarding must find in each. The files are committed, so the
tests need neither Chrome nor this script; run it again only to change them.
"""
# ruff: noqa: RUF001, E501 - CVs are written with en dashes, and a fixture reads best whole
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import docx
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls
from docx.shared import Pt

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))

BODY = [
    ("PROFILE", ["Engineer who designs parts that are easy to make."]),
    ("EXPERIENCE", ["Mechanical Design Engineer, Acme Pumps\tJan 2020 – Present",
                    "• Designed pump housings in SolidWorks for a line of 40 products",
                    "• Cut machining time by 18% with simpler tolerances",
                    "Mechanical Engineer, Globex\tSep 2016 – Dec 2019",
                    "• Ran thermal tests on gearboxes"]),
    ("EDUCATION", ["MEng Mechanical Engineering, University of Leeds\t2012 – 2016"]),
    ("SKILLS", ["SolidWorks, AutoCAD, GD&T, FEA, Python"]),
]

EXPECTED: dict[str, dict] = {}


def _body(document) -> None:
    for heading, lines in BODY:
        document.add_paragraph(heading)
        for line in lines:
            document.add_paragraph(line)


def _hyperlink(paragraph, url: str, text: str) -> None:
    """A real Word hyperlink: the text shows, the address lives in the document's relationships."""
    rel = paragraph.part.relate_to(url, docx.opc.constants.RELATIONSHIP_TYPE.HYPERLINK, is_external=True)
    link = parse_xml(f'<w:hyperlink {nsdecls("w", "r")} r:id="{rel}"><w:r><w:t>{text}</w:t></w:r></w:hyperlink>')
    paragraph._p.append(link)


def _save(document, name: str, expected: dict) -> None:
    buffer = io.BytesIO()
    document.save(buffer)
    (HERE / name).write_bytes(buffer.getvalue())
    EXPECTED[name] = expected


def word_header() -> None:
    """Name and contact in the page header, which every page repeats: Word's own CV templates."""
    document = docx.Document()
    header = document.sections[0].header
    header.paragraphs[0].text = "Priya Raman"
    header.paragraphs[0].runs[0].font.size = Pt(24)
    header.add_paragraph("Bristol, UK | priya.raman@example.com | +44 7700 900456")
    _body(document)
    _save(document, "word-header.docx", {"name": "Priya Raman", "email": "priya.raman@example.com",
                                         "phone": "+44 7700 900456", "links": []})


def word_table() -> None:
    """The whole top laid out in a table: name and title on the left, contact on the right."""
    document = docx.Document()
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Tomás Novak\nMechanical Design Engineer"
    table.cell(0, 1).text = "tomas.novak@example.cz\n+420 601 234 567\nlinkedin.com/in/tomasnovak"
    _body(document)
    _save(document, "word-table.docx", {"name": "Tomás Novak", "email": "tomas.novak@example.cz",
                                        "phone": "+420 601 234 567", "links": ["linkedin.com/in/tomasnovak"]})


def word_textbox() -> None:
    """The name in a text box, as the "modern" templates draw it: not a paragraph of the body."""
    document = docx.Document()
    box = parse_xml(
        f'<w:r {nsdecls("w")} xmlns:v="urn:schemas-microsoft-com:vml"><w:pict><v:shape style="width:300pt;height:60pt"><v:textbox>'
        '<w:txbxContent><w:p><w:r><w:t>Grace Okafor</w:t></w:r></w:p>'
        '<w:p><w:r><w:t>grace.okafor@example.ng · +234 803 123 4567</w:t></w:r></w:p>'
        '</w:txbxContent></v:textbox></v:shape></w:pict></w:r>')
    document.add_paragraph()._p.append(box)
    _body(document)
    _save(document, "word-textbox.docx", {"name": "Grace Okafor", "email": "grace.okafor@example.ng",
                                          "phone": "+234 803 123 4567", "links": []})


def word_hyperlinks() -> None:
    """Links as the word "LinkedIn", with the address behind it: the text alone has no address."""
    document = docx.Document()
    document.add_paragraph("Liam Byrne")
    contact = document.add_paragraph("Cork, Ireland | liam.byrne@example.ie | ")
    _hyperlink(contact, "https://www.linkedin.com/in/liambyrne/", "LinkedIn")
    contact.add_run(" | ")
    _hyperlink(contact, "https://github.com/liambyrne", "GitHub")
    _hyperlink(contact, "mailto:liam.byrne@example.ie", "Email me")
    _body(document)
    _save(document, "word-hyperlinks.docx", {
        "name": "Liam Byrne", "email": "liam.byrne@example.ie", "phone": None,
        "links": ["https://www.linkedin.com/in/liambyrne/", "https://github.com/liambyrne"]})


def _page(top: str, style: str = "") -> str:
    body = "".join(f"<h2>{heading}</h2>" + "".join(
        f"<p>{line.replace(chr(9), ' &nbsp; ')}</p>" for line in lines) for heading, lines in BODY)
    return (f"<!doctype html><html><head><meta charset='utf-8'><style>body{{font-family:sans-serif}}"
            f"h2{{font-size:12pt}} p{{margin:2pt 0}} {style}</style></head><body>{top}{body}</body></html>")


def pdfs() -> None:
    from services.docgen import pdf

    def save(name: str, html: str, expected: dict) -> None:
        (HERE / name).write_bytes(pdf.render(html))
        EXPECTED[name] = expected

    save("pdf-split-name.pdf", _page(
        "<div class='name'>JORDAN</div><div class='name'>PETERSON</div>"
        "<p>Leeds, UK · jordan.p@example.co.uk · +44 7700 900789</p>",
        ".name{font-size:40pt;font-weight:800;line-height:1}"),
        {"name": "Jordan Peterson", "email": "jordan.p@example.co.uk", "phone": "+44 7700 900789", "links": []})
    save("pdf-letter-spaced.pdf", _page(
        "<div class='name'>MARIA GARCIA</div><p>Valencia, Spain · hola@mariagarcia.es · +34 612 345 678</p>",
        ".name{font-size:28pt;letter-spacing:0.5em}"),
        {"name": "Maria Garcia", "email": "hola@mariagarcia.es", "phone": "+34 612 345 678", "links": []})
    save("pdf-name-beside-title.pdf", _page(
        "<div style='display:flex;justify-content:space-between;align-items:baseline'>"
        "<span style='font-size:26pt'>Florence Adeyemi</span><span>Mechanical Design Engineer</span></div>"
        "<p>London · florence@adeyemi.dev · +44 7700 900321</p>"),
        {"name": "Florence Adeyemi", "email": "florence@adeyemi.dev", "phone": "+44 7700 900321", "links": []})
    save("pdf-hyperlinks.pdf", _page(
        "<h1>Sofia Lima</h1><p>Porto, Portugal · <a href='mailto:sofia@example.pt'>sofia@example.pt</a> · "
        "<a href='https://www.linkedin.com/in/sofialima'>LinkedIn</a> · "
        "<a href='https://sofialima.design'>Portfolio</a></p>"),
        {"name": "Sofia Lima", "email": "sofia@example.pt", "phone": None,
         "links": ["https://www.linkedin.com/in/sofialima", "https://sofialima.design/"]})
    save("pdf-sidebar.pdf", _page(
        "<div style='display:flex;gap:24pt'><div style='width:150pt'><h2>CONTACT</h2>"
        "<p>chen.wei@example.com</p><p>+31 6 1234 5678</p><p>Rotterdam</p><h2>LANGUAGES</h2><p>English, Dutch</p>"
        "</div><div><div style='font-size:30pt'>Chen Wei</div><p>Mechanical Design Engineer</p></div></div>"),
        {"name": "Chen Wei", "email": "chen.wei@example.com", "phone": "+31 6 1234 5678", "links": []})


LETTER = ["Dear Hiring Manager,",
          "I am applying for the Product Designer role at Initech. For four years I have worked as a "
          "UX designer at Globex, where I led the redesign of our booking flow.",
          "Before that I was a graphic designer for a small studio, which taught me to work fast "
          "and to explain my choices.",
          "Kind regards,", "Noor Haddad"]


def letters() -> None:
    """A cover letter is prose: no dated jobs and no skills list, only the roles named in sentences."""
    from services.docgen import pdf

    expected = {"titles": ["product designer", "ux designer", "graphic designer"]}
    document = docx.Document()
    for line in LETTER:
        document.add_paragraph(line)
    buffer = io.BytesIO()
    document.save(buffer)
    (HERE / "letter.docx").write_bytes(buffer.getvalue())
    (HERE / "letter.pdf").write_bytes(pdf.render(
        "<!doctype html><html><body>" + "".join(f"<p>{line}</p>" for line in LETTER) + "</body></html>"))
    (HERE / "letters.json").write_text(json.dumps({"letter.docx": expected, "letter.pdf": expected},
                                                  indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    word_header()
    word_table()
    word_textbox()
    word_hyperlinks()
    pdfs()
    letters()
    (HERE / "expected.json").write_text(json.dumps(EXPECTED, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {len(EXPECTED)} CVs to {HERE}")
