#!/usr/bin/env python3
"""
Stamp Application - Add digital stamps to DOCX files and convert to PDF
"""
import os
import uuid
import subprocess
import shutil
from flask import Flask, request, jsonify, send_file, render_template
from PIL import Image
import io

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max

STAMPS_DIR = os.path.join(os.path.dirname(__file__), 'stamps')
UPLOAD_DIR = '/tmp/stamp_uploads'
os.makedirs(UPLOAD_DIR, exist_ok=True)

STAMPS = {
    'kazybai': {'file': 'Казыбай_АН.png', 'label': 'ИП Казыбай А.Н.'},
    'alfa': {'file': 'Alfa_and_Omega.png', 'label': 'Alfa and Omega'},
    'tempstroy': {'file': 'Temp_Stroy.png', 'label': 'ИП Керимбеков Temp Stroy'},
}


@app.route('/')
def index():
    return render_template('index.html', stamps=STAMPS)


@app.route('/process', methods=['POST'])
def process():
    if 'docx' not in request.files:
        return jsonify({'error': 'Файл не загружен'}), 400

    docx_file = request.files['docx']
    stamp_key = request.form.get('stamp')
    position = request.form.get('position', 'bottom-left')  # bottom-left, bottom-right, bottom-center

    if not stamp_key or stamp_key not in STAMPS:
        return jsonify({'error': 'Печать не выбрана'}), 400

    if not docx_file.filename.endswith('.docx'):
        return jsonify({'error': 'Нужен файл формата .docx'}), 400

    # Create working directory
    job_id = str(uuid.uuid4())
    job_dir = os.path.join(UPLOAD_DIR, job_id)
    os.makedirs(job_dir)

    try:
        # Save uploaded docx
        docx_path = os.path.join(job_dir, 'input.docx')
        docx_file.save(docx_path)

        # Convert docx -> pdf via LibreOffice
        result = subprocess.run(
            ['libreoffice', '--headless', '--convert-to', 'pdf', '--outdir', job_dir, docx_path],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode != 0:
            return jsonify({'error': f'Ошибка конвертации: {result.stderr}'}), 500

        pdf_path = os.path.join(job_dir, 'input.pdf')
        if not os.path.exists(pdf_path):
            return jsonify({'error': 'PDF не создан'}), 500

        # Add stamp to PDF using pypdf + reportlab
        stamp_path = os.path.join(STAMPS_DIR, STAMPS[stamp_key]['file'])
        output_path = os.path.join(job_dir, 'output.pdf')

        add_stamp_to_pdf(pdf_path, stamp_path, output_path, position)

        # Return the stamped PDF
        original_name = os.path.splitext(docx_file.filename)[0]
        download_name = f'{original_name}_с_печатью.pdf'

        return send_file(
            output_path,
            as_attachment=True,
            download_name=download_name,
            mimetype='application/pdf'
        )

    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        # Cleanup after a delay (let file be sent first)
        pass


def add_stamp_to_pdf(pdf_path, stamp_path, output_path, position='bottom-left'):
    """Add stamp image to last page of PDF."""
    from pypdf import PdfReader, PdfWriter
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4

    reader = PdfReader(pdf_path)
    last_page = reader.pages[-1]
    page_width = float(last_page.mediabox.width)
    page_height = float(last_page.mediabox.height)

    # Load stamp image to get aspect ratio
    stamp_img = Image.open(stamp_path).convert('RGBA')
    img_w, img_h = stamp_img.size
    aspect = img_w / img_h

    # Stamp size: 5cm wide
    stamp_width_pt = 150  # ~5.3 cm in points
    stamp_height_pt = stamp_width_pt / aspect

    margin = 40  # points from edge

    # Position
    if position == 'bottom-left':
        x = margin
        y = margin
    elif position == 'bottom-right':
        x = page_width - stamp_width_pt - margin
        y = margin
    else:  # bottom-center
        x = (page_width - stamp_width_pt) / 2
        y = margin

    # Create stamp overlay PDF in memory
    stamp_pdf_buf = io.BytesIO()
    c = canvas.Canvas(stamp_pdf_buf, pagesize=(page_width, page_height))

    # Save stamp as temp PNG for reportlab
    tmp_stamp = '/tmp/stamp_overlay.png'
    stamp_img.save(tmp_stamp, format='PNG')

    c.drawImage(tmp_stamp, x, y, width=stamp_width_pt, height=stamp_height_pt, mask='auto')
    c.save()
    stamp_pdf_buf.seek(0)

    # Merge stamp onto last page
    from pypdf import PdfReader as PR
    stamp_reader = PR(stamp_pdf_buf)
    stamp_page = stamp_reader.pages[0]

    writer = PdfWriter()

    # All pages except last — unchanged
    for i in range(len(reader.pages) - 1):
        writer.add_page(reader.pages[i])

    # Last page: merge stamp
    last_page.merge_page(stamp_page)
    writer.add_page(last_page)

    with open(output_path, 'wb') as f:
        writer.write(f)


if __name__ == '__main__':
    print("=" * 50)
    print("Stamp Application запущена!")
    print("Откройте браузер: http://localhost:5000")
    print("=" * 50)
    app.run(debug=False, port=5000, host='0.0.0.0')


@app.route('/stamp_image/<key>')
def stamp_image(key):
    if key not in STAMPS:
        return '', 404
    path = os.path.join(STAMPS_DIR, STAMPS[key]['file'])
    return send_file(path, mimetype='image/png')
