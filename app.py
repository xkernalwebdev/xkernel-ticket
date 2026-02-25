from flask import Flask, request, render_template, jsonify, flash, redirect, url_for
from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField
from wtforms.validators import DataRequired, Email, Length
from pymongo import MongoClient
import qrcode
from PIL import Image, ImageDraw, ImageFont
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
import os, uuid
from config import Config
import certifi
from datetime import datetime
import pandas as pd  # for Excel

app = Flask(__name__)
app.config.from_object(Config)
app.secret_key = app.config['SECRET_KEY']

client = MongoClient(app.config['MONGO_URI'], tlsCAFile=certifi.where())
db = client.event_tickets
tickets = db.tickets

UPLOAD_FOLDER = 'uploads'
QR_FOLDER = 'qrcodes'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(QR_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = {'xlsx'}


class ManualTicketForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired(), Length(min=2)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    event = StringField('Event Name', validators=[DataRequired()])
    phone = StringField('Phone', validators=[Length(min=10)])
    submit = SubmitField('Generate Ticket')


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def create_ticket_image(ticket_id, name, event, qr_data, save_path):
    bg = "#0f172a"
    card = "#020617"
    accent = "#38bdf8"
    main = "#e5e7eb"
    muted = "#94a3b8"

    qr = qrcode.make(qr_data).resize((260, 260))

    w, h = 900, 350
    img = Image.new("RGB", (w, h), bg)
    draw = ImageDraw.Draw(img)

    draw.rounded_rectangle([20, 20, w - 20, h - 20], 26, fill=card)
    draw.rectangle([20, 20, 32, h - 20], fill=accent)

    try:
        title = ImageFont.truetype("arialbd.ttf", 32)
        label = ImageFont.truetype("arial.ttf", 18)
        value = ImageFont.truetype("arial.ttf", 20)
        small = ImageFont.truetype("arial.ttf", 14)
    except Exception:
        title = label = value = small = ImageFont.load_default()

    left = 70
    draw.text((left, 50), "Event Access Pass", fill=main, font=title)

    y = 120
    draw.text((left, y), "Name", fill=muted, font=label)
    draw.text((left + 140, y), name, fill=main, font=value)

    y += 45
    draw.text((left, y), "Event", fill=muted, font=label)
    draw.text((left + 140, y), event, fill=main, font=value)

    y += 45
    draw.text((left, y), "Ticket ID", fill=muted, font=label)
    draw.text((left + 140, y), ticket_id, fill=main, font=value)

    footer = "Show this pass at entry • QR is mandatory • Issued by X-Kernel"
    draw.text((left, h - 55), footer, fill=muted, font=small)

    img.paste(qr, (w - 320, 45))
    draw.text((w - 260, 315), "Scan at gate", fill=muted, font=small)

    img.save(save_path)


def send_ticket_email(email, name, ticket_id, qr_path, event):
    msg = MIMEMultipart()
    msg['From'] = app.config['EMAIL_USER']
    msg['To'] = email
    msg['Subject'] = f'Your X-Kernel Ticket for {event} - ID: {ticket_id}'

    body = f"""Dear {name},

Your event ticket is attached.

Ticket ID: {ticket_id}
Event: {event}

Please show this at entry.

Regards,
X-Kernel Team
"""
    msg.attach(MIMEText(body, 'plain'))

    with open(qr_path, 'rb') as f:
        img = MIMEImage(f.read())
        img.add_header('Content-Disposition', 'attachment', filename=f'{ticket_id}_ticket.png')
        msg.attach(img)

    try:
        server = smtplib.SMTP(app.config['MAIL_SERVER'], app.config['MAIL_PORT'], timeout=20)
        server.starttls()
        server.login(app.config['EMAIL_USER'], app.config['EMAIL_PASS'])
        server.send_message(msg)
        server.quit()
    except Exception as e:
        print("EMAIL ERROR:", e)


@app.route('/', methods=['GET', 'POST'])
def home():
    form = ManualTicketForm()

    # Manual ticket generate
    if form.validate_on_submit():
        ticket_id = str(uuid.uuid4())[:8].upper()

        tickets.insert_one({
            'ticket_id': ticket_id,
            'name': form.name.data,
            'email': form.email.data,
            'event': form.event.data,
            'phone': form.phone.data or '',
            'branch': None,
            'roll_number': None,
            'used': False,
            'scanned_at': None
        })

        qr_data = f"TICKET:{ticket_id}:{form.event.data}"
        qr_path = os.path.join(QR_FOLDER, f"{ticket_id}.png")
        create_ticket_image(ticket_id, form.name.data, form.event.data, qr_data, qr_path)

        send_ticket_email(form.email.data, form.name.data, ticket_id, qr_path, form.event.data)

        flash(f'Ticket {ticket_id} generated and emailed!')
        return redirect(url_for('home'))

    # Excel upload for bulk tickets
    if 'file' in request.files:
        file = request.files['file']
        if file.filename and allowed_file(file.filename):
            path = os.path.join(UPLOAD_FOLDER, file.filename)
            file.save(path)

            try:
                df = pd.read_excel(path)

                required_cols = ['Name', 'Branch', 'Roll Number', 'Event', 'Mail']
                if not all(col in df.columns for col in required_cols):
                    flash('Excel must have columns: Name, Branch, Roll Number, Event, Mail')
                    return redirect(url_for('home'))

                for _, row in df.iterrows():
                    ticket_id = str(uuid.uuid4())[:8].upper()

                    name = str(row['Name']).strip()
                    branch = str(row['Branch']).strip() if pd.notna(row['Branch']) else ''
                    roll_number = str(row['Roll Number']).strip() if pd.notna(row['Roll Number']) else ''
                    event_name = str(row['Event']).strip()
                    email_addr = str(row['Mail']).strip()

                    tickets.insert_one({
                        'ticket_id': ticket_id,
                        'name': name,
                        'email': email_addr,
                        'event': event_name,
                        'phone': '',
                        'branch': branch,
                        'roll_number': roll_number,
                        'used': False,
                        'scanned_at': None
                    })

                    qr_data = f"TICKET:{ticket_id}:{event_name}"
                    qr_path = os.path.join(QR_FOLDER, f"{ticket_id}.png")
                    create_ticket_image(ticket_id, name, event_name, qr_data, qr_path)

                    send_ticket_email(email_addr, name, ticket_id, qr_path, event_name)

                flash('Excel tickets generated and emailed!')
            except Exception as e:
                flash(f'Error processing Excel: {e}')
            finally:
                if os.path.exists(path):
                    os.remove(path)

            return redirect(url_for('home'))

    return render_template('upload.html', form=form)


@app.route('/scanner')
def scanner():
    return render_template('scanner.html')


@app.route('/tickets')
def tickets_page():
    all_tickets = list(tickets.find().sort('_id', -1))
    return render_template('tickets.html', tickets=all_tickets)

@app.route('/report')
def report_page():
    all_tickets = list(tickets.find().sort('_id', -1))
    return render_template('report.html', tickets=all_tickets)


@app.route('/verify', methods=['POST'])
def verify_ticket():
    data = request.json.get('ticket_data')

    if not data or not data.startswith('TICKET:'):
        return jsonify({'valid': False, 'message': 'Invalid QR format'}), 400

    parts = data.split(':')
    if len(parts) != 3:
        return jsonify({'valid': False, 'message': 'Invalid QR data'}), 400

    ticket_id = parts[1]
    ticket = tickets.find_one({'ticket_id': ticket_id})

    if not ticket:
        return jsonify({'valid': False, 'message': 'Invalid Ticket'}), 400

    if ticket['used']:
        return jsonify({
            'valid': False,
            'message': 'Already Used',
            'name': ticket.get('name'),
            'event': ticket.get('event'),
            'ticket_id': ticket.get('ticket_id'),
            'scanned_at': ticket.get('scanned_at')
        }), 400

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


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)