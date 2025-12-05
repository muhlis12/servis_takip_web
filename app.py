import os
import shutil
import io
import csv
from functools import wraps
from datetime import date

from flask import (
    Flask, render_template, request, redirect,
    url_for, flash, send_file, session
)
import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash

# ----------------- AYARLAR -----------------
DB_NAME = "servis_takip.db"

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "yerelde-cok-gizli-olmayan-bir-sey")


# ----------------- DB BAĞLANTI -----------------
def get_conn():
    return sqlite3.connect(DB_NAME)


def create_tables():
    conn = get_conn()
    c = conn.cursor()

    # ÖĞRENCİLER
    c.execute("""
    CREATE TABLE IF NOT EXISTS students (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        school TEXT,
        parent_name TEXT,
        phone TEXT,
        monthly_fee REAL NOT NULL,
        start_year INTEGER,
        start_month INTEGER,
        is_active INTEGER NOT NULL DEFAULT 1
    )
    """)

    # ÖDEMELER
    c.execute("""
    CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER NOT NULL,
        year INTEGER NOT NULL,
        month INTEGER NOT NULL,
        pay_date TEXT NOT NULL,
        amount REAL NOT NULL,
        description TEXT,
        FOREIGN KEY(student_id) REFERENCES students(id)
    )
    """)

    # ARAÇLAR
    c.execute("""
    CREATE TABLE IF NOT EXISTS vehicles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        plate TEXT NOT NULL,
        name TEXT,
        capacity INTEGER,
        route TEXT,
        is_active INTEGER NOT NULL DEFAULT 1
    )
    """)

    # Eski db'lerde route yoksa ekle
    try:
        c.execute("ALTER TABLE vehicles ADD COLUMN route TEXT")
    except sqlite3.OperationalError:
        pass

    # ÖĞRENCİ–ARAÇ
    c.execute("""
    CREATE TABLE IF NOT EXISTS student_vehicle (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER NOT NULL,
        vehicle_id INTEGER NOT NULL,
        start_date TEXT NOT NULL,
        end_date TEXT,
        FOREIGN KEY(student_id) REFERENCES students(id),
        FOREIGN KEY(vehicle_id) REFERENCES vehicles(id)
    )
    """)

    # GİDERLER
    c.execute("""
    CREATE TABLE IF NOT EXISTS expenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        vehicle_id INTEGER,
        exp_date TEXT NOT NULL,
        category TEXT NOT NULL,
        amount REAL NOT NULL,
        description TEXT,
        FOREIGN KEY(vehicle_id) REFERENCES vehicles(id)
    )
    """)

    # META (yedekleme vs.)
    c.execute("""
    CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """)

    # KULLANICILAR
    c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        full_name TEXT,
        role TEXT
    )
    """)

    # Eğer hiç kullanıcı yoksa 4 tane varsayılan ekle
    c.execute("SELECT COUNT(*) FROM users")
    count_users = c.fetchone()[0]
    if count_users == 0:
        default_users = [
            ("admin", "1234", "Yönetici", "admin"),
            ("muhlis", "1234", "Muhlis Öztürk", "user"),
            ("kullanici1", "1234", "Kullanıcı 1", "user"),
            ("kullanici2", "1234", "Kullanıcı 2", "user"),
        ]
        for uname, pwd, fname, role in default_users:
            c.execute(
                "INSERT INTO users (username, password_hash, full_name, role) VALUES (?, ?, ?, ?)",
                (uname, generate_password_hash(pwd), fname, role),
            )
        conn.commit()

    conn.close()


# ----------------- GÜNLÜK YEDEK -----------------
def ensure_daily_backup():
    """Her gün ilk girişte db'nin kopyasını backups klasörüne alır."""
    today_str = date.today().isoformat()

    conn = get_conn()
    c = conn.cursor()

    c.execute("SELECT value FROM meta WHERE key='last_backup_date'")
    row = c.fetchone()
    last_backup = row[0] if row else None

    if last_backup != today_str:
        os.makedirs("backups", exist_ok=True)
        backup_path = os.path.join("backups", f"servis_takip_{today_str}.db")
        try:
            shutil.copy2(DB_NAME, backup_path)
            if row:
                c.execute(
                    "UPDATE meta SET value=? WHERE key='last_backup_date'",
                    (today_str,),
                )
            else:
                c.execute(
                    "INSERT INTO meta (key, value) VALUES ('last_backup_date', ?)",
                    (today_str,),
                )
            conn.commit()
            print("Günlük yedek alındı:", backup_path)
        except Exception as e:
            print("Yedekleme hatası:", e)

    conn.close()


