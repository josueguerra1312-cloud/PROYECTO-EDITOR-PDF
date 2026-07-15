from __future__ import annotations
import csv, hashlib, io, json, re, tempfile, zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
import streamlit as st
try:
    from pypdf import PdfReader, PdfWriter
except ModuleNotFoundError:
    st.error("Falta instalar pypdf. Verifica requirements.txt y reinicia la app."); st.stop()

PREDRAW=re.compile(r"P/N\s+Pre-Draw\s+Print",re.I)
TASK_PAGE=re.compile(r"\bPage\s*:\s*(\d+)\s+of\s+(\d+)",re.I)
EO_PAGE=re.compile(r"\bPAGE\s*:\s*(\d+)\s+of\s+(\d+)",re.I)
FORM_PAGE=re.compile(r"\bPAGE\s+(\d+)\s+OF\s+(\d+)",re.I)
EO_ID=re.compile(r"E\.O\.\s*No\.\s*:\s*([A-Z0-9._-]+)",re.I)
TASK_ID=re.compile(r"Task\s*Card\s*:\s*(.+?)(?=\s+A\s*/?\s*C\s+Reg\.|\s+Description\s*:|$)",re.I)
SEQ=re.compile(r"Seq\s+No\.\s*:\s*(\d+)",re.I)
@dataclass
class Info: source_page:int; kind:str; doc_id:str; page_no:int; page_total:int; seq_no:str; decision:str; reason:str
@dataclass
class Block: kind:str; doc_id:str; page_total:int; pages:list[Info]; keep:bool=True; reason:str="recognized document"
def clean(t): return " ".join((t or "").replace("\x00"," ").split())
def tid(t,f=""): m=TASK_ID.search(t); return clean(m.group(1)) if m else f
def classify(text,n):
    t=clean(text); seq=SEQ.search(t); seq=seq.group(1) if seq else ""; u=t.upper()
    if not t:return Info(n,"blank","",0,0,seq,"remove","blank")
    if PREDRAW.search(t):return Info(n,"predraw",tid(t),1,1,seq,"remove","Pre-Draw")
    ei,ep=EO_ID.search(t),EO_PAGE.search(t)
    if ei and ep:return Info(n,"engineering_order",ei.group(1).rstrip("_.-"),int(ep.group(1)),int(ep.group(2)),seq,"keep","EO")
    fp=FORM_PAGE.search(t)
    if fp and any(x in u for x in ("DAILY CHECK","WEEKLY CHECK","FORM N","FORM Nº")):
        return Info(n,"check_form",tid(t,"CHECK_FORM"),int(fp.group(1)),int(fp.group(2)),seq,"keep","check")
    tp=TASK_PAGE.search(t)
    if tp and "TASK CARD" in u:return Info(n,"task_card",tid(t,"TASK_CARD"),int(tp.group(1)),int(tp.group(2)),seq,"keep","task")
    return Info(n,"attachment",tid(t),0,0,seq,"remove","attachment")
def blocks(infos):
    result=[]; cur=None; known={"task_card","engineering_order","check_form"}
    for x in infos:
        if x.kind not in known: continue
        cont=cur and x.kind==cur.kind and x.page_no==cur.pages[-1].page_no+1 and x.page_total==cur.page_total
        if x.page_no==1 or not cont:
            if cur: result.append(cur)
            cur=Block(x.kind,x.doc_id,x.page_total,[x])
        else: cur.pages.append(x)
    if cur: result.append(cur)
    for i,b in enumerate(result):
        if len(b.pages)!=b.page_total:
            b.keep=False;b.reason="incomplete"
        elif b.kind=="task_card" and b.page_total==1 and i+1<len(result):
            q=result[i+1]
            if q.pages[0].source_page==b.pages[-1].source_page+1 and q.kind in {"engineering_order","check_form"}:
                b.keep=False;b.reason="wrapper"
        if not b.keep:
            for x in b.pages:x.decision="remove";x.reason=b.reason
    return result
def output_name(path):
    stem=Path(path).stem
    if stem.endswith(" F") or stem.endswith(" D"): stem=stem[:-2]
    return f"{stem} D.pdf"
