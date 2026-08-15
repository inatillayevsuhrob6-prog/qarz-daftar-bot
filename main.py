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
from fastapi.responses import HTMLResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart, Command
from aiogram.types import WebAppInfo, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.client.default import DefaultBotProperties

# ============ KONFIGURATSIYA ============
BOT_TOKEN = "8651436055:AAH3FgpFyhcnBo4RXXYQpLMv1Wk4qNiCXX0"
WEBAPP_URL = "https://qarz-daftar-bot.onrender.com"
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
        check_list = [f"{k}={data[k]}" for k in sorted(data.keys())]
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
        raise HTTPException(status_code=401, detail="Auth xatosi")


async def get_current_user(request: Request) -> dict:
    init_data = request.headers.get("X-Telegram-Init-Data")
    if not init_data:
        init_data = request.query_params.get("initData", "")
    if not init_data:
        return {"telegram_id": 123456, "username": "test", "first_name": "Test", "last_name": "User"}
    return verify_telegram_auth(init_data)


# ============ FASTAPI ============
app = FastAPI(title="Qarz Daftar API")

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


# ============ HTML ============
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="uz">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>Qarz Daftar Pro</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}
:root{
--bg-gradient:linear-gradient(135deg,#667eea 0%,#764ba2 50%,#f093fb 100%);
--card-bg:rgba(255,255,255,0.98);
--primary:#667eea;--accent:#f093fb;--success:#10b981;--danger:#ef4444;--warning:#f59e0b;
--text:#1f2937;--text-light:#6b7280;
--shadow-sm:0 8px 32px rgba(0,0,0,0.12);--glow:0 0 40px rgba(102,126,234,0.4)}
[data-theme="dark"]{
--bg-gradient:linear-gradient(135deg,#0f172a 0%,#1e293b 50%,#312e81 100%);
--card-bg:rgba(30,41,59,0.98);--text:#f8fafc;--text-light:#94a3b8;
--shadow-sm:0 8px 32px rgba(0,0,0,0.3)}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:var(--bg-gradient);min-height:100vh;color:var(--text);padding:20px;padding-bottom:110px;overflow-x:hidden}
body::before{content:'';position:fixed;top:-50%;left:-50%;width:200%;height:200%;background:radial-gradient(circle at 20% 50%,rgba(102,126,234,0.15) 0%,transparent 50%),radial-gradient(circle at 80% 80%,rgba(240,147,251,0.15) 0%,transparent 50%);pointer-events:none;z-index:0;animation:bgFloat 20s ease-in-out infinite}
@keyframes bgFloat{0%,100%{transform:translate(0,0)}33%{transform:translate(30px,-30px)}66%{transform:translate(-20px,20px)}}
.container{max-width:600px;margin:0 auto;position:relative;z-index:1}
.header{display:flex;justify-content:space-between;align-items:center;margin-bottom:24px;animation:slideDown .6s cubic-bezier(.34,1.56,.64,1)}
.logo{display:flex;align-items:center;gap:14px}
.logo-icon{width:52px;height:52px;background:linear-gradient(135deg,var(--primary),var(--accent));border-radius:18px;display:flex;align-items:center;justify-content:center;font-size:26px;box-shadow:var(--glow);animation:pulse 2s ease-in-out infinite}
@keyframes pulse{0%,100%{transform:scale(1)}50%{transform:scale(1.05)}}
.logo-text h1{font-size:24px;font-weight:900;background:linear-gradient(135deg,var(--primary),var(--accent));-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.logo-text p{font-size:11px;color:var(--text-light);font-weight:500}
.header-actions{display:flex;gap:10px}
.icon-btn{width:44px;height:44px;border-radius:50%;background:var(--card-bg);border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:18px;box-shadow:var(--shadow-sm);color:var(--text);transition:all .3s}
.icon-btn:active{transform:scale(.85) rotate(180deg)}
.stats-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:14px;margin-bottom:24px}
.stat-card{background:var(--card-bg);backdrop-filter:blur(20px);padding:20px;border-radius:24px;box-shadow:var(--shadow-sm);position:relative;overflow:hidden;animation:fadeInUp .5s ease backwards}
.stat-card:nth-child(1){animation-delay:.1s}.stat-card:nth-child(2){animation-delay:.2s}
.stat-card:nth-child(3){animation-delay:.3s}.stat-card:nth-child(4){animation-delay:.4s}
.stat-card:active{transform:scale(.95)}
.stat-icon{width:48px;height:48px;border-radius:16px;display:flex;align-items:center;justify-content:center;font-size:22px;margin-bottom:12px;color:#fff;box-shadow:0 8px 20px rgba(0,0,0,.15)}
.stat-icon.blue{background:linear-gradient(135deg,#667eea,#764ba2)}
.stat-icon.green{background:linear-gradient(135deg,#10b981,#059669)}
.stat-icon.orange{background:linear-gradient(135deg,#f59e0b,#d97706)}
.stat-icon.purple{background:linear-gradient(135deg,#8b5cf6,#7c3aed)}
.stat-label{font-size:12px;color:var(--text-light);margin-bottom:4px;font-weight:600;text-transform:uppercase;letter-spacing:.5px}
.stat-value{font-size:22px;font-weight:900}
.main-btn{width:100%;padding:18px;background:linear-gradient(135deg,var(--primary),var(--accent));border:none;border-radius:22px;color:#fff;font-size:17px;font-weight:800;cursor:pointer;margin-bottom:24px;box-shadow:0 12px 40px rgba(102,126,234,.5);display:flex;align-items:center;justify-content:center;gap:12px;transition:all .3s}
.main-btn:active{transform:scale(.95)}
.section-title{font-size:19px;font-weight:800;margin-bottom:16px;display:flex;align-items:center;gap:10px}
.section-title i{color:var(--primary)}
.debtor-card{background:var(--card-bg);backdrop-filter:blur(20px);padding:20px;border-radius:24px;margin-bottom:16px;box-shadow:var(--shadow-sm);position:relative;overflow:hidden;animation:slideIn .4s cubic-bezier(.34,1.56,.64,1) backwards}
.debtor-card::before{content:'';position:absolute;left:0;top:0;bottom:0;width:5px;background:linear-gradient(180deg,var(--primary),var(--accent))}
.debtor-card.overdue::before{background:linear-gradient(180deg,var(--danger),#dc2626)}
.debtor-card.paid::before{background:linear-gradient(180deg,var(--success),#059669)}
.debtor-header{display:flex;justify-content:space-between;align-items:start;margin-bottom:12px}
.debtor-name{font-size:18px;font-weight:800}
.debtor-amount{font-size:20px;font-weight:900;background:linear-gradient(135deg,var(--primary),var(--accent));-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.debtor-info{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:12px;font-size:13px;color:var(--text-light)}
.debtor-info span{display:flex;align-items:center;gap:6px}
.debtor-info i{color:var(--primary)}
.debtor-badge{display:inline-block;padding:6px 12px;border-radius:20px;font-size:11px;font-weight:700;margin-bottom:12px;text-transform:uppercase;letter-spacing:.5px}
.badge-active{background:rgba(102,126,234,.15);color:var(--primary)}
.badge-overdue{background:rgba(239,68,68,.15);color:var(--danger)}
.badge-paid{background:rgba(16,185,129,.15);color:var(--success)}
.debtor-details{font-size:12px;color:var(--text-light);margin-bottom:14px;padding:10px;background:rgba(0,0,0,.03);border-radius:12px}
.debtor-actions{display:flex;gap:10px}
.btn-action{flex:1;padding:13px;border:none;border-radius:14px;font-size:14px;font-weight:700;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:8px;transition:all .3s}
.btn-action:active{transform:scale(.9)}
.btn-pay{background:linear-gradient(135deg,var(--success),#059669);color:#fff;box-shadow:0 8px 20px rgba(16,185,129,.4)}
.btn-delete{background:rgba(239,68,68,.15);color:var(--danger)}
.empty-state{text-align:center;padding:60px 20px;color:var(--text-light)}
.empty-icon{font-size:70px;margin-bottom:16px;opacity:.2;animation:float 3s ease-in-out infinite}
@keyframes float{0%,100%{transform:translateY(0)}50%{transform:translateY(-20px)}}
.modal-overlay{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.7);backdrop-filter:blur(12px);z-index:1000;align-items:flex-end;justify-content:center}
.modal-overlay.active{display:flex;animation:fadeIn .3s}
.modal-content{background:var(--card-bg);width:100%;max-width:600px;border-radius:32px 32px 0 0;padding:32px 24px;max-height:90vh;overflow-y:auto;animation:slideUp .4s cubic-bezier(.34,1.56,.64,1)}
.modal-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:24px}
.modal-title{font-size:24px;font-weight:900;background:linear-gradient(135deg,var(--primary),var(--accent));-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.modal-close{width:38px;height:38px;border-radius:50%;background:rgba(0,0,0,.1);border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:18px;color:var(--text)}
.form-group{margin-bottom:20px}
.form-label{display:block;font-size:13px;font-weight:700;margin-bottom:8px;text-transform:uppercase;letter-spacing:.5px}
.form-input{width:100%;padding:16px;border:2px solid rgba(0,0,0,.1);border-radius:16px;font-size:16px;background:rgba(0,0,0,.03);color:var(--text);transition:all .3s}
.form-input:focus{outline:none;border-color:var(--primary);box-shadow:0 0 0 4px rgba(102,126,234,.15)}
.form-row{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.btn-submit{width:100%;padding:18px;background:linear-gradient(135deg,var(--primary),var(--accent));border:none;border-radius:18px;color:#fff;font-size:17px;font-weight:800;cursor:pointer;margin-top:8px;box-shadow:0 12px 40px rgba(102,126,234,.5)}
.btn-submit:active{transform:scale(.95)}
.toast{position:fixed;top:30px;left:50%;transform:translateX(-50%) translateY(-150px);background:linear-gradient(135deg,var(--success),#059669);color:#fff;padding:16px 32px;border-radius:30px;font-weight:700;font-size:15px;box-shadow:0 15px 40px rgba(0,0,0,.3);z-index:2000;opacity:0;transition:all .4s cubic-bezier(.34,1.56,.64,1)}
.toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
.toast.error{background:linear-gradient(135deg,var(--danger),#dc2626)}
#confetti-canvas{position:fixed;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:1500}
.page{display:none}.page.active{display:block;animation:fadeIn .3s}
.chart-container{background:var(--card-bg);border-radius:24px;padding:20px;margin-bottom:20px;box-shadow:var(--shadow-sm)}
.nav-bottom{position:fixed;bottom:0;left:0;right:0;background:var(--card-bg);backdrop-filter:blur(20px);display:flex;justify-content:space-around;padding:10px 0 calc(10px + env(safe-area-inset-bottom));box-shadow:0 -8px 32px rgba(0,0,0,.1);z-index:100}
.nav-item{display:flex;flex-direction:column;align-items:center;gap:4px;padding:6px 18px;border:none;background:none;cursor:pointer;color:var(--text-light);font-size:11px;font-weight:600;transition:all .3s}
.nav-item i{font-size:20px;transition:all .3s}
.nav-item.active{color:var(--primary)}
.nav-item.active i{transform:translateY(-3px) scale(1.1)}
.lang-selector{display:flex;gap:8px;margin-bottom:18px}
.lang-btn{flex:1;padding:12px;border:2px solid rgba(0,0,0,.1);border-radius:14px;background:var(--card-bg);cursor:pointer;font-weight:700;font-size:13px;color:var(--text);transition:all .3s}
.lang-btn.active{background:linear-gradient(135deg,var(--primary),var(--accent));color:#fff;border-color:transparent}
.report-box{background:var(--card-bg);border-radius:24px;padding:22px;box-shadow:var(--shadow-sm)}
@keyframes fadeIn{from{opacity:0}to{opacity:1}}
@keyframes fadeInUp{from{opacity:0;transform:translateY(30px)}to{opacity:1;transform:translateY(0)}}
@keyframes slideDown{from{opacity:0;transform:translateY(-30px)}to{opacity:1;transform:translateY(0)}}
@keyframes slideIn{from{opacity:0;transform:translateX(-30px)}to{opacity:1;transform:translateX(0)}}
@keyframes slideUp{from{transform:translateY(100%)}to{transform:translateY(0)}}
@media(max-width:480px){.form-row{grid-template-columns:1fr}}
</style>
</head>
<body>
<canvas id="confetti-canvas"></canvas>
<div id="toast" class="toast">OK</div>

<div class="container">
<div class="header">
<div class="logo">
<div class="logo-icon">💎</div>
<div class="logo-text"><h1 data-i18n="appTitle">Qarz Daftar</h1><p data-i18n="appSubtitle">Premium Edition</p></div>
</div>
<div class="header-actions">
<button class="icon-btn" onclick="toggleLang()"><i class="fas fa-language"></i></button>
<button class="icon-btn" onclick="toggleTheme()"><i class="fas fa-moon" id="theme-icon"></i></button>
</div>
</div>

<div id="page-home" class="page active">
<div class="stats-grid">
<div class="stat-card"><div class="stat-icon blue"><i class="fas fa-wallet"></i></div><div class="stat-label" data-i18n="totalGiven">Jami berilgan</div><div class="stat-value" id="total-given">0</div></div>
<div class="stat-card"><div class="stat-icon orange"><i class="fas fa-clock"></i></div><div class="stat-label" data-i18n="remaining">Qolgan qarz</div><div class="stat-value" id="total-remaining">0</div></div>
<div class="stat-card"><div class="stat-icon green"><i class="fas fa-check-circle"></i></div><div class="stat-label" data-i18n="paid">Tolangan</div><div class="stat-value" id="total-paid">0</div></div>
<div class="stat-card"><div class="stat-icon purple"><i class="fas fa-users"></i></div><div class="stat-label" data-i18n="debtors">Qarzdorlar</div><div class="stat-value" id="total-count">0</div></div>
</div>
<button class="main-btn" onclick="openAddModal()"><i class="fas fa-plus-circle"></i><span data-i18n="addDebtor">Yangi qarzdor qo'shish</span></button>
<div class="section-title"><i class="fas fa-list-ul"></i><span data-i18n="debtorList">Qarzdorlar ro'yxati</span></div>
<div id="debtors-list"></div>
</div>

<div id="page-stats" class="page">
<div class="section-title"><i class="fas fa-chart-pie"></i><span data-i18n="statistics">Statistika</span></div>
<div class="chart-container"><canvas id="pieChart"></canvas></div>
<div class="chart-container"><canvas id="barChart"></canvas></div>
</div>

<div id="page-report" class="page">
<div class="section-title"><i class="fas fa-file-alt"></i><span data-i18n="reports">Hisobotlar</span></div>
<div class="lang-selector">
<button class="lang-btn active" onclick="setReportLang('uz',this)">🇺🇿 O'zbek</button>
<button class="lang-btn" onclick="setReportLang('ru',this)">🇷🇺 Русский</button>
<button class="lang-btn" onclick="setReportLang('en',this)">🇬🇧 English</button>
</div>
<button class="main-btn" onclick="generateReport()"><i class="fas fa-file-alt"></i><span data-i18n="generateReport">Hisobot yaratish</span></button>
<div id="report-preview" class="report-box"></div>
</div>
</div>

<div class="nav-bottom">
<button class="nav-item active" onclick="switchPage('home',this)"><i class="fas fa-home"></i><span data-i18n="navHome">Bosh</span></button>
<button class="nav-item" onclick="switchPage('stats',this)"><i class="fas fa-chart-pie"></i><span data-i18n="navStats">Stat</span></button>
<button class="nav-item" onclick="switchPage('report',this)"><i class="fas fa-file-alt"></i><span data-i18n="navReport">Hisobot</span></button>
</div>

<div id="addModal" class="modal-overlay"><div class="modal-content">
<div class="modal-header"><h2 class="modal-title" data-i18n="newDebtor">Yangi qarzdor</h2><button class="modal-close" onclick="closeModal('addModal')"><i class="fas fa-times"></i></button></div>
<div class="form-group"><label class="form-label" data-i18n="name">Ism familiya *</label><input type="text" class="form-input" id="add-name" placeholder="Akramov Jasur"></div>
<div class="form-row">
<div class="form-group"><label class="form-label" data-i18n="phone">Telefon</label><input type="tel" class="form-input" id="add-phone" placeholder="+998..."></div>
<div class="form-group"><label class="form-label" data-i18n="amount">Summa *</label><input type="number" class="form-input" id="add-amount" placeholder="1000000"></div>
</div>
<div class="form-row">
<div class="form-group"><label class="form-label" data-i18n="dueDate">Muddat</label><input type="date" class="form-input" id="add-date"></div>
<div class="form-group"><label class="form-label" data-i18n="category">Kategoriya</label><select class="form-input" id="add-category"><option>Shaxsiy</option><option>Biznes</option><option>Oila</option><option>Do'st</option></select></div>
</div>
<div class="form-group"><label class="form-label" data-i18n="note">Izoh</label><textarea class="form-input" id="add-note" rows="2"></textarea></div>
<button class="btn-submit" onclick="addDebtor()"><i class="fas fa-check"></i> <span data-i18n="save">Saqlash</span></button>
</div></div>

<div id="payModal" class="modal-overlay"><div class="modal-content">
<div class="modal-header"><h2 class="modal-title" data-i18n="addPayment">To'lov qo'shish</h2><button class="modal-close" onclick="closeModal('payModal')"><i class="fas fa-times"></i></button></div>
<input type="hidden" id="pay-debtor-id">
<div class="form-group"><label class="form-label" data-i18n="amount">Summa</label><input type="number" class="form-input" id="pay-amount"></div>
<div class="form-group"><label class="form-label" data-i18n="note">Izoh</label><input type="text" class="form-input" id="pay-note"></div>
<button class="btn-submit" onclick="addPayment()"><i class="fas fa-check"></i> <span data-i18n="confirm">Tasdiqlash</span></button>
</div></div>

<script>
const tg=window.Telegram?.WebApp;
if(tg){tg.ready();tg.expand()}
const headers={'Content-Type':'application/json'};
if(tg?.initData)headers['X-Telegram-Init-Data']=tg.initData;

const translations={
uz:{appTitle:"Qarz Daftar",appSubtitle:"Premium Edition",totalGiven:"Jami berilgan",remaining:"Qolgan qarz",paid:"Tolangan",debtors:"Qarzdorlar",addDebtor:"Yangi qarzdor qo'shish",debtorList:"Qarzdorlar ro'yxati",statistics:"Statistika",reports:"Hisobotlar",generateReport:"Hisobot yaratish",navHome:"Bosh",navStats:"Stat",navReport:"Hisobot",newDebtor:"Yangi qarzdor",name:"Ism familiya *",phone:"Telefon",amount:"Summa *",dueDate:"Muddat",category:"Kategoriya",note:"Izoh",save:"Saqlash",addPayment:"To'lov qo'shish",confirm:"Tasdiqlash",pay:"To'lov",delete:"O'chirish",noDebtors:"Hali qarzdor yo'q",tapAbove:"Yuqoridagi tugmani bosing",debtorAdded:"Qarzdor qo'shildi! ✨",paymentReceived:"To'lov qabul qilindi! 💰🎉",deleted:"O'chirildi! 🗑",confirmDelete:"O'chirishni tasdiqlaysizmi?",nameAmountRequired:"Ism va summa majburiy!",invalidAmount:"Noto'g'ri summa",total:"Jami",paidAmount:"Tolangan",statusActive:"Faol",statusOverdue:"Muddati o'tgan",statusPaid:"To'langan",reportTitle:"QARZ DAFTAR HISOBOTI",reportDate:"Sana",reportTotalDebtors:"Jami qarzdorlar",reportTotalGiven:"Jami berilgan",reportTotalPaid:"Tolangan",reportTotalRemaining:"Qolgan qarz",reportDebtorList:"Qarzdorlar ro'yxati"},
ru:{appTitle:"Долговая Книга",appSubtitle:"Премиум",totalGiven:"Всего выдано",remaining:"Остаток",paid:"Оплачено",debtors:"Должники",addDebtor:"Добавить должника",debtorList:"Список должников",statistics:"Статистика",reports:"Отчёты",generateReport:"Создать отчёт",navHome:"Главная",navStats:"Стат",navReport:"Отчёт",newDebtor:"Новый должник",name:"Имя фамилия *",phone:"Телефон",amount:"Сумма *",dueDate:"Срок",category:"Категория",note:"Примечание",save:"Сохранить",addPayment:"Добавить платёж",confirm:"Подтвердить",pay:"Оплата",delete:"Удалить",noDebtors:"Пока нет должников",tapAbove:"Нажмите кнопку выше",debtorAdded:"Должник добавлен! ✨",paymentReceived:"Платёж принят! 💰",deleted:"Удалено! 🗑",confirmDelete:"Подтвердить удаление?",nameAmountRequired:"Имя и сумма обязательны!",invalidAmount:"Неверная сумма",total:"Всего",paidAmount:"Оплачено",statusActive:"Активен",statusOverdue:"Просрочен",statusPaid:"Оплачен",reportTitle:"ОТЧЁТ ДОЛГОВОЙ КНИГИ",reportDate:"Дата",reportTotalDebtors:"Всего должников",reportTotalGiven:"Всего выдано",reportTotalPaid:"Оплачено",reportTotalRemaining:"Остаток",reportDebtorList:"Список должников"},
en:{appTitle:"Debt Book",appSubtitle:"Premium",totalGiven:"Total Given",remaining:"Remaining",paid:"Paid",debtors:"Debtors",addDebtor:"Add New Debtor",debtorList:"Debtors List",statistics:"Statistics",reports:"Reports",generateReport:"Generate Report",navHome:"Home",navStats:"Stats",navReport:"Report",newDebtor:"New Debtor",name:"Full Name *",phone:"Phone",amount:"Amount *",dueDate:"Due Date",category:"Category",note:"Note",save:"Save",addPayment:"Add Payment",confirm:"Confirm",pay:"Pay",delete:"Delete",noDebtors:"No debtors yet",tapAbove:"Tap the button above",debtorAdded:"Debtor added! ✨",paymentReceived:"Payment received! 💰🎉",deleted:"Deleted! 🗑",confirmDelete:"Confirm deletion?",nameAmountRequired:"Name and amount required!",invalidAmount:"Invalid amount",total:"Total",paidAmount:"Paid",statusActive:"Active",statusOverdue:"Overdue",statusPaid:"Paid",reportTitle:"DEBT BOOK REPORT",reportDate:"Date",reportTotalDebtors:"Total Debtors",reportTotalGiven:"Total Given",reportTotalPaid:"Total Paid",reportTotalRemaining:"Remaining",reportDebtorList:"Debtors List"}
};

let currentLang=localStorage.getItem('lang')||'uz';
let reportLang='uz';
let pieChart=null,barChart=null;

function t(k){return translations[currentLang][k]||k}
function updateTranslations(){document.querySelectorAll('[data-i18n]').forEach(el=>{el.textContent=t(el.getAttribute('data-i18n'))})}
function toggleLang(){playClickSound();const L=['uz','ru','en'];currentLang=L[(L.indexOf(currentLang)+1)%3];localStorage.setItem('lang',currentLang);updateTranslations();loadData();showToast('🌐 '+currentLang.toUpperCase())}
function setReportLang(l,btn){playClickSound();reportLang=l;document.querySelectorAll('.lang-btn').forEach(b=>b.classList.remove('active'));btn.classList.add('active');generateReport()}
function switchPage(p,btn){playClickSound();document.querySelectorAll('.page').forEach(x=>x.classList.remove('active'));document.getElementById('page-'+p).classList.add('active');document.querySelectorAll('.nav-item').forEach(n=>n.classList.remove('active'));btn.classList.add('active');if(p==='stats')loadCharts();if(p==='report')generateReport()}

function playSuccessSound(){try{const c=new (window.AudioContext||window.webkitAudioContext)();const o=c.createOscillator();const g=c.createGain();o.connect(g);g.connect(c.destination);o.frequency.setValueAtTime(523.25,c.currentTime);o.frequency.setValueAtTime(659.25,c.currentTime+0.1);o.frequency.setValueAtTime(783.99,c.currentTime+0.2);g.gain.setValueAtTime(0.3,c.currentTime);g.gain.exponentialRampToValueAtTime(0.01,c.currentTime+0.5);o.start(c.currentTime);o.stop(c.currentTime+0.5)}catch(e){}}
function playClickSound(){try{const c=new (window.AudioContext||window.webkitAudioContext)();const o=c.createOscillator();const g=c.createGain();o.connect(g);g.connect(c.destination);o.frequency.setValueAtTime(800,c.currentTime);g.gain.setValueAtTime(0.2,c.currentTime);g.gain.exponentialRampToValueAtTime(0.01,c.currentTime+0.1);o.start(c.currentTime);o.stop(c.currentTime+0.1)}catch(e){}}

function launchConfetti(){const cv=document.getElementById('confetti-canvas');const ctx=cv.getContext('2d');cv.width=innerWidth;cv.height=innerHeight;const ps=[];const cols=['#667eea','#f093fb','#10b981','#f59e0b','#ef4444'];for(let i=0;i<100;i++){ps.push({x:cv.width/2,y:cv.height/2,vx:(Math.random()-.5)*20,vy:(Math.random()-.5)*20-10,color:cols[Math.floor(Math.random()*cols.length)],size:Math.random()*8+4,life:1})}function an(){ctx.clearRect(0,0,cv.width,cv.height);ps.forEach((p,i)=>{p.x+=p.vx;p.y+=p.vy;p.vy+=0.5;p.life-=0.02;ctx.fillStyle=p.color;ctx.globalAlpha=p.life;ctx.fillRect(p.x,p.y,p.size,p.size);if(p.life<=0)ps.splice(i,1)});if(ps.length>0)requestAnimationFrame(an)}an()}

function formatMoney(a){return new Intl.NumberFormat('uz-UZ').format(a||0)+" so'm"}
function showToast(m,e){const t2=document.getElementById('toast');t2.textContent=m;t2.className='toast show'+(e?' error':'');setTimeout(()=>t2.classList.remove('show'),3000)}
function toggleTheme(){playClickSound();const b=document.body;const d=b.getAttribute('data-theme')==='dark';b.setAttribute('data-theme',d?'light':'dark');localStorage.setItem('theme',d?'light':'dark');document.getElementById('theme-icon').className=d?'fas fa-moon':'fas fa-sun'}

async function loadData(){
try{
const s=await (await fetch('/api/stats',{headers})).json();
document.getElementById('total-given').textContent=formatMoney(s.total_given);
document.getElementById('total-remaining').textContent=formatMoney(s.total_remaining);
document.getElementById('total-paid').textContent=formatMoney(s.total_paid);
document.getElementById('total-count').textContent=s.total_debtors;
const ds=await (await fetch('/api/debtors',{headers})).json();
const list=document.getElementById('debtors-list');
if(!ds.length){list.innerHTML='<div class="empty-state"><div class="empty-icon"><i class="fas fa-inbox"></i></div><p style="font-size:17px;font-weight:600">'+t('noDebtors')+'</p><p style="font-size:13px;margin-top:6px;opacity:.7">'+t('tapAbove')+'</p></div>';return}
list.innerHTML=ds.map((d,i)=>{
const sc=d.status==='OVERDUE'?'overdue':d.status==='PAID'?'paid':'';
const bc=d.status==='OVERDUE'?'badge-overdue':d.status==='PAID'?'badge-paid':'badge-active';
const st=d.status==='OVERDUE'?t('statusOverdue'):d.status==='PAID'?t('statusPaid'):t('statusActive');
return '<div class="debtor-card '+sc+'" style="animation-delay:'+(i*0.05)+'s"><div class="debtor-header"><div class="debtor-name">'+d.name+'</div><div class="debtor-amount">'+formatMoney(d.remaining_amount)+'</div></div><div class="debtor-info"><span><i class="fas fa-phone"></i>'+(d.phone||'-')+'</span><span><i class="fas fa-tag"></i>'+d.category+'</span>'+(d.due_date?'<span><i class="fas fa-calendar"></i>'+d.due_date+'</span>':'')+'</div><span class="debtor-badge '+bc+'">'+st+'</span><div class="debtor-details"><strong>'+t('total')+':</strong> '+formatMoney(d.total_amount)+' | <strong>'+t('paidAmount')+':</strong> '+formatMoney(d.paid_amount)+'</div><div class="debtor-actions">'+(d.status!=='PAID'?'<button class="btn-action btn-pay" onclick="openPayModal('+d.id+','+d.remaining_amount+')"><i class="fas fa-money-bill-wave"></i>'+t('pay')+'</button>':'')+'<button class="btn-action btn-delete" onclick="deleteDebtor('+d.id+')"><i class="fas fa-trash"></i>'+t('delete')+'</button></div></div>'}).join('');
}catch(e){console.error(e);showToast('Xato: '+e.message,true)}
}

async function loadCharts(){
try{
const s=await (await fetch('/api/stats',{headers})).json();
const ds=await (await fetch('/api/debtors',{headers})).json();
if(pieChart)pieChart.destroy();
pieChart=new Chart(document.getElementById('pieChart'),{type:'doughnut',data:{labels:[t('paid'),t('remaining')],datasets:[{data:[s.total_paid,s.total_remaining],backgroundColor:['#10b981','#ef4444'],borderWidth:0}]},options:{responsive:true,plugins:{legend:{position:'bottom'}}}});
if(barChart)barChart.destroy();
const top=ds.slice(0,5);
barChart=new Chart(document.getElementById('barChart'),{type:'bar',data:{labels:top.map(d=>d.name),datasets:[{label:t('remaining'),data:top.map(d=>d.remaining_amount),backgroundColor:'rgba(102,126,234,0.8)',borderRadius:8}]},options:{responsive:true,plugins:{legend:{display:false}},scales:{y:{beginAtZero:true}}}});
}catch(e){console.error(e)}
}

async function generateReport(){
try{
const s=await (await fetch('/api/stats',{headers})).json();
const ds=await (await fetch('/api/debtors',{headers})).json();
const tr=translations[reportLang];
const today=new Date().toLocaleDateString();
let h='<h2 style="text-align:center;margin-bottom:14px;color:var(--primary)">'+tr.reportTitle+'</h2><p style="text-align:center;margin-bottom:20px;color:var(--text-light)">'+tr.reportDate+': '+today+'</p><div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:20px"><div style="padding:14px;background:rgba(102,126,234,.1);border-radius:12px"><div style="font-size:11px;color:var(--text-light)">'+tr.reportTotalDebtors+'</div><div style="font-size:18px;font-weight:800">'+s.total_debtors+'</div></div><div style="padding:14px;background:rgba(16,185,129,.1);border-radius:12px"><div style="font-size:11px;color:var(--text-light)">'+tr.reportTotalGiven+'</div><div style="font-size:18px;font-weight:800">'+formatMoney(s.total_given)+'</div></div><div style="padding:14px;background:rgba(245,158,11,.1);border-radius:12px"><div style="font-size:11px;color:var(--text-light)">'+tr.reportTotalPaid+'</div><div style="font-size:18px;font-weight:800">'+formatMoney(s.total_paid)+'</div></div><div style="padding:14px;background:rgba(239,68,68,.1);border-radius:12px"><div style="font-size:11px;color:var(--text-light)">'+tr.reportTotalRemaining+'</div><div style="font-size:18px;font-weight:800">'+formatMoney(s.total_remaining)+'</div></div></div><h3 style="margin-bottom:10px">'+tr.reportDebtorList+'</h3>';
if(!ds.length){h+='<p style="text-align:center;color:var(--text-light);padding:30px">'+t('noDebtors')+'</p>'}
else{h+=ds.map(d=>'<div style="padding:12px;background:rgba(0,0,0,.03);border-radius:10px;margin-bottom:8px"><div style="display:flex;justify-content:space-between;margin-bottom:4px"><strong>'+d.name+'</strong><strong style="color:var(--primary)">'+formatMoney(d.remaining_amount)+'</strong></div><div style="font-size:12px;color:var(--text-light)">'+(d.phone||'-')+' | '+d.category+' | '+d.status+'</div></div>').join('')}
document.getElementById('report-preview').innerHTML=h;
}catch(e){console.error(e)}
}

function openAddModal(){playClickSound();document.getElementById('addModal').classList.add('active')}
function closeModal(id){playClickSound();document.getElementById(id).classList.remove('active')}

async function addDebtor(){
const data={name:document.getElementById('add-name').value.trim(),phone:document.getElementById('add-phone').value.trim(),amount:parseFloat(document.getElementById('add-amount').value),due_date:document.getElementById('add-date').value||null,category:document.getElementById('add-category').value,note:document.getElementById('add-note').value.trim()};
if(!data.name||!data.amount){showToast(t('nameAmountRequired'),true);return}
try{
const r=await fetch('/api/debtors',{method:'POST',headers,body:JSON.stringify(data)});
if(!r.ok)throw new Error('Add error');
playSuccessSound();showToast(t('debtorAdded'));closeModal('addModal');
['add-name','add-phone','add-amount','add-date','add-note'].forEach(id=>document.getElementById(id).value='');
loadData();
}catch(e){showToast('Xato: '+e.message,true)}
}

function openPayModal(id,rem){playClickSound();document.getElementById('pay-debtor-id').value=id;document.getElementById('pay-amount').value=rem;document.getElementById('pay-note').value='';document.getElementById('payModal').classList.add('active')}

async function addPayment(){
const id=document.getElementById('pay-debtor-id').value;
const amount=parseFloat(document.getElementById('pay-amount').value);
const note=document.getElementById('pay-note').value.trim();
if(!amount||amount<=0){showToast(t('invalidAmount'),true);return}
try{
const r=await fetch('/api/debtors/'+id+'/pay',{method:'PUT',headers,body:JSON.stringify({amount,note})});
if(!r.ok)throw new Error('Pay error');
playSuccessSound();launchConfetti();showToast(t('paymentReceived'));closeModal('payModal');loadData();
}catch(e){showToast('Xato: '+e.message,true)}
}

async function deleteDebtor(id){
if(!confirm(t('confirmDelete')))return;
try{
const r=await fetch('/api/debtors/'+id,{method:'DELETE',headers});
if(!r.ok)throw new Error('Del error');
playClickSound();showToast(t('deleted'));loadData();
}catch(e){showToast('Xato: '+e.message,true)}
}

window.onload=()=>{
const th=localStorage.getItem('theme')||'light';
document.body.setAttribute('data-theme',th);
document.getElementById('theme-icon').className=th==='dark'?'fas fa-sun':'fas fa-moon';
updateTranslations();loadData();
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
        cur = await db.execute("""
            SELECT COUNT(*) as total_debtors,
                   COALESCE(SUM(total_amount),0) as total_given,
                   COALESCE(SUM(paid_amount),0) as total_paid,
                   COALESCE(SUM(remaining_amount),0) as total_remaining
            FROM debtors WHERE user_id=?
        """, (user["telegram_id"],))
        return dict(await cur.fetchone())


@app.get("/api/debtors")
async def get_debtors(user: dict = Depends(get_current_user)):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM debtors WHERE user_id=? ORDER BY created_at DESC", (user["telegram_id"],))
        return [dict(r) for r in await cur.fetchall()]


@app.post("/api/debtors")
async def create_debtor(d: DebtorCreate, user: dict = Depends(get_current_user)):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            INSERT INTO debtors (user_id,name,phone,category,note,total_amount,paid_amount,remaining_amount,due_date)
            VALUES (?,?,?,?,?,?,0,?,?)
        """, (user["telegram_id"], d.name, d.phone, d.category, d.note, d.amount, d.amount, d.due_date))
        await db.commit()
        return {"id": cur.lastrowid}


@app.put("/api/debtors/{debtor_id}/pay")
async def add_payment(debtor_id: int, p: PaymentCreate, user: dict = Depends(get_current_user)):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT total_amount,paid_amount,remaining_amount FROM debtors WHERE id=? AND user_id=?", (debtor_id, user["telegram_id"]))
        row = await cur.fetchone()
        if not row:
            raise HTTPException(404, "Topilmadi")
        if p.amount <= 0 or p.amount > row[2]:
            raise HTTPException(400, "Summa xato")
        new_paid = row[1] + p.amount
        new_rem = row[2] - p.amount
        new_status = "PAID" if new_rem == 0 else "ACTIVE"
        await db.execute("INSERT INTO payments (debtor_id,amount,note) VALUES (?,?,?)", (debtor_id, p.amount, p.note))
        await db.execute("UPDATE debtors SET paid_amount=?,remaining_amount=?,status=? WHERE id=?", (new_paid, new_rem, new_status, debtor_id))
        await db.commit()
        return {"remaining": new_rem, "status": new_status}


@app.delete("/api/debtors/{debtor_id}")
async def delete_debtor(debtor_id: int, user: dict = Depends(get_current_user)):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM payments WHERE debtor_id=?", (debtor_id,))
        await db.execute("DELETE FROM debtors WHERE id=? AND user_id=?", (debtor_id, user["telegram_id"]))
        await db.commit()
        return {"ok": True}


@app.get("/api/export/csv")
async def export_csv(user: dict = Depends(get_current_user)):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT name,phone,total_amount,paid_amount,remaining_amount,status,due_date FROM debtors WHERE user_id=?", (user["telegram_id"],))
        rows = await cur.fetchall()
    lines = ["Ism;Telefon;Jami;Tolangan;Qolgan;Holat;Muddat"]
    for r in rows:
        lines.append(";".join("" if x is None else str(x) for x in r))
    return Response("\n".join(lines), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=qarzlar.csv"})


@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    update = types.Update(**data)
    await dp.feed_update(bot, update)
    return {"ok": True}


# ============ BOT ============
@dp.message(CommandStart())
async def cmd_start(m: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📒 Ilovani ochish", web_app=WebAppInfo(url=WEBAPP_URL))]])
    await m.answer(
        "<b>💎 Qarz Daftar Pro</b>\n\n"
        "✨ Premium dizayn\n"
        "🎵 Tovush + 🎊 Confetti\n"
        "📊 Grafiklar va statistika\n"
        "📄 Hisobotlar (UZ/RU/EN)\n"
        "🌍 3 til qo'llab-quvvatlanadi\n\n"
        "Tugmani bosing 👇",
        reply_markup=kb)


@dp.message(Command("report"))
async def cmd_report(m: types.Message):
    uid = m.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("""
            SELECT COUNT(*) as n, COALESCE(SUM(total_amount),0) g,
                   COALESCE(SUM(paid_amount),0) p, COALESCE(SUM(remaining_amount),0) r
            FROM debtors WHERE user_id=?""", (uid,))
        s = dict(await cur.fetchone())
        cur = await db.execute("SELECT name,phone,remaining_amount,status FROM debtors WHERE user_id=? ORDER BY created_at DESC", (uid,))
        ds = await cur.fetchall()
    txt = (f"📊 <b>QARZ DAFTAR HISOBOTI</b>\n📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n"
           f"👥 Qarzdorlar: <b>{s['n']}</b>\n💰 Berilgan: <b>{s['g']:,.0f} so'm</b>\n"
           f"✅ Tolangan: <b>{s['p']:,.0f} so'm</b>\n⏳ Qolgan: <b>{s['r']:,.0f} so'm</b>\n\n<b>📋 Ro'yxat:</b>\n")
    if not ds:
        txt += "\n<i>Hali qarzdor yo'q</i>"
    else:
        for d in ds:
            e = "✅" if d["status"] == "PAID" else "⏳" if d["status"] == "ACTIVE" else "⚠️"
            txt += f"\n{e} <b>{d['name']}</b> — {d['remaining_amount']:,.0f} so'm"
    await m.answer(txt)


# ============ START ============
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