# ----------------- LOGIN KONTROL DECORATOR -----------------
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function


# ----------------- LOGIN / LOGOUT -----------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        conn = get_conn()
        c = conn.cursor()
        c.execute(
            "SELECT id, username, password_hash, full_name, role FROM users WHERE username=?",
            (username,),
        )
        row = c.fetchone()
        conn.close()

        if row and check_password_hash(row[2], password):
            session["user_id"] = row[0]
            session["username"] = row[1]
            session["full_name"] = row[3]
            session["role"] = row[4]
            flash("Giriş başarılı.", "success")
            return redirect(url_for("index"))
        else:
            flash("Kullanıcı adı veya şifre hatalı.", "danger")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Oturum kapatıldı.", "info")
    return redirect(url_for("login"))


# ----------------- ANA SAYFA -----------------
@app.route("/")
@login_required
def index():
    ensure_daily_backup()  # Her gün ilk girişte yedek al

    conn = get_conn()
    c = conn.cursor()

    active_tab = request.args.get("tab", "students")
    raw_filter_date = request.args.get("filter_date", "").strip()
    filter_date = raw_filter_date if raw_filter_date else None

    # GENEL ÖZET
    c.execute("SELECT COUNT(*) FROM students WHERE is_active=1")
    student_count = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM vehicles WHERE is_active=1")
    vehicle_count = c.fetchone()[0]

    c.execute("SELECT COALESCE(SUM(amount),0) FROM payments")
    total_income = c.fetchone()[0] or 0.0

    c.execute("SELECT COALESCE(SUM(amount),0) FROM expenses")
    total_expense = c.fetchone()[0] or 0.0

    summary = {
        "student_count": student_count,
        "vehicle_count": vehicle_count,
        "total_income": total_income,
        "total_expense": total_expense,
        "profit": total_income - total_expense,
    }

    # ÖĞRENCİLER
    c.execute("""
        SELECT id, name, school, parent_name, phone,
               monthly_fee, start_year, start_month
        FROM students
        WHERE is_active=1
        ORDER BY id
    """)
    students = c.fetchall()

    # ÖDEMELER
    if filter_date:
        c.execute("""
            SELECT p.id, s.name, p.pay_date, p.year, p.month,
                   p.amount, p.description
            FROM payments p
            JOIN students s ON s.id = p.student_id
            WHERE p.pay_date = ?
            ORDER BY p.id DESC
        """, (filter_date,))
    else:
        c.execute("""
            SELECT p.id, s.name, p.pay_date, p.year, p.month,
                   p.amount, p.description
            FROM payments p
            JOIN students s ON s.id = p.student_id
            ORDER BY p.id DESC
            LIMIT 50
        """)
    payments = c.fetchall()

    # Tarih özet
    date_summary = None
    if filter_date:
        c.execute("""
            SELECT
                COUNT(DISTINCT student_id) AS student_count,
                COUNT(*) AS payment_count,
                COALESCE(SUM(amount), 0) AS total_amount
            FROM payments
            WHERE pay_date = ?
        """, (filter_date,))
        row = c.fetchone()
        if row:
            date_summary = {
                "student_count": row[0] or 0,
                "payment_count": row[1] or 0,
                "total_amount": row[2] or 0.0,
            }

    # ARAÇLAR
    c.execute("""
        SELECT id, plate, name, capacity, route
        FROM vehicles
        WHERE is_active=1
        ORDER BY id
    """)
    vehicles = c.fetchall()

    # GİDERLER
    c.execute("""
        SELECT e.id, e.exp_date, IFNULL(v.plate,'Genel'),
               e.category, e.amount, e.description
        FROM expenses e
        LEFT JOIN vehicles v ON v.id = e.vehicle_id
        ORDER BY e.exp_date DESC, e.id DESC
        LIMIT 50
    """)
    expenses = c.fetchall()

    # FORM SEÇENEKLERİ
    c.execute("SELECT id, name FROM students WHERE is_active=1 ORDER BY name")
    student_options = c.fetchall()

    c.execute("SELECT id, plate FROM vehicles WHERE is_active=1 ORDER BY plate")
    vehicle_options = c.fetchall()

    # ARAÇLARA GÖRE ÖĞRENCİLER
    c.execute("""
        SELECT v.plate, v.id, s.name, s.school
        FROM student_vehicle sv
        JOIN vehicles v ON v.id = sv.vehicle_id
        JOIN students s ON s.id = sv.student_id
        WHERE (sv.end_date IS NULL OR sv.end_date = '')
        ORDER BY v.plate, s.name
    """)
    vehicle_student_list = c.fetchall()

    # OKULLARA GÖRE ÖĞRENCİ SAYISI
    c.execute("""
        SELECT school, COUNT(*)
        FROM students
        WHERE is_active=1
        GROUP BY school
        ORDER BY school
    """)
    school_stats = c.fetchall()

    conn.close()

    today = date.today().isoformat()
    current_year = date.today().year

    return render_template(
        "dashboard.html",
        summary=summary,
        students=students,
        payments=payments,
        vehicles=vehicles,
        expenses=expenses,
        student_options=student_options,
        vehicle_options=vehicle_options,
        vehicle_student_list=vehicle_student_list,
        school_stats=school_stats,
        today=today,
        current_year=current_year,
        filter_date=filter_date,
        date_summary=date_summary,
        active_tab=active_tab,
    )


