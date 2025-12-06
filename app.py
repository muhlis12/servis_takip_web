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


# create_tables()
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
                (uname, generate_password_hash(pwd, method="pbkdf2:sha256", salt_length=16), fname, role),
            )
        conn.commit()
        conn.close()

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
        shutil.copyfile(DB_NAME, backup_path)

        if row:
            c.execute(
                "UPDATE meta SET value=? WHERE key='last_backup_date'",
                (today_str,),
            )
        else:
            c.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?)",
                ("last_backup_date", today_str),
            )
        conn.commit()

    conn.close()


# ----------------- SMS (MOCK) -----------------
def send_sms_to_parent(student_id, amount, pay_date, description):
    """
    Burada gerçek SMS servisi (NetGSM, İleti Merkezi vs.) ile entegrasyon yapılabilir.
    Şimdilik sadece konsola yazıyor.
    """
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT parent_name, phone, name FROM students WHERE id=?", (student_id,))
    row = c.fetchone()
    conn.close()

    if not row:
        return

    parent_name, phone, student_name = row[0], row[1], row[2]
    if not phone:
        return

    message = (
        f"{parent_name} velimiz, {student_name} için "
        f"{pay_date} tarihinde {amount:.2f} TL ödeme alınmıştır. "
        "Öz Ceylan Turizm teşekkür eder."
    )
    # Şimdilik sadece log:
    print(f"[SMS MOCK] {phone} -> {message}")


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


# --- ANA SAYFA ---
@app.route("/")
@login_required
def index():
    # Her gün ilk girişte yedek al
    ensure_daily_backup()

    conn = get_conn()
    c = conn.cursor()

    # Öğrenciler
    c.execute("""
        SELECT id, name, school, parent_name, phone, monthly_fee,
               start_year, start_month, is_active
        FROM students
        ORDER BY name
    """)
    students_rows = c.fetchall()

    # Araçlar
    c.execute("""
        SELECT id, plate, name, capacity, route, is_active
        FROM vehicles
        ORDER BY plate
    """)
    vehicles_rows = c.fetchall()

    # Okullar (distinct + sayım)
    c.execute("""
        SELECT school, COUNT(*) as cnt
        FROM students
        WHERE is_active = 1
        GROUP BY school
        ORDER BY school
    """)
    schools_stats = c.fetchall()

    # Okul-öğrenci detayı
    c.execute("""
        SELECT id, name, school, parent_name, phone, monthly_fee, is_active
        FROM students
        ORDER BY school, name
    """)
    school_students = c.fetchall()

    # Aktif öğrenci listesi (id, name)
    c.execute("""
        SELECT id, name
        FROM students
        WHERE is_active = 1
        ORDER BY name
    """)
    students_for_select = c.fetchall()

    # Ödemeler (son 50)
    c.execute("""
        SELECT p.id, s.name, p.pay_date, p.amount, p.description
        FROM payments p
        JOIN students s ON s.id = p.student_id
        ORDER BY p.pay_date DESC, p.id DESC
        LIMIT 50
    """)
    payments_rows = c.fetchall()

    # Tüm ödemelerden öğrenci bazlı toplam (aidat hesabı için)
    c.execute("""
        SELECT student_id, COALESCE(SUM(amount), 0)
        FROM payments
        GROUP BY student_id
    """)
    payment_totals_rows = c.fetchall()

    # Giderler (son 50)
    c.execute("""
        SELECT e.id, e.exp_date, e.category, e.amount, e.description,
               v.plate, v.name
        FROM expenses e
        LEFT JOIN vehicles v ON v.id = e.vehicle_id
        ORDER BY e.exp_date DESC, e.id DESC
        LIMIT 50
    """)
    expenses_rows = c.fetchall()

    conn.close()

    # Ödemelerden sözlük: {student_id: toplam_odeme}
    payment_totals = {row[0]: row[1] for row in payment_totals_rows}

    # >>> AİDAT GECİKME LİSTESİ HESABI <<<
    today = date.today()
    current_year = today.year
    current_month = today.month

    overdue_dues = []

    for s in students_rows:
        sid = s[0]
        name = s[1]
        school = s[2]
        parent_name = s[3]
        phone = s[4]
        monthly_fee = s[5] or 0
        start_year = s[6]
        start_month = s[7]
        is_active = s[8]

        if is_active != 1:
            continue
        if monthly_fee <= 0:
            continue
        if not start_year or not start_month:
            # Başlangıç tarihi yoksa gecikme hesabını atlıyoruz
            continue

        # Kaç ay geçmiş? (maks 9 ay - okul dönemi)
        start_index = start_year * 12 + start_month
        today_index = current_year * 12 + current_month
        months_passed = today_index - start_index + 1
        if months_passed < 0:
            months_passed = 0
        if months_passed > 9:
            months_passed = 9

        # Yıllık toplam ve bugüne kadar ödenmesi gereken
        annual_total = monthly_fee * 9
        expected_so_far = monthly_fee * months_passed

        total_paid = payment_totals.get(sid, 0.0)
        overdue_amount = max(expected_so_far - total_paid, 0.0)
        remaining_year = max(annual_total - total_paid, 0.0)

        if overdue_amount > 1:  # 1 TL'den küçükleri sayma
            overdue_dues.append({
                "student_id": sid,
                "student_name": name,
                "school": school,
                "parent_name": parent_name,
                "phone": phone,
                "monthly_fee": monthly_fee,
                "start_year": start_year,
                "start_month": start_month,
                "total_paid": total_paid,
                "annual_total": annual_total,
                "expected_so_far": expected_so_far,
                "overdue_amount": overdue_amount,
                "remaining_year": remaining_year,
            })

    # ÜST KARTLAR İÇİN ÖZET
    total_income = sum(p[3] for p in payments_rows) if payments_rows else 0.0
    total_expense = sum(e[3] for e in expenses_rows) if expenses_rows else 0.0

    # aktif öğrenci sayısı (is_active = 1)
    active_student_count = len([s for s in students_rows if s[8] == 1])

    summary = {
        "student_count": active_student_count,
        "vehicle_count": len(vehicles_rows),
        "total_income": total_income,
        "profit": total_income - total_expense,
    }

    active_tab = request.args.get("tab", "students")

    return render_template(
        "dashboard.html",
        students=students_rows,
        vehicles=vehicles_rows,
        schools_stats=schools_stats,
        school_students=school_students,
        students_for_select=students_for_select,
        payments=payments_rows,
        expenses=expenses_rows,
        summary=summary,
        overdue_dues=overdue_dues,
        active_tab=active_tab
    )


