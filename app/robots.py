"""Load the robot roster from robots.ods (project root).

Stdlib only — no odfpy/pandas. ODS is just a zipped XML bundle; we read
content.xml from Sheet1 and pull out the (Number, Name, Ears) columns.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

ROBOTS_PATH = Path(__file__).parent.parent / "robots.ods"

_TABLE_NS = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
_TEXT_NS = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
_TABLE = f"{{{_TABLE_NS}}}"
_TEXT = f"{{{_TEXT_NS}}}"

_EARS_NORMALIZE = {"aligned": "aligned", "separated": "separated"}


def _row_text(row: ET.Element) -> list[str]:
    """Return non-empty trimmed text of cells in a row, expanding repeats."""
    out: list[str] = []
    for cell in row.findall(f"{_TABLE}table-cell"):
        text = " ".join(
            (p.text or "") for p in cell.iter(f"{_TEXT}p")
        ).strip()
        repeat = int(
            cell.attrib.get(f"{_TABLE}number-columns-repeated", "1")
        )
        out.extend([text] * repeat)
    while out and not out[-1]:
        out.pop()
    return out


def load_robots() -> list[dict]:
    """Return [{'number': int, 'name': str, 'ears': str}, ...] sorted by number.

    Raises a clear error if the file is missing, the sheet is empty, or any
    row has a bad ears value. Called once at app startup so a bad deploy
    fails loudly instead of silently serving a stale or broken roster.
    """
    if not ROBOTS_PATH.exists():
        raise FileNotFoundError(
            f"robots.ods not found at {ROBOTS_PATH}. The roster must be "
            "shipped with the deploy."
        )

    with zipfile.ZipFile(ROBOTS_PATH) as zf:
        xml = zf.read("content.xml")
    root = ET.fromstring(xml)
    sheet = root.find(f".//{_TABLE}table")
    if sheet is None:
        raise ValueError("robots.ods has no sheets")

    rows = [_row_text(r) for r in sheet.findall(f"{_TABLE}table-row")]
    rows = [r for r in rows if r]
    if not rows:
        raise ValueError("robots.ods Sheet1 is empty")

    header = [h.strip().lower() for h in rows[0]]
    try:
        i_num = header.index("number")
        i_name = header.index("name")
        i_ears = header.index("ears")
    except ValueError as exc:
        raise ValueError(
            f"robots.ods Sheet1 header must contain Number, Name, Ears "
            f"(got {rows[0]!r})"
        ) from exc

    out: list[dict] = []
    for r in rows[1:]:
        if len(r) <= max(i_num, i_name, i_ears):
            continue
        num_txt, name, ears_raw = r[i_num], r[i_name], r[i_ears]
        if not name.strip():
            continue
        try:
            number = int(num_txt)
        except ValueError as exc:
            raise ValueError(
                f"robots.ods: row {r!r} has non-integer number {num_txt!r}"
            ) from exc
        ears = _EARS_NORMALIZE.get(ears_raw.strip().lower())
        if ears is None:
            raise ValueError(
                f"robots.ods: robot {name!r} has unknown ears value "
                f"{ears_raw!r} (expected Aligned or Separated)"
            )
        out.append({"number": number, "name": name.strip(), "ears": ears})

    if not out:
        raise ValueError("robots.ods Sheet1 has a header but no robot rows")
    out.sort(key=lambda r: r["number"])
    return out
