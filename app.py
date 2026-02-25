from flask import Flask, request, render_template, jsonify, send_from_directory, flash, redirect, url_for
from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField
from wtforms.validators import DataRequired, Email, Length
from pymongo import MongoClient
import pandas as pd
import qrcode
from PIL import Image, ImageDraw, ImageFont
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
import os
import uuid
from werkzeug.utils import secure_filename
from config import Config
import certifi
from datetime import datetime

app = Flask(__name__)
app.config.from_object(Config)
app.secret_key = app.config['SECRET_KEY']

# MongoDB Atlas client
ca = certifi.where()
client = MongoClient(app.config['MONGO_URI'], tlsCAFile=ca)
db = client.event_tickets
tickets = db.tickets

# Folders
UPLOAD_FOLDER = 'uploads'
QR_FOLDER = 'qrcodes'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(QR_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['QR_FOLDER'] = QR_FOLDER
ALLOWED_EXTENSIONS = {'xlsx'}


# WTForms manual form
class ManualTicketForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired(), Length(min=2)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    event = StringField('Event Name', validators=[DataRequired(), Length(min=1)])
    phone = StringField('Phone', validators=[Length(min=10)])
    submit = SubmitField('Generate Ticket')


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


# --- TEXT SIZE HELPER (no textsize error) ---
def measure_text(draw, text, font):
    try:
        bbox = font.getbbox(text)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
    except Exception:
        try:
            w = draw.textlength(text, font=font)
            h = font.size if hasattr(font, "size") else 16
        except Exception:
            w, h = 0, 0
    return w, h


# --- TICKET DESIGN ---
def create_ticket_image(ticket_id, name, event, qr_data, save_path):
    # X-Kernel color palette
    bg_color = "#0f172a"       # page / outer
    card_color = "#020617"     # main card
    accent_color = "#38bdf8"   # accent / highlights
    text_primary = "#e5e7eb"   # main text
    text_muted = "#64748b"     # secondary text

    # 1. Generate QR code
    qr = qrcode.QRCode(version=1, box_size=6, border=2)
    qr.add_data(qr_data)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")

    qr_size = 260
    qr_img = qr_img.resize((qr_size, qr_size))

    # 2. Create ticket canvas
    width, height = 900, 350
    ticket = Image.new("RGB", (width, height), color=bg_color)
    draw = ImageDraw.Draw(ticket)

    # 3. Main card rectangle
    margin = 20
    card_rect = [margin, margin, width - margin, height - margin]
    draw.rounded_rectangle(card_rect, radius=24, fill=card_color)

    # 4. Accent bar on left
    accent_width = 10
    draw.rounded_rectangle(
        [card_rect[0], card_rect[1], card_rect[0] + accent_width, card_rect[3]],
        radius=8,
        fill=accent_color
    )

    # 5. Fonts
    try:
        font_title = ImageFont.truetype("arialbd.ttf", 32)
        font_label = ImageFont.truetype("arial.ttf", 18)
        font_value = ImageFont.truetype("arial.ttf", 20)
        font_small = ImageFont.truetype("arial.ttf", 14)
    except Exception:
        font_title = ImageFont.load_default()
        font_label = ImageFont.load_default()
        font_value = ImageFont.load_default()
        font_small = ImageFont.load_default()

    # 6. Left side text area
    padding_left = card_rect[0] + accent_width + 24
    padding_top = card_rect[1] + 24
    line_gap = 34

    # Event tag
    tag_text = "X-Kernel Events"
    tag_box_w, tag_box_h = 160, 30
    draw.rounded_rectangle(
        [padding_left, padding_top, padding_left + tag_box_w, padding_top + tag_box_h],
        radius=12,
        fill="#020617",
        outline=accent_color,
        width=2
    )
    tw, th = measure_text(draw, tag_text, font_small)
    draw.text(
        (padding_left + (tag_box_w - tw) / 2, padding_top + (tag_box_h - th) / 2),
        tag_text,
        font=font_small,
        fill=accent_color
    )

    # Main title
    title_y = padding_top + tag_box_h + 12
    draw.text((padding_left, title_y), "Event Access Pass", font=font_title, fill=text_primary)

    # Info lines
    info_y = title_y + line_gap * 1.6

    draw.text((padding_left, info_y), "Name", font=font_label, fill=text_muted)
    draw.text((padding_left + 140, info_y), str(name), font=font_value, fill=text_primary)

    info_y += line_gap
    draw.text((padding_left, info_y), "Event", font=font_label, fill=text_muted)
    draw.text((padding_left + 140, info_y), str(event), font=font_value, fill=text_primary)

    info_y += line_gap
    draw.text((padding_left, info_y), "Ticket ID", font=font_label, fill=text_muted)
    draw.text((padding_left + 140, info_y), ticket_id, font=font_value, fill=text_primary)

    # Footer
    footer_y = card_rect[3] - 50
    footer_text = "Show this pass at entry • QR is mandatory • Issued by X-Kernel Web Dev Team"
    draw.text((padding_left, footer_y), footer_text, font=font_small, fill=text_muted)

    # 7. QR section on right
    qr_x = card_rect[2] - qr_size - 40
    qr_y = (height - qr_size) // 2
    ticket.paste(qr_img, (qr_x, qr_y))

    # Small caption under QR
    qr_caption = "Scan at gate"
    qc_w, qc_h = measure_text(draw, qr_caption, font_small)
    draw.text(
        (qr_x + (qr_size - qc_w) / 2, qr_y + qr_size + 8),
        qr_caption,
        font=font_small,
        fill=text_muted
    )

    ticket.save(save_path)


def send_ticket_email(email, name, ticket_id, qr_path, event):
    msg = MIMEMultipart()
    msg['From'] = app.config['EMAIL_USER']
    msg['To'] = email
    msg['Subject'] = f'Your X-Kernel Ticket for {event} - ID: {ticket_id}'

    body = (
        f"Dear {name},\n\n"
        f"Your X-Kernel event ticket is attached.\n\n"
        f"Ticket ID: {ticket_id}\n"
        f"Event: {event}\n\n"
        f"Please show this ticket at the event entrance.\n\n"
        f"Regards,\n"
        f"X-Kernel Team"
    )
    msg.attach(MIMEText(body, 'plain'))

    with open(qr_path, 'rb') as f:
        img_data = f.read()
        img = MIMEImage(img_data)
        img.add_header('Content-Disposition', 'attachment', filename=f'{ticket_id}_ticket.png')
        msg.attach(img)

    try:
        server = smtplib.SMTP(app.config['MAIL_SERVER'], app.config['MAIL_PORT'])
        server.starttls()
        server.login(app.config['EMAIL_USER'], app.config['EMAIL_PASS'])
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        print(f"Email error: {e}")
        return False


@app.route('/', methods=['GET', 'POST'])
def upload_excel():
    form = ManualTicketForm()

    # Manual form handling
    if form.validate_on_submit():
        ticket_id = str(uuid.uuid4())[:8].upper()
        ticket = {
            'ticket_id': ticket_id,
            'name': form.name.data,
            'email': form.email.data,
            'event': form.event.data,
            'phone': form.phone.data or '',
            'used': False,
            'scanned_at': None
        }
        tickets.insert_one(ticket)

        # Generate ticket image
        qr_data = f"TICKET:{ticket_id}:{form.event.data}"
        qr_path = os.path.join(app.config['QR_FOLDER'], f"{ticket_id}.png")
        create_ticket_image(ticket_id, form.name.data, form.event.data, qr_data, qr_path)

        # Send email
        email_sent = send_ticket_email(
            form.email.data,
            form.name.data,
            ticket_id,
            qr_path,
            form.event.data
        )
        status = "and emailed" if email_sent else "(email failed)"
        flash(f'Ticket {ticket_id} generated {status}!')
        return redirect(url_for('upload_excel'))

    # Excel upload handling
    if 'file' in request.files:
        file = request.files['file']
        if file.filename != '' and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)

            try:
                df = pd.read_excel(filepath)
                required_cols = ['Name', 'Email', 'Event Name', 'Phone']
                if not all(col in df.columns for col in required_cols):
                    flash('Excel must have columns: Name, Email, Event Name, Phone')
                    os.remove(filepath)
                    return redirect(url_for('upload_excel'))

                success_count = 0
                for idx, row in df.iterrows():
                    ticket_id = str(uuid.uuid4())[:8].upper()
                    name = str(row['Name']).strip()
                    event = str(row['Event Name']).strip()
                    email = str(row['Email']).strip()
                    phone = str(row['Phone']).strip() if pd.notna(row['Phone']) else ''

                    ticket = {
                        'ticket_id': ticket_id,
                        'name': name,
                        'email': email,
                        'event': event,
                        'phone': phone,
                        'used': False,
                        'scanned_at': None
                    }
                    tickets.insert_one(ticket)

                    qr_data = f"TICKET:{ticket_id}:{event}"
                    qr_path = os.path.join(app.config['QR_FOLDER'], f"{ticket_id}.png")
                    create_ticket_image(ticket_id, name, event, qr_data, qr_path)

                    send_ticket_email(
                        email,
                        name,
                        ticket_id,
                        qr_path,
                        event
                    )
                    success_count += 1

                flash(f'Successfully processed {success_count} tickets from Excel!')
            except Exception as e:
                flash(f'Error processing Excel: {str(e)}')
            finally:
                if os.path.exists(filepath):
                    os.remove(filepath)

            return redirect(url_for('upload_excel'))

    return render_template('upload.html', form=form)