# ----------------- ÖĞRENCİ İŞLEMLERİ -----------------
@app.route("/add_student", methods=["POST"])
@login_required
def add_student():
    name = request.form.get("name", "").strip()
    school = request.form.get("school", "").strip()
    parent_name = request.form.get("parent_name", "").strip()
    phone = request.form.get("phone", "").strip()
    monthly_fee = request.form.get("monthly_fee", "").strip()
    annual_fee = request.form.get("annual_fee", "").strip()
    start_year = request.form.get("start_year", "").strip()
    start_month = request.form.get("start_month", "").strip()

    if not name or (not monthly_fee and not annual_fee):
        flash("Öğrenci adı ve (aylık veya yıllık) ücret zorunludur.", "danger")
        return redirect(url_for("index", tab="students"))

    monthly_fee_val = None
    annual_fee_val = None

    # Yıllık ücret varsa
    if annual_fee:
        try:
            annual_fee_val = float(annual_fee.replace(",", "."))
        except ValueError:
            flash("Yıllık ücret sayısal olmalıdır.", "danger")
            return redirect(url_for("index", tab="students"))

    # Aylık ücret varsa
    if monthly_fee:
        try:
            monthly_fee_val = float(monthly_fee.replace(",", "."))
        except ValueError:
            flash("Aylık ücret sayısal olmalıdır.", "danger")
            return redirect(url_for("index", tab="students"))

    if annual_fee_val is None and monthly_fee_val is None:
        flash("En az aylık veya yıllık ücretten birini giriniz.", "danger")
        return redirect(url_for("index", tab="students"))

    if annual_fee_val is not None and monthly_fee_val is None:
        monthly_fee_val = annual_fee_val / 9.0  # 9 aylık eğitim yılı

    if monthly_fee_val is not None and annual_fee_val is None:
        annual_fee_val = monthly_fee_val * 9.0   # 9 aylık eğitim yılı

    try:
        sy = int(start_year) if start_year else None
        sm = int(start_month) if start_month else None
    except ValueError:
        sy, sm = None, None

    conn = get_conn()
    c = conn.cursor()
    c.execute("""
    INSERT INTO students (name, school, parent_name, phone, monthly_fee, start_year, start_month, is_active)
    VALUES (?, ?, ?, ?, ?, ?, ?, 1)
    """, (name, school, parent_name, phone, monthly_fee_val, sy, sm))
    conn.commit()
    conn.close()

    flash("Öğrenci eklendi.", "success")
    return redirect(url_for("index", tab="students"))