# ----------------- ÖĞRENCİ İŞLEMLERİ -----------------
@app.route("/add_student", methods=["POST"])
@login_required
def add_student():
    name = request.form.get("name", "").strip()
    school = request.form.get("school", "").strip()
    parent_name = request.form.get("parent_name", "").strip()
    phone = request.form.get("phone", "").strip()
    monthly_fee = request.form.get("monthly_fee", "").replace(",", ".")
    start_year = request.form.get("start_year", "")
    start_month = request.form.get("start_month", "")

    if not name or not monthly_fee:
        flash("Ad soyad ve aylık ücret zorunludur.", "danger")
        return redirect(url_for("index", tab="students"))

    try:
        fee_val = float(monthly_fee)
        sy = int(start_year) if start_year else date.today().year
        sm = int(start_month) if start_month else date.today().month
    except ValueError:
        flash("Ücret, yıl ve ay sayısal olmalıdır.", "danger")
        return redirect(url_for("index", tab="students"))

    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO students
        (name, school, parent_name, phone,
         monthly_fee, start_year, start_month)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (name, school, parent_name, phone, fee_val, sy, sm))
    conn.commit()
    conn.close()

    flash("Öğrenci kaydedildi.", "success")
    return redirect(url_for("index", tab="students"))


@app.route("/delete_student/<int:student_id>", methods=["POST"])
@login_required
def delete_student(student_id):
    conn = get_conn()
    c = conn.cursor()

    c.execute("UPDATE students SET is_active=0 WHERE id=?", (student_id,))

    today_str = date.today().isoformat()
    c.execute("""
        UPDATE student_vehicle
        SET end_date=?
        WHERE student_id=? AND (end_date IS NULL OR end_date='')
    """, (today_str, student_id))

    conn.commit()
    conn.close()

    flash("Öğrenci silindi (pasif yapıldı).", "success")
    return redirect(url_for("index", tab="students"))


# ----------------- ÖDEME İŞLEMLERİ -----------------
@app.route("/add_payment", methods=["POST"])
@login_required
def add_payment():
    student_id = request.form.get("student_id")
    amount = request.form.get("amount", "").replace(",", ".")
    year = request.form.get("year", "")
    month = request.form.get("month", "")
    pay_date = request.form.get("pay_date", "") or date.today().isoformat()
    description = request.form.get("description", "").strip()

    if not student_id or not amount:
        flash("Öğrenci ve tutar zorunludur.", "danger")
        return redirect(url_for("index", tab="payments"))

    try:
        sid = int(student_id)
        amt = float(amount)
        yy = int(year) if year else date.today().year
        mm = int(month) if month else date.today().month
    except ValueError:
        flash("Tutar, yıl ve ay sayısal olmalıdır.", "danger")
        return redirect(url_for("index", tab="payments"))

    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO payments
        (student_id, year, month, pay_date, amount, description)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (sid, yy, mm, pay_date, amt, description))
    conn.commit()
    conn.close()

    flash("Ödeme kaydedildi.", "success")
    return redirect(url_for("index", tab="payments", filter_date=pay_date))


