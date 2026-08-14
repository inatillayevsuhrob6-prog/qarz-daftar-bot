import os
import json
import hmac
import hashlib
import logging
import asyncio
from datetime import datetime, date, timedelta
from typing import Optional
from urllib.parse import parse_qsl
from contextlib import asynccontextmanager

import aiosqlite
import uvicorn
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart, Command
from aiogram.types import WebAppInfo, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.client.default import DefaultBotProperties

# ---------------- SOZLAMALAR ----------------
BOT_TOKEN = "8651436055:AAH3FgpFyhcnBo4RXXYQpLMv1Wk4qNiCXX0"
WEBAPP_URL = "https://jamshidrahmatullayev80.pythonanywhere.com"
DB_PATH = "qarz_daftar.db"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("qarz_daftar")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()


# ---------------- MODELS ----------------
class DebtorCreate(BaseModel):
    name: str
    phone: Optional[str] = None
    amount: float
    due_date: Optional[str] = None
    category: str = "Shaxsiy"
    note: Optional[str] = None

class PaymentCreate(BaseModel):
    amount: float
    note: Optional[str] = None


# ---------------- DATABASE ----------------
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""CREATE TABLE IF NOT EXISTS debtors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            phone TEXT,
            category TEXT DEFAULT 'Shaxsiy',
            note TEXT,
            total_amount REAL NOT NULL,
            paid_amount REAL DEFAULT 0,
            remaining_amount REAL NOT NULL,
            status TEXT DEFAULT 'ACTIVE',
            due_date TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            debtor_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            note TEXT,
            payment_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        await db.commit()


# ---------------- AUTH ----------------
def verify_init_data(init_data: str) -> dict:
    if not init_data:
        raise HTTPException(401, "Auth ma'lumoti yo'q")
    data = dict(parse_qsl(init_data))
    check_hash = data.pop("hash", "")
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    if hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest() != check_hash:
        raise HTTPException(401, "Auth xatoligi")
    user = json.loads(data.get("user", "{}"))
    return {"telegram_id": user.get("id", 0), "username": user.get("username", "")}


async def get_current_user(request: Request) -> dict:
    init_data = request.headers.get("X-Telegram-Init-Data") or request.query_params.get("initData", "")
    return verify_init_data(init_data)


# ---------------- FASTAPI ----------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(notification_scheduler())
    yield
    task.cancel()

