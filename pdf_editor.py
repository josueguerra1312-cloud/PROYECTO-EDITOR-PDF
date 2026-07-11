from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


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
    from pypdf import PdfReader, PdfWriter
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



import io
import tempfile
import zipfile
from pathlib import Path

import streamlit as st

st.set_page_config(page_title="Editor PDF Tecnico", page_icon="📄", layout="centered")
st.title("Editor de documentacion tecnica PDF")
st.caption("Prepara tareas para impresion a doble cara y genera archivos con F al final.")

try:
    import pypdf
except ModuleNotFoundError:
    st.error("La dependencia pypdf no fue instalada por Streamlit Cloud.")
    st.code("requirements.txt\n\nstreamlit>=1.36,<2\npypdf==5.9.0")
    st.warning("Verifica que requirements.txt este en la raiz del repositorio y reinicia la aplicacion.")
    st.stop()

with st.expander("Reglas aplicadas"):
    st.markdown("""
- Elimina hojas **P/N Pre-Draw Print**.
- Conserva Task Cards multipagina y Engineering Orders completas.
- Elimina caratulas de una pagina antes de EO, Daily Check o Weekly Check.
- Agrega una hoja blanca cuando una tarea termina con cantidad impar de paginas.
- Genera el nombre de salida con **F** al final.
""")

files = st.file_uploader("Selecciona uno o varios PDF", type=["pdf"], accept_multiple_files=True)

if not files:
    st.info("Carga al menos un archivo PDF para comenzar.")
else:
    st.write(f"Archivos seleccionados: **{len(files)}**")
    if st.button("Procesar archivos", type="primary", use_container_width=True):
        results = []
        errors = []
        progress = st.progress(0)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "entrada"
            target = root / "salida"
            source.mkdir()
            target.mkdir()
            for index, uploaded in enumerate(files, 1):
                try:
                    name = Path(uploaded.name).name
                    input_path = source / name
                    input_path.write_bytes(uploaded.getvalue())
                    output_path = edit_pdf(input_path, target, audit=True)
                    audit_path = output_path.with_suffix(".audit.json")
                    results.append((output_path.name, output_path.read_bytes(), audit_path.name, audit_path.read_bytes()))
                except Exception as exc:
                    errors.append(f"{uploaded.name}: {exc}")
                progress.progress(index / len(files))
        progress.empty()

        if results:
            st.success(f"Se procesaron {len(results)} archivo(s).")
            bundle = io.BytesIO()
            with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
                for pdf_name, pdf_data, audit_name, audit_data in results:
                    archive.writestr(pdf_name, pdf_data)
                    archive.writestr(audit_name, audit_data)
            st.download_button("Descargar resultados ZIP", bundle.getvalue(), "pdf_editados.zip", "application/zip", use_container_width=True)
            for pdf_name, pdf_data, audit_name, audit_data in results:
                with st.container(border=True):
                    st.write(f"**{pdf_name}**")
                    left, right = st.columns(2)
                    left.download_button("Descargar PDF", pdf_data, pdf_name, "application/pdf", key="pdf-" + pdf_name, use_container_width=True)
                    right.download_button("Descargar auditoria", audit_data, audit_name, "application/json", key="json-" + audit_name, use_container_width=True)
        if errors:
            st.error("Algunos archivos no se procesaron:")
            for error in errors:
                st.code(error)
