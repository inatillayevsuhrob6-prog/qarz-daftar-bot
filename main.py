import os
import json
import hmac
import hashlib
import logging
import asyncio
import threading
from datetime import datetime
from typing import Optional
from urllib.parse import parse_qsl
from io import BytesIO

import aiosqlite
import httpx
import uvicorn
from fastapi import FastAPI, Request, HTTPException, Depends, UploadFile, File
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart, Command
from aiogram.types import WebAppInfo, InlineKeyboardButton, InlineKeyboardMarkup, FSInputFile
from aiogram.client.default import DefaultBotProperties
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

# ============ KONFIGURATSIYA ============
BOT_TOKEN = "8651436055:AAH3FgpFyhcnBo4RXXYQpLMv1Wk4qNiCXX0"
WEBAPP_URL = "https://qarz-daftar-bot.onrender.com"
DB_PATH = "qarz_daftar.db"
IMAGES_DIR = "images"
os.makedirs(IMAGES_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
currency_cache = {"rates": {}, "updated": 0}


# ============ MODELS ============
class DebtorCreate(BaseModel):
    name: str
    phone: Optional[str] = None
    amount: float
    currency: str = "UZS"
    due_date: Optional[str] = None
    category: str = "Shaxsiy"
    note: Optional[str] = None
    rating: int = 3


class PaymentCreate(BaseModel):
    amount: float
    note: Optional[str] = None


class PinSet(BaseModel):
    pin: Optional[str] = None


# ============ DATABASE ============
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS debtors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                phone TEXT,
                category TEXT DEFAULT 'Shaxsiy',
                note TEXT,
                total_amount REAL NOT NULL,
                paid_amount REAL DEFAULT 0,
                remaining_amount REAL NOT NULL,
                currency TEXT DEFAULT 'UZS',
                status TEXT DEFAULT 'ACTIVE',
                rating INTEGER DEFAULT 3,
                image_path TEXT,
                due_date TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                debtor_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                note TEXT,
                payment_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                user_id INTEGER PRIMARY KEY,
                pin_code TEXT,
                language TEXT DEFAULT 'uz',
                theme TEXT DEFAULT 'light'
            )
        """)
        for col, typ in [("currency", "TEXT DEFAULT 'UZS'"), ("rating", "INTEGER DEFAULT 3"), ("image_path", "TEXT")]:
            try:
                await db.execute(f"ALTER TABLE debtors ADD COLUMN {col} {typ}")
            except Exception:
                pass
        await db.commit()
    logger.info("✅ Database tayyor")


# ============ VALYUTA ============
async def get_currency_rates():
    now = datetime.now().timestamp()
    if currency_cache["rates"] and now - currency_cache["updated"] < 86400:
        return currency_cache["rates"]
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get("https://open.er-api.com/v6/latest/USD")
            data = r.json()
            rates = data.get("rates", {})
            currency_cache["rates"] = {
                "USD": 1.0,
                "EUR": rates.get("EUR", 0.92),
                "UZS": rates.get("UZS", 12500)
            }
            currency_cache["updated"] = now
            logger.info("💱 Valyuta yangilandi")
    except Exception as e:
        logger.warning(f"Valyuta xatosi: {e}")
        if not currency_cache["rates"]:
            currency_cache["rates"] = {"USD": 1.0, "EUR": 0.92, "UZS": 12500}
    return currency_cache["rates"]


def convert_amount(amount, from_cur, to_cur, rates):
    usd = amount / rates.get(from_cur, 1)
    return usd * rates.get(to_cur, 1)


# ============ AUTH ============
def verify_telegram_auth(init_data):
    if not init_data:
        raise HTTPException(401, "Auth yo'q")
    try:
        data = dict(parse_qsl(init_data))
        received_hash = data.pop("hash", None)
        if not received_hash:
            raise HTTPException(401)
        check_string = "\n".join(f"{k}={data[k]}" for k in sorted(data.keys()))
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calculated_hash, received_hash):
            raise HTTPException(401)
        user_data = json.loads(data.get("user", "{}"))
        return {"telegram_id": user_data.get("id"), "username": user_data.get("username", ""),
                "first_name": user_data.get("first_name", ""), "last_name": user_data.get("last_name", "")}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(401)


async def get_current_user(request: Request):
    init_data = request.headers.get("X-Telegram-Init-Data") or request.query_params.get("initData", "")
    if not init_data:
        return {"telegram_id": 123456, "username": "test", "first_name": "Test", "last_name": "User"}
    return verify_telegram_auth(init_data)


# ============ FASTAPI ============
app = FastAPI(title="Qarz Daftar Pro")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
async def startup():
    await init_db()
    await get_currency_rates()
    logger.info("🚀 Server ishga tushdi")


# ============ SOZLAMALAR (PIN) ============
@app.get("/api/settings")
async def get_settings(user: dict = Depends(get_current_user)):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT pin_code, language, theme FROM settings WHERE user_id=?", (user["telegram_id"],))
        row = await cur.fetchone()
        if not row:
            return {"pin_set": False, "pin_code": None, "language": "uz", "theme": "light"}
        return {"pin_set": bool(row[0] and len(row[0]) == 4), "pin_code": row[0] if row[0] and len(row[0]) == 4 else None,
                "language": row[1] or "uz", "theme": row[2] or "light"}


@app.post("/api/settings/pin")
async def set_pin(data: PinSet, user: dict = Depends(get_current_user)):
    async with aiosqlite.connect(DB_PATH) as db:
        if data.pin and len(data.pin) == 4 and data.pin.isdigit():
            await db.execute("INSERT OR REPLACE INTO settings (user_id, pin_code) VALUES (?, ?)",
                             (user["telegram_id"], data.pin))
            await db.commit()
            return {"ok": True, "pin_set": True}
        else:
            await db.execute("INSERT OR REPLACE INTO settings (user_id, pin_code) VALUES (?, NULL)",
                             (user["telegram_id"],))
            await db.commit()
            return {"ok": True, "pin_set": False}


@app.post("/api/settings/pin/verify")
async def verify_pin(data: PinSet, user: dict = Depends(get_current_user)):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT pin_code FROM settings WHERE user_id=?", (user["telegram_id"],))
        row = await cur.fetchone()
    if not row or not row[0]:
        return {"ok": True, "valid": True, "no_pin": True}
    return {"ok": True, "valid": data.pin == row[0]}


# ============ STATISTIKA ============
@app.get("/api/stats")
async def get_stats(user: dict = Depends(get_current_user)):
    rates = await get_currency_rates()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM debtors WHERE user_id=?", (user["telegram_id"],))
        rows = [dict(r) for r in await cur.fetchall()]
    total_given = total_paid = total_rem = 0
    for d in rows:
        c = d.get("currency") or "UZS"
        total_given += convert_amount(d["total_amount"], c, "UZS", rates)
        total_paid += convert_amount(d["paid_amount"], c, "UZS", rates)
        total_rem += convert_amount(d["remaining_amount"], c, "UZS", rates)
    return {"total_debtors": len(rows), "total_given": total_given, "total_paid": total_paid,
            "total_remaining": total_rem, "rates": rates}


@app.get("/api/debtors")
async def get_debtors(user: dict = Depends(get_current_user)):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM debtors WHERE user_id=? ORDER BY created_at DESC", (user["telegram_id"],))
        return [dict(r) for r in await cur.fetchall()]


@app.get("/api/debtors/top")
async def top_debtors(user: dict = Depends(get_current_user)):
    rates = await get_currency_rates()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM debtors WHERE user_id=? AND remaining_amount>0", (user["telegram_id"],))
        rows = [dict(r) for r in await cur.fetchall()]
    for d in rows:
        c = d.get("currency") or "UZS"
        d["remaining_uzs"] = convert_amount(d["remaining_amount"], c, "UZS", rates)
    rows.sort(key=lambda x: x["remaining_uzs"], reverse=True)
    return rows[:10]


@app.get("/api/payments/{debtor_id}")
async def get_payments(debtor_id: int, user: dict = Depends(get_current_user)):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("""
            SELECT p.* FROM payments p JOIN debtors d ON p.debtor_id=d.id
            WHERE p.debtor_id=? AND d.user_id=? ORDER BY p.payment_date DESC
        """, (debtor_id, user["telegram_id"]))
        return [dict(r) for r in await cur.fetchall()]


@app.post("/api/debtors")
async def create_debtor(d: DebtorCreate, user: dict = Depends(get_current_user)):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            INSERT INTO debtors (user_id,name,phone,category,note,total_amount,paid_amount,remaining_amount,currency,rating,due_date)
            VALUES (?,?,?,?,?,?,0,?,?,?,?,?)
        """, (user["telegram_id"], d.name, d.phone, d.category, d.note, d.amount, d.amount, d.currency, d.rating, d.due_date))
        await db.commit()
        return {"id": cur.lastrowid}


