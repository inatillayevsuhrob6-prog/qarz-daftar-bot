import os
import json
import hmac
import hashlib
import logging
import asyncio
import threading
from datetime import datetime, date, timedelta
from typing import Optional
from urllib.parse import parse_qsl

import aiosqlite
import uvicorn
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.types import WebAppInfo, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.client.default import DefaultBotProperties

# ============ KONFIGURATSIYA ============
BOT_TOKEN = "8651436055:AAH3FgpFyhcnBo4RXXYQpLMv1Wk4qNiCXX0"
WEBAPP_URL = "https://happy-wasps-shine.loca.lt"  # O'z URL'ingizni qo'ying
DB_PATH = "qarz_daftar.db"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()


# ============ MODELS ============
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
                status TEXT DEFAULT 'ACTIVE',
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
        await db.commit()
    logger.info("✅ Database yaratildi")


# ============ AUTH ============
def verify_telegram_auth(init_data: str) -> dict:
    if not init_data:
        raise HTTPException(status_code=401, detail="Telegram auth yo'q")
    
    try:
        data = dict(parse_qsl(init_data))
        received_hash = data.pop("hash", None)
        
        if not received_hash:
            raise HTTPException(status_code=401, detail="Hash yo'q")
        
        check_list = []
        for key in sorted(data.keys()):
            check_list.append(f"{key}={data[key]}")
        check_string = "\n".join(check_list)
        
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
        
        if not hmac.compare_digest(calculated_hash, received_hash):
            raise HTTPException(status_code=401, detail="Hash xato")
        
        user_data = json.loads(data.get("user", "{}"))
        
        return {
            "telegram_id": user_data.get("id"),
            "username": user_data.get("username", ""),
            "first_name": user_data.get("first_name", ""),
            "last_name": user_data.get("last_name", "")
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Auth xatosi: {e}")
        raise HTTPException(status_code=401, detail=f"Auth xatosi: {str(e)}")


async def get_current_user(request: Request) -> dict:
    init_data = request.headers.get("X-Telegram-Init-Data")
    if not init_data:
        init_data = request.query_params.get("initData", "")
    
    if not init_data:
        logger.warning("⚠️ Auth yo'q - test mode")
        return {"telegram_id": 123456, "username": "test_user", "first_name": "Test", "last_name": "User"}
    
    return verify_telegram_auth(init_data)


# ============ FASTAPI APP ============
app = FastAPI(title="Qarz Daftar API", debug=True)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup():
    await init_db()
    logger.info("🚀 Server ishga tushdi")


# ============ HTML TEMPLATE ============
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="uz">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Qarz Daftar Pro</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
:root {
  --bg: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  --card: rgba(255,255,255,0.95);
  --primary: #667eea;
  --danger: #ef4444;
  --success: #10b981;
  --text: #1f2937;
  --muted: #6b7280;
}
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  background: var(--bg);
  min-height: 100vh;
  color: var(--text);
  padding: 20px;
  padding-bottom: 100px;
}
.container { max-width: 600px; margin: 0 auto; }
.glass {
  background: var(--card);
  backdrop-filter: blur(10px);
  border-radius: 20px;
  padding: 20px;
  box-shadow: 0 8px 32px rgba(0,0,0,0.1);
  margin-bottom: 16px;
}
h1 { font-size: 24px; margin-bottom: 20px; color: var(--primary); }
.stats {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin-bottom: 20px;
}
.stat {
  background: var(--card);
  padding: 16px;
  border-radius: 16px;
  text-align: center;
}
.stat-label { font-size: 12px; color: var(--muted); margin-bottom: 4px; }
.stat-value { font-size: 20px; font-weight: bold; }
.btn {
  width: 100%;
  padding: 14px;
  border: none;
  border-radius: 12px;
  font-size: 16px;
  font-weight: 600;
  cursor: pointer;
  margin-bottom: 10px;
}
.btn-primary { background: var(--primary); color: white; }
.btn-success { background: var(--success); color: white; }
.btn-danger { background: var(--danger); color: white; }
.card {
  background: var(--card);
  padding: 16px;
  border-radius: 16px;
  margin-bottom: 12px;
}
.card-header {
  display: flex;
  justify-content: space-between;
  margin-bottom: 8px;
}
.card-name { font-weight: bold; font-size: 18px; }
.card-amount { color: var(--primary); font-weight: bold; }
.card-info { font-size: 14px; color: var(--muted); margin-top: 8px; }
.nav {
  position: fixed;
  bottom: 0;
  left: 0;
  right: 0;
  background: white;
  display: flex;
  justify-content: space-around;
  padding: 12px;
  box-shadow: 0 -4px 20px rgba(0,0,0,0.1);
}
.nav-btn {
  padding: 8px 16px;
  border: none;
  background: none;
  cursor: pointer;
  font-size: 14px;
}
.modal {
  display: none;
  position: fixed;
  top: 0; left: 0; right: 0; bottom: 0;
  background: rgba(0,0,0,0.5);
  z-index: 1000;
  align-items: center;
  justify-content: center;
}
.modal.active { display: flex; }
.modal-content {
  background: white;
  padding: 24px;
  border-radius: 20px;
  max-width: 500px;
  width: 90%;
  max-height: 90vh;
  overflow-y: auto;
}
.form-group { margin-bottom: 16px; }
.form-group label { display: block; margin-bottom: 6px; font-weight: 600; }
.form-group input, .form-group select, .form-group textarea {
  width: 100%;
  padding: 12px;
  border: 2px solid #e5e7eb;
  border-radius: 10px;
  font-size: 16px;
}
.toast {
  position: fixed;
  top: 20px;
  left: 50%;
  transform: translateX(-50%);
  background: var(--success);
  color: white;
  padding: 12px 24px;
  border-radius: 30px;
  z-index: 2000;
  opacity: 0;
  transition: opacity 0.3s;
}
.toast.show { opacity: 1; }
</style>
</head>
<body>
<div id="toast" class="toast">OK</div>
<div class="container">
  <h1>💎 Qarz Daftar</h1>
  
  <div class="stats">
    <div class="stat">
      <div class="stat-label">Jami berilgan</div>
      <div class="stat-value" id="total-given">0</div>
    </div>
    <div class="stat">
      <div class="stat-label">Qolgan</div>
      <div class="stat-value" id="total-remaining">0</div>
    </div>
    <div class="stat">
      <div class="stat-label">Tolangan</div>
      <div class="stat-value" id="total-paid">0</div>
    </div>
    <div class="stat">
      <div class="stat-label">Qarzdorlar</div>
      <div class="stat-value" id="total-count">0</div>
    </div>
  </div>

  <button class="btn btn-primary" onclick="openAddModal()">➕ Yangi qarzdor qo'shish</button>

  <div id="debtors-list"></div>
