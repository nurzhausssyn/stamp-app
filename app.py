#!/usr/bin/env python3
"""
Stamp Application - Add digital stamps to DOCX files and convert to PDF
"""
import os
import uuid
import subprocess
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


@app.route('/stamp_image/<key>')
def stamp_image(key):
    if key not in STAMPS:
        return '', 404
    path = os.path.join(STAMPS_DIR, STAMPS[key]['file'])
    return send_file(path, mimetype='image/png')


@app.route('/convert', methods=['POST'])
def convert():
    """Convert DOCX to PDF and return it for preview."""
    if 'docx' not in request.files:
        return jsonify({'error': 'Файл не загружен'}), 400

    docx_file = request.files['docx']
    if not docx_file.filename.endswith('.docx'):
        return jsonify({'error': 'Нужен файл формата .docx'}), 400

    job_id = str(uuid.uuid4())
    job_dir = os.path.join(UPLOAD_DIR, job_id)
    os.makedirs(job_dir)

    docx_path = os.path.join(job_dir, 'input.docx')
    docx_file.save(docx_path)

    result = subprocess.run(
        ['libreoffice', '--headless', '--convert-to', 'pdf', '--outdir', job_dir, docx_path],
        capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        return jsonify({'error': f'Ошибка конвертации: {result.stderr}'}), 500

    pdf_path = os.path.join(job_dir, 'input.pdf')
    if not os.path.exists(pdf_path):
        return jsonify({'error': 'PDF не создан'}), 500

    # Return job_id so we can reference it later
    return jsonify({'job_id': job_id})


@app.route('/preview_pdf/<job_id>')
def preview_pdf(job_id):
    # Security: only alphanumeric and dashes
    if not all(c in '0123456789abcdef-' for c in job_id):
        return '', 400
    pdf_path = os.path.join(UPLOAD_DIR, job_id, 'input.pdf')
    if not os.path.exists(pdf_path):
        return '', 404
    return send_file(pdf_path, mimetype='application/pdf')


@app.route('/process', methods=['POST'])
def process():
    data = request.get_json()
    job_id = data.get('job_id')
    stamp_key = data.get('stamp')
    # x, y in percent of page (0-100), from bottom-left
    x_pct = float(data.get('x', 10))
    y_pct = float(data.get('y', 5))
    stamp_size_pct = float(data.get('size', 20))  # stamp width as % of page width
    filename = data.get('filename', 'document')

    if not job_id or not all(c in '0123456789abcdef-' for c in job_id):
        return jsonify({'error': 'Неверный job_id'}), 400
    if not stamp_key or stamp_key not in STAMPS:
        return jsonify({'error': 'Печать не выбрана'}), 400

    pdf_path = os.path.join(UPLOAD_DIR, job_id, 'input.pdf')
    if not os.path.exists(pdf_path):
        return jsonify({'error': 'PDF не найден'}), 404

    stamp_path = os.path.join(STAMPS_DIR, STAMPS[stamp_key]['file'])
    output_path = os.path.join(UPLOAD_DIR, job_id, 'output.pdf')

    try:
        add_stamp_to_pdf(pdf_path, stamp_path, output_path, x_pct, y_pct, stamp_size_pct)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    return jsonify({'download_url': f'/download/{job_id}/{filename}'})


@app.route('/download/<job_id>/<filename>')
def download(job_id, filename):
    if not all(c in '0123456789abcdef-' for c in job_id):
        return '', 400
    output_path = os.path.join(UPLOAD_DIR, job_id, 'output.pdf')
    if not os.path.exists(output_path):
        return '', 404
    download_name = f'{filename}_с_печатью.pdf'
    return send_file(output_path, as_attachment=True, download_name=download_name, mimetype='application/pdf')


def add_stamp_to_pdf(pdf_path, stamp_path, output_path, x_pct, y_pct, stamp_size_pct):
    """Add stamp image to last page of PDF at given percent position."""
    from pypdf import PdfReader, PdfWriter
    from reportlab.pdfgen import canvas

    reader = PdfReader(pdf_path)
    last_page = reader.pages[-1]
    page_width = float(last_page.mediabox.width)
    page_height = float(last_page.mediabox.height)

    stamp_img = Image.open(stamp_path).convert('RGBA')
    img_w, img_h = stamp_img.size
    aspect = img_w / img_h

    stamp_width_pt = page_width * (stamp_size_pct / 100)
    stamp_height_pt = stamp_width_pt / aspect

    # x_pct, y_pct are from top-left of page in preview
    # PDF y=0 is bottom, so flip y
    x = page_width * (x_pct / 100)
    y = page_height - page_height * (y_pct / 100) - stamp_height_pt

    stamp_pdf_buf = io.BytesIO()
    c = canvas.Canvas(stamp_pdf_buf, pagesize=(page_width, page_height))

    tmp_stamp = f'/tmp/stamp_{os.getpid()}.png'
    stamp_img.save(tmp_stamp, format='PNG')
    c.drawImage(tmp_stamp, x, y, width=stamp_width_pt, height=stamp_height_pt, mask='auto')
    c.save()
    stamp_pdf_buf.seek(0)

    from pypdf import PdfReader as PR
    stamp_reader = PR(stamp_pdf_buf)
    stamp_page = stamp_reader.pages[0]

    writer = PdfWriter()
    for i in range(len(reader.pages) - 1):
        writer.add_page(reader.pages[i])

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
