import hashlib
import os
import secrets
import sqlite3
import uuid
from datetime import datetime
from functools import wraps
from io import BytesIO
from pathlib import Path

from flask import Flask, flash, redirect, render_template, request, send_file, session, url_for
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from werkzeug.middleware.dispatcher import DispatcherMiddleware
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
from werkzeug.wrappers import Response


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
PROTECTED_DIR = DATA_DIR / "protected"
DATABASE_PATH = DATA_DIR / "app.db"
ALLOWED_EXTENSIONS = {"pdf"}


app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", secrets.token_hex(32))
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024


def ensure_storage() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    UPLOAD_DIR.mkdir(exist_ok=True)
    PROTECTED_DIR.mkdir(exist_ok=True)


def get_db_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    with get_db_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS protected_books (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_filename TEXT NOT NULL,
                stored_upload_path TEXT NOT NULL,
                protected_filename TEXT NOT NULL,
                protected_path TEXT NOT NULL,
                recipient_name TEXT NOT NULL,
                recipient_email TEXT NOT NULL,
                recipient_hash TEXT NOT NULL,
                license_id TEXT NOT NULL,
                open_password_hint TEXT NOT NULL,
                owner_password_hint TEXT NOT NULL,
                created_at TEXT NOT NULL,
                created_by INTEGER NOT NULL,
                FOREIGN KEY(created_by) REFERENCES users(id)
            )
            """
        )


def has_users() -> bool:
    with get_db_connection() as connection:
        row = connection.execute("SELECT COUNT(*) AS total FROM users").fetchone()
    return bool(row["total"])


def bootstrap_admin_from_env() -> None:
    admin_name = os.getenv("ADMIN_NAME")
    admin_email = os.getenv("ADMIN_EMAIL")
    admin_password = os.getenv("ADMIN_PASSWORD")
    if has_users() or not all([admin_name, admin_email, admin_password]):
        return

    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_db_connection() as connection:
        connection.execute(
            "INSERT INTO users (name, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
            (admin_name.strip(), admin_email.strip().lower(), generate_password_hash(admin_password), created_at),
        )


def login_required(view_func):
    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return view_func(*args, **kwargs)

    return wrapped_view


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def build_watermark_page(page_width: float, page_height: float, recipient_name: str, recipient_email: str, recipient_hash: str, license_id: str, issued_at: str):
    buffer = BytesIO()
    pdf_canvas = canvas.Canvas(buffer, pagesize=(page_width, page_height))
    center_x = page_width / 2

    pdf_canvas.setFont("Helvetica", 8)
    pdf_canvas.setFillGray(0.25)
    pdf_canvas.drawString(32, page_height - 24, f"Documento protegido | Hash: {recipient_hash}")

    pdf_canvas.setFont("Helvetica-Bold", 8)
    pdf_canvas.setFillGray(0.15)
    pdf_canvas.drawCentredString(center_x, 48, f"COPIA REGISTRADA A NOMBRE DE: {recipient_name}")

    pdf_canvas.setFont("Helvetica", 7)
    pdf_canvas.setFillGray(0.35)
    pdf_canvas.drawCentredString(
        center_x,
        38,
        "La reproducción no autorizada está prohibida. Este archivo está vinculado a una licencia individual.",
    )
    pdf_canvas.drawCentredString(center_x, 28, f"Licencia: {recipient_email} | ID: {license_id} | {issued_at}")

    pdf_canvas.save()
    buffer.seek(0)
    return PdfReader(buffer).pages[0]


def create_protected_pdf(input_pdf: Path, output_pdf: Path, recipient_name: str, recipient_email: str, open_password: str, owner_password: str | None):
    reader = PdfReader(str(input_pdf))
    writer = PdfWriter()

    license_id = str(uuid.uuid4())[:8]
    recipient_hash = hashlib.md5(recipient_email.strip().lower().encode("utf-8")).hexdigest()[:8]
    issued_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    resolved_owner_password = owner_password.strip() if owner_password else f"drm_{recipient_hash}_{license_id}"

    for page in reader.pages:
        watermark_page = build_watermark_page(
            float(page.mediabox.width),
            float(page.mediabox.height),
            recipient_name,
            recipient_email,
            recipient_hash,
            license_id,
            issued_at,
        )
        page.merge_page(watermark_page)
        writer.add_page(page)

    writer.add_metadata(
        {
            "/Title": f"Documento Protegido - {input_pdf.stem}",
            "/Author": "Sistema DRM Social",
            "/Subject": f"Licenciado para: {recipient_name}",
            "/Creator": f"DRM Social App - Licencia {license_id}",
            "/Producer": f"Usuario {recipient_hash} | Fecha {issued_at}",
            "/Keywords": f"DRM, PDF protegido, {license_id}",
        }
    )
    writer.encrypt(
        user_password=open_password.strip(),
        owner_password=resolved_owner_password,
        permissions_flag=0,
    )

    with output_pdf.open("wb") as file_pointer:
        writer.write(file_pointer)

    return {
        "license_id": license_id,
        "recipient_hash": recipient_hash,
        "issued_at": issued_at,
        "owner_password": resolved_owner_password,
        "open_password_hint": "Configurada" if open_password.strip() else "Sin contraseña",
        "owner_password_hint": "Manual" if owner_password and owner_password.strip() else "Autogenerada",
    }


@app.route("/")
def index():
    if not has_users():
        return redirect(url_for("setup"))
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/setup", methods=["GET", "POST"])
def setup():
    if has_users():
        return redirect(url_for("login"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not all([name, email, password, confirm_password]):
            flash("Completa todos los campos para crear el usuario inicial.", "error")
        elif password != confirm_password:
            flash("Las contraseñas no coinciden.", "error")
        else:
            created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            try:
                with get_db_connection() as connection:
                    connection.execute(
                        "INSERT INTO users (name, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
                        (name, email, generate_password_hash(password), created_at),
                    )
                flash("Usuario inicial creado. Ya puedes iniciar sesión.", "success")
                return redirect(url_for("login"))
            except sqlite3.IntegrityError:
                flash("Ese correo ya está registrado.", "error")

    return render_template("setup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if not has_users():
        return redirect(url_for("setup"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        with get_db_connection() as connection:
            user = connection.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

        if not user or not check_password_hash(user["password_hash"], password):
            flash("Credenciales inválidas.", "error")
        else:
            session.clear()
            session["user_id"] = user["id"]
            session["user_name"] = user["name"]
            session["user_email"] = user["email"]
            return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    with get_db_connection() as connection:
        recent_books = connection.execute(
            """
            SELECT pb.*, u.name AS creator_name
            FROM protected_books pb
            JOIN users u ON u.id = pb.created_by
            ORDER BY pb.id DESC
            LIMIT 5
            """
        ).fetchall()

    return render_template("dashboard.html", recent_books=recent_books)


@app.route("/protect", methods=["POST"])
@login_required
def protect_book():
    uploaded_file = request.files.get("book")
    recipient_name = request.form.get("recipient_name", "").strip()
    recipient_email = request.form.get("recipient_email", "").strip().lower()
    open_password = request.form.get("open_password", "")
    owner_password = request.form.get("owner_password", "")

    if not uploaded_file or not uploaded_file.filename:
        flash("Selecciona un archivo PDF para proteger.", "error")
        return redirect(url_for("dashboard"))

    if not allowed_file(uploaded_file.filename):
        flash("Solo se permiten archivos PDF.", "error")
        return redirect(url_for("dashboard"))

    if not recipient_name or not recipient_email:
        flash("Debes indicar el nombre y el email del destinatario.", "error")
        return redirect(url_for("dashboard"))

    safe_filename = secure_filename(uploaded_file.filename)
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    upload_filename = f"{timestamp}_{safe_filename}"
    protected_filename = f"{Path(safe_filename).stem}_protegido_{timestamp}.pdf"
    upload_path = UPLOAD_DIR / upload_filename
    protected_path = PROTECTED_DIR / protected_filename

    uploaded_file.save(upload_path)
    protection_data = create_protected_pdf(
        upload_path,
        protected_path,
        recipient_name,
        recipient_email,
        open_password,
        owner_password,
    )

    with get_db_connection() as connection:
        connection.execute(
            """
            INSERT INTO protected_books (
                original_filename,
                stored_upload_path,
                protected_filename,
                protected_path,
                recipient_name,
                recipient_email,
                recipient_hash,
                license_id,
                open_password_hint,
                owner_password_hint,
                created_at,
                created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uploaded_file.filename,
                str(upload_path.relative_to(BASE_DIR)),
                protected_filename,
                str(protected_path.relative_to(BASE_DIR)),
                recipient_name,
                recipient_email,
                protection_data["recipient_hash"],
                protection_data["license_id"],
                protection_data["open_password_hint"],
                protection_data["owner_password_hint"],
                protection_data["issued_at"],
                session["user_id"],
            ),
        )

    flash(
        f"PDF protegido correctamente. ID de licencia: {protection_data['license_id']} | Contraseña de apertura: {open_password.strip() or 'sin contraseña'} | Contraseña de propietario: {protection_data['owner_password']}",
        "success",
    )
    return redirect(url_for("history"))


@app.route("/history")
@login_required
def history():
    with get_db_connection() as connection:
        books = connection.execute(
            """
            SELECT pb.*, u.name AS creator_name
            FROM protected_books pb
            JOIN users u ON u.id = pb.created_by
            ORDER BY pb.id DESC
            """
        ).fetchall()

    return render_template("history.html", books=books)


@app.route("/download/<int:book_id>")
@login_required
def download_book(book_id: int):
    with get_db_connection() as connection:
        book = connection.execute(
            "SELECT protected_filename, protected_path FROM protected_books WHERE id = ?",
            (book_id,),
        ).fetchone()

    if not book:
        flash("No se encontró el archivo solicitado.", "error")
        return redirect(url_for("history"))

    return send_file(BASE_DIR / book["protected_path"], as_attachment=True, download_name=book["protected_filename"])


ensure_storage()
init_db()
bootstrap_admin_from_env()

application = DispatcherMiddleware(Response("Not Found", status=404), {"/drmsocial": app})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")), debug=False)