@app.route("/update_student/<int:student_id>", methods=["POST"])
@login_required
def update_student(student_id):
    name = request.form.get("name", "").strip()
    school = request.form.get("school", "").strip()
    parent_name = request.form.get("parent_name", "").strip()
    phone = request.form.get("phone", "").strip()
    monthly_fee = request.form.get("monthly_fee", "").strip()
    annual_fee = request.form.get("annual_fee", "").strip()
    start_year = request.form.get("start_year", "").strip()
    start_month = request.form.get("start_month", "").strip()
    is_active = request.form.get("is_active", "1")

    if not name or (not monthly_fee and not annual_fee):
        flash("Öğrenci adı ve (aylık veya yıllık) ücret zorunludur.", "danger")
        return redirect(url_for("index", tab="students"))

    monthly_fee_val = None
    annual_fee_val = None

    if annual_fee:
        try:
            annual_fee_val = float(annual_fee.replace(",", "."))
        except ValueError:
            flash("Yıllık ücret sayısal olmalıdır.", "danger")
            return redirect(url_for("index", tab="students"))

    if monthly_fee:
        try:
            monthly_fee_val = float(monthly_fee.replace(",", "."))
        except ValueError:
            flash("Aylık ücret sayısal olmalıdır.", "danger")
            return redirect(url_for("index", tab="students"))

    if annual_fee_val is None and monthly_fee_val is None:
        flash("En az aylık veya yıllık ücretten birini giriniz.", "danger")
        return redirect(url_for("index", tab="students"))

    if annual_fee_val is not None and monthly_fee_val is None:
        monthly_fee_val = annual_fee_val / 9.0

    if monthly_fee_val is not None and annual_fee_val is None:
        annual_fee_val = monthly_fee_val * 9.0

    try:
        sy = int(start_year) if start_year else None
        sm = int(start_month) if start_month else None
    except ValueError:
        sy, sm = None, None

    is_active_val = 1 if is_active == "1" else 0

    conn = get_conn()
    c = conn.cursor()
    c.execute("""
    UPDATE students
    SET name=?, school=?, parent_name=?, phone=?, monthly_fee=?,
        start_year=?, start_month=?, is_active=?
    WHERE id=?
    """, (name, school, parent_name, phone, monthly_fee_val, sy, sm, is_active_val, student_id))
    conn.commit()
    conn.close()

    flash("Öğrenci güncellendi.", "success")
    return redirect(url_for("index", tab="students"))


