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
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>Qarz Daftar Pro</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css" rel="stylesheet">
<style>
* { margin: 0; padding: 0; box-sizing: border-box; -webkit-tap-highlight-color: transparent; }

:root {
  --bg-gradient: linear-gradient(135deg, #667eea 0%, #764ba2 50%, #f093fb 100%);
  --card-bg: rgba(255, 255, 255, 0.98);
  --primary: #667eea;
  --accent: #f093fb;
  --success: #10b981;
  --danger: #ef4444;
  --warning: #f59e0b;
  --text: #1f2937;
  --text-light: #6b7280;
  --shadow-sm: 0 8px 32px rgba(0, 0, 0, 0.12);
  --glow: 0 0 40px rgba(102, 126, 234, 0.4);
}

[data-theme="dark"] {
  --bg-gradient: linear-gradient(135deg, #0f172a 0%, #1e293b 50%, #312e81 100%);
  --card-bg: rgba(30, 41, 59, 0.98);
  --text: #f8fafc;
  --text-light: #94a3b8;
  --shadow-sm: 0 8px 32px rgba(0, 0, 0, 0.3);
}

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: var(--bg-gradient);
  min-height: 100vh;
  color: var(--text);
  padding: 20px;
  padding-bottom: 120px;
  overflow-x: hidden;
  position: relative;
}

body::before {
  content: '';
  position: fixed;
  top: -50%;
  left: -50%;
  width: 200%;
  height: 200%;
  background: 
    radial-gradient(circle at 20% 50%, rgba(102, 126, 234, 0.15) 0%, transparent 50%),
    radial-gradient(circle at 80% 80%, rgba(240, 147, 251, 0.15) 0%, transparent 50%),
    radial-gradient(circle at 40% 20%, rgba(16, 185, 129, 0.1) 0%, transparent 50%);
  pointer-events: none;
  z-index: 0;
  animation: bgFloat 20s ease-in-out infinite;
}

@keyframes bgFloat {
  0%, 100% { transform: translate(0, 0) rotate(0deg); }
  33% { transform: translate(30px, -30px) rotate(120deg); }
  66% { transform: translate(-20px, 20px) rotate(240deg); }
}

.container { max-width: 600px; margin: 0 auto; position: relative; z-index: 1; }

.header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 28px;
  animation: slideDown 0.6s cubic-bezier(0.34, 1.56, 0.64, 1);
}

.logo { display: flex; align-items: center; gap: 14px; }

.logo-icon {
  width: 56px;
  height: 56px;
  background: linear-gradient(135deg, var(--primary), var(--accent));
  border-radius: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 28px;
  box-shadow: var(--glow);
  animation: pulse 2s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { transform: scale(1); box-shadow: var(--glow); }
  50% { transform: scale(1.05); box-shadow: 0 0 60px rgba(102, 126, 234, 0.6); }
}

.logo-text h1 {
  font-size: 26px;
  font-weight: 900;
  background: linear-gradient(135deg, var(--primary), var(--accent));
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  letter-spacing: -0.5px;
}

.logo-text p {
  font-size: 12px;
  color: var(--text-light);
  margin-top: 2px;
  font-weight: 500;
}

.theme-toggle {
  width: 48px;
  height: 48px;
  border-radius: 50%;
  background: var(--card-bg);
  border: none;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 22px;
  box-shadow: var(--shadow-sm);
  transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
}

.theme-toggle:active { transform: scale(0.85) rotate(180deg); }

.stats-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 16px;
  margin-bottom: 28px;
}

.stat-card {
  background: var(--card-bg);
  backdrop-filter: blur(20px);
  padding: 22px;
  border-radius: 28px;
  box-shadow: var(--shadow-sm);
  position: relative;
  overflow: hidden;
  animation: fadeInUp 0.5s ease backwards;
  transition: transform 0.3s ease;
}

.stat-card:nth-child(1) { animation-delay: 0.1s; }
.stat-card:nth-child(2) { animation-delay: 0.2s; }
.stat-card:nth-child(3) { animation-delay: 0.3s; }
.stat-card:nth-child(4) { animation-delay: 0.4s; }

.stat-card:active { transform: scale(0.95); }