@app.route("/payments_by_date", methods=["POST"])
@login_required
def payments_by_date():
    filter_date = request.form.get("filter_date", "").strip()

    if not filter_date:
        flash("Lütfen bir tarih seçin.", "danger")
        return redirect(url_for("index", tab="payments"))

    return redirect(url_for("index", tab="payments", filter_date=filter_date))


# ----- GÜNLÜK RAPOR (EXCEL/PDF) -----
@app.route("/daily_report", methods=["POST"])
@login_required
def daily_report():
    report_date = request.form.get("report_date", "").strip()
    report_format = request.form.get("report_format", "excel")

    if not report_date:
        flash("Rapor için bir tarih seçmelisiniz.", "danger")
        return redirect(url_for("index", tab="payments"))

    conn = get_conn()
    c = conn.cursor()

    # O tarihteki ödemeler
    c.execute("""
        SELECT p.id, s.name, p.pay_date, p.amount, p.description
        FROM payments p
        JOIN students s ON s.id = p.student_id
        WHERE p.pay_date = ?
        ORDER BY p.id
    """, (report_date,))
    payments = c.fetchall()

    # O tarihteki giderler
    c.execute("""
        SELECT e.id, e.exp_date, IFNULL(v.plate,'Genel'), e.category, e.amount, e.description
        FROM expenses e
        LEFT JOIN vehicles v ON v.id = e.vehicle_id
        WHERE e.exp_date = ?
        ORDER BY e.id
    """, (report_date,))
    expenses = c.fetchall()

    # Özet
    c.execute("SELECT COALESCE(SUM(amount),0) FROM payments WHERE pay_date=?", (report_date,))
    total_income = c.fetchone()[0] or 0.0

    c.execute("SELECT COALESCE(SUM(amount),0) FROM expenses WHERE exp_date=?", (report_date,))
    total_expense = c.fetchone()[0] or 0.0

    conn.close()

    if report_format == "excel":
        # CSV (Excel ile açılabilir)
        output = io.StringIO()
        writer = csv.writer(output, delimiter=';')

        writer.writerow([f"Günlük Rapor - {report_date}"])
        writer.writerow([])
        writer.writerow(["Ödemeler"])
        writer.writerow(["ID", "Öğrenci", "Tarih", "Tutar", "Açıklama"])
        for p in payments:
            writer.writerow([p[0], p[1], p[2], f"{p[3]:.2f}", p[4]])

        writer.writerow([])
        writer.writerow(["Giderler"])
        writer.writerow(["ID", "Tarih", "Araç", "Kategori", "Tutar", "Açıklama"])
        for e in expenses:
            writer.writerow([e[0], e[1], e[2], e[3], f"{e[4]:.2f}", e[5]])

        writer.writerow([])
        writer.writerow(["Özet"])
        writer.writerow(["Toplam Gelir", f"{total_income:.2f} TL"])
        writer.writerow(["Toplam Gider", f"{total_expense:.2f} TL"])
        writer.writerow(["Kâr / Zarar", f"{(total_income-total_expense):.2f} TL"])

        csv_bytes = io.BytesIO(output.getvalue().encode("utf-8-sig"))
        filename = f"gunluk_rapor_{report_date}.csv"
        return send_file(
            csv_bytes,
            as_attachment=True,
            download_name=filename,
            mimetype="text/csv",
        )
    else:
        # PDF
        try:
            from reportlab.pdfgen import canvas
            from reportlab.lib.pagesizes import A4
        except ImportError:
            flash("PDF için 'reportlab' kütüphanesini kurmalısınız: pip install reportlab", "danger")
            return redirect(url_for("index", tab="payments"))

        buffer = io.BytesIO()
        cpdf = canvas.Canvas(buffer, pagesize=A4)
        width, height = A4

        y = height - 40
        cpdf.setFont("Helvetica-Bold", 14)
        cpdf.drawString(40, y, f"Günlük Rapor - {report_date}")
        y -= 30

        cpdf.setFont("Helvetica-Bold", 12)
        cpdf.drawString(40, y, "Ödemeler")
        y -= 20
        cpdf.setFont("Helvetica", 9)
        for p in payments:
            line = f"#{p[0]} - {p[1]} - {p[2]} - {p[3]:.2f} TL - {p[4] or ''}"
            cpdf.drawString(40, y, line[:110])
            y -= 14
            if y < 50:
                cpdf.showPage()
                y = height - 40
                cpdf.setFont("Helvetica", 9)

        y -= 10
        cpdf.setFont("Helvetica-Bold", 12)
        cpdf.drawString(40, y, "Giderler")
        y -= 20
        cpdf.setFont("Helvetica", 9)
        for e in expenses:
            line = f"#{e[0]} - {e[1]} - {e[2]} - {e[3]} - {e[4]:.2f} TL - {e[5] or ''}"
            cpdf.drawString(40, y, line[:110])
            y -= 14
            if y < 50:
                cpdf.showPage()
                y = height - 40
                cpdf.setFont("Helvetica", 9)

        y -= 20
        cpdf.setFont("Helvetica-Bold", 11)
        cpdf.drawString(40, y, f"Toplam Gelir : {total_income:.2f} TL")
        y -= 14
        cpdf.drawString(40, y, f"Toplam Gider : {total_expense:.2f} TL")
        y -= 14
        cpdf.drawString(40, y, f"Kâr / Zarar : {(total_income-total_expense):.2f} TL")

        cpdf.showPage()
        cpdf.save()
        buffer.seek(0)

        filename = f"gunluk_rapor_{report_date}.pdf"
        return send_file(
            buffer,
            as_attachment=True,
            download_name=filename,
            mimetype="application/pdf",
        )