@app.put("/api/debtors/{debtor_id}/pay")
async def add_payment(debtor_id: int, p: PaymentCreate, user: dict = Depends(get_current_user)):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT total_amount,paid_amount,remaining_amount FROM debtors WHERE id=? AND user_id=?", (debtor_id, user["telegram_id"]))
        row = await cur.fetchone()
        if not row:
            raise HTTPException(404)
        if p.amount <= 0 or p.amount > row[2]:
            raise HTTPException(400, f"Qolgan: {row[2]}")
        new_paid = row[1] + p.amount
        new_rem = row[2] - p.amount
        new_status = "PAID" if new_rem == 0 else "ACTIVE"
        await db.execute("INSERT INTO payments (debtor_id,amount,note) VALUES (?,?,?)", (debtor_id, p.amount, p.note))
        await db.execute("UPDATE debtors SET paid_amount=?,remaining_amount=?,status=? WHERE id=?", (new_paid, new_rem, new_status, debtor_id))
        await db.commit()
        return {"remaining": new_rem, "status": new_status}


@app.put("/api/debtors/{debtor_id}/rating")
async def set_rating(debtor_id: int, rating: int, user: dict = Depends(get_current_user)):
    if not 1 <= rating <= 5:
        raise HTTPException(400)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE debtors SET rating=? WHERE id=? AND user_id=?", (rating, debtor_id, user["telegram_id"]))
        await db.commit()
    return {"ok": True}


@app.delete("/api/debtors/{debtor_id}")
async def delete_debtor(debtor_id: int, user: dict = Depends(get_current_user)):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT image_path FROM debtors WHERE id=? AND user_id=?", (debtor_id, user["telegram_id"]))
        row = await cur.fetchone()
        if row and row[0] and os.path.exists(row[0]):
            try:
                os.remove(row[0])
            except Exception:
                pass
        await db.execute("DELETE FROM payments WHERE debtor_id=?", (debtor_id,))
        await db.execute("DELETE FROM debtors WHERE id=? AND user_id=?", (debtor_id, user["telegram_id"]))
        await db.commit()
    return {"ok": True}


# ============ RASM YUKLASH ============
@app.post("/api/debtors/{debtor_id}/image")
async def upload_image(debtor_id: int, file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    ext = file.filename.split(".")[-1].lower() if file.filename else "jpg"
    if ext not in ["jpg", "jpeg", "png", "webp"]:
        ext = "jpg"
    path = os.path.join(IMAGES_DIR, f"{user['telegram_id']}_{debtor_id}_{int(datetime.now().timestamp())}.{ext}")
    content = await file.read()
    with open(path, "wb") as f:
        f.write(content)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE debtors SET image_path=? WHERE id=? AND user_id=?", (path, debtor_id, user["telegram_id"]))
        await db.commit()
    return {"ok": True, "url": f"/images/{os.path.basename(path)}"}


@app.get("/images/{filename}")
async def serve_image(filename: str):
    path = os.path.join(IMAGES_DIR, filename)
    if not os.path.exists(path):
        raise HTTPException(404)
    with open(path, "rb") as f:
        content = f.read()
    media_type = "image/png" if filename.endswith("png") else "image/jpeg"
    return Response(content, media_type=media_type)


# ============ PDF ============
@app.get("/api/export/pdf")
async def export_pdf(user: dict = Depends(get_current_user), lang: str = "uz"):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT COUNT(*) n, COALESCE(SUM(total_amount),0) g, COALESCE(SUM(paid_amount),0) p, COALESCE(SUM(remaining_amount),0) r FROM debtors WHERE user_id=?", (user["telegram_id"],))
        s = dict(await cur.fetchone())
        cur = await db.execute("SELECT name,phone,total_amount,paid_amount,remaining_amount,currency,status,rating,due_date FROM debtors WHERE user_id=? ORDER BY remaining_amount DESC", (user["telegram_id"],))
        debtors = [dict(r) for r in await cur.fetchall()]

    tr = {
        "uz": {"title": "QARZ DAFTAR HISOBOTI", "date": "Sana", "debtors": "Qarzdorlar", "given": "Berilgan", "paid": "Tolangan", "rem": "Qolgan", "list": "Qarzdorlar ro'yxati", "name": "Ism", "phone": "Tel", "total": "Jami", "status": "Holat"},
        "ru": {"title": "ОТЧЁТ ДОЛГОВОЙ КНИГИ", "date": "Дата", "debtors": "Должники", "given": "Выдано", "paid": "Оплачено", "rem": "Остаток", "list": "Список должников", "name": "Имя", "phone": "Тел", "total": "Всего", "status": "Статус"},
        "en": {"title": "DEBT BOOK REPORT", "date": "Date", "debtors": "Debtors", "given": "Given", "paid": "Paid", "rem": "Remaining", "list": "Debtors List", "name": "Name", "phone": "Phone", "total": "Total", "status": "Status"}
    }.get(lang, {"title": "REPORT", "date": "Date", "debtors": "Debtors", "given": "Given", "paid": "Paid", "rem": "Remaining", "list": "List", "name": "Name", "phone": "Phone", "total": "Total", "status": "Status"})

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)
    styles = getSampleStyleSheet()
    title_st = ParagraphStyle('T', parent=styles['Heading1'], fontSize=22, textColor=colors.HexColor('#667eea'), alignment=1, spaceAfter=16)
    head_st = ParagraphStyle('H', parent=styles['Heading2'], fontSize=14, textColor=colors.HexColor('#764ba2'), spaceBefore=14, spaceAfter=10)
    norm_st = ParagraphStyle('N', parent=styles['Normal'], fontSize=11, spaceAfter=4)

    els = [Paragraph(tr["title"], title_st), Paragraph(f"{tr['date']}: {datetime.now().strftime('%d.%m.%Y %H:%M')}", norm_st), Spacer(1, 16)]

    stats_data = [[tr["debtors"], tr["given"]+" (UZS)", tr["paid"]+" (UZS)", tr["rem"]+" (UZS)"],
                  [str(s["n"]), f"{s['g']:,.0f}", f"{s['p']:,.0f}", f"{s['r']:,.0f}"]]
    st_tbl = Table(stats_data, colWidths=[100, 150, 150, 150])
    st_tbl.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#667eea')), ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                                ('ALIGN', (0, 0), (-1, -1), 'CENTER'), ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'), ('FONTSIZE', (0, 0), (-1, 0), 11),
                                ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#f8fafc')), ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e5e7eb')),
                                ('FONTSIZE', (0, 1), (-1, -1), 12), ('TOPPADDING', (0, 0), (-1, -1), 8), ('BOTTOMPADDING', (0, 0), (-1, -1), 8)]))
    els.append(st_tbl)
    els.append(Spacer(1, 20))
    els.append(Paragraph(tr["list"], head_st))

    tbl_data = [[tr["name"], tr["phone"], tr["total"], tr["rem"], tr["status"]]]
    for d in debtors:
        c = d.get("currency") or "UZS"
        tbl_data.append([d["name"][:20], d["phone"] or "-", f"{d['total_amount']:,.0f} {c}", f"{d['remaining_amount']:,.0f}", d["status"]])

    if len(tbl_data) > 1:
        d_tbl = Table(tbl_data, colWidths=[140, 100, 110, 110, 80])
        d_tbl.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#764ba2')), ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                                   ('ALIGN', (2, 0), (-1, -1), 'RIGHT'), ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'), ('FONTSIZE', (0, 0), (-1, -1), 9),
                                   ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e5e7eb')), ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8fafc')]),
                                   ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6)]))
        els.append(d_tbl)
    else:
        els.append(Paragraph("—", norm_st))

    doc.build(els)
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/pdf", headers={"Content-Disposition": f"attachment; filename=qarz_hisobot_{lang}.pdf"})