</div>

<div class="nav">
  <button class="nav-btn" onclick="loadData()">🔄 Yangilash</button>
</div>

<div id="addModal" class="modal">
  <div class="modal-content">
    <h2>Yangi qarzdor</h2>
    <div class="form-group">
      <label>Ism *</label>
      <input type="text" id="add-name" placeholder="Akramov Jasur">
    </div>
    <div class="form-group">
      <label>Telefon</label>
      <input type="tel" id="add-phone" placeholder="+998...">
    </div>
    <div class="form-group">
      <label>Summa *</label>
      <input type="number" id="add-amount" placeholder="1000000">
    </div>
    <div class="form-group">
      <label>Muddat</label>
      <input type="date" id="add-date">
    </div>
    <div class="form-group">
      <label>Kategoriya</label>
      <select id="add-category">
        <option>Shaxsiy</option>
        <option>Biznes</option>
        <option>Oila</option>
      </select>
    </div>
    <div class="form-group">
      <label>Izoh</label>
      <textarea id="add-note" rows="2"></textarea>
    </div>
    <button class="btn btn-primary" onclick="addDebtor()">Saqlash</button>
    <button class="btn btn-danger" onclick="closeModal('addModal')">Bekor</button>
  </div>
</div>

<div id="payModal" class="modal">
  <div class="modal-content">
    <h2>To'lov qo'shish</h2>
    <input type="hidden" id="pay-debtor-id">
    <div class="form-group">
      <label>Summa</label>
      <input type="number" id="pay-amount">
    </div>
    <div class="form-group">
      <label>Izoh</label>
      <input type="text" id="pay-note">
    </div>
    <button class="btn btn-success" onclick="addPayment()">Tasdiqlash</button>
    <button class="btn btn-danger" onclick="closeModal('payModal')">Bekor</button>
  </div>
</div>

<script>
const tg = window.Telegram?.WebApp;
if (tg) {
  tg.ready();
  tg.expand();
}

const headers = {
  'Content-Type': 'application/json',
};

if (tg?.initData) {
  headers['X-Telegram-Init-Data'] = tg.initData;
}

function formatMoney(amount) {
  return new Intl.NumberFormat('uz-UZ').format(amount || 0) + " so'm";
}

