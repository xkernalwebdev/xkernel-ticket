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
import os, uuid, threading
from werkzeug.utils import secure_filename
from config import Config
import certifi
from datetime import datetime

app = Flask(__name__)
app.config.from_object(Config)
app.secret_key = app.config["SECRET_KEY"]

client = MongoClient(app.config["MONGO_URI"], tlsCAFile=certifi.where())
db = client.event_tickets
tickets = db.tickets

UPLOAD_FOLDER = "uploads"
QR_FOLDER = "qrcodes"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(QR_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = {"xlsx"}


class ManualTicketForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired()])
    email = StringField("Email", validators=[DataRequired(), Email()])
    event = StringField("Event", validators=[DataRequired()])
    phone = StringField("Phone")
    submit = SubmitField("Generate")


def allowed_file(f):
    return "." in f and f.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def create_ticket(ticket_id, name, event, data, path):
    qr = qrcode.make(data)
    qr = qr.resize((250, 250))

    img = Image.new("RGB", (900, 350), "#020617")
    img.paste(qr, (600, 50))

    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()

    draw.text((40, 80), f"Name: {name}", fill="white", font=font)
    draw.text((40, 130), f"Event: {event}", fill="white", font=font)
    draw.text((40, 180), f"Ticket: {ticket_id}", fill="white", font=font)

    img.save(path)


def send_ticket_email(email, name, ticket_id, qr_path, event):
    msg = MIMEMultipart()
    msg["From"] = app.config["EMAIL_USER"]
    msg["To"] = email
    msg["Subject"] = f"X-Kernel Ticket - {event}"

    body = f"Hello {name},\n\nYour ticket for {event} is attached.\nTicket ID: {ticket_id}"
    msg.attach(MIMEText(body, "plain"))

    with open(qr_path, "rb") as f:
        img = MIMEImage(f.read())
        img.add_header("Content-Disposition", "attachment", filename=f"{ticket_id}.png")
        msg.attach(img)

    server = smtplib.SMTP(app.config["MAIL_SERVER"], app.config["MAIL_PORT"])
    server.starttls()
    server.login(app.config["EMAIL_USER"], app.config["EMAIL_PASS"])
    server.send_message(msg)
    server.quit()


def send_async(*args):
    threading.Thread(target=send_ticket_email, args=args).start()


@app.route("/", methods=["GET", "POST"])
def home():
    form = ManualTicketForm()

    if form.validate_on_submit():
        tid = str(uuid.uuid4())[:8].upper()
        tickets.insert_one({
            "ticket_id": tid,
            "name": form.name.data,
            "email": form.email.data,
            "event": form.event.data,
            "phone": form.phone.data,
            "used": False,
            "scanned_at": None
        })

        qr_data = f"TICKET:{tid}:{form.event.data}"
        path = os.path.join(QR_FOLDER, f"{tid}.png")

        create_ticket(tid, form.name.data, form.event.data, qr_data, path)
        send_async(form.email.data, form.name.data, tid, path, form.event.data)

        flash("Ticket generated and sent!")
        return redirect(url_for("home"))

    if "file" in request.files:
        file = request.files["file"]
        if allowed_file(file.filename):
            path = os.path.join(UPLOAD_FOLDER, secure_filename(file.filename))
            file.save(path)

            df = pd.read_excel(path, engine="openpyxl")

            for _, r in df.iterrows():
                tid = str(uuid.uuid4())[:8].upper()

                tickets.insert_one({
                    "ticket_id": tid,
                    "name": str(r["Name"]),
                    "email": str(r["Email"]),
                    "event": str(r["Event Name"]),
                    "phone": str(r["Phone"]),
                    "used": False,
                    "scanned_at": None
                })

                qr_data = f"TICKET:{tid}:{r['Event Name']}"
                qpath = os.path.join(QR_FOLDER, f"{tid}.png")

                create_ticket(tid, r["Name"], r["Event Name"], qr_data, qpath)
                send_async(r["Email"], r["Name"], tid, qpath, r["Event Name"])

            os.remove(path)
            flash("Excel processed and mails sent!")

    return render_template("upload.html", form=form)


@app.route("/scanner")
def scanner():
    return render_template("scanner.html")


@app.route("/verify", methods=["POST"])
def verify():
    data = request.json["ticket_data"]

    if not data.startswith("TICKET:"):
        return jsonify({"valid": False})

    tid = data.split(":")[1]
    ticket = tickets.find_one({"ticket_id": tid})

    if not ticket:
        return jsonify({"valid": False, "msg": "Invalid"})

    if ticket["used"]:
        return jsonify({"valid": False, "msg": "Already used"})

    tickets.update_one(
        {"ticket_id": tid},
        {"$set": {"used": True, "scanned_at": datetime.utcnow()}}
    )

    return jsonify({"valid": True, "msg": "Welcome!"})


@app.route("/report/download")
def report():
    data = list(tickets.find({}, {"_id": 0}))
    df = pd.DataFrame(data)
    path = os.path.join(UPLOAD_FOLDER, "attendance.xlsx")
    df.to_excel(path, index=False)
    return send_from_directory(UPLOAD_FOLDER, "attendance.xlsx", as_attachment=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)