app = FastAPI(title="Qarz Daftar API", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def index():
    with open("index.html", encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.get("/api/stats")
async def get_stats(user: dict = Depends(get_current_user)):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("""
            SELECT COUNT(*) AS total_debtors,
                   COALESCE(SUM(total_amount), 0) AS total_given,
                   COALESCE(SUM(paid_amount), 0) AS total_paid,
                   COALESCE(SUM(remaining_amount), 0) AS total_remaining,
                   COALESCE(SUM(CASE WHEN status = 'OVERDUE' THEN 1 ELSE 0 END), 0) AS overdue_count
            FROM debtors WHERE user_id = ?""", (user["telegram_id"],))
        return dict(await cur.fetchone())


@app.get("/api/debtors")
async def get_debtors(search: str = "", status: str = "ALL", page: int = 1, limit: int = 50,
                      user: dict = Depends(get_current_user)):
    query = "SELECT * FROM debtors WHERE user_id = ?"
    params = [user["telegram_id"]]
    if search:
        query += " AND (name LIKE ? OR phone LIKE ?)"
        params += [f"%{search}%", f"%{search}%"]
    if status != "ALL":
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params += [limit, (page - 1) * limit]
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(query, params)
        return [dict(r) for r in await cur.fetchall()]


@app.post("/api/debtors")
async def add_debtor(d: DebtorCreate, user: dict = Depends(get_current_user)):
    due = d.due_date or None
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            INSERT INTO debtors (user_id, name, phone, category, note, total_amount, paid_amount, remaining_amount, due_date)
            VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)""",
            (user["telegram_id"], d.name, d.phone, d.category, d.note, d.amount, d.amount, due))
        await db.commit()
        return {"id": cur.lastrowid}


@app.put("/api/debtors/{debtor_id}/pay")
async def add_payment(debtor_id: int, p: PaymentCreate, user: dict = Depends(get_current_user)):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT total_amount, paid_amount FROM debtors WHERE id = ? AND user_id = ?",
                               (debtor_id, user["telegram_id"]))
        row = await cur.fetchone()
        if not row:
            raise HTTPException(404, "Qarzdor topilmadi")
        remaining = row[0] - row[1]
        if p.amount <= 0 or p.amount > remaining:
            raise HTTPException(400, f"Noto'g'ri summa (qolgan: {remaining})")
        new_paid = row[1] + p.amount
        new_remaining = remaining - p.amount
        status = "PAID" if new_remaining == 0 else "ACTIVE"
        await db.execute("INSERT INTO payments (debtor_id, amount, note) VALUES (?, ?, ?)",
                         (debtor_id, p.amount, p.note))
        await db.execute("UPDATE debtors SET paid_amount = ?, remaining_amount = ?, status = ? WHERE id = ?",
                         (new_paid, new_remaining, status, debtor_id))
        await db.commit()
        return {"remaining": new_remaining}


@app.delete("/api/debtors/{debtor_id}")
async def delete_debtor(debtor_id: int, user: dict = Depends(get_current_user)):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM payments WHERE debtor_id = ?", (debtor_id,))
        await db.execute("DELETE FROM debtors WHERE id = ? AND user_id = ?", (debtor_id, user["telegram_id"]))
        await db.commit()
        return {"ok": True}


@app.get("/api/export/csv")
async def export_csv(user: dict = Depends(get_current_user)):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""SELECT name, phone, total_amount, paid_amount, remaining_amount, status, due_date
                                  FROM debtors WHERE user_id = ?""", (user["telegram_id"],))
        rows = await cur.fetchall()
    lines = ["Ism;Telefon;Jami;Tolangan;Qolgan;Holat;Muddat"]
    for r in rows:
        lines.append(";".join("" if x is None else str(x) for x in r))
    return Response("\n".join(lines), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=qarzlar.csv"})


# ---------------- ESLATMALAR (har kuni 9:00 da) ----------------
async def notification_scheduler():
    while True:
        now = datetime.now()
        target = now.replace(hour=9, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        await send_reminders()


async def send_reminders():
    today = date.today()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""UPDATE debtors SET status = 'OVERDUE'
                            WHERE status = 'ACTIVE' AND remaining_amount > 0
                              AND due_date IS NOT NULL AND due_date != '' AND due_date < ?""", (today.isoformat(),))
        cur = await db.execute("""SELECT user_id, name, remaining_amount, due_date, status FROM debtors
                                  WHERE remaining_amount > 0 AND due_date IS NOT NULL AND due_date != ''
                                    AND ((status = 'OVERDUE' AND due_date = ?)
                                      OR (status = 'ACTIVE' AND due_date BETWEEN ? AND ?))""",
                               ((today - timedelta(days=1)).isoformat(),
                                today.isoformat(), (today + timedelta(days=3)).isoformat()))
        rows = await cur.fetchall()
        await db.commit()
    for user_id, name, remaining, due, status in rows:
        if status == "OVERDUE":
            text = f"⚠️ <b>MUDDATI O'TDI!</b>\n👤 {name}\n💰 {remaining:,.0f} so'm\n📅 {due}"
        else:
            text = f"⏳ <b>Eslatma</b>\n👤 {name}\n💰 {remaining:,.0f} so'm\n📅 Muddat: {due}"
        try:
            await bot.send_message(user_id, text)
        except Exception as e:
            logger.warning(e)


# ---------------- BOT ----------------
@dp.message(CommandStart())
async def cmd_start(m: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📒 Ilovani ochish", web_app=WebAppInfo(url=WEBAPP_URL))]])
    await m.answer("<b>📒 Qarz Daftar</b> — qarzlaringizni nazorat qiling!\n\n"
                   "✅ Qarzdorlar ro'yxati va qidiruv\n"
                   "✅ To'lovlar tarixi va statistika\n"
                   "✅ Avtomatik eslatmalar\n"
                   "✅ CSV export, dark/light rejim\n\n"
                   "Ma'lumotlaringiz faqat sizga ko'rinadi 🔒", reply_markup=kb)


@dp.message(Command("app"))
async def cmd_app(m: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📒 Ilovani ochish", web_app=WebAppInfo(url=WEBAPP_URL))]])
    await m.answer("Mini App:", reply_markup=kb)


# ---------------- START ----------------
async def main():
    await init_db()
    logger.info("✅ Server va bot ishga tushmoqda...")
    server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="warning"))
    await asyncio.gather(dp.start_polling(bot), server.serve())

if __name__ == "__main__":
    asyncio.run(main())