@app.route('/scanner')
def scanner():
    return render_template('scanner.html')


@app.route('/verify', methods=['POST'])
def verify_ticket():
    data = request.json.get('ticket_data')
    if not data or not data.startswith('TICKET:'):
        return jsonify({'valid': False, 'message': 'Invalid QR format'}), 400

    parts = data.split(':')
    if len(parts) != 3:
        return jsonify({'valid': False, 'message': 'Invalid QR data'}), 400

    ticket_id, event = parts[1], parts[2]
    ticket = tickets.find_one({'ticket_id': ticket_id})

    if not ticket:
        return jsonify({'valid': False, 'message': 'Invalid Ticket'}), 400

    # Already used → still send all details
    if ticket['used']:
        return jsonify({
            'valid': False,
            'message': 'Already Used',
            'name': ticket.get('name'),
            'event': ticket.get('event'),
            'ticket_id': ticket.get('ticket_id'),
            'scanned_at': ticket.get('scanned_at')
        }), 400

    # First-time valid scan
    tickets.update_one(
        {'_id': ticket['_id']},
        {'$set': {'used': True, 'scanned_at': datetime.utcnow().isoformat()}}
    )
    return jsonify({
        'valid': True,
        'message': 'Valid Ticket - Welcome!',
        'name': ticket['name'],
        'event': ticket['event'],
        'ticket_id': ticket_id
    })



@app.route('/report')
def report_page():
    return render_template('report.html')


@app.route('/report/download')
def attendance_report():
    try:
        data = list(tickets.find({}, {'_id': 0}))
        if not data:
            flash('No attendance data available')
            return redirect(url_for('upload_excel'))

        df = pd.DataFrame(data)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], 'attendance_report.xlsx')
        df.to_excel(filepath, index=False)

        return send_from_directory(
            app.config['UPLOAD_FOLDER'],
            'attendance_report.xlsx',
            as_attachment=True
        )
    except Exception as e:
        flash(f'Report error: {str(e)}')
        return redirect(url_for('upload_excel'))


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
