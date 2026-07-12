from __future__ import annotations

import io
import hashlib
import csv
import json
import re
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

import streamlit as st

try:
    from pypdf import PdfReader, PdfWriter
except ModuleNotFoundError:
    st.error("Falta instalar pypdf. Verifica requirements.txt y reinicia la app.")
    st.stop()

PREDRAW_RE = re.compile(r"P/N\s+Pre-Draw\s+Print", re.I)
TASK_PAGE_RE = re.compile(r"\bPage\s*:\s*(\d+)\s+of\s+(\d+)", re.I)
EO_PAGE_RE = re.compile(r"\bPAGE\s*:\s*(\d+)\s+of\s+(\d+)", re.I)
FORM_PAGE_RE = re.compile(r"\bPAGE\s+(\d+)\s+OF\s+(\d+)", re.I)
EO_ID_RE = re.compile(r"E\.O\.\s*No\.\s*:\s*([A-Z0-9._-]+)", re.I)
FOOTER_TASK_RE = re.compile(r"Task\s*Card\s*:\s*(.+?)(?=\s+A\s*/?\s*C\s+Reg\.|\s+Description\s*:|$)", re.I)
SEQ_RE = re.compile(r"Seq\s+No\.\s*:\s*(\d+)", re.I)

@dataclass
class PageInfo:
    source_page: int
    kind: str
    doc_id: str
    page_no: int
    page_total: int
    seq_no: str
    decision: str
    reason: str

@dataclass
class Block:
    kind: str
    doc_id: str
    page_total: int
    pages: list[PageInfo]
    keep: bool = True
    reason: str = "recognized document"


def clean_text(text: str) -> str:
    return " ".join((text or "").replace("\x00", " ").split())


def task_id(text: str, fallback: str) -> str:
    m = FOOTER_TASK_RE.search(text)
    return clean_text(m.group(1)) if m else fallback


def classify(text: str, source_page: int) -> PageInfo:
    t = clean_text(text)
    seq = (SEQ_RE.search(t).group(1) if SEQ_RE.search(t) else "")
    if not t:
        return PageInfo(source_page, "blank", "", 0, 0, seq, "remove", "blank source page")
    if PREDRAW_RE.search(t):
        return PageInfo(source_page, "predraw", task_id(t, ""), 1, 1, seq, "remove", "P/N Pre-Draw Print")

    eo_id = EO_ID_RE.search(t)
    eo_page = EO_PAGE_RE.search(t)
    if eo_id and eo_page:
        doc = eo_id.group(1).rstrip("_.-")
        return PageInfo(source_page, "engineering_order", doc, int(eo_page.group(1)), int(eo_page.group(2)), seq, "keep", "EO numbered page")

    form_page = FORM_PAGE_RE.search(t)
    upper = t.upper()
    if form_page and any(x in upper for x in ("DAILY CHECK", "WEEKLY CHECK", "FORM N", "FORM Nº")):
        return PageInfo(source_page, "check_form", task_id(t, "CHECK_FORM"), int(form_page.group(1)), int(form_page.group(2)), seq, "keep", "numbered check form")

    task_page = TASK_PAGE_RE.search(t)
    if task_page and "TASK CARD" in upper:
        return PageInfo(source_page, "task_card", task_id(t, "TASK_CARD"), int(task_page.group(1)), int(task_page.group(2)), seq, "keep", "numbered task card")

    return PageInfo(source_page, "attachment", task_id(t, ""), 0, 0, seq, "remove", "attachment or unsupported page")


def build_blocks(infos: list[PageInfo]) -> list[Block]:
    blocks: list[Block] = []
    current: Block | None = None
    recognized = {"task_card", "engineering_order", "check_form"}

    for info in infos:
        if info.kind not in recognized:
            continue
        starts_new = info.page_no == 1
        continues = (
            current is not None
            and info.kind == current.kind
            and info.page_no == current.pages[-1].page_no + 1
            and info.page_total == current.page_total
        )
        if starts_new or not continues:
            if current:
                blocks.append(current)
            current = Block(info.kind, info.doc_id, info.page_total, [info])
        else:
            current.pages.append(info)
    if current:
        blocks.append(current)

    # A one-page Task Card is a wrapper only when the next recognized block begins
    # on the immediately following source page and is an EO or check form.
    for idx, block in enumerate(blocks):
        complete = len(block.pages) == block.page_total
        if not complete:
            block.keep = False
            block.reason = "incomplete numbered document"
            for p in block.pages:
                p.decision, p.reason = "remove", block.reason
            continue
        if block.kind == "task_card" and block.page_total == 1 and idx + 1 < len(blocks):
            nxt = blocks[idx + 1]
            adjacent = nxt.pages[0].source_page == block.pages[-1].source_page + 1
            if adjacent and nxt.kind in {"engineering_order", "check_form"}:
                block.keep = False
                block.reason = f"single-page wrapper before {nxt.kind}"
                for p in block.pages:
                    p.decision, p.reason = "remove", block.reason
    return blocks