# ----------------- ARAÇ İŞLEMLERİ -----------------
@app.route("/add_vehicle", methods=["POST"])
@login_required
def add_vehicle():
    plate = request.form.get("plate", "").strip()
    driver_name = request.form.get("vehicle_name", "").strip()
    capacity = request.form.get("capacity", "").strip()
    route = request.form.get("route", "").strip()

    if not plate:
        flash("Plaka zorunludur.", "danger")
        return redirect(url_for("index", tab="vehicles"))

    try:
        cap = int(capacity) if capacity else 0
    except ValueError:
        cap = 0

    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO vehicles (plate, name, capacity, route)
        VALUES (?, ?, ?, ?)
    """, (plate, driver_name, cap, route))
    conn.commit()
    conn.close()

    flash("Araç eklendi.", "success")
    return redirect(url_for("index", tab="vehicles"))


@app.route("/update_vehicle/<int:vehicle_id>", methods=["POST"])
@login_required
def update_vehicle(vehicle_id):
    plate = request.form.get("plate", "").strip()
    driver_name = request.form.get("vehicle_name", "").strip()
    capacity = request.form.get("capacity", "").strip()
    route = request.form.get("route", "").strip()

    if not plate:
        flash("Plaka boş olamaz.", "danger")
        return redirect(url_for("index", tab="vehicles"))

    try:
        cap = int(capacity) if capacity else None
    except ValueError:
        cap = None

    conn = get_conn()
    c = conn.cursor()
    if cap is None:
        c.execute(
            "UPDATE vehicles SET plate = ?, name = ?, capacity = NULL, route = ? WHERE id = ?",
            (plate, driver_name, route, vehicle_id),
        )
    else:
        c.execute(
            "UPDATE vehicles SET plate = ?, name = ?, capacity = ?, route = ? WHERE id = ?",
            (plate, driver_name, cap, route, vehicle_id),
        )
    conn.commit()
    conn.close()

    flash("Araç bilgileri güncellendi.", "success")
    return redirect(url_for("index", tab="vehicles"))

@app.route("/assign_vehicle", methods=["POST"])
@login_required
def assign_vehicle():
    student_id = request.form.get("student_id_assign")
    vehicle_id = request.form.get("vehicle_id_assign")

    if not student_id or not vehicle_id:
        flash("Öğrenci ve araç seçmelisiniz.", "danger")
        return redirect(url_for("index", tab="vehicles"))

    conn = get_conn()
    c = conn.cursor()

    today = date.today().isoformat()

    # Bu öğrencinin eski araç bağını kapat (varsa)
    c.execute("""
        UPDATE student_vehicle
        SET end_date = ?
        WHERE student_id = ?
          AND (end_date IS NULL OR end_date = '')
    """, (today, student_id))

    # Yeni araç ataması
    c.execute("""
        INSERT INTO student_vehicle (student_id, vehicle_id, start_date)
        VALUES (?, ?, ?)
    """, (student_id, vehicle_id, today))

    conn.commit()
    conn.close()

    flash("Öğrenci seçilen araca bağlandı.", "success")
    return redirect(url_for("index", tab="vehicles"))

@app.route("/vehicle_report/<int:vehicle_id>/<string:report_format>")
@login_required
def vehicle_report(vehicle_id, report_format):
    conn = get_conn()
    c = conn.cursor()

    # Araç bilgisi
    c.execute(
        "SELECT id, plate, name, capacity, route FROM vehicles WHERE id=?",
        (vehicle_id,)
    )
    vh = c.fetchone()
    if not vh:
        conn.close()
        flash("Araç bulunamadı.", "danger")
        return redirect(url_for("index", tab="vehicles"))

    # Araca bağlı aktif öğrenciler
    c.execute("""
        SELECT s.name, s.school, s.parent_name, s.phone, s.monthly_fee
        FROM student_vehicle sv
        JOIN students s ON s.id = sv.student_id
        WHERE sv.vehicle_id = ?
          AND (sv.end_date IS NULL OR sv.end_date = '')
          AND s.is_active = 1
        ORDER BY s.name
    """, (vehicle_id,))
    students_rows = c.fetchall()
    conn.close()

    total_fee = sum((r[4] or 0) for r in students_rows)

    # ---------- EXCEL (CSV) ----------
    if report_format.lower() == "excel":
        output = io.StringIO()
        writer = csv.writer(output, delimiter=';')

        writer.writerow([f"Araç Öğrenci Listesi - {vh[1]}"])
        writer.writerow([])
        writer.writerow(["Plaka", vh[1]])
        writer.writerow(["Şoför", vh[2] or ""])
        writer.writerow(["Kapasite", vh[3] or ""])
        writer.writerow(["Güzergah", vh[4] or ""])
        writer.writerow([])
        writer.writerow(["Öğrenci", "Okul", "Veli", "Telefon", "Aylık Ücret (TL)"])

        for r in students_rows:
            writer.writerow([
                r[0],           # öğrenci
                r[1] or "",     # okul
                r[2] or "",     # veli
                r[3] or "",     # telefon
                "%.2f" % (r[4] or 0),
            ])

        writer.writerow([])
        writer.writerow(["Toplam Öğrenci", len(students_rows)])
        writer.writerow(["Toplam Aylık Ücret", "%.2f TL" % total_fee])

        data = io.BytesIO(output.getvalue().encode("utf-8-sig"))
        filename = f"arac_{vh[1]}_ogrenci_listesi.csv"

        return send_file(
            data,
            as_attachment=True,
            download_name=filename,
            mimetype="text/csv",
        )

    # ---------- PDF ----------
    else:
        try:
            from reportlab.pdfgen import canvas
            from reportlab.lib.pagesizes import A4
        except ImportError:
            flash("PDF için 'reportlab' kütüphanesini kurmalısınız: pip install reportlab", "danger")
            return redirect(url_for("index", tab="vehicles"))

        buffer = io.BytesIO()
        cpdf = canvas.Canvas(buffer, pagesize=A4)
        width, height = A4

        y = height - 40
        cpdf.setFont("Helvetica-Bold", 14)
        cpdf.drawString(40, y, f"Araç Öğrenci Listesi - {vh[1]}")
        y -= 25

        cpdf.setFont("Helvetica", 10)
        cpdf.drawString(40, y, f"Plaka   : {vh[1]}")
        y -= 14
        cpdf.drawString(40, y, f"Şoför   : {vh[2] or ''}")
        y -= 14
        cpdf.drawString(40, y, f"Kapasite: {vh[3] or ''}")
        y -= 14
        cpdf.drawString(40, y, f"Güzergah: {vh[4] or ''}")
        y -= 24

        cpdf.setFont("Helvetica-Bold", 11)
        cpdf.drawString(40, y, "Öğrenci Listesi")
        y -= 18
        cpdf.setFont("Helvetica", 9)

        for r in students_rows:
            line = f"{r[0]} | {r[1] or ''} | {r[2] or ''} | {r[3] or ''} | { (r[4] or 0):.2f} TL"
            cpdf.drawString(40, y, line[:110])
            y -= 14
            if y < 60:
                cpdf.showPage()
                y = height - 40
                cpdf.setFont("Helvetica", 9)

        y -= 16
        cpdf.setFont("Helvetica-Bold", 10)
        cpdf.drawString(40, y, f"Toplam Öğrenci : {len(students_rows)}")
        y -= 14
        cpdf.drawString(40, y, f"Toplam Aylık Ücret : {total_fee:.2f} TL")

        cpdf.showPage()
        cpdf.save()
        buffer.seek(0)

        filename = f"arac_{vh[1]}_ogrenci_listesi.pdf"
        return send_file(
            buffer,
            as_attachment=True,
            download_name=filename,
            mimetype="application/pdf",
        )

def assign_vehicle():
    student_id = request.form.get("student_id_assign")
    vehicle_id = request.form.get("vehicle_id_assign")

    if not student_id or not vehicle_id:
        flash("Öğrenci ve araç seçmelisiniz.", "danger")
        return redirect(url_for("index", tab="vehicles"))

    conn = get_conn()
    c = conn.cursor()

    try:
        sid = int(student_id)
        vid = int(vehicle_id)
    except ValueError:
        flash("Seçim hatalı.", "danger")
        conn.close()
        return redirect(url_for("index", tab="vehicles"))

    # Kapasite kontrolü
    c.execute("SELECT capacity FROM vehicles WHERE id=?", (vid,))
    row = c.fetchone()
    capacity = row[0] if row else None

    current = 0
    if capacity and capacity > 0:
        c.execute("""
            SELECT COUNT(*)
            FROM student_vehicle
            WHERE vehicle_id=? AND (end_date IS NULL OR end_date='')
        """, (vid,))
        current = c.fetchone()[0] or 0

    if capacity and capacity > 0 and current >= capacity:
        flash(f"Araç kapasitesi dolu! Kapasite: {capacity}, kayıtlı öğrenci: {current}", "warning")
    else:
        c.execute("""
            INSERT INTO student_vehicle (student_id, vehicle_id, start_date)
            VALUES (?, ?, ?)
        """, (sid, vid, date.today().isoformat()))
        conn.commit()
        flash("Öğrenci araca bağlandı.", "success")

    conn.close()
    return redirect(url_for("index", tab="vehicles"))


# ----------------- GİDER / KÂR-ZARAR -----------------
@app.route("/add_expense", methods=["POST"])
@login_required
def add_expense():
    vehicle_id = request.form.get("vehicle_id_exp", "")
    exp_date = request.form.get("exp_date", "") or date.today().isoformat()
    category = request.form.get("category", "").strip()
    amount = request.form.get("amount_exp", "").replace(",", ".")
    description = request.form.get("description_exp", "").strip()

    if not category or not amount:
        flash("Kategori ve tutar zorunludur.", "danger")
        return redirect(url_for("index", tab="expenses"))

    try:
        amt = float(amount)
    except ValueError:
        flash("Tutar sayısal olmalıdır.", "danger")
        return redirect(url_for("index", tab="expenses"))

    try:
        vid = int(vehicle_id) if vehicle_id else None
    except ValueError:
        vid = None

    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO expenses (vehicle_id, exp_date, category, amount, description)
        VALUES (?, ?, ?, ?, ?)
    """, (vid, exp_date, category, amt, description))
    conn.commit()
    conn.close()

    flash("Gider kaydedildi.", "success")
    return redirect(url_for("index", tab="expenses"))


