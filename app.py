import math
import os
import shutil
import subprocess
import tempfile
from io import BytesIO
from pathlib import Path

import streamlit as st
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4, A3, landscape, portrait
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader

GS = chr(29)


def find_zint():
    zint_path = shutil.which("zint")
    if zint_path:
        return zint_path

    possible = [
        "/usr/bin/zint",
        "/usr/local/bin/zint",
        "C:\\Program Files\\Zint\\zint.exe",
        "C:\\Program Files (x86)\\Zint\\zint.exe",
    ]

    for p in possible:
        if os.path.exists(p):
            return p

    return None


def read_codes(uploaded_file):
    lines = uploaded_file.read().splitlines()
    codes = []

    for line in lines:
        if not line.strip():
            continue

        text = line.decode("utf-8", errors="replace").strip()

        if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
            text = text[1:-1].replace('""', '"')

        text = text.replace("<GS>", GS)

        if text:
            codes.append(text)

    return codes


def gs1_to_zint_text(code):
    code = code.replace("<GS>", GS).strip()

    if not code.startswith("01"):
        return code

    gtin = code[2:16]
    rest = code[16:]

    if not rest.startswith("21"):
        return f"[01]{gtin}{rest}"

    rest = rest[2:]
    parts = rest.split(GS)

    serial = parts[0] if len(parts) > 0 else ""
    crypto91 = ""
    crypto92 = ""

    for p in parts[1:]:
        if p.startswith("91"):
            crypto91 = p[2:]
        elif p.startswith("92"):
            crypto92 = p[2:]

    out = f"[01]{gtin}[21]{serial}"

    if crypto91:
        out += f"[91]{crypto91}"

    if crypto92:
        out += f"[92]{crypto92}"

    return out


def make_datamatrix_png(zint_path, code, out_png):
    zint_text = gs1_to_zint_text(code)

    cmd = [
        zint_path,
        "-b", "71",
        "--gs1",
        "--scale", "4",
        "--border", "0",
        "--output", str(out_png),
        "--data", zint_text,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        raise RuntimeError("Zint error: " + (result.stderr or result.stdout or "unknown error"))


def create_pdf(
    codes,
    page_size_name,
    orientation,
    label_width_mm,
    label_height_mm,
    margin_mm,
    gap_mm,
    dm_percent,
    show_border,
):
    zint_path = find_zint()

    if not zint_path:
        raise RuntimeError("Zint bulunamadı. Streamlit Cloud için repo içine packages.txt ekleyin ve içine sadece zint yazın.")

    page_size = A3 if page_size_name == "A3" else A4
    page_size = landscape(page_size) if orientation == "landscape" else portrait(page_size)

    page_w, page_h = page_size

    label_w = label_width_mm * mm
    label_h = label_height_mm * mm
    margin = margin_mm * mm
    gap = gap_mm * mm

    cols = int((page_w - 2 * margin + gap) // (label_w + gap))
    rows = int((page_h - 2 * margin + gap) // (label_h + gap))

    if cols <= 0 or rows <= 0:
        raise ValueError("Etiket sayfaya sığmıyor. Ölçüleri küçültün.")

    labels_per_page = cols * rows

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=page_size)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        for i, code in enumerate(codes):
            pos = i % labels_per_page
            row = pos // cols
            col = pos % cols

            x = margin + col * (label_w + gap)
            y = page_h - margin - ((row + 1) * label_h) - (row * gap)

            dm_size = min(label_w, label_h) * (dm_percent / 100.0)
            dm_x = x + (label_w - dm_size) / 2
            dm_y = y + (label_h - dm_size) / 2

            png_path = tmp_path / f"dm_{i}.png"
            make_datamatrix_png(zint_path, code, png_path)

            img = ImageReader(str(png_path))
            pdf.drawImage(img, dm_x, dm_y, width=dm_size, height=dm_size, preserveAspectRatio=True, mask="auto")

            if show_border:
                pdf.rect(x, y, label_w, label_h)

            if (i + 1) % labels_per_page == 0:
                pdf.showPage()

    if len(codes) % labels_per_page != 0:
        pdf.showPage()

    pdf.save()
    buffer.seek(0)

    info = {
        "cols": cols,
        "rows": rows,
        "labels_per_page": labels_per_page,
        "total_pages": math.ceil(len(codes) / labels_per_page),
        "total_labels": len(codes),
    }

    return buffer.getvalue(), info


def main():
    st.set_page_config(page_title="GS1 DataMatrix PDF Generator", layout="wide")
    st.title("GS1 DataMatrix PDF Generator - Zint GS1 Mode")
    st.write("CSV içindeki kodları gerçek GS1 DataMatrix olarak üretir ve A4/A3 PDF'e dizer.")

    uploaded_file = st.file_uploader("correct_gs1_for_datamatrix.csv yükle", type=["csv", "txt"])

    col1, col2, col3 = st.columns(3)

    with col1:
        page_size_name = st.selectbox("Sayfa", ["A4", "A3"], index=0)
        orientation = st.selectbox("Yön", ["portrait", "landscape"], index=0)
        label_width_mm = st.number_input("Etiket genişliği mm", 5.0, 200.0, 25.0)

    with col2:
        label_height_mm = st.number_input("Etiket yüksekliği mm", 5.0, 200.0, 25.0)
        margin_mm = st.number_input("Kenar boşluğu mm", 0.0, 50.0, 5.0)
        gap_mm = st.number_input("Etiket arası boşluk mm", 0.0, 50.0, 2.0)

    with col3:
        dm_percent = st.slider("DataMatrix boyutu %", 40, 100, 80)
        show_border = st.checkbox("Etiket sınır çizgisi", value=True)

    if not uploaded_file:
        st.info("CSV/TXT dosyasını yükleyin.")
        st.stop()

    codes = read_codes(uploaded_file)

    if not codes:
        st.error("Kod bulunamadı.")
        st.stop()

    st.success(f"{len(codes)} kod yüklendi.")

    zint_path = find_zint()
    if zint_path:
        st.info(f"Zint bulundu: {zint_path}")
    else:
        st.error("Zint bulunamadı. Streamlit Cloud repo içine packages.txt ekleyin ve içine zint yazın.")
        st.stop()

    with st.spinner("GS1 DataMatrix PDF oluşturuluyor..."):
        try:
            pdf_data, info = create_pdf(
                codes=codes,
                page_size_name=page_size_name,
                orientation=orientation,
                label_width_mm=label_width_mm,
                label_height_mm=label_height_mm,
                margin_mm=margin_mm,
                gap_mm=gap_mm,
                dm_percent=dm_percent,
                show_border=show_border,
            )
        except Exception as e:
            st.error(str(e))
            st.stop()

    st.write(
        f"Kolon: {info['cols']} | Satır: {info['rows']} | "
        f"Sayfa başı etiket: {info['labels_per_page']} | "
        f"Toplam sayfa: {info['total_pages']}"
    )

    st.download_button("PDF indir", data=pdf_data, file_name="gs1_datamatrix_labels.pdf", mime="application/pdf")

    with st.expander("İlk 5 kod kontrol"):
        for code in codes[:5]:
            st.code(code.replace(GS, "<GS>"))
            st.code(gs1_to_zint_text(code))


if __name__ == "__main__":
    main()