def output_name(path: Path) -> str:
    stem = path.stem[:-2] if path.stem.endswith(" F") else path.stem
    return f"{stem} F.pdf"


def edit_pdf(input_path: Path, output_dir: Path, audit: bool = True) -> Path:
    reader = PdfReader(str(input_path), strict=False)
    infos = [classify(page.extract_text() or "", n) for n, page in enumerate(reader.pages, 1)]
    blocks = build_blocks(infos)
    kept = [b for b in blocks if b.keep]
    if not kept:
        raise ValueError("No se detectaron documentos tecnicos completos para conservar.")

    writer = PdfWriter()
    blanks = []
    for block in kept:
        for info in block.pages:
            writer.add_page(reader.pages[info.source_page - 1])
        if len(block.pages) % 2 == 1:
            last = reader.pages[block.pages[-1].source_page - 1]
            writer.add_blank_page(width=float(last.mediabox.width), height=float(last.mediabox.height))
            blanks.append({"after": block.doc_id, "source_page": block.pages[-1].source_page})

    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / output_name(input_path)
    with output.open("wb") as fh:
        writer.write(fh)

    if audit:
        report = {
            "version": "4.0",
            "input": input_path.name,
            "output": output.name,
            "input_pages": len(reader.pages),
            "output_pages": len(writer.pages),
            "blocks": [
                {"kind": b.kind, "doc_id": b.doc_id, "expected_pages": b.page_total,
                 "source_pages": [p.source_page for p in b.pages], "keep": b.keep, "reason": b.reason}
                for b in blocks
            ],
            "all_pages": [asdict(p) for p in infos],
            "inserted_blank_pages": blanks,
        }
        output.with_suffix(".audit.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return output


@st.cache_data(show_spinner=False, max_entries=30)
def process_pdf_bytes(file_name: str, file_bytes: bytes, content_hash: str) -> dict:
    """Procesa un PDF de forma determinista y conserva el resultado entre reruns."""
    del content_hash  # forma parte de la llave del cache
    if not file_bytes.startswith(b"%PDF-"):
        raise ValueError("Cabecera PDF invalida")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        src = root / Path(file_name).name
        out_dir = root / "salida"
        src.write_bytes(file_bytes)
        output = edit_pdf(src, out_dir, audit=True)
        audit = output.with_suffix(".audit.json")
        pdf_data = output.read_bytes()
        audit_data = audit.read_bytes()
        check = PdfReader(io.BytesIO(pdf_data), strict=False)
        audit_obj = json.loads(audit_data.decode("utf-8"))
        if not check.pages:
            raise ValueError("El resultado no contiene paginas")
        if audit_obj["output_pages"] != len(check.pages):
            raise ValueError("Conteo inconsistente entre PDF y auditoria")
        return {
            "input_name": Path(file_name).name,
            "pdf_name": output_name(Path(file_name).name and Path(file_name)),
            "pdf_data": pdf_data,
            "audit_name": str(Path(output_name(Path(file_name))).with_suffix(".audit.json")),
            "audit_data": audit_data,
            "input_pages": audit_obj["input_pages"],
            "output_pages": audit_obj["output_pages"],
            "blocks": audit_obj["blocks"],
        }


def unique_result_names(results: list[dict]) -> list[dict]:
    seen: dict[str, int] = {}
    fixed = []
    for item in results:
        copy = dict(item)
        base = copy["pdf_name"]
        count = seen.get(base.lower(), 0) + 1
        seen[base.lower()] = count
        if count > 1:
            stem = Path(base).stem
            copy["pdf_name"] = f"{stem}_{count}.pdf"
            copy["audit_name"] = f"{stem}_{count}.audit.json"
        fixed.append(copy)
    return fixed


@st.cache_data(show_spinner=False, max_entries=20)
def make_batch_zip(batch_signature: str, payload: tuple) -> bytes:
    """Genera un ZIP autocontenido con carpeta, PDFs, auditorias y manifiesto."""
    del batch_signature
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
        manifest_rows = [["archivo_entrada", "archivo_salida", "paginas_entrada", "paginas_salida"]]
        for input_name, pdf_name, pdf_data, audit_name, audit_data, in_pages, out_pages in payload:
            archive.writestr(f"PDF_EDITADOS/{pdf_name}", pdf_data)
            archive.writestr(f"AUDITORIAS/{audit_name}", audit_data)
            manifest_rows.append([input_name, pdf_name, str(in_pages), str(out_pages)])
        csv_buffer = io.StringIO(newline="")
        csv.writer(csv_buffer).writerows(manifest_rows)
        archive.writestr("MANIFIESTO.csv", csv_buffer.getvalue().encode("utf-8-sig"))
    zip_bytes = buffer.getvalue()
    # Verificacion integral: CRC y apertura de cada PDF desde el propio ZIP.
    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as check_zip:
        bad = check_zip.testzip()
        if bad is not None:
            raise ValueError(f"ZIP corrupto en {bad}")
        pdf_entries = [n for n in check_zip.namelist() if n.startswith("PDF_EDITADOS/") and n.lower().endswith(".pdf")]
        if len(pdf_entries) != len(payload):
            raise ValueError("El ZIP no contiene todos los PDF esperados")
        for entry in pdf_entries:
            if len(PdfReader(io.BytesIO(check_zip.read(entry)), strict=False).pages) == 0:
                raise ValueError(f"PDF invalido dentro del ZIP: {entry}")
    return zip_bytes


st.set_page_config(page_title="Editor PDF Tecnico", page_icon="📄", layout="centered")
st.title("Editor de documentacion tecnica PDF")
st.caption("Motor v6 - cache por contenido, N533VL reforzado y ZIP verificado.")

with st.expander("Logica aplicada"):
    st.markdown("""
- Elimina Pre-Draw, anexos no operativos y hojas vacias de origen.
- Reconstruye cada tarea con su numeracion completa `1 of N`.
- Conserva Task Cards, EO y formularios Daily/Weekly Check.
- Elimina caratulas de una pagina antes de EO o Check.
- Agrega una hoja blanca al final de cada bloque impar.
- El ZIP contiene **todos los PDF** dentro de `PDF_EDITADOS/`, auditorias y manifiesto.
""")

uploaded = st.file_uploader("Selecciona uno o varios PDF", type=["pdf"], accept_multiple_files=True, key="files_v6")

if not uploaded:
    st.info("Carga al menos un PDF para comenzar.")
else:
    total_mb = sum(len(f.getvalue()) for f in uploaded) / (1024 * 1024)
    st.write(f"Archivos seleccionados: **{len(uploaded)}** - Tamano total: **{total_mb:.1f} MB**")
    if st.button("Procesar archivos", type="primary", use_container_width=True):
        processed, errors = [], []
        progress = st.progress(0, text="Iniciando...")
        for index, file in enumerate(uploaded, 1):
            try:
                data = file.getvalue()
                digest = hashlib.sha256(data).hexdigest()
                processed.append(process_pdf_bytes(file.name, data, digest))
            except Exception as exc:
                errors.append(f"{file.name}: {type(exc).__name__}: {exc}")
            progress.progress(index / len(uploaded), text=f"Procesando {index} de {len(uploaded)}")
        progress.empty()
        processed = unique_result_names(processed)
        st.session_state["v6_results"] = processed
        st.session_state["v6_errors"] = errors

results = st.session_state.get("v6_results", [])
errors = st.session_state.get("v6_errors", [])

if results:
    signature_source = "|".join(f"{r['input_name']}:{hashlib.sha256(r['pdf_data']).hexdigest()}" for r in results)
    signature = hashlib.sha256(signature_source.encode()).hexdigest()
    payload = tuple((r["input_name"], r["pdf_name"], r["pdf_data"], r["audit_name"], r["audit_data"], r["input_pages"], r["output_pages"]) for r in results)
    zip_data = make_batch_zip(signature, payload)
    st.success(f"Listo: {len(results)} PDF procesados. ZIP verificado: {len(zip_data)/(1024*1024):.1f} MB")
    st.download_button("Descargar carpeta ZIP con todos los PDF", zip_data, "PDF_EDITADOS.zip", "application/zip", key=f"zip-{signature}", use_container_width=True)
    for i, result in enumerate(results):
        with st.container(border=True):
            st.write(f"**{result['input_name']}**: {result['input_pages']} -> {result['output_pages']} paginas")
            c1, c2 = st.columns(2)
            c1.download_button("Descargar PDF", result["pdf_data"], result["pdf_name"], "application/pdf", key=f"pdf-{signature}-{i}", use_container_width=True)
            c2.download_button("Auditoria", result["audit_data"], result["audit_name"], "application/json", key=f"audit-{signature}-{i}", use_container_width=True)

if errors:
    st.error(f"No se procesaron {len(errors)} archivo(s)")
    for error in errors:
        st.code(error)
