from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from pypdf import PdfReader, PdfWriter

PREDRAW_RE = re.compile(r"P/N\s+Pre-Draw\s+Print", re.I)
TASK_PAGE_RE = re.compile(r"Page\s*:\s*(\d+)\s+of\s+(\d+)", re.I)
EO_PAGE_RE = re.compile(r"PAGE\s*:\s*(\d+)\s+of\s+(\d+)", re.I)
FORM_PAGE_RE = re.compile(r"PAGE\s+(\d+)\s+OF\s+(\d+)", re.I)
TASK_ID_RE = re.compile(r"Task\s*Card\s*:\s*([^\n]+?)(?:\s{2,}|A/C|Description:|$)", re.I)
EO_ID_RE = re.compile(r"E\.O\.\s*No\.\s*:\s*([A-Z0-9._-]+)", re.I)


@dataclass
class PageInfo:
    source_page: int
    kind: str
    doc_id: str
    page_no: int
    page_total: int
    decision: str
    reason: str


def clean_text(text: str) -> str:
    return " ".join((text or "").replace("\x00", " ").split())


def _task_id(text: str, fallback: str) -> str:
    match = TASK_ID_RE.search(text)
    return clean_text(match.group(1)) if match else fallback


def classify_page(text: str, source_page: int) -> PageInfo:
    t = clean_text(text)
    if not t:
        return PageInfo(source_page, "blank", "", 0, 0, "remove", "blank source page")
    if PREDRAW_RE.search(t):
        return PageInfo(source_page, "predraw", _task_id(t, ""), 1, 1, "remove", "P/N pre-draw")

    eo_num = EO_ID_RE.search(t)
    eo_page = EO_PAGE_RE.search(t)
    if eo_num and eo_page:
        return PageInfo(source_page, "engineering_order", eo_num.group(1), int(eo_page.group(1)), int(eo_page.group(2)), "keep", "EO page")

    form_page = FORM_PAGE_RE.search(t)
    upper = t.upper()
    if form_page and ("DAILY CHECK" in upper or "WEEKLY CHECK" in upper or "FORM N" in upper):
        return PageInfo(source_page, "check_form", _task_id(t, "CHECK_FORM"), int(form_page.group(1)), int(form_page.group(2)), "keep", "check form page")

    task_page = TASK_PAGE_RE.search(t)
    if task_page and "TASK CARD" in upper:
        return PageInfo(source_page, "task_card", _task_id(t, "TASK_CARD"), int(task_page.group(1)), int(task_page.group(2)), "keep", "task card page")

    return PageInfo(source_page, "attachment", _task_id(t, ""), 0, 0, "remove", "unrecognized attachment/support page")


def select_documents(infos: list[PageInfo]) -> list[list[PageInfo]]:
    # Single-page task-card wrappers are removed when the next recognized document
    # belongs to the same task package (EO/check form). Multi-page task cards remain.
    recognized = [i for i in infos if i.decision == "keep"]
    for index, info in enumerate(recognized):
        if info.kind == "task_card" and info.page_total == 1:
            nxt = recognized[index + 1] if index + 1 < len(recognized) else None
            if nxt and nxt.source_page == info.source_page + 1 and nxt.kind in {"engineering_order", "check_form"}:
                info.decision = "remove"
                info.reason = f"single-page wrapper before {nxt.kind}"

    docs: list[list[PageInfo]] = []
    current: list[PageInfo] = []
    for info in infos:
        if info.decision != "keep":
            continue
        if not current:
            current = [info]
            continue
        prev = current[-1]
        same_doc = (
            info.kind == prev.kind
            and info.doc_id == prev.doc_id
            and info.page_no == prev.page_no + 1
        )
        if same_doc:
            current.append(info)
        else:
            docs.append(current)
            current = [info]
    if current:
        docs.append(current)
    return docs


def output_name(input_path: Path) -> Path:
    stem = input_path.stem
    if stem.endswith(" F"):
        stem = stem[:-2]
    return input_path.with_name(f"{stem} F.pdf")


def edit_pdf(input_path: Path, output_dir: Path, audit: bool = True) -> Path:
    reader = PdfReader(str(input_path))
    infos = [classify_page(page.extract_text() or "", n) for n, page in enumerate(reader.pages, start=1)]
    docs = select_documents(infos)
    if not docs:
        raise ValueError("No se detectaron documentos tecnicos conservables.")

    writer = PdfWriter()
    inserted_blanks: list[dict] = []
    for doc in docs:
        for info in doc:
            writer.add_page(reader.pages[info.source_page - 1])
        if len(doc) % 2 == 1:
            last = reader.pages[doc[-1].source_page - 1]
            writer.add_blank_page(width=float(last.mediabox.width), height=float(last.mediabox.height))
            inserted_blanks.append({"after_doc": doc[-1].doc_id, "after_source_page": doc[-1].source_page})

    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / output_name(input_path).name
    with out.open("wb") as fh:
        writer.write(fh)

    if audit:
        report = {
            "input": input_path.name,
            "output": out.name,
            "input_pages": len(reader.pages),
            "output_pages": len(writer.pages),
            "documents": [[asdict(p) for p in doc] for doc in docs],
            "all_pages": [asdict(p) for p in infos],
            "inserted_blank_pages": inserted_blanks,
        }
        out.with_suffix(".audit.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def batch_edit(inputs: Iterable[Path], output_dir: Path, audit: bool = True) -> list[Path]:
    outputs = []
    for path in inputs:
        if path.suffix.lower() == ".pdf" and not path.stem.endswith(" F"):
            outputs.append(edit_pdf(path, output_dir, audit=audit))
    return outputs


def _expand_paths(paths, recursive=False):
    result = []
    for path in paths:
        path = Path(path)
        if path.is_dir():
            result.extend(sorted(path.glob('**/*.pdf' if recursive else '*.pdf')))
        else:
            result.append(path)
    return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Editor de documentacion tecnica PDF para impresion duplex.')
    parser.add_argument('inputs', nargs='+', type=Path, help='PDF o carpeta de entrada')
    parser.add_argument('-o', '--output-dir', type=Path, default=Path('salida'))
    parser.add_argument('--recursive', action='store_true')
    parser.add_argument('--no-audit', action='store_true')
    args = parser.parse_args()
    outputs = batch_edit(_expand_paths(args.inputs, args.recursive), args.output_dir, audit=not args.no_audit)
    for output in outputs:
        print(output)


if __name__ == '__main__':
    main()
