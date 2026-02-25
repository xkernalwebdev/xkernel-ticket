from flask import Flask, request, render_template, jsonify, send_from_directory, flash, redirect, url_for
from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField
from wtforms.validators import DataRequired, Email
from pymongo import MongoClient
import pandas as pd
import qrcode
from PIL import Image, ImageDraw, ImageFont
import os, uuid, base64
from werkzeug.utils import secure_filename
from config import Config
import certifi
from datetime import datetime
import sib_api_v3_sdk
from sib_api_v3_sdk.api import transactional_emails_api
from sib_api_v3_sdk.models import SendSmtpEmail, SendSmtpEmailAttachment

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


def allowed_file(name):
    return "." in name and name.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def create_ticket(tid, name, event, data, path):
    qr = qrcode.make(data).resize((250, 250))
    img = Image.new("RGB", (900, 350), "#020617")
    img.paste(qr, (600, 50))

    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()

    draw.text((40, 90), f"Name: {name}", fill="white", font=font)
    draw.text((40, 140), f"Event: {event}", fill="white", font=font)
    draw.text((40, 190), f"Ticket: {tid}", fill="white", font=font)

    img.save(path)


def send_brevo_email(to_email, name, tid, qr_path, event):
    try:
        config = sib_api_v3_sdk.Configuration()
        config.api_key["api-key"] = os.getenv("BREVO_API_KEY")

        api = transactional_emails_api.TransactionalEmailsApi(
            sib_api_v3_sdk.ApiClient(config)
        )

        with open(qr_path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode()

        attachment = SendSmtpEmailAttachment(
            content=encoded,
            name=f"{tid}.png"
        )

        email = SendSmtpEmail(
            to=[{"email": to_email, "name": name}],
            sender={"email": app.config["EMAIL_USER"], "name": "X-Kernel"},
            subject=f"Your X-Kernel Ticket - {event}",
            html_content=f"""
            <h2>Hello {name}</h2>
            <p>Your event ticket is attached.</p>
            <p><b>Ticket ID:</b> {tid}</p>
            <p><b>Event:</b> {event}</p>
            """,
            attachment=[attachment]
        )

        api.send_transac_email(email)
        print("BREVO EMAIL SENT")

    except Exception as e:
        print("BREVO ERROR:", e)


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

        path = os.path.join(QR_FOLDER, f"{tid}.png")
        create_ticket(tid, form.name.data, form.event.data, f"TICKET:{tid}", path)

        send_brevo_email(
            form.email.data,
            form.name.data,
            tid,
            path,
            form.event.data
        )

        flash("Ticket generated and emailed!")
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

                qr_path = os.path.join(QR_FOLDER, f"{tid}.png")
                create_ticket(tid, r["Name"], r["Event Name"], f"TICKET:{tid}", qr_path)

                send_brevo_email(
                    r["Email"],
                    r["Name"],
                    tid,
                    qr_path,
                    r["Event Name"]
                )

            os.remove(path)
            flash("Excel processed and tickets sent!")

    return render_template("upload.html", form=form)


@app.route("/scanner")
def scanner():
    return render_template("scanner.html")


@app.route("/verify", methods=["POST"])
def verify():
    data = request.json.get("ticket_data", "")
    tid = data.replace("TICKET:", "")

    ticket = tickets.find_one({"ticket_id": tid})

    if not ticket:
        return jsonify({"valid": False})

    if ticket["used"]:
        return jsonify({"valid": False, "msg": "Already used"})

    tickets.update_one(
        {"ticket_id": tid},
        {"$set": {"used": True, "scanned_at": datetime.utcnow()}}
    )

    return jsonify({"valid": True})


@app.route("/report/download")
def report():
    data = list(tickets.find({}, {"_id": 0}))
    df = pd.DataFrame(data)
    path = os.path.join(UPLOAD_FOLDER, "attendance.xlsx")
    df.to_excel(path, index=False)
    return send_from_directory(UPLOAD_FOLDER, "attendance.xlsx", as_attachment=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)