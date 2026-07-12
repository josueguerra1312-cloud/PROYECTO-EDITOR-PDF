from __future__ import annotations

import io
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


st.set_page_config(page_title="Editor PDF Tecnico", page_icon="📄", layout="centered")
st.title("Editor de documentacion tecnica PDF")
st.caption("Motor v5 - procesamiento multiple persistente y validacion reforzada.")

# Los resultados deben sobrevivir a los reruns que Streamlit ejecuta al pulsar
# cualquier boton de descarga. En v4 eran variables locales dentro del boton
# Procesar; por eso desaparecian o el ZIP podia quedar inaccesible.
if "results_v5" not in st.session_state:
    st.session_state.results_v5 = []
if "errors_v5" not in st.session_state:
    st.session_state.errors_v5 = []
if "zip_v5" not in st.session_state:
    st.session_state.zip_v5 = b""
if "batch_id_v5" not in st.session_state:
    st.session_state.batch_id_v5 = 0

with st.expander("Logica aplicada"):
    st.markdown("""
- Elimina hojas **P/N Pre-Draw Print**, anexos y hojas fuente vacias.
- Reconstruye documentos mediante su numeracion **1 of N**.
- Conserva Task Cards multipagina, Engineering Orders y formularios Daily/Weekly Check.
- Elimina caratulas de una pagina antes de EO o Check.
- Agrega una hoja blanca al final de cada documento impar.
- Mantiene el orden original y genera auditoria JSON.
""")

files = st.file_uploader(
    "Selecciona uno o varios PDF",
    type=["pdf"],
    accept_multiple_files=True,
    key="uploader_v5",
)

col_process, col_clear = st.columns([3, 1])
process_clicked = col_process.button(
    "Procesar archivos",
    type="primary",
    use_container_width=True,
    disabled=not files,
)
clear_clicked = col_clear.button("Limpiar", use_container_width=True)

if clear_clicked:
    st.session_state.results_v5 = []
    st.session_state.errors_v5 = []
    st.session_state.zip_v5 = b""
    st.session_state.batch_id_v5 += 1
    st.rerun()

if not files and not st.session_state.results_v5:
    st.info("Carga al menos un PDF para comenzar.")

if process_clicked:
    st.session_state.results_v5 = []
    st.session_state.errors_v5 = []
    st.session_state.zip_v5 = b""
    st.session_state.batch_id_v5 += 1
    batch_id = st.session_state.batch_id_v5
    progress = st.progress(0, text="Preparando archivos...")
    used_names: dict[str, int] = {}

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "entrada"
        target = root / "salida"
        source.mkdir()
        target.mkdir()

        for i, uploaded in enumerate(files, 1):
            try:
                original_name = Path(uploaded.name).name
                raw = uploaded.getvalue()
                if not raw.startswith(b"%PDF-"):
                    raise ValueError("El archivo no tiene una cabecera PDF valida.")
                if len(raw) == 0:
                    raise ValueError("El archivo esta vacio.")

                # Evita colisiones si se cargan dos archivos con el mismo nombre.
                count = used_names.get(original_name, 0)
                used_names[original_name] = count + 1
                disk_name = original_name if count == 0 else f"{Path(original_name).stem}_{count + 1}.pdf"
                src = source / disk_name
                src.write_bytes(raw)

                result = edit_pdf(src, target, audit=True)
                audit = result.with_suffix(".audit.json")
                pdf_data = result.read_bytes()
                audit_data = audit.read_bytes()

                # Verificacion secundaria antes de exponer la descarga.
                check = PdfReader(io.BytesIO(pdf_data), strict=False)
                if len(check.pages) == 0:
                    raise ValueError("El resultado no contiene paginas.")
                audit_obj = json.loads(audit_data.decode("utf-8"))
                if audit_obj.get("output_pages") != len(check.pages):
                    raise ValueError("La auditoria y el PDF generado no coinciden.")

                display_name = output_name(Path(original_name))
                if count:
                    display_name = f"{Path(original_name).stem}_{count + 1} F.pdf"
                display_audit = str(Path(display_name).with_suffix(".audit.json"))
                st.session_state.results_v5.append({
                    "input_name": original_name,
                    "pdf_name": display_name,
                    "pdf_data": pdf_data,
                    "audit_name": display_audit,
                    "audit_data": audit_data,
                    "input_pages": audit_obj.get("input_pages"),
                    "output_pages": audit_obj.get("output_pages"),
                    "batch_id": batch_id,
                })
            except Exception as exc:
                st.session_state.errors_v5.append(f"{uploaded.name}: {type(exc).__name__}: {exc}")
            progress.progress(i / len(files), text=f"Procesando {i} de {len(files)}")

    # Construye el ZIP una sola vez y lo guarda en session_state. zipfile necesita
    # que el buffer permanezca vivo despues del rerun del boton de descarga.
    if st.session_state.results_v5:
        bundle = io.BytesIO()
        with zipfile.ZipFile(bundle, mode="w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            for item in st.session_state.results_v5:
                zf.writestr(item["pdf_name"], item["pdf_data"])
                zf.writestr(item["audit_name"], item["audit_data"])
        st.session_state.zip_v5 = bundle.getvalue()
    progress.empty()

results = st.session_state.results_v5
errors = st.session_state.errors_v5

if results:
    st.success(f"Proceso terminado: {len(results)} archivo(s) correctos.")
    st.download_button(
        "Descargar todos los resultados en ZIP",
        data=st.session_state.zip_v5,
        file_name="pdf_editados.zip",
        mime="application/zip",
        key=f"zip-{st.session_state.batch_id_v5}",
        use_container_width=True,
    )
    st.subheader("Resultados")
    for index, item in enumerate(results):
        with st.container(border=True):
            st.write(f"**{item['input_name']}**: {item['input_pages']} paginas de entrada → {item['output_pages']} paginas de salida")
            c1, c2 = st.columns(2)
            c1.download_button(
                "Descargar PDF",
                data=item["pdf_data"],
                file_name=item["pdf_name"],
                mime="application/pdf",
                key=f"pdf-{item['batch_id']}-{index}",
                use_container_width=True,
            )
            c2.download_button(
                "Descargar auditoria",
                data=item["audit_data"],
                file_name=item["audit_name"],
                mime="application/json",
                key=f"audit-{item['batch_id']}-{index}",
                use_container_width=True,
            )

if errors:
    st.error(f"No se procesaron {len(errors)} archivo(s).")
    for err in errors:
        st.code(err)