@app.route("/profit", methods=["POST"])
@login_required
def profit():
    start = request.form.get("start_date_profit", "")
    end = request.form.get("end_date_profit", "")

    if not start or not end:
        flash("Başlangıç ve bitiş tarihi girilmelidir.", "danger")
        return redirect(url_for("index", tab="expenses"))

    conn = get_conn()
    c = conn.cursor()

    c.execute("""
        SELECT COALESCE(SUM(amount),0)
        FROM payments
        WHERE pay_date >= ? AND pay_date <= ?
    """, (start, end))
    income = c.fetchone()[0] or 0.0

    c.execute("""
        SELECT COALESCE(SUM(amount),0)
        FROM expenses
        WHERE exp_date >= ? AND exp_date <= ?
    """, (start, end))
    expense = c.fetchone()[0] or 0.0

    conn.close()

    profit_val = income - expense
    flash(
        f"Dönem: {start} - {end} | Gelir: {income:.2f} TL | "
        f"Gider: {expense:.2f} TL | Kâr/Zarar: {profit_val:.2f} TL",
        "info",
    )
    return redirect(url_for("index", tab="expenses"))


# ----------------- MAIN -----------------
if __name__ == "__main__":
    create_tables()
    app.run(debug=True) # Sadece kendi bilgisayarında çalıştırırken