@app.route("/delete_student/<int:student_id>", methods=["POST"])
@login_required
def delete_student(student_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE students SET is_active=0 WHERE id=?", (student_id,))
    conn.commit()
    conn.close()

    flash("Öğrenci pasife alındı.", "info")
    return redirect(url_for("index", tab="students"))


# ----------------- ÖDEME İŞLEMLERİ -----------------
@app.route("/add_payment", methods=["POST"])
@login_required
def add_payment():
    student_id = request.form.get("student_id")
    amount_str = request.form.get("amount", "").strip()
    pay_date = request.form.get("pay_date", "").strip()
    description = request.form.get("description", "").strip()

    if not student_id or not amount_str or not pay_date:
        flash("Öğrenci, tutar ve tarih zorunlu alanlardır.", "danger")
        return redirect(url_for("index", tab="payments"))

    try:
        amount = float(amount_str.replace(",", "."))
    except ValueError:
        flash("Tutar sayısal olmalıdır.", "danger")
        return redirect(url_for("index", tab="payments"))

    conn = get_conn()
    c = conn.cursor()
    c.execute("""
    INSERT INTO payments (student_id, pay_date, amount, description)
    VALUES (?, ?, ?, ?)
    """, (student_id, pay_date, amount, description))
    conn.commit()
    conn.close()

    # Ödeme sonrası veliye SMS (mock)
    try:
        send_sms_to_parent(int(student_id), amount, pay_date, description)
    except Exception as e:
        print(f"[SMS HATASI] {e}")

    flash("Ödeme eklendi.", "success")
    return redirect(url_for("index", tab="payments"))


@app.route("/payments_by_date", methods=["POST"])
@login_required
def payments_by_date():
    filter_date = request.form.get("filter_date", "").strip()

    if not filter_date:
        flash("Lütfen tarih seçiniz.", "danger")
        return redirect(url_for("index", tab="payments"))

    conn = get_conn()
    c = conn.cursor()
    c.execute("""
    SELECT p.id, s.name, p.pay_date, p.amount, p.description
    FROM payments p
    JOIN students s ON s.id = p.student_id
    WHERE p.pay_date=?
    ORDER BY s.name
    """, (filter_date,))
    rows = c.fetchall()

    conn.close()

    total_amount = sum(r[3] for r in rows) if rows else 0.0

    flash(f"{filter_date} tarihinde {len(rows)} ödeme var. Toplam: {total_amount:.2f} TL", "info")
    return redirect(url_for("index", tab="payments"))


@app.route("/daily_report", methods=["POST"])
@login_required
def daily_report():
    report_date = request.form.get("report_date", "").strip()
    report_format = request.form.get("report_format", "excel")

    if not report_date:
        flash("Rapor tarihi seçiniz.", "danger")
        return redirect(url_for("index", tab="payments"))

    conn = get_conn()
    c = conn.cursor()

    # Ödemeler
    c.execute("""
    SELECT s.name, s.school, p.amount, p.description
    FROM payments p
    JOIN students s ON s.id = p.student_id
    WHERE p.pay_date=?
    ORDER BY s.name
    """, (report_date,))
    pay_rows = c.fetchall()

    # Giderler
    c.execute("""
    SELECT e.exp_date, e.category, e.amount, e.description,
           v.plate, v.name
    FROM expenses e
    LEFT JOIN vehicles v ON v.id = e.vehicle_id
    WHERE e.exp_date=?
    ORDER BY e.id
    """, (report_date,))
    exp_rows = c.fetchall()

    conn.close()

    total_income = sum(r[2] for r in pay_rows) if pay_rows else 0.0
    total_expense = sum(r[2] for r in exp_rows) if exp_rows else 0.0
    profit = total_income - total_expense

    if report_format == "excel":
        output = io.StringIO()
        writer = csv.writer(output, delimiter=';')

        writer.writerow([f"Günlük Rapor - {report_date}"])
        writer.writerow([])

        writer.writerow(["ÖDEMELER"])
        writer.writerow(["Öğrenci", "Okul", "Tutar (TL)", "Açıklama"])
        for r in pay_rows:
            writer.writerow([r[0], r[1] or "", f"{r[2]:.2f}", r[3] or ""])

        writer.writerow([])
        writer.writerow(["GİDERLER"])
        writer.writerow(["Tarih", "Kategori", "Tutar (TL)", "Açıklama", "Araç Plaka", "Araç Adı"])
        for e in exp_rows:
            writer.writerow([
                e[0],
                e[1] or "",
                f"{e[2]:.2f}",
                e[3] or "",
                e[4] or "",
                e[5] or "",
            ])

        writer.writerow([])
        writer.writerow(["GENEL ÖZET"])
        writer.writerow(["Toplam Gelir", f"{total_income:.2f} TL"])
        writer.writerow(["Toplam Gider", f"{total_expense:.2f} TL"])
        writer.writerow(["Kâr/Zarar", f"{profit:.2f} TL"])

        data = io.BytesIO(output.getvalue().encode("utf-8-sig"))
        filename = f"gunluk_rapor_{report_date}.csv"

        return send_file(
            data,
            as_attachment=True,
            download_name=filename,
            mimetype="text/csv",
        )

    else:
        try:
            from reportlab.pdfgen import canvas
            from reportlab.lib.pagesizes import A4
        except ImportError:
            flash("PDF oluşturmak için 'reportlab' kütüphanesini kurmalısınız.", "danger")
            return redirect(url_for("index", tab="payments"))

        buffer = io.BytesIO()
        cpdf = canvas.Canvas(buffer, pagesize=A4)
        width, height = A4

        y = height - 40
        cpdf.setFont("Helvetica-Bold", 14)
        cpdf.drawString(40, y, f"Günlük Rapor - {report_date}")
        y -= 25

        cpdf.setFont("Helvetica-Bold", 11)
        cpdf.drawString(40, y, "Ödemeler")
        y -= 18
        cpdf.setFont("Helvetica", 9)

        for r in pay_rows:
            line = f"{r[0]} | {r[1] or ''} | {r[2]:.2f} TL | {r[3] or ''}"
            cpdf.drawString(40, y, line[:110])
            y -= 12
            if y < 60:
                cpdf.showPage()
                y = height - 40
                cpdf.setFont("Helvetica", 9)

        y -= 16
        cpdf.setFont("Helvetica-Bold", 11)
        cpdf.drawString(40, y, "Giderler")
        y -= 18
        cpdf.setFont("Helvetica", 9)

        for e in exp_rows:
            line = f"{e[0]} | {e[1] or ''} | {e[2]:.2f} TL | {e[3] or ''} | {e[4] or ''} | {e[5] or ''}"
            cpdf.drawString(40, y, line[:120])
            y -= 12
            if y < 60:
                cpdf.showPage()
                y = height - 40
                cpdf.setFont("Helvetica", 9)

        y -= 16
        cpdf.setFont("Helvetica-Bold", 10)
        cpdf.drawString(40, y, f"Toplam Gelir   : {total_income:.2f} TL")
        y -= 12
        cpdf.drawString(40, y, f"Toplam Gider   : {total_expense:.2f} TL")
        y -= 12
        cpdf.drawString(40, y, f"Kâr / Zarar    : {profit:.2f} TL")

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


# ----------------- ARAÇ / HAT İŞLEMLERİ -----------------
@app.route("/add_vehicle", methods=["POST"])
@login_required
def add_vehicle():
    plate = request.form.get("plate", "").strip()
    driver_name = request.form.get("driver_name", "").strip()
    capacity = request.form.get("capacity", "").strip()
    route = request.form.get("route", "").strip()

    if not plate:
        flash("Araç plakası zorunludur.", "danger")
        return redirect(url_for("index", tab="vehicles"))

    try:
        capacity_val = int(capacity) if capacity else None
    except ValueError:
        capacity_val = None

    conn = get_conn()
    c = conn.cursor()
    c.execute("""
    INSERT INTO vehicles (plate, name, capacity, route, is_active)
    VALUES (?, ?, ?, ?, 1)
    """, (plate, driver_name, capacity_val, route))
    conn.commit()
    conn.close()

    flash("Araç eklendi.", "success")
    return redirect(url_for("index", tab="vehicles"))


@app.route("/update_vehicle/<int:vehicle_id>", methods=["POST", "GET"])
@login_required
def update_vehicle(vehicle_id):
    if request.method == "GET":
        return redirect(url_for("index", tab="vehicles"))

    plate = request.form.get("plate", "").strip()
    driver_name = request.form.get("driver_name", "").strip()
    capacity = request.form.get("capacity", "").strip()
    route = request.form.get("route", "").strip()
    is_active = request.form.get("is_active", "1")

    if not plate:
        flash("Araç plakası zorunludur.", "danger")
        return redirect(url_for("index", tab="vehicles"))

    try:
        capacity_val = int(capacity) if capacity else None
    except ValueError:
        capacity_val = None

    is_active_val = 1 if is_active == "1" else 0

    conn = get_conn()
    c = conn.cursor()
    c.execute("""
    UPDATE vehicles
    SET plate=?, name=?, capacity=?, route=?, is_active=?
    WHERE id=?
    """, (plate, driver_name, capacity_val, route, is_active_val, vehicle_id))
    conn.commit()
    conn.close()

    flash("Araç güncellendi.", "success")
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

    c.execute("""
    UPDATE student_vehicle
    SET end_date=?
    WHERE student_id=? AND (end_date IS NULL OR end_date='')
    """, (today, student_id))

    c.execute("""
    INSERT INTO student_vehicle (student_id, vehicle_id, start_date)
    VALUES (?, ?, ?)
    """, (student_id, vehicle_id, today))

    conn.commit()
    conn.close()

    flash("Öğrenci araca atandı.", "success")
    return redirect(url_for("index", tab="vehicles"))


@app.route("/vehicle_report/<int:vehicle_id>/<string:report_format>")
@login_required
def vehicle_report(vehicle_id, report_format):
    conn = get_conn()
    c = conn.cursor()

    c.execute(
        "SELECT id, plate, name, capacity, route FROM vehicles WHERE id=?",
        (vehicle_id,),
    )
    vh = c.fetchone()
    if not vh:
        conn.close()
        flash("Araç bulunamadı.", "danger")
        return redirect(url_for("index", tab="vehicles"))

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
                r[0],
                r[1] or "",
                r[2] or "",
                r[3] or "",
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


# ----------------- GİDER İŞLEMLERİ -----------------
@app.route("/add_expense", methods=["POST"])
@login_required
def add_expense():
    vehicle_id = request.form.get("vehicle_id_exp", "")
    exp_date = request.form.get("exp_date", "").strip()
    category = request.form.get("category", "").strip()
    amount_str = request.form.get("amount_exp", "").strip()
    description = request.form.get("description_exp", "").strip()

    if not exp_date or not category or not amount_str:
        flash("Tarih, kategori ve tutar zorunlu alanlardır.", "danger")
        return redirect(url_for("index", tab="expenses"))

    try:
        amount = float(amount_str.replace(",", "."))
    except ValueError:
        flash("Tutar sayısal olmalıdır.", "danger")
        return redirect(url_for("index", tab="expenses"))

    vehicle_id_val = int(vehicle_id) if vehicle_id else None

    conn = get_conn()
    c = conn.cursor()
    c.execute("""
    INSERT INTO expenses (vehicle_id, exp_date, category, amount, description)
    VALUES (?, ?, ?, ?, ?)
    """, (vehicle_id_val, exp_date, category, amount, description))
    conn.commit()
    conn.close()

    flash("Gider eklendi.", "success")
    return redirect(url_for("index", tab="expenses"))


@app.route("/profit", methods=["POST"])
@login_required
def profit():
    start = request.form.get("start_date_profit", "")
    end = request.form.get("end_date_profit", "")

    if not start or not end:
        flash("Başlangıç ve bitiş tarihlerini giriniz.", "danger")
        return redirect(url_for("index", tab="expenses"))

    conn = get_conn()
    c = conn.cursor()

    c.execute("""
    SELECT COALESCE(SUM(amount), 0)
    FROM payments
    WHERE pay_date BETWEEN ? AND ?
    """, (start, end))
    income = c.fetchone()[0] or 0.0

    c.execute("""
    SELECT COALESCE(SUM(amount), 0)
    FROM expenses
    WHERE exp_date BETWEEN ? AND ?
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
    app.run(debug=True)  # Sadece kendi bilgisayarında çalıştırırken