def edit_pdf(src,outdir):
    r=PdfReader(str(src),strict=False); infos=[classify(p.extract_text() or "",i) for i,p in enumerate(r.pages,1)]; bs=blocks(infos)
    keep=[b for b in bs if b.keep]
    if not keep: raise ValueError("No se detectaron documentos completos")
    w=PdfWriter(); blanks=[]
    for b in keep:
        for x in b.pages:w.add_page(r.pages[x.source_page-1])
        if len(b.pages)%2:
            p=r.pages[b.pages[-1].source_page-1];w.add_blank_page(width=float(p.mediabox.width),height=float(p.mediabox.height));blanks.append(b.doc_id)
    outdir.mkdir(parents=True,exist_ok=True); out=outdir/output_name(src)
    with out.open("wb") as f:w.write(f)
    report={"version":"7.0","input":src.name,"output":out.name,"input_pages":len(r.pages),"output_pages":len(w.pages),"blocks":[{"kind":b.kind,"doc_id":b.doc_id,"source_pages":[x.source_page for x in b.pages],"keep":b.keep,"reason":b.reason} for b in bs],"all_pages":[asdict(x) for x in infos],"inserted_blank_after":blanks}
    out.with_suffix(".audit.json").write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8"); return out
@st.cache_data(show_spinner=False,max_entries=30)
def process(name,data,digest):
    del digest
    with tempfile.TemporaryDirectory() as d:
        root=Path(d); src=root/Path(name).name;src.write_bytes(data);out=edit_pdf(src,root/"out"); audit=out.with_suffix(".audit.json"); obj=json.loads(audit.read_text())
        pdf=out.read_bytes(); assert len(PdfReader(io.BytesIO(pdf),strict=False).pages)==obj["output_pages"]
        return {"input_name":Path(name).name,"pdf_name":output_name(Path(name)),"pdf_data":pdf,"audit_name":Path(output_name(Path(name))).with_suffix(".audit.json").name,"audit_data":audit.read_bytes(),"input_pages":obj["input_pages"],"output_pages":obj["output_pages"]}
def make_zip(results):
    b=io.BytesIO()
    with zipfile.ZipFile(b,"w",zipfile.ZIP_DEFLATED,allowZip64=True) as z:
        rows=[["entrada","salida","paginas_entrada","paginas_salida"]]
        for x in results:z.writestr("PDF_EDITADOS/"+x["pdf_name"],x["pdf_data"]);z.writestr("AUDITORIAS/"+x["audit_name"],x["audit_data"]);rows.append([x["input_name"],x["pdf_name"],x["input_pages"],x["output_pages"]])
        s=io.StringIO();csv.writer(s).writerows(rows);z.writestr("MANIFIESTO.csv",s.getvalue().encode("utf-8-sig"))
    data=b.getvalue()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        if z.testzip():raise ValueError("ZIP corrupto")
    return data
st.set_page_config(page_title="Editor PDF Tecnico",page_icon="📄",layout="centered")
st.title("Editor de documentacion tecnica PDF")
st.caption("Procesamiento PDF y descarga multiple verificada")
files=st.file_uploader("Selecciona uno o varios PDF",type=["pdf"],accept_multiple_files=True)
if not files:st.info("Carga al menos un PDF para comenzar.")
elif st.button("Procesar archivos",type="primary",use_container_width=True):
    res=[];errors=[];bar=st.progress(0)
    for i,f in enumerate(files,1):
        try:
            data=f.getvalue();res.append(process(f.name,data,hashlib.sha256(data).hexdigest()))
        except Exception as e:errors.append(f"{f.name}: {e}")
        bar.progress(i/len(files))
    bar.empty();st.session_state.results=res;st.session_state.errors=errors
res=st.session_state.get("results",[]);errors=st.session_state.get("errors",[])
if res:
    z=make_zip(res);st.success(f"Listo: {len(res)} PDF procesados")
    st.download_button("Descargar carpeta ZIP con todos los PDF",z,"PDF_EDITADOS.zip","application/zip",use_container_width=True)
    for i,x in enumerate(res):
        with st.container(border=True):
            st.write(f"**{x['input_name']}**: {x['input_pages']} -> {x['output_pages']} paginas");c1,c2=st.columns(2)
            c1.download_button("Descargar PDF",x["pdf_data"],x["pdf_name"],"application/pdf",key=f"p{i}")
            c2.download_button("Auditoria",x["audit_data"],x["audit_name"],"application/json",key=f"a{i}")
if errors:
    st.error("Algunos archivos no se procesaron")
    for e in errors:st.code(e)
