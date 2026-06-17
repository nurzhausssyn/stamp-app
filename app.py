#!/usr/bin/env python3
"""
Stamp Application v3 - Multi-page, multi-stamp, page deletion
"""
import os
import uuid
import subprocess
import json
from flask import Flask, request, jsonify, send_file, render_template
from PIL import Image
import io

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024

STAMPS_DIR = os.path.join(os.path.dirname(__file__), 'stamps')
UPLOAD_DIR = '/tmp/stamp_uploads'
os.makedirs(UPLOAD_DIR, exist_ok=True)

STAMPS = {
    'kazybai': {'file': 'Печать Казыбай А.Н..png', 'label': 'ИП Казыбай А.Н.'},
    'alfa': {'file': 'Печать Alfa and Omega.png', 'label': 'Alfa and Omega'},
    'tempstroy': {'file': 'Печать Temp Stroy.png', 'label': 'ИП Керимбеков Temp Stroy'},
    'orley': {'file': 'орлеу.png', 'label': 'ТОО Орлеу'},
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
    if 'docx' not in request.files:
        return jsonify({'error': 'Файл не загружен'}), 400
    docx_file = request.files['docx']
    if not docx_file.filename.endswith('.docx'):
        return jsonify({'error': 'Нужен файл .docx'}), 400

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

    # Get page count
    from pypdf import PdfReader
    reader = PdfReader(pdf_path)
    page_count = len(reader.pages)

    return jsonify({'job_id': job_id, 'page_count': page_count})


@app.route('/preview_pdf/<job_id>')
def preview_pdf(job_id):
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
    stamps = data.get('stamps', [])       # list of {page, stamp_key, x, y, size}
    deleted_pages = data.get('deleted_pages', [])  # list of 1-based page numbers to remove
    filename = data.get('filename', 'document')

    if not job_id or not all(c in '0123456789abcdef-' for c in job_id):
        return jsonify({'error': 'Неверный job_id'}), 400

    pdf_path = os.path.join(UPLOAD_DIR, job_id, 'input.pdf')
    if not os.path.exists(pdf_path):
        return jsonify({'error': 'PDF не найден'}), 404

    output_path = os.path.join(UPLOAD_DIR, job_id, 'output.pdf')

    try:
        build_output_pdf(pdf_path, output_path, stamps, deleted_pages)
    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'trace': traceback.format_exc()}), 500

    return jsonify({'download_url': f'/download/{job_id}/{filename}'})


def build_output_pdf(pdf_path, output_path, stamps_data, deleted_pages):
    from pypdf import PdfReader, PdfWriter
    from reportlab.pdfgen import canvas as rl_canvas

    reader = PdfReader(pdf_path)
    total = len(reader.pages)
    deleted_set = set(deleted_pages)  # 1-based

    writer = PdfWriter()

    for page_num in range(1, total + 1):
        if page_num in deleted_set:
            continue

        page = reader.pages[page_num - 1]
        page_width = float(page.mediabox.width)
        page_height = float(page.mediabox.height)

        # Find stamps for this page
        page_stamps = [s for s in stamps_data if s.get('page') == page_num]

        if page_stamps:
            # Build overlay PDF with all stamps for this page
            buf = io.BytesIO()
            c = rl_canvas.Canvas(buf, pagesize=(page_width, page_height))

            for s in page_stamps:
                stamp_key = s.get('stamp')
                if stamp_key not in STAMPS:
                    continue
                stamp_path = os.path.join(STAMPS_DIR, STAMPS[stamp_key]['file'])
                stamp_img = Image.open(stamp_path).convert('RGBA')
                iw, ih = stamp_img.size
                aspect = iw / ih

                stamp_w = page_width * (s.get('size', 20) / 100)
                stamp_h = stamp_w / aspect

                x_pct = s.get('x', 0)
                y_pct = s.get('y', 0)
                x = page_width * (x_pct / 100)
                # y_pct from top-left; PDF y=0 is bottom
                y = page_height - page_height * (y_pct / 100) - stamp_h

                tmp = f'/tmp/stamp_{os.getpid()}_{stamp_key}.png'
                stamp_img.save(tmp, format='PNG')
                c.drawImage(tmp, x, y, width=stamp_w, height=stamp_h, mask='auto')

            c.save()
            buf.seek(0)

            from pypdf import PdfReader as PR
            overlay_reader = PR(buf)
            overlay_page = overlay_reader.pages[0]
            page.merge_page(overlay_page)

        writer.add_page(page)

    with open(output_path, 'wb') as f:
        writer.write(f)


@app.route('/download/<job_id>/<filename>')
def download(job_id, filename):
    if not all(c in '0123456789abcdef-' for c in job_id):
        return '', 400
    output_path = os.path.join(UPLOAD_DIR, job_id, 'output.pdf')
    if not os.path.exists(output_path):
        return '', 404
    return send_file(output_path, as_attachment=True,
                     download_name=f'{filename}_с_печатью.pdf',
                     mimetype='application/pdf')


if __name__ == '__main__':
    print("Stamp App v3 → http://localhost:5000")
    app.run(debug=False, port=5000, host='0.0.0.0')