function showToast(msg, isError = false) {
  const toast = document.getElementById('toast');
  toast.textContent = msg;
  toast.style.background = isError ? 'var(--danger)' : 'var(--success)';
  toast.classList.add('show');
  setTimeout(() => toast.classList.remove('show'), 3000);
}

async function loadData() {
  try {
    const statsRes = await fetch('/api/stats', { headers });
    if (!statsRes.ok) throw new Error('Stats xatosi');
    const stats = await statsRes.json();
    
    document.getElementById('total-given').textContent = formatMoney(stats.total_given);
    document.getElementById('total-remaining').textContent = formatMoney(stats.total_remaining);
    document.getElementById('total-paid').textContent = formatMoney(stats.total_paid);
    document.getElementById('total-count').textContent = stats.total_debtors;

    const debtorsRes = await fetch('/api/debtors', { headers });
    if (!debtorsRes.ok) throw new Error('Debtors xatosi');
    const debtors = await debtorsRes.json();

    const list = document.getElementById('debtors-list');
    if (debtors.length === 0) {
      list.innerHTML = '<div class="card"><p>Hali qarzdor yo\\'q</p></div>';
      return;
    }

    list.innerHTML = debtors.map(d => `
      <div class="card">
        <div class="card-header">
          <div class="card-name">${d.name}</div>
          <div class="card-amount">${formatMoney(d.remaining_amount)}</div>
        </div>
        <div class="card-info">
          ${d.phone || 'Telefon yo\\'q'} | ${d.category}<br>
          Jami: ${formatMoney(d.total_amount)} | Tolangan: ${formatMoney(d.paid_amount)}
          ${d.due_date ? '<br>Muddat: ' + d.due_date : ''}
        </div>
        <div style="margin-top: 12px; display: flex; gap: 8px;">
          ${d.status !== 'PAID' ? `<button class="btn btn-success" style="flex:1" onclick="openPayModal(${d.id}, ${d.remaining_amount})">💰 To'lov</button>` : ''}
          <button class="btn btn-danger" style="flex:1" onclick="deleteDebtor(${d.id})">🗑 O'chirish</button>
        </div>
      </div>
    `).join('');
  } catch (error) {
    console.error('Xato:', error);
    showToast('Xato: ' + error.message, true);
  }
}

function openAddModal() {
  document.getElementById('addModal').classList.add('active');
}

function closeModal(id) {
  document.getElementById(id).classList.remove('active');
}

async function addDebtor() {
  const data = {
    name: document.getElementById('add-name').value.trim(),
    phone: document.getElementById('add-phone').value.trim(),
    amount: parseFloat(document.getElementById('add-amount').value),
    due_date: document.getElementById('add-date').value || null,
    category: document.getElementById('add-category').value,
    note: document.getElementById('add-note').value.trim()
  };

  if (!data.name || !data.amount) {
    showToast('Ism va summa majburiy!', true);
    return;
  }

  try {
    const res = await fetch('/api/debtors', {
      method: 'POST',
      headers,
      body: JSON.stringify(data)
    });

    if (!res.ok) throw new Error('Qo\\'shish xatosi');

    showToast('Qarzdor qo\\'shildi!');
    closeModal('addModal');
    document.getElementById('add-name').value = '';
    document.getElementById('add-phone').value = '';
    document.getElementById('add-amount').value = '';
    document.getElementById('add-date').value = '';
    document.getElementById('add-note').value = '';
    loadData();
  } catch (error) {
    showToast('Xato: ' + error.message, true);
  }
}

function openPayModal(id, remaining) {
  document.getElementById('pay-debtor-id').value = id;
  document.getElementById('pay-amount').value = remaining;
  document.getElementById('pay-note').value = '';
  document.getElementById('payModal').classList.add('active');
}

async function addPayment() {
  const id = document.getElementById('pay-debtor-id').value;
  const amount = parseFloat(document.getElementById('pay-amount').value);
  const note = document.getElementById('pay-note').value.trim();

  if (!amount || amount <= 0) {
    showToast('Noto\\'g\\'ri summa', true);
    return;
  }

  try {
    const res = await fetch(`/api/debtors/${id}/pay`, {
      method: 'PUT',
      headers,
      body: JSON.stringify({ amount, note })
    });

    if (!res.ok) throw new Error('To\\'lov xatosi');

    showToast('To\\'lov qabul qilindi!');
    closeModal('payModal');
    loadData();
  } catch (error) {
    showToast('Xato: ' + error.message, true);
  }
}

async function deleteDebtor(id) {
  if (!confirm('O\\'chirishni tasdiqlaysizmi?')) return;

  try {
    const res = await fetch(`/api/debtors/${id}`, {
      method: 'DELETE',
      headers
    });

    if (!res.ok) throw new Error('O\\'chirish xatosi');

    showToast('O\\'chirildi!');
    loadData();
  } catch (error) {
    showToast('Xato: ' + error.message, true);
  }
}

window.onload = () => {
  loadData();
};
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(HTML_TEMPLATE)


@app.get("/api/stats")
async def get_stats(user: dict = Depends(get_current_user)):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT 
                COUNT(*) as total_debtors,
                COALESCE(SUM(total_amount), 0) as total_given,
                COALESCE(SUM(paid_amount), 0) as total_paid,
                COALESCE(SUM(remaining_amount), 0) as total_remaining
            FROM debtors 
            WHERE user_id = ?
        """, (user["telegram_id"],))
        
        row = await cursor.fetchone()
        return dict(row)


@app.get("/api/debtors")
async def get_debtors(user: dict = Depends(get_current_user)):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT * FROM debtors 
            WHERE user_id = ? 
            ORDER BY created_at DESC
        """, (user["telegram_id"],))
        
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


@app.post("/api/debtors")
async def create_debtor(debtor: DebtorCreate, user: dict = Depends(get_current_user)):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            INSERT INTO debtors 
            (user_id, name, phone, category, note, total_amount, paid_amount, remaining_amount, due_date)
            VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
        """, (
            user["telegram_id"],
            debtor.name,
            debtor.phone,
            debtor.category,
            debtor.note,
            debtor.amount,
            debtor.amount,
            debtor.due_date
        ))
        await db.commit()
        
        return {"id": cursor.lastrowid, "message": "Qarzdor qo'shildi"}


@app.put("/api/debtors/{debtor_id}/pay")
async def add_payment(debtor_id: int, payment: PaymentCreate, user: dict = Depends(get_current_user)):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT total_amount, paid_amount, remaining_amount 
            FROM debtors 
            WHERE id = ? AND user_id = ?
        """, (debtor_id, user["telegram_id"]))
        
        debtor = await cursor.fetchone()
        if not debtor:
            raise HTTPException(status_code=404, detail="Qarzdor topilmadi")
        
        if payment.amount <= 0:
            raise HTTPException(status_code=400, detail="Summa 0 dan katta bo'lishi kerak")
        
        if payment.amount > debtor[2]:
            raise HTTPException(status_code=400, detail=f"Summa qolgan qarzdan katta (qolgan: {debtor[2]})")
        
        new_paid = debtor[1] + payment.amount
        new_remaining = debtor[2] - payment.amount
        new_status = "PAID" if new_remaining == 0 else "ACTIVE"
        
        await db.execute("""
            INSERT INTO payments (debtor_id, amount, note)
            VALUES (?, ?, ?)
        """, (debtor_id, payment.amount, payment.note))
        
        await db.execute("""
            UPDATE debtors 
            SET paid_amount = ?, remaining_amount = ?, status = ?
            WHERE id = ?
        """, (new_paid, new_remaining, new_status, debtor_id))
        
        await db.commit()
        
        return {
            "message": "To'lov qabul qilindi",
            "new_remaining": new_remaining,
            "status": new_status
        }


@app.delete("/api/debtors/{debtor_id}")
async def delete_debtor(debtor_id: int, user: dict = Depends(get_current_user)):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM payments WHERE debtor_id = ?", (debtor_id,))
        
        cursor = await db.execute("""
            DELETE FROM debtors 
            WHERE id = ? AND user_id = ?
        """, (debtor_id, user["telegram_id"]))
        
        await db.commit()
        
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Qarzdor topilmadi")
        
        return {"message": "Qarzdor o'chirildi"}


# ============ BOT HANDLERS ============
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="📒 Ilovani ochish",
            web_app=WebAppInfo(url=WEBAPP_URL)
        )]
    ])
    
    await message.answer(
        "<b>💎 Qarz Daftar Pro</b>\n\n"
        "✅ Qarzdorlar ro'yxati\n"
        "✅ To'lovlar va statistika\n"
        "✅ Chiroyli dizayn\n\n"
        "Tugmani bosing 👇",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


# ============ BOT THREAD ============
def run_bot():
    try:
        logger.info("🤖 Bot ishga tushmoqda...")
        asyncio.run(dp.start_polling(bot))
    except Exception as e:
        logger.error(f"Bot xatosi: {e}")


# ============ MAIN ============
if __name__ == "__main__":
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")