.stat-card::before {
  content: '';
  position: absolute;
  top: -50%;
  right: -50%;
  width: 200%;
  height: 200%;
  background: radial-gradient(circle, rgba(102, 126, 234, 0.08) 0%, transparent 70%);
  pointer-events: none;
}

.stat-icon {
  width: 52px;
  height: 52px;
  border-radius: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 26px;
  margin-bottom: 14px;
  position: relative;
  z-index: 1;
  box-shadow: 0 8px 20px rgba(0, 0, 0, 0.15);
}

.stat-icon.blue { background: linear-gradient(135deg, #667eea, #764ba2); }
.stat-icon.green { background: linear-gradient(135deg, #10b981, #059669); }
.stat-icon.orange { background: linear-gradient(135deg, #f59e0b, #d97706); }
.stat-icon.purple { background: linear-gradient(135deg, #8b5cf6, #7c3aed); }

.stat-label {
  font-size: 13px;
  color: var(--text-light);
  margin-bottom: 6px;
  font-weight: 600;
  position: relative;
  z-index: 1;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.stat-value {
  font-size: 26px;
  font-weight: 900;
  color: var(--text);
  position: relative;
  z-index: 1;
  letter-spacing: -0.5px;
}

.main-btn {
  width: 100%;
  padding: 20px;
  background: linear-gradient(135deg, var(--primary), var(--accent));
  border: none;
  border-radius: 24px;
  color: white;
  font-size: 18px;
  font-weight: 800;
  cursor: pointer;
  margin-bottom: 28px;
  box-shadow: 0 12px 40px rgba(102, 126, 234, 0.5);
  transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  position: relative;
  overflow: hidden;
}

.main-btn::before {
  content: '';
  position: absolute;
  top: 50%;
  left: 50%;
  width: 0;
  height: 0;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.3);
  transform: translate(-50%, -50%);
  transition: width 0.6s, height 0.6s;
}

.main-btn:active::before { width: 300px; height: 300px; }
.main-btn:active { transform: scale(0.95); box-shadow: 0 6px 20px rgba(102, 126, 234, 0.4); }

.section-title {
  font-size: 20px;
  font-weight: 800;
  margin-bottom: 18px;
  display: flex;
  align-items: center;
  gap: 10px;
  color: var(--text);
}

.section-title i {
  background: linear-gradient(135deg, var(--primary), var(--accent));
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

.debtor-card {
  background: var(--card-bg);
  backdrop-filter: blur(20px);
  padding: 22px;
  border-radius: 28px;
  margin-bottom: 18px;
  box-shadow: var(--shadow-sm);
  position: relative;
  overflow: hidden;
  animation: slideIn 0.4s cubic-bezier(0.34, 1.56, 0.64, 1) backwards;
  transition: transform 0.3s ease;
}

.debtor-card:active { transform: scale(0.98); }

.debtor-card::before {
  content: '';
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 5px;
  background: linear-gradient(180deg, var(--primary), var(--accent));
  border-radius: 0 4px 4px 0;
}

.debtor-card.overdue::before { background: linear-gradient(180deg, var(--danger), #dc2626); }
.debtor-card.paid::before { background: linear-gradient(180deg, var(--success), #059669); }

.debtor-header {
  display: flex;
  justify-content: space-between;
  align-items: start;
  margin-bottom: 14px;
}

.debtor-name {
  font-size: 19px;
  font-weight: 800;
  color: var(--text);
}

.debtor-amount {
  font-size: 22px;
  font-weight: 900;
  background: linear-gradient(135deg, var(--primary), var(--accent));
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

.debtor-info {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  margin-bottom: 14px;
  font-size: 14px;
  color: var(--text-light);
}

.debtor-info span {
  display: flex;
  align-items: center;
  gap: 6px;
}

.debtor-info i { color: var(--primary); }

.debtor-badge {
  display: inline-block;
  padding: 7px 14px;
  border-radius: 20px;
  font-size: 12px;
  font-weight: 700;
  margin-bottom: 14px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.badge-active { background: rgba(102, 126, 234, 0.15); color: var(--primary); }
.badge-overdue { background: rgba(239, 68, 68, 0.15); color: var(--danger); }
.badge-paid { background: rgba(16, 185, 129, 0.15); color: var(--success); }

.debtor-details {
  font-size: 13px;
  color: var(--text-light);
  margin-bottom: 16px;
  padding: 12px;
  background: rgba(0, 0, 0, 0.03);
  border-radius: 14px;
}

.debtor-actions { display: flex; gap: 12px; }

.btn-action {
  flex: 1;
  padding: 14px;
  border: none;
  border-radius: 16px;
  font-size: 15px;
  font-weight: 700;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
}

.btn-action:active { transform: scale(0.9); }

.btn-pay {
  background: linear-gradient(135deg, var(--success), #059669);
  color: white;
  box-shadow: 0 8px 20px rgba(16, 185, 129, 0.4);
}

.btn-delete {
  background: rgba(239, 68, 68, 0.15);
  color: var(--danger);
}

.empty-state {
  text-align: center;
  padding: 80px 20px;
  color: var(--text-light);
}

.empty-icon {
  font-size: 80px;
  margin-bottom: 20px;
  opacity: 0.2;
  animation: float 3s ease-in-out infinite;
}

@keyframes float {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-20px); }
}

.modal-overlay {
  display: none;
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.7);
  backdrop-filter: blur(12px);
  z-index: 1000;
  align-items: flex-end;
  justify-content: center;
}

.modal-overlay.active {
  display: flex;
  animation: fadeIn 0.3s ease;
}

.modal-content {
  background: var(--card-bg);
  width: 100%;
  max-width: 600px;
  border-radius: 36px 36px 0 0;
  padding: 36px 28px;
  max-height: 90vh;
  overflow-y: auto;
  animation: slideUp 0.4s cubic-bezier(0.34, 1.56, 0.64, 1);
}

.modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 28px;
}

.modal-title {
  font-size: 26px;
  font-weight: 900;
  background: linear-gradient(135deg, var(--primary), var(--accent));
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

.modal-close {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.1);
  border: none;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  transition: all 0.3s ease;
}

.modal-close:active { transform: scale(0.8) rotate(90deg); }

.form-group { margin-bottom: 22px; }

.form-label {
  display: block;
  font-size: 14px;
  font-weight: 700;
  margin-bottom: 10px;
  color: var(--text);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.form-input {
  width: 100%;
  padding: 18px;
  border: 2px solid rgba(0, 0, 0, 0.1);
  border-radius: 18px;
  font-size: 17px;
  background: rgba(0, 0, 0, 0.03);
  color: var(--text);
  transition: all 0.3s ease;
  font-weight: 500;
}

.form-input:focus {
  outline: none;
  border-color: var(--primary);
  box-shadow: 0 0 0 4px rgba(102, 126, 234, 0.15);
  background: rgba(0, 0, 0, 0.05);
}

.form-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
}

.btn-submit {
  width: 100%;
  padding: 20px;
  background: linear-gradient(135deg, var(--primary), var(--accent));
  border: none;
  border-radius: 20px;
  color: white;
  font-size: 18px;
  font-weight: 800;
  cursor: pointer;
  margin-top: 10px;
  box-shadow: 0 12px 40px rgba(102, 126, 234, 0.5);
  transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
}

.btn-submit:active { transform: scale(0.95); }

.toast {
  position: fixed;
  top: 30px;
  left: 50%;
  transform: translateX(-50%) translateY(-150px);
  background: linear-gradient(135deg, var(--success), #059669);
  color: white;
  padding: 18px 36px;
  border-radius: 30px;
  font-weight: 700;
  font-size: 16px;
  box-shadow: 0 15px 40px rgba(0, 0, 0, 0.3);
  z-index: 2000;
  opacity: 0;
  transition: all 0.4s cubic-bezier(0.34, 1.56, 0.64, 1);
}

.toast.show {
  opacity: 1;
  transform: translateX(-50%) translateY(0);
}

.toast.error { background: linear-gradient(135deg, var(--danger), #dc2626); }

#confetti-canvas {
  position: fixed;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
  z-index: 1500;
}

@keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }

@keyframes fadeInUp {
  from { opacity: 0; transform: translateY(30px); }
  to { opacity: 1; transform: translateY(0); }
}

@keyframes slideDown {
  from { opacity: 0; transform: translateY(-30px); }
  to { opacity: 1; transform: translateY(0); }
}

@keyframes slideIn {
  from { opacity: 0; transform: translateX(-30px); }
  to { opacity: 1; transform: translateX(0); }
}

@keyframes slideUp {
  from { transform: translateY(100%); }
  to { transform: translateY(0); }
}

@media (max-width: 480px) {
  .stats-grid { gap: 12px; }
  .stat-value { font-size: 22px; }
  .form-row { grid-template-columns: 1fr; }
}
</style>
</head>
<body>
<canvas id="confetti-canvas"></canvas>
<div id="toast" class="toast">OK</div>

<div class="container">
  <div class="header">
    <div class="logo">
      <div class="logo-icon">💎</div>
      <div class="logo-text">
        <h1>Qarz Daftar</h1>
        <p>Premium Edition</p>
      </div>
    </div>
    <button class="theme-toggle" onclick="toggleTheme()">
      <i class="fas fa-moon"></i>
    </button>
  </div>

  <div class="stats-grid">
    <div class="stat-card">
      <div class="stat-icon blue"><i class="fas fa-wallet"></i></div>
      <div class="stat-label">Jami berilgan</div>
      <div class="stat-value" id="total-given">0</div>
    </div>
    <div class="stat-card">
      <div class="stat-icon orange"><i class="fas fa-clock"></i></div>
      <div class="stat-label">Qolgan qarz</div>
      <div class="stat-value" id="total-remaining">0</div>
    </div>
    <div class="stat-card">
      <div class="stat-icon green"><i class="fas fa-check-circle"></i></div>
      <div class="stat-label">Tolangan</div>
      <div class="stat-value" id="total-paid">0</div>
    </div>
    <div class="stat-card">
      <div class="stat-icon purple"><i class="fas fa-users"></i></div>
      <div class="stat-label">Qarzdorlar</div>
      <div class="stat-value" id="total-count">0</div>
    </div>
  </div>

  <button class="main-btn" onclick="openAddModal()">
    <i class="fas fa-plus-circle"></i>
    Yangi qarzdor qo'shish
  </button>

  <div class="section-title">
    <i class="fas fa-list-ul"></i>
    Qarzdorlar ro'yxati
  </div>
  <div id="debtors-list"></div>
</div>

<div id="addModal" class="modal-overlay">
  <div class="modal-content">
    <div class="modal-header">
      <h2 class="modal-title">Yangi qarzdor</h2>
      <button class="modal-close" onclick="closeModal('addModal')">
        <i class="fas fa-times"></i>
      </button>
    </div>
    
    <div class="form-group">
      <label class="form-label">Ism familiya *</label>
      <input type="text" class="form-input" id="add-name" placeholder="Akramov Jasur">
    </div>
    
    <div class="form-row">
      <div class="form-group">
        <label class="form-label">Telefon</label>
        <input type="tel" class="form-input" id="add-phone" placeholder="+998...">
      </div>
      <div class="form-group">
        <label class="form-label">Summa *</label>
        <input type="number" class="form-input" id="add-amount" placeholder="1000000">
      </div>
    </div>
    
    <div class="form-row">
      <div class="form-group">
        <label class="form-label">Muddat</label>
        <input type="date" class="form-input" id="add-date">
      </div>
      <div class="form-group">
        <label class="form-label">Kategoriya</label>
        <select class="form-input" id="add-category">
          <option>Shaxsiy</option>
          <option>Biznes</option>
          <option>Oila</option>
          <option>Do'st</option>
        </select>
      </div>
    </div>
    
    <div class="form-group">
      <label class="form-label">Izoh</label>
      <textarea class="form-input" id="add-note" rows="3" placeholder="Qo'shimcha ma'lumot..."></textarea>
    </div>
    
    <button class="btn-submit" onclick="addDebtor()">
      <i class="fas fa-check"></i> Saqlash
    </button>
  </div>
</div>

<div id="payModal" class="modal-overlay">
  <div class="modal-content">
    <div class="modal-header">
      <h2 class="modal-title">To'lov qo'shish</h2>
      <button class="modal-close" onclick="closeModal('payModal')">
        <i class="fas fa-times"></i>
      </button>
    </div>
    
    <input type="hidden" id="pay-debtor-id">
    
    <div class="form-group">
      <label class="form-label">Summa</label>
      <input type="number" class="form-input" id="pay-amount">
    </div>
    
    <div class="form-group">
      <label class="form-label">Izoh</label>
      <input type="text" class="form-input" id="pay-note" placeholder="Naqd / Karta...">
    </div>
    
    <button class="btn-submit" onclick="addPayment()">
      <i class="fas fa-check"></i> Tasdiqlash
    </button>
  </div>
</div>

<script>
const tg = window.Telegram?.WebApp;
if (tg) {
  tg.ready();
  tg.expand();
  tg.setHeaderColor('#667eea');
}

const headers = { 'Content-Type': 'application/json' };
if (tg?.initData) headers['X-Telegram-Init-Data'] = tg.initData;

function playSuccessSound() {
  try {
    const audioContext = new (window.AudioContext || window.webkitAudioContext)();
    const oscillator = audioContext.createOscillator();
    const gainNode = audioContext.createGain();
    
    oscillator.connect(gainNode);
    gainNode.connect(audioContext.destination);
    
    oscillator.frequency.setValueAtTime(523.25, audioContext.currentTime);
    oscillator.frequency.setValueAtTime(659.25, audioContext.currentTime + 0.1);
    oscillator.frequency.setValueAtTime(783.99, audioContext.currentTime + 0.2);
    
    gainNode.gain.setValueAtTime(0.3, audioContext.currentTime);
    gainNode.gain.exponentialRampToValueAtTime(0.01, audioContext.currentTime + 0.5);
    
    oscillator.start(audioContext.currentTime);
    oscillator.stop(audioContext.currentTime + 0.5);
  } catch (e) {}
}

function playClickSound() {
  try {
    const audioContext = new (window.AudioContext || window.webkitAudioContext)();
    const oscillator = audioContext.createOscillator();
    const gainNode = audioContext.createGain();
    
    oscillator.connect(gainNode);
    gainNode.connect(audioContext.destination);
    
    oscillator.frequency.setValueAtTime(800, audioContext.currentTime);
    gainNode.gain.setValueAtTime(0.2, audioContext.currentTime);
    gainNode.gain.exponentialRampToValueAtTime(0.01, audioContext.currentTime + 0.1);
    
    oscillator.start(audioContext.currentTime);
    oscillator.stop(audioContext.currentTime + 0.1);
  } catch (e) {}
}

function launchConfetti() {
  const canvas = document.getElementById('confetti-canvas');
  const ctx = canvas.getContext('2d');
  canvas.width = window.innerWidth;
  canvas.height = window.innerHeight;
  
  const particles = [];
  const colors = ['#667eea', '#f093fb', '#10b981', '#f59e0b', '#ef4444'];
  
  for (let i = 0; i < 100; i++) {
    particles.push({
      x: canvas.width / 2,
      y: canvas.height / 2,
      vx: (Math.random() - 0.5) * 20,
      vy: (Math.random() - 0.5) * 20 - 10,
      color: colors[Math.floor(Math.random() * colors.length)],
      size: Math.random() * 8 + 4,
      life: 1
    });
  }
  
  function animate() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    
    particles.forEach((p, i) => {
      p.x += p.vx;
      p.y += p.vy;
      p.vy += 0.5;
      p.life -= 0.02;
      
      ctx.fillStyle = p.color;
      ctx.globalAlpha = p.life;
      ctx.fillRect(p.x, p.y, p.size, p.size);
      
      if (p.life <= 0) particles.splice(i, 1);
    });
    
    if (particles.length > 0) requestAnimationFrame(animate);
  }
  
  animate();
}

function formatMoney(amount) {
  return new Intl.NumberFormat('uz-UZ').format(amount || 0) + " so'm";
}

function showToast(msg, isError = false) {
  const toast = document.getElementById('toast');
  toast.textContent = msg;
  toast.className = 'toast show' + (isError ? ' error' : '');
  setTimeout(() => toast.classList.remove('show'), 3000);
}

function toggleTheme() {
  playClickSound();
  const body = document.body;
  const isDark = body.getAttribute('data-theme') === 'dark';
  body.setAttribute('data-theme', isDark ? 'light' : 'dark');
  localStorage.setItem('theme', isDark ? 'light' : 'dark');
  
  const icon = document.querySelector('.theme-toggle i');
  icon.className = isDark ? 'fas fa-moon' : 'fas fa-sun';
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
      list.innerHTML = `
        <div class="empty-state">
          <div class="empty-icon"><i class="fas fa-inbox"></i></div>
          <p style="font-size: 18px; font-weight: 600;">Hali qarzdor yo'q</p>
          <p style="font-size: 14px; margin-top: 8px; opacity: 0.7;">Yuqoridagi tugmani bosing</p>
        </div>
      `;
      return;
    }

    list.innerHTML = debtors.map((d, i) => {
      const statusClass = d.status === 'OVERDUE' ? 'overdue' : d.status === 'PAID' ? 'paid' : '';
      const badgeClass = d.status === 'OVERDUE' ? 'badge-overdue' : d.status === 'PAID' ? 'badge-paid' : 'badge-active';
      const statusText = d.status === 'OVERDUE' ? 'Muddati o\\'tgan' : d.status === 'PAID' ? 'To\\'langan' : 'Faol';
      
      return `
        <div class="debtor-card ${statusClass}" style="animation-delay: ${i * 0.05}s">
          <div class="debtor-header">
            <div class="debtor-name">${d.name}</div>
            <div class="debtor-amount">${formatMoney(d.remaining_amount)}</div>
          </div>
          
          <div class="debtor-info">
            <span><i class="fas fa-phone"></i> ${d.phone || '-'}</span>
            <span><i class="fas fa-tag"></i> ${d.category}</span>
            ${d.due_date ? `<span><i class="fas fa-calendar"></i> ${d.due_date}</span>` : ''}
          </div>
          
          <span class="debtor-badge ${badgeClass}">${statusText}</span>
          
          <div class="debtor-details">
            <strong>Jami:</strong> ${formatMoney(d.total_amount)} | 
            <strong>Tolangan:</strong> ${formatMoney(d.paid_amount)}
          </div>
          
          <div class="debtor-actions">
            ${d.status !== 'PAID' ? `
              <button class="btn-action btn-pay" onclick="openPayModal(${d.id}, ${d.remaining_amount})">
                <i class="fas fa-money-bill-wave"></i> To'lov
              </button>
            ` : ''}
            <button class="btn-action btn-delete" onclick="deleteDebtor(${d.id})">
              <i class="fas fa-trash"></i> O'chirish
            </button>
          </div>
        </div>
      `;
    }).join('');
  } catch (error) {
    console.error('Xato:', error);
    showToast('Xato: ' + error.message, true);
  }
}

function openAddModal() {
  playClickSound();
  document.getElementById('addModal').classList.add('active');
}

function closeModal(id) {
  playClickSound();
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

    playSuccessSound();
    showToast('Qarzdor qo\\'shildi! ✨');
    closeModal('addModal');
    
    ['add-name', 'add-phone', 'add-amount', 'add-date', 'add-note'].forEach(id => {
      document.getElementById(id).value = '';
    });
    
    loadData();
  } catch (error) {
    showToast('Xato: ' + error.message, true);
  }
}

function openPayModal(id, remaining) {
  playClickSound();
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

    playSuccessSound();
    launchConfetti();
    showToast('To\\'lov qabul qilindi! 💰🎉');
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

    playClickSound();
    showToast('O\\'chirildi! 🗑');
    loadData();
  } catch (error) {
    showToast('Xato: ' + error.message, true);
  }
}

window.onload = () => {
  const savedTheme = localStorage.getItem('theme') || 'light';
  document.body.setAttribute('data-theme', savedTheme);
  
  const icon = document.querySelector('.theme-toggle i');
  icon.className = savedTheme === 'dark' ? 'fas fa-sun' : 'fas fa-moon';
  
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
        
        # Yangi event loop yaratish
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # Signal handler'larni o'chirish (thread muammosini hal qilish)
        try:
            loop.add_signal_handler = lambda *args, **kwargs: None
        except Exception:
            pass
        
        # Botni ishga tushirish
        loop.run_until_complete(dp.start_polling(bot))
    except Exception as e:
        logger.error(f"Bot xatosi: {e}")


# ============ MAIN ============
if __name__ == "__main__":
    # Botni alohida thread'da ishga tushirish
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    # Serverni ishga tushirish
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")