@app.get("/api/export/csv")
async def export_csv(user: dict = Depends(get_current_user)):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT name,phone,total_amount,currency,paid_amount,remaining_amount,status,rating,due_date FROM debtors WHERE user_id=?", (user["telegram_id"],))
        rows = await cur.fetchall()
    lines = ["Ism;Telefon;Jami;Valyuta;Tolangan;Qolgan;Holat;Reyting;Muddat"]
    for r in rows:
        lines.append(";".join("" if x is None else str(x) for x in r))
    return Response("\n".join(lines), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=qarzlar.csv"})


@app.get("/api/remind/{debtor_id}")
async def remind_text(debtor_id: int, user: dict = Depends(get_current_user)):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT name, remaining_amount, currency, due_date, phone FROM debtors WHERE id=? AND user_id=?", (debtor_id, user["telegram_id"]))
        d = await cur.fetchone()
    if not d:
        raise HTTPException(404)
    c = d["currency"] or "UZS"
    txt = (f"Assalomu alaykum, {d['name']}!\n\n"
           f"Sizda {d['remaining_amount']:,.0f} {c} qarz bor.\n"
           f"Iltimos, imkon qadar tezroq to'lang.\n\nRahmat! 🙏")
    return {"text": txt, "phone": d["phone"]}


@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    await dp.feed_update(bot, types.Update(**data))
    return {"ok": True}


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="uz"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>Qarz Daftar Pro</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}
:root{--bg:linear-gradient(135deg,#667eea,#764ba2 50%,#f093fb);--card:rgba(255,255,255,.98);--pri:#667eea;--acc:#f093fb;--ok:#10b981;--err:#ef4444;--warn:#f59e0b;--txt:#1f2937;--mut:#6b7280;--sh:0 8px 32px rgba(0,0,0,.12);--glow:0 0 40px rgba(102,126,234,.4)}
[data-theme=dark]{--bg:linear-gradient(135deg,#0f172a,#1e293b 50%,#312e81);--card:rgba(30,41,59,.98);--txt:#f8fafc;--mut:#94a3b8;--sh:0 8px 32px rgba(0,0,0,.3)}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:var(--bg);min-height:100vh;color:var(--txt);padding:20px;padding-bottom:110px}
body::before{content:'';position:fixed;inset:-50%;background:radial-gradient(circle at 20% 50%,rgba(102,126,234,.15),transparent 50%),radial-gradient(circle at 80% 80%,rgba(240,147,251,.15),transparent 50%);z-index:0;animation:bg 20s ease-in-out infinite}
@keyframes bg{0%,100%{transform:translate(0,0)}33%{transform:translate(30px,-30px)}66%{transform:translate(-20px,20px)}}
.container{max-width:600px;margin:0 auto;position:relative;z-index:1}
.header{display:flex;justify-content:space-between;align-items:center;margin-bottom:22px}
.logo{display:flex;align-items:center;gap:12px}
.logo-icon{width:50px;height:50px;background:linear-gradient(135deg,var(--pri),var(--acc));border-radius:16px;display:flex;align-items:center;justify-content:center;font-size:24px;box-shadow:var(--glow);animation:pulse 2s infinite}
@keyframes pulse{0%,100%{transform:scale(1)}50%{transform:scale(1.05)}}
.logo-text h1{font-size:22px;font-weight:900;background:linear-gradient(135deg,var(--pri),var(--acc));-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.logo-text p{font-size:11px;color:var(--mut)}
.ha{display:flex;gap:8px}
.ib{width:42px;height:42px;border-radius:50%;background:var(--card);border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:17px;box-shadow:var(--sh);color:var(--txt)}
.sg{display:grid;grid-template-columns:repeat(2,1fr);gap:12px;margin-bottom:22px}
.sc{background:var(--card);padding:18px;border-radius:22px;box-shadow:var(--sh);animation:fu .5s backwards}
.sc:nth-child(1){animation-delay:.1s}.sc:nth-child(2){animation-delay:.2s}.sc:nth-child(3){animation-delay:.3s}.sc:nth-child(4){animation-delay:.4s}
.si{width:46px;height:46px;border-radius:14px;display:flex;align-items:center;justify-content:center;font-size:20px;margin-bottom:10px;color:#fff}
.si.b{background:linear-gradient(135deg,#667eea,#764ba2)}.si.g{background:linear-gradient(135deg,#10b981,#059669)}.si.o{background:linear-gradient(135deg,#f59e0b,#d97706)}.si.p{background:linear-gradient(135deg,#8b5cf6,#7c3aed)}
.sl{font-size:11px;color:var(--mut);font-weight:600;text-transform:uppercase}
.sv{font-size:20px;font-weight:900;margin-top:2px}
.mb{width:100%;padding:17px;background:linear-gradient(135deg,var(--pri),var(--acc));border:none;border-radius:20px;color:#fff;font-size:16px;font-weight:800;cursor:pointer;margin-bottom:22px;box-shadow:0 12px 40px rgba(102,126,234,.5);display:flex;align-items:center;justify-content:center;gap:10px}
.st{font-size:18px;font-weight:800;margin-bottom:14px;display:flex;align-items:center;gap:8px}
.st i{color:var(--pri)}
.dc{background:var(--card);padding:18px;border-radius:22px;margin-bottom:14px;box-shadow:var(--sh);position:relative;overflow:hidden;animation:si .4s backwards}
.dc::before{content:'';position:absolute;left:0;top:0;bottom:0;width:5px;background:linear-gradient(180deg,var(--pri),var(--acc))}
.dc.overdue::before{background:linear-gradient(180deg,var(--err),#dc2626)}.dc.paid::before{background:linear-gradient(180deg,var(--ok),#059669)}
.dh{display:flex;justify-content:space-between;align-items:start;margin-bottom:10px}
.dn{font-size:17px;font-weight:800}
.da{font-size:18px;font-weight:900;background:linear-gradient(135deg,var(--pri),var(--acc));-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.di{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:10px;font-size:12px;color:var(--mut)}
.di span{display:flex;align-items:center;gap:5px}.di i{color:var(--pri)}
.di a{color:var(--pri);text-decoration:none;font-weight:600}
.badge{display:inline-block;padding:5px 11px;border-radius:18px;font-size:10px;font-weight:700;margin-bottom:10px;text-transform:uppercase}
.ba{background:rgba(102,126,234,.15);color:var(--pri)}.bo{background:rgba(239,68,68,.15);color:var(--err)}.bp{background:rgba(16,185,129,.15);color:var(--ok)}
.dd{font-size:11px;color:var(--mut);margin-bottom:12px;padding:9px;background:rgba(0,0,0,.03);border-radius:10px}
.rat{color:#f59e0b;font-size:14px;letter-spacing:2px;margin-top:2px}
.dac{display:flex;gap:8px;flex-wrap:wrap}
.ba2{flex:1;min-width:70px;padding:11px;border:none;border-radius:12px;font-size:13px;font-weight:700;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:6px}
.bpay{background:linear-gradient(135deg,var(--ok),#059669);color:#fff}
.bwa{background:#25D366;color:#fff}.brem{background:linear-gradient(135deg,#3b82f6,#2563eb);color:#fff}
.bdel{background:rgba(239,68,68,.15);color:var(--err)}
.bhist{background:rgba(139,92,246,.15);color:#8b5cf6}
.bimg{background:rgba(245,158,11,.15);color:#f59e0b}
.es{text-align:center;padding:50px 20px;color:var(--mut)}
.ei{font-size:60px;margin-bottom:14px;opacity:.2}
.mo{display:none;position:fixed;inset:0;background:rgba(0,0,0,.7);backdrop-filter:blur(12px);z-index:1000;align-items:flex-end;justify-content:center}
.mo.ac{display:flex}
.mc{background:var(--card);width:100%;max-width:600px;border-radius:30px 30px 0 0;padding:28px 22px;max-height:90vh;overflow-y:auto}
.mh{display:flex;justify-content:space-between;align-items:center;margin-bottom:22px}
.mt{font-size:22px;font-weight:900;background:linear-gradient(135deg,var(--pri),var(--acc));-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.mx{width:36px;height:36px;border-radius:50%;background:rgba(0,0,0,.1);border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:17px;color:var(--txt)}
.fg{margin-bottom:18px}
.fl{display:block;font-size:12px;font-weight:700;margin-bottom:7px;text-transform:uppercase}
.fi{width:100%;padding:14px;border:2px solid rgba(0,0,0,.1);border-radius:14px;font-size:15px;background:rgba(0,0,0,.03);color:var(--txt)}
.fi:focus{outline:none;border-color:var(--pri)}
.fr{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.bs{width:100%;padding:16px;background:linear-gradient(135deg,var(--pri),var(--acc));border:none;border-radius:16px;color:#fff;font-size:16px;font-weight:800;cursor:pointer;margin-top:6px}
.toast{position:fixed;top:28px;left:50%;transform:translateX(-50%) translateY(-150px);background:linear-gradient(135deg,var(--ok),#059669);color:#fff;padding:14px 28px;border-radius:28px;font-weight:700;font-size:14px;box-shadow:0 15px 40px rgba(0,0,0,.3);z-index:2000;opacity:0;transition:.4s}
.toast.sh{opacity:1;transform:translateX(-50%) translateY(0)}.toast.er{background:linear-gradient(135deg,var(--err),#dc2626)}
#cc{position:fixed;inset:0;pointer-events:none;z-index:1500}
.page{display:none}.page.ac{display:block}
.cc{background:var(--card);border-radius:22px;padding:18px;margin-bottom:18px;box-shadow:var(--sh)}
.nb{position:fixed;bottom:0;left:0;right:0;background:var(--card);backdrop-filter:blur(20px);display:flex;justify-content:space-around;padding:8px 0 calc(8px + env(safe-area-inset-bottom));box-shadow:0 -8px 32px rgba(0,0,0,.1);z-index:100}
.ni{display:flex;flex-direction:column;align-items:center;gap:3px;padding:5px 10px;border:none;background:none;cursor:pointer;color:var(--mut);font-size:10px;font-weight:600}
.ni i{font-size:18px}.ni.ac{color:var(--pri)}.ni.ac i{transform:translateY(-3px) scale(1.1)}
.ls{display:flex;gap:7px;margin-bottom:16px}
.lb{flex:1;padding:11px;border:2px solid rgba(0,0,0,.1);border-radius:12px;background:var(--card);cursor:pointer;font-weight:700;font-size:12px;color:var(--txt)}
.lb.ac{background:linear-gradient(135deg,var(--pri),var(--acc));color:#fff;border-color:transparent}
.rb{background:var(--card);border-radius:22px;padding:20px;box-shadow:var(--sh)}
.pin-screen{position:fixed;inset:0;background:var(--bg);z-index:3000;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:30px}
.pin-box{background:var(--card);padding:40px 30px;border-radius:28px;box-shadow:var(--sh);text-align:center;width:100%;max-width:340px}
.pin-title{font-size:22px;font-weight:900;margin-bottom:8px;background:linear-gradient(135deg,var(--pri),var(--acc));-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.pin-sub{color:var(--mut);font-size:13px;margin-bottom:24px}
.pin-dots{display:flex;gap:14px;justify-content:center;margin-bottom:28px}
.pin-dot{width:16px;height:16px;border-radius:50%;background:rgba(0,0,0,.1);transition:.2s}
.pin-dot.filled{background:var(--pri);transform:scale(1.2)}
.pin-pad{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
.pin-key{padding:18px;border:none;border-radius:16px;background:rgba(0,0,0,.04);font-size:22px;font-weight:700;cursor:pointer;color:var(--txt)}
.pin-key:active{background:var(--pri);color:#fff}
.pin-key.fn{background:transparent;font-size:16px}
.pay-item{padding:12px;background:rgba(0,0,0,.03);border-radius:12px;margin-bottom:8px;display:flex;justify-content:space-between;align-items:center}
.cur-sel{display:flex;gap:6px;margin-bottom:14px}
.cur-btn{flex:1;padding:10px;border:2px solid rgba(0,0,0,.1);border-radius:12px;background:var(--card);cursor:pointer;font-weight:700;font-size:13px;color:var(--txt)}
.cur-btn.ac{background:linear-gradient(135deg,var(--pri),var(--acc));color:#fff;border-color:transparent}
@keyframes fu{from{opacity:0;transform:translateY(30px)}to{opacity:1;transform:translateY(0)}}
@keyframes si{from{opacity:0;transform:translateX(-30px)}to{opacity:1;transform:translateX(0)}}
@media(max-width:480px){.fr{grid-template-columns:1fr}.sv{font-size:18px}}
</style></head>
<body>
<canvas id="cc"></canvas>
<div id="toast" class="toast">OK</div>

<div id="pinScreen" class="pin-screen" style="display:none">
<div class="pin-box">
<div class="pin-title">🔐 PIN</div>
<div class="pin-sub" id="pinSub">Kirish uchun PIN kiriting</div>
<div class="pin-dots" id="pinDots"><div class="pin-dot"></div><div class="pin-dot"></div><div class="pin-dot"></div><div class="pin-dot"></div></div>
<div class="pin-pad">
<button class="pin-key" onclick="pinKey('1')">1</button><button class="pin-key" onclick="pinKey('2')">2</button><button class="pin-key" onclick="pinKey('3')">3</button>
<button class="pin-key" onclick="pinKey('4')">4</button><button class="pin-key" onclick="pinKey('5')">5</button><button class="pin-key" onclick="pinKey('6')">6</button>
<button class="pin-key" onclick="pinKey('7')">7</button><button class="pin-key" onclick="pinKey('8')">8</button><button class="pin-key" onclick="pinKey('9')">9</button>
<button class="pin-key fn" onclick="pinClear()">C</button><button class="pin-key" onclick="pinKey('0')">0</button><button class="pin-key fn" onclick="pinDel()">⌫</button>
</div>
</div>
</div>

<div class="container" id="mainApp" style="display:none">
<div class="header">
<div class="logo"><div class="logo-icon">💎</div><div class="logo-text"><h1 data-i18n="appTitle">Qarz Daftar</h1><p>Pro Edition</p></div></div>
<div class="ha">
<button class="ib" onclick="toggleLang()"><i class="fas fa-language"></i></button>
<button class="ib" onclick="toggleTheme()"><i class="fas fa-moon" id="ti"></i></button>
</div>
</div>

<div id="page-home" class="page ac">
<div class="sg">
<div class="sc"><div class="si b"><i class="fas fa-wallet"></i></div><div class="sl" data-i18n="totalGiven">Jami berilgan</div><div class="sv" id="tg">0</div></div>
<div class="sc"><div class="si o"><i class="fas fa-clock"></i></div><div class="sl" data-i18n="remaining">Qolgan</div><div class="sv" id="tr">0</div></div>
<div class="sc"><div class="si g"><i class="fas fa-check-circle"></i></div><div class="sl" data-i18n="paid">Tolangan</div><div class="sv" id="tp">0</div></div>
<div class="sc"><div class="si p"><i class="fas fa-users"></i></div><div class="sl" data-i18n="debtors">Qarzdorlar</div><div class="sv" id="tc">0</div></div>
</div>
<button class="mb" onclick="openAdd()"><i class="fas fa-plus-circle"></i><span data-i18n="addDebtor">Yangi qarzdor</span></button>
<div class="st"><i class="fas fa-list-ul"></i><span data-i18n="debtorList">Qarzdorlar</span></div>
<div id="dl"></div>
</div>

<div id="page-top" class="page">
<div class="st"><i class="fas fa-trophy"></i><span data-i18n="topDebtors">TOP Qarzdorlar</span></div>
<div id="topList"></div>
</div>

<div id="page-stats" class="page">
<div class="st"><i class="fas fa-chart-pie"></i><span data-i18n="statistics">Statistika</span></div>
<div class="cc"><canvas id="pie"></canvas></div>
<div class="cc"><canvas id="bar"></canvas></div>
</div>

<div id="page-report" class="page">
<div class="st"><i class="fas fa-file-pdf"></i><span data-i18n="reports">Hisobotlar</span></div>
<div class="ls">
<button class="lb ac" onclick="setRL('uz',this)">🇺🇿 O'zbek</button>
<button class="lb" onclick="setRL('ru',this)">🇷🇺 Рус</button>
<button class="lb" onclick="setRL('en',this)">🇬🇧 EN</button>
</div>
<button class="mb" onclick="downloadPDF()"><i class="fas fa-download"></i><span data-i18n="downloadPdf">PDF yuklab olish</span></button>
<button class="mb" style="background:linear-gradient(135deg,#10b981,#059669)" onclick="downloadCSV()"><i class="fas fa-file-csv"></i><span>CSV yuklab olish</span></button>
</div>

<div id="page-settings" class="page">
<div class="st"><i class="fas fa-cog"></i><span data-i18n="settings">Sozlamalar</span></div>
<div class="cc">
<h3 style="margin-bottom:12px">🔐 PIN kod</h3>
<p style="font-size:13px;color:var(--mut);margin-bottom:14px">Ilovani himoyalash uchun 4 xonali PIN</p>
<div id="pinStatus" style="margin-bottom:12px;font-weight:700"></div>
<button class="bs" onclick="changePin()">🔑 PIN o'rnatish / O'zgartirish</button>
<button class="bs" style="background:linear-gradient(135deg,#ef4444,#dc2626);margin-top:8px" onclick="removePin()">🗑️ PIN o'chirish</button>
</div>
<div class="cc">
<h3 style="margin-bottom:12px">💱 Valyuta kurslari</h3>
<div id="ratesBox" style="font-size:14px"></div>
<p style="font-size:11px;color:var(--mut);margin-top:10px">Kurslar har kuni avtomatik yangilanadi</p>
</div>
</div>
</div>

<div class="nb">
<button class="ni ac" onclick="sw('home',this)"><i class="fas fa-home"></i><span data-i18n="navHome">Bosh</span></button>
<button class="ni" onclick="sw('top',this)"><i class="fas fa-trophy"></i><span>TOP</span></button>
<button class="ni" onclick="sw('stats',this)"><i class="fas fa-chart-pie"></i><span>Stat</span></button>
<button class="ni" onclick="sw('report',this)"><i class="fas fa-file-pdf"></i><span>PDF</span></button>
<button class="ni" onclick="sw('settings',this)"><i class="fas fa-cog"></i><span>⚙️</span></button>
</div>

<div id="addM" class="mo"><div class="mc">
<div class="mh"><h2 class="mt" data-i18n="newDebtor">Yangi qarzdor</h2><button class="mx" onclick="cm('addM')"><i class="fas fa-times"></i></button></div>
<div class="fg"><label class="fl" data-i18n="name">Ism *</label><input class="fi" id="an" placeholder="Akramov Jasur"></div>
<div class="fr">
<div class="fg"><label class="fl" data-i18n="phone">Telefon</label><input class="fi" id="aph" placeholder="+998..."></div>
<div class="fg"><label class="fl" data-i18n="amount">Summa *</label><input type="number" class="fi" id="aam" placeholder="1000000"></div>
</div>
<div class="fg"><label class="fl" data-i18n="currency">Valyuta</label>
<div class="cur-sel"><button class="cur-btn ac" onclick="selCur('UZS',this)">🇺🇿 UZS</button><button class="cur-btn" onclick="selCur('USD',this)">🇸 USD</button><button class="cur-btn" onclick="selCur('EUR',this)">🇪🇺 EUR</button></div>
<input type="hidden" id="acur" value="UZS"></div>
<div class="fr">
<div class="fg"><label class="fl" data-i18n="dueDate">Muddat</label><input type="date" class="fi" id="adt"></div>
<div class="fg"><label class="fl" data-i18n="category">Kategoriya</label><select class="fi" id="aca"><option>Shaxsiy</option><option>Biznes</option><option>Oila</option><option>Do'st</option></select></div>
</div>
<div class="fg"><label class="fl">⭐ Ishonch reytingi</label>
<div class="rat" id="arat"></div>
<input type="hidden" id="aratv" value="3"></div>
<div class="fg"><label class="fl" data-i18n="note">Izoh</label><textarea class="fi" id="ano" rows="2"></textarea></div>
<button class="bs" onclick="addD()"><i class="fas fa-check"></i> <span data-i18n="save">Saqlash</span></button>
</div></div>

<div id="payM" class="mo"><div class="mc">
<div class="mh"><h2 class="mt" data-i18n="addPayment">To'lov</h2><button class="mx" onclick="cm('payM')"><i class="fas fa-times"></i></button></div>
<input type="hidden" id="pid">
<div class="fg"><label class="fl" data-i18n="amount">Summa</label><input type="number" class="fi" id="pam"></div>
<div class="fg"><label class="fl" data-i18n="note">Izoh</label><input class="fi" id="pno"></div>
<button class="bs" onclick="payD()"><i class="fas fa-check"></i> <span data-i18n="confirm">Tasdiqlash</span></button>
</div></div>

<div id="histM" class="mo"><div class="mc">
<div class="mh"><h2 class="mt">💳 To'lovlar tarixi</h2><button class="mx" onclick="cm('histM')"><i class="fas fa-times"></i></button></div>
<div id="histList"></div>
</div></div>

<div id="imgM" class="mo"><div class="mc">
<div class="mh"><h2 class="mt">📸 Rasm</h2><button class="mx" onclick="cm('imgM')"><i class="fas fa-times"></i></button></div>
<div id="imgPrev" style="text-align:center;margin-bottom:16px"></div>
<input type="file" id="imgFile" accept="image/*" style="margin-bottom:14px">
<input type="hidden" id="imgId">
<button class="bs" onclick="uploadImg()"><i class="fas fa-upload"></i> Yuklash</button>
</div></div>

<div id="remM" class="mo"><div class="mc">
<div class="mh"><h2 class="mt">🔔 Eslatma</h2><button class="mx" onclick="cm('remM')"><i class="fas fa-times"></i></button></div>
<textarea class="fi" id="remTxt" rows="6"></textarea>
<p style="font-size:12px;color:var(--mut);margin:12px 0">Nusxalab, qarzdorga Telegram/WhatsApp orqali yuboring</p>
<button class="bs" onclick="copyRem()"><i class="fas fa-copy"></i> Nusxalash</button>
</div></div>

<script>
const tg=window.Telegram?.WebApp;if(tg){tg.ready();tg.expand()}
const H={'Content-Type':'application/json'};if(tg?.initData)H['X-Telegram-Init-Data']=tg.initData;
let LANG=localStorage.getItem('lang')||'uz',RL='uz',pie=null,bar=null,pinBuf='',pinMode='check',selRating=3;

// ===== GLOBAL AUDIO CONTEXT (mobile fix) =====
let audioCtx=null;
function initAudio(){if(!audioCtx){try{audioCtx=new(window.AudioContext||window.webkitAudioContext)()}catch(e){}}if(audioCtx&&audioCtx.state==='suspended'){audioCtx.resume()}return audioCtx}

const T={
uz:{appTitle:"Qarz Daftar",totalGiven:"Jami berilgan",remaining:"Qolgan",paid:"Tolangan",debtors:"Qarzdorlar",addDebtor:"Yangi qarzdor",debtorList:"Qarzdorlar",topDebtors:"TOP Qarzdorlar",statistics:"Statistika",reports:"Hisobotlar",downloadPdf:"PDF yuklab olish",settings:"Sozlamalar",navHome:"Bosh",newDebtor:"Yangi qarzdor",name:"Ism *",phone:"Telefon",amount:"Summa *",currency:"Valyuta",dueDate:"Muddat",category:"Kategoriya",note:"Izoh",save:"Saqlash",addPayment:"To'lov",confirm:"Tasdiqlash",pay:"To'lov",delete:"O'chirish",noDebtors:"Hali qarzdor yo'q",tapAbove:"Yuqoridagi tugmani bosing",debtorAdded:"Qo'shildi! ✨",paymentReceived:"Qabul qilindi! 💰🎉",deleted:"O'chirildi!",confirmDelete:"O'chirilsinmi?",nameAmountRequired:"Ism va summa majburiy!",invalidAmount:"Noto'g'ri summa",total:"Jami",paidAmount:"Tolangan",statusActive:"Faol",statusOverdue:"Muddati o'tgan",statusPaid:"To'langan",pinEnter:"PIN kiriting",pinSet:"Yangi PIN kiriting",pinConfirm:"PIN tasdiqlang",pinWrong:"Noto'g'ri PIN!",pinSaved:"PIN saqlandi",pinRemoved:"PIN o'chirildi",copied:"Nusxalandi!"},
ru:{appTitle:"Долговая Книга",totalGiven:"Выдано",remaining:"Остаток",paid:"Оплачено",debtors:"Должники",addDebtor:"Новый должник",debtorList:"Должники",topDebtors:"ТОП Должники",statistics:"Статистика",reports:"Отчёты",downloadPdf:"Скачать PDF",settings:"Настройки",navHome:"Главная",newDebtor:"Новый должник",name:"Имя *",phone:"Телефон",amount:"Сумма *",currency:"Валюта",dueDate:"Срок",category:"Категория",note:"Примечание",save:"Сохранить",addPayment:"Платёж",confirm:"Подтвердить",pay:"Оплата",delete:"Удалить",noDebtors:"Нет должников",tapAbove:"Нажмите кнопку выше",debtorAdded:"Добавлен! ✨",paymentReceived:"Принят! 💰🎉",deleted:"Удалено!",confirmDelete:"Удалить?",nameAmountRequired:"Имя и сумма обязательны!",invalidAmount:"Неверная сумма",total:"Всего",paidAmount:"Оплачено",statusActive:"Активен",statusOverdue:"Просрочен",statusPaid:"Оплачен",pinEnter:"Введите PIN",pinSet:"Новый PIN",pinConfirm:"Подтвердите PIN",pinWrong:"Неверный PIN!",pinSaved:"PIN сохранён",pinRemoved:"PIN удалён",copied:"Скопировано!"},
en:{appTitle:"Debt Book",totalGiven:"Total Given",remaining:"Remaining",paid:"Paid",debtors:"Debtors",addDebtor:"New Debtor",debtorList:"Debtors",topDebtors:"TOP Debtors",statistics:"Statistics",reports:"Reports",downloadPdf:"Download PDF",settings:"Settings",navHome:"Home",newDebtor:"New Debtor",name:"Name *",phone:"Phone",amount:"Amount *",currency:"Currency",dueDate:"Due Date",category:"Category",note:"Note",save:"Save",addPayment:"Payment",confirm:"Confirm",pay:"Pay",delete:"Delete",noDebtors:"No debtors yet",tapAbove:"Tap the button above",debtorAdded:"Added! ✨",paymentReceived:"Received! 💰🎉",deleted:"Deleted!",confirmDelete:"Delete?",nameAmountRequired:"Name and amount required!",invalidAmount:"Invalid amount",total:"Total",paidAmount:"Paid",statusActive:"Active",statusOverdue:"Overdue",statusPaid:"Paid",pinEnter:"Enter PIN",pinSet:"New PIN",pinConfirm:"Confirm PIN",pinWrong:"Wrong PIN!",pinSaved:"PIN saved",pinRemoved:"PIN removed",copied:"Copied!"}
};
function t(k){return T[LANG][k]||k}
function ut(){document.querySelectorAll('[data-i18n]').forEach(e=>e.textContent=t(e.getAttribute('data-i18n')))}
function toggleLang(){snd2();const L=['uz','ru','en'];LANG=L[(L.indexOf(LANG)+1)%3];localStorage.setItem('lang',LANG);ut();load();toast('🌐 '+LANG.toUpperCase())}
function setRL(l,b){snd2();RL=l;document.querySelectorAll('.lb').forEach(x=>x.classList.remove('ac'));b.classList.add('ac')}
function sw(p,b){snd2();document.querySelectorAll('.page').forEach(x=>x.classList.remove('ac'));document.getElementById('page-'+p).classList.add('ac');document.querySelectorAll('.ni').forEach(n=>n.classList.remove('ac'));b.classList.add('ac');if(p==='stats')charts();if(p==='top')loadTop();if(p==='settings')loadSettings()}
function selCur(c,b){document.querySelectorAll('.cur-btn').forEach(x=>x.classList.remove('ac'));b.classList.add('ac');document.getElementById('acur').value=c}

function snd1(){const c=initAudio();if(!c)return;try{const o=c.createOscillator(),g=c.createGain();o.connect(g);g.connect(c.destination);o.frequency.setValueAtTime(523.25,c.currentTime);o.frequency.setValueAtTime(659.25,c.currentTime+.1);o.frequency.setValueAtTime(783.99,c.currentTime+.2);g.gain.setValueAtTime(.3,c.currentTime);g.gain.exponentialRampToValueAtTime(.01,c.currentTime+.5);o.start(c.currentTime);o.stop(c.currentTime+.5)}catch(e){}}
function snd2(){const c=initAudio();if(!c)return;try{const o=c.createOscillator(),g=c.createGain();o.connect(g);g.connect(c.destination);o.frequency.setValueAtTime(800,c.currentTime);g.gain.setValueAtTime(.2,c.currentTime);g.gain.exponentialRampToValueAtTime(.01,c.currentTime+.1);o.start(c.currentTime);o.stop(c.currentTime+.1)}catch(e){}}

function confetti(){const cv=document.getElementById('cc'),x=cv.getContext('2d');cv.width=innerWidth;cv.height=innerHeight;const ps=[],cl=['#667eea','#f093fb','#10b981','#f59e0b','#ef4444'];for(let i=0;i<100;i++)ps.push({x:cv.width/2,y:cv.height/2,vx:(Math.random()-.5)*20,vy:(Math.random()-.5)*20-10,c:cl[Math.floor(Math.random()*cl.length)],s:Math.random()*8+4,l:1});(function a(){x.clearRect(0,0,cv.width,cv.height);ps.forEach((p,i)=>{p.x+=p.vx;p.y+=p.vy;p.vy+=.5;p.l-=.02;x.fillStyle=p.c;x.globalAlpha=p.l;x.fillRect(p.x,p.y,p.s,p.s);if(p.l<=0)ps.splice(i,1)});if(ps.length)requestAnimationFrame(a)})()}
function fm(a,c){c=c||'UZS';const s=new Intl.NumberFormat('uz-UZ').format(a||0);return c==='UZS'?s+" so'm":s+' '+c}
function toast(m,e){const t2=document.getElementById('toast');t2.textContent=m;t2.className='toast sh'+(e?' er':'');setTimeout(()=>t2.classList.remove('sh'),3000)}
function toggleTheme(){snd2();const b=document.body,d=b.getAttribute('data-theme')==='dark';b.setAttribute('data-theme',d?'light':'dark');localStorage.setItem('theme',d?'light':'dark');document.getElementById('ti').className=d?'fas fa-moon':'fas fa-sun'}

// ===== PIN SYSTEM (FIXED) =====
async function checkPin(){
try{
const s=await(await fetch('/api/settings',{headers:H})).json();
if(s.pin_set&&s.pin_code){
  document.getElementById('pinScreen').style.display='flex';
  document.getElementById('mainApp').style.display='none';
  pinMode='check';pinBuf='';
  document.getElementById('pinSub').textContent=t('pinEnter');
  updDots();
}else{
  document.getElementById('pinScreen').style.display='none';
  document.getElementById('mainApp').style.display='block';
  load();
}
}catch(e){
  document.getElementById('pinScreen').style.display='none';
  document.getElementById('mainApp').style.display='block';
  load();
}}

function updDots(){document.querySelectorAll('.pin-dot').forEach((d,i)=>d.classList.toggle('filled',i<pinBuf.length))}
function pinKey(k){initAudio();if(pinBuf.length>=4)return;pinBuf+=k;updDots();snd2();if(pinBuf.length===4)setTimeout(handlePin,300)}
function pinDel(){pinBuf=pinBuf.slice(0,-1);updDots()}
function pinClear(){pinBuf='';updDots()}

let pinFirst='';
async function handlePin(){
if(pinMode==='check'){
  try{
    const r=await fetch('/api/settings/pin/verify',{method:'POST',headers:H,body:JSON.stringify({pin:pinBuf})});
    const data=await r.json();
    if(data.valid){
      document.getElementById('pinScreen').style.display='none';
      document.getElementById('mainApp').style.display='block';
      load();
    }else{
      toast(t('pinWrong'),true);
      pinBuf='';updDots();
      document.querySelectorAll('.pin-dot').forEach(d=>{d.style.background='#ef4444';setTimeout(()=>d.style.background='',400)});
    }
  }catch(e){document.getElementById('pinScreen').style.display='none';document.getElementById('mainApp').style.display='block';load()}
}else if(pinMode==='set'){
  pinFirst=pinBuf;pinBuf='';
  document.getElementById('pinSub').textContent=t('pinConfirm');
  updDots();pinMode='confirm';
}else if(pinMode==='confirm'){
  if(pinBuf===pinFirst){
    await fetch('/api/settings/pin',{method:'POST',headers:H,body:JSON.stringify({pin:pinFirst})});
    toast('✅ '+t('pinSaved'));
    document.getElementById('pinScreen').style.display='none';
    document.getElementById('mainApp').style.display='block';
    loadSettings();load();
  }else{
    toast(t('pinWrong'),true);
    pinMode='set';pinBuf='';
    document.getElementById('pinSub').textContent=t('pinSet');
    updDots();
  }
}}

function changePin(){snd2();document.getElementById('pinScreen').style.display='flex';document.getElementById('mainApp').style.display='none';pinMode='set';pinBuf='';pinFirst='';document.getElementById('pinSub').textContent=t('pinSet');updDots()}
async function removePin(){snd2();await fetch('/api/settings/pin',{method:'POST',headers:H,body:JSON.stringify({pin:null})});toast('✅ '+t('pinRemoved'));loadSettings()}

async function loadSettings(){
try{const s=await(await fetch('/api/settings',{headers:H})).json();
document.getElementById('pinStatus').innerHTML=s.pin_set?'✅ PIN o\\'rnatilgan (4 xona)':'❌ PIN yo\\'q';
const st=await(await fetch('/api/stats',{headers:H})).json();
const r=st.rates||{};
document.getElementById('ratesBox').innerHTML='💵 1 USD = '+(r.UZS?Math.round(r.UZS).toLocaleString():'—')+' UZS<br>💶 1 EUR = '+(r.UZS&&r.EUR?Math.round(r.UZS/r.EUR).toLocaleString():'—')+' UZS'}catch(e){}}

async function load(){
try{const s=await(await fetch('/api/stats',{headers:H})).json();
document.getElementById('tg').textContent=fm(s.total_given);document.getElementById('tr').textContent=fm(s.total_remaining);
document.getElementById('tp').textContent=fm(s.total_paid);document.getElementById('tc').textContent=s.total_debtors;
const ds=await(await fetch('/api/debtors',{headers:H})).json();
const l=document.getElementById('dl');
if(!ds.length){l.innerHTML='<div class="es"><div class="ei"><i class="fas fa-inbox"></i></div><p style="font-size:16px;font-weight:600">'+t('noDebtors')+'</p><p style="font-size:12px;margin-top:6px;opacity:.7">'+t('tapAbove')+'</p></div>';return}
l.innerHTML=ds.map((d,i)=>{const sc=d.status==='OVERDUE'?'overdue':d.status==='PAID'?'paid':'';const bc=d.status==='OVERDUE'?'bo':d.status==='PAID'?'bp':'ba';const st=d.status==='OVERDUE'?t('statusOverdue'):d.status==='PAID'?t('statusPaid'):t('statusActive');const stars='⭐'.repeat(d.rating||3);const c=d.currency||'UZS';
return '<div class="dc '+sc+'"><div class="dh"><div><div class="dn">'+d.name+'</div><div class="rat" id="rat-'+d.id+'">'+stars+'</div></div><div class="da">'+fm(d.remaining_amount,c)+'</div></div><div class="di">'+(d.phone?'<a href="tel:'+d.phone.replace(/[^+0-9]/g,'')+'"><i class="fas fa-phone"></i> '+d.phone+'</a>':'<span><i class="fas fa-phone"></i> -</span>')+'<span><i class="fas fa-tag"></i>'+d.category+'</span><span><i class="fas fa-coins"></i>'+c+'</span>'+(d.due_date?'<span><i class="fas fa-calendar"></i>'+d.due_date+'</span>':'')+'</div><span class="badge '+bc+'">'+st+'</span>'+(d.image_path?'<div style="margin-bottom:10px"><img src="'+d.image_path+'" style="max-width:100%;max-height:120px;border-radius:10px"></div>':'')+'<div class="dd"><b>'+t('total')+':</b> '+fm(d.total_amount,c)+' | <b>'+t('paidAmount')+':</b> '+fm(d.paid_amount,c)+'</div><div class="dac">'+(d.status!=='PAID'?'<button class="ba2 bpay" onclick="op('+d.id+','+d.remaining_amount+')"><i class="fas fa-money-bill-wave"></i>'+t('pay')+'</button>':'')+(d.phone?'<button class="ba2 bwa" onclick="wa(\\''+d.phone.replace(/[^+0-9]/g,'')+'\\')"><i class="fab fa-whatsapp"></i></button>':'')+'<button class="ba2 brem" onclick="rem('+d.id+')"><i class="fas fa-bell"></i></button><button class="ba2 bhist" onclick="hist('+d.id+')"><i class="fas fa-history"></i></button><button class="ba2 bimg" onclick="opImg('+d.id+',\\''+(d.image_path||'')+'\\')"><i class="fas fa-camera"></i></button><button class="ba2 bdel" onclick="delD('+d.id+')"><i class="fas fa-trash"></i></button></div></div>'}).join('');
ds.forEach(d=>{const el=document.getElementById('rat-'+d.id);if(el){el.innerHTML='';for(let i=1;i<=5;i++){const sp=document.createElement('span');sp.textContent=i<=(d.rating||3)?'⭐':'☆';sp.style.cursor='pointer';sp.onclick=()=>setR2(d.id,i);el.appendChild(sp)}}})
}catch(e){console.error(e);toast('Xato',true)}}

async function loadTop(){try{const ds=await(await fetch('/api/debtors/top',{headers:H})).json();const l=document.getElementById('topList');if(!ds.length){l.innerHTML='<div class="es"><div class="ei"><i class="fas fa-trophy"></i></div><p>'+t('noDebtors')+'</p></div>';return}
l.innerHTML=ds.map((d,i)=>{const c=d.currency||'UZS';const medal=i===0?'🥇':i===1?'🥈':i===2?'🥉':'<b style="color:var(--pri)">#'+(i+1)+'</b>';return '<div class="dc"><div class="dh"><div><div class="dn">'+medal+' '+d.name+'</div><div class="di"><span><i class="fas fa-star"></i>'+'⭐'.repeat(d.rating||3)+'</span></div></div><div class="da">'+fm(d.remaining_amount,c)+'</div></div></div>'}).join('')}catch(e){}}

async function charts(){try{const s=await(await fetch('/api/stats',{headers:H})).json();const ds=await(await fetch('/api/debtors',{headers:H})).json();
if(pie)pie.destroy();pie=new Chart(document.getElementById('pie'),{type:'doughnut',data:{labels:[t('paid'),t('remaining')],datasets:[{data:[s.total_paid,s.total_remaining],backgroundColor:['#10b981','#ef4444'],borderWidth:0}]},options:{plugins:{legend:{position:'bottom'}}}});
if(bar)bar.destroy();const top=ds.slice(0,5);bar=new Chart(document.getElementById('bar'),{type:'bar',data:{labels:top.map(d=>d.name),datasets:[{label:t('remaining'),data:top.map(d=>d.remaining_amount),backgroundColor:'rgba(102,126,234,.8)',borderRadius:8}]},options:{plugins:{legend:{display:false}},scales:{y:{beginAtZero:true}}}})}catch(e){}}

function downloadPDF(){snd1();window.open('/api/export/pdf?lang='+RL+'&initData='+encodeURIComponent(tg?.initData||''),'_blank')}
function downloadCSV(){snd2();window.open('/api/export/csv?initData='+encodeURIComponent(tg?.initData||''),'_blank')}

function openAdd(){snd2();document.getElementById('addM').classList.add('ac');initRating()}
function cm(id){snd2();document.getElementById(id).classList.remove('ac')}
function initRating(){const ar=document.getElementById('arat');if(ar){ar.innerHTML='';for(let i=1;i<=5;i++){const sp=document.createElement('span');sp.textContent=i<=3?'⭐':'☆';sp.style.cursor='pointer';sp.onclick=()=>setR(i);ar.appendChild(sp)}selRating=3;document.getElementById('aratv').value=3}}
function setR(v){selRating=v;document.getElementById('aratv').value=v;const ar=document.getElementById('arat');ar.innerHTML='';for(let i=1;i<=5;i++){const sp=document.createElement('span');sp.textContent=i<=v?'⭐':'☆';sp.style.cursor='pointer';sp.onclick=()=>setR(i);ar.appendChild(sp)}}

async function addD(){const d={name:document.getElementById('an').value.trim(),phone:document.getElementById('aph').value.trim(),amount:parseFloat(document.getElementById('aam').value),currency:document.getElementById('acur').value,due_date:document.getElementById('adt').value||null,category:document.getElementById('aca').value,note:document.getElementById('ano').value.trim(),rating:parseInt(document.getElementById('aratv').value)||3};
if(!d.name||!d.amount){toast(t('nameAmountRequired'),true);return}
try{const r=await fetch('/api/debtors',{method:'POST',headers:H,body:JSON.stringify(d)});if(!r.ok)throw 0;snd1();toast('✅ '+t('debtorAdded'));cm('addM');['an','aph','aam','adt','ano'].forEach(i=>document.getElementById(i).value='');load()}catch(e){toast('Xato',true)}}

function op(id,rem){snd2();document.getElementById('pid').value=id;document.getElementById('pam').value=rem;document.getElementById('pno').value='';document.getElementById('payM').classList.add('ac')}
async function payD(){const id=document.getElementById('pid').value,am=parseFloat(document.getElementById('pam').value),no=document.getElementById('pno').value.trim();if(!am||am<=0){toast(t('invalidAmount'),true);return}
try{const r=await fetch('/api/debtors/'+id+'/pay',{method:'PUT',headers:H,body:JSON.stringify({amount:am,note:no})});if(!r.ok)throw 0;snd1();confetti();toast('✅ '+t('paymentReceived'));cm('payM');load()}catch(e){toast('Xato',true)}}

async function setR2(id,r){snd2();try{await fetch('/api/debtors/'+id+'/rating?rating='+r,{method:'PUT',headers:H});load()}catch(e){}}
function wa(ph){snd2();window.open('https://wa.me/'+ph.replace(/^\\+/,''),'_blank')}
async function rem(id){snd2();try{const r=await(await fetch('/api/remind/'+id,{headers:H})).json();document.getElementById('remTxt').value=r.text;document.getElementById('remM').classList.add('ac')}catch(e){toast('Xato',true)}}
function copyRem(){const tx=document.getElementById('remTxt');tx.select();navigator.clipboard.writeText(tx.value);toast('✅ '+t('copied'))}
async function hist(id){snd2();try{const ps=await(await fetch('/api/payments/'+id,{headers:H})).json();const l=document.getElementById('histList');if(!ps.length){l.innerHTML='<p style="text-align:center;color:var(--mut);padding:30px">Hali to\'lov yo\\'q</p>'}else{l.innerHTML=ps.map(p=>'<div class="pay-item"><div><div style="font-weight:700">'+fm(p.amount)+'</div><div style="font-size:11px;color:var(--mut)">'+(p.note||'—')+'</div></div><div style="font-size:11px;color:var(--mut)">'+new Date(p.payment_date).toLocaleDateString()+'</div></div>').join('')}document.getElementById('histM').classList.add('ac')}catch(e){}}
function opImg(id,path){snd2();document.getElementById('imgId').value=id;document.getElementById('imgPrev').innerHTML=path?'<img src="'+path+'" style="max-width:100%;max-height:200px;border-radius:12px">':'<p style="color:var(--mut)">Rasm yo\\'q</p>';document.getElementById('imgFile').value='';document.getElementById('imgM').classList.add('ac')}
async function uploadImg(){const id=document.getElementById('imgId').value,f=document.getElementById('imgFile').files[0];if(!f){toast('Rasm tanlang',true);return}const fd=new FormData();fd.append('file',f);try{const r=await fetch('/api/debtors/'+id+'/image',{method:'POST',headers:{'X-Telegram-Init-Data':tg?.initData||''},body:fd});if(!r.ok)throw 0;snd1();toast('✅ Yuklandi!');cm('imgM');load()}catch(e){toast('Xato',true)}}
async function delD(id){if(!confirm(t('confirmDelete')))return;try{const r=await fetch('/api/debtors/'+id,{method:'DELETE',headers:H});if(!r.ok)throw 0;snd2();toast('✅ '+t('deleted'));load()}catch(e){toast('Xato',true)}}

document.addEventListener('click',()=>initAudio(),{once:true});
document.addEventListener('touchstart',()=>initAudio(),{once:true});

window.onload=()=>{const th=localStorage.getItem('theme')||'light';document.body.setAttribute('data-theme',th);document.getElementById('ti').className=th==='dark'?'fas fa-sun':'fas fa-moon';ut();checkPin()};
</script>
</body></html>"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(HTML_TEMPLATE)


# ============ BOT ============
@dp.message(CommandStart())
async def cmd_start(m: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📒 Ilovani ochish", web_app=WebAppInfo(url=WEBAPP_URL))]])
    await m.answer("<b>💎 Qarz Daftar Pro</b>\n\n🔐 PIN himoya\n📸 Rasm biriktirish\n🖨️ PDF hisobot (3 til)\n🏆 TOP qarzdorlar\n⭐ Ishonch reytingi\n🔔 Eslatma matni\n💱 Multi-valyuta\n💳 To'lovlar tarixi\n\nTugmani bosing 👇", reply_markup=kb)


@dp.message(Command("report"))
async def cmd_report(m: types.Message):
    uid = m.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT COUNT(*) n,COALESCE(SUM(total_amount),0) g,COALESCE(SUM(paid_amount),0) p,COALESCE(SUM(remaining_amount),0) r FROM debtors WHERE user_id=?", (uid,))
        s = dict(await cur.fetchone())
    await m.answer(f"📊 <b>HISOBOT</b>\n👥 {s['n']}\n💰 {s['g']:,.0f}\n✅ {s['p']:,.0f}\n⏳ {s['r']:,.0f}")


@dp.message(Command("backup"))
async def cmd_backup(m: types.Message):
    """⚠️ MA'LUMOTLARNI SAQLASH — Telegram'da CSV fayl"""
    uid = m.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT name,phone,total_amount,currency,paid_amount,remaining_amount,status,rating,due_date,note,category,created_at FROM debtors WHERE user_id=?", (uid,))
        rows = await cur.fetchall()
        pay_cur = await db.execute("SELECT debtor_id,amount,note,payment_date FROM payments")
        pays = await pay_cur.fetchall()
    
    lines = ["# DEBTORS"]
    lines.append("Ism;Telefon;Jami;Valyuta;Tolangan;Qolgan;Holat;Reyting;Muddat;Izoh;Kategoriya;Yaratilgan")
    for r in rows:
        lines.append(";".join("" if x is None else str(x) for x in r))
    
    lines.append("")
    lines.append("# PAYMENTS")
    lines.append("DebtorID;Summa;Izoh;Sana")
    for p in pays:
        lines.append(";".join("" if x is None else str(x) for x in p))
    
    await m.answer_document(FSInputFile(BytesIO("\n".join(lines).encode("utf-8")), filename=f"backup_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"))
    await m.answer("✅ Backup tayyor!\n\n📥 Bu faylni Telegram'da saqlab qo'ying.\n\n⚠️ Render bepul tarifda ma'lumotlar har deploy'da o'chirilishi mumkin. Haftada 1 marta `/backup` qiling!")


def run_bot():
    try:
        logger.info("🤖 Bot ishga tushmoqda...")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.add_signal_handler = lambda *a, **k: None
        loop.run_until_complete(dp.start_polling(bot))
    except Exception as e:
        logger.error(f"Bot xatosi: {e}")


if __name__ == "__main__":
    threading.Thread(target=run_bot, daemon=True).start()
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")