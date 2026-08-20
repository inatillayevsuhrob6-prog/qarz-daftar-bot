import os
import json
import hmac
import hashlib
import html
import logging
import asyncio
import threading
from datetime import datetime
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

# ================= CONFIG =================
# IMPORTANT: put BOT_TOKEN into Render Environment Variables.
BOT_TOKEN = os.getenv("BOT_TOKEN", "8651436055:AAH3FgpFyhcnBo4RXXYQpLMv1Wk4qNiCXX0").strip()
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://qarz-daftar-bot.onrender.com").strip()
DB_PATH = os.getenv("DB_PATH", "qarz_daftar.db")
DEV_MODE = os.getenv("DEV_MODE", "false").lower() == "true"

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is not set")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("qarz-daftar")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()


# ================= MODELS =================
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


class SendMessage(BaseModel):
    debtor_id: int
    message: str


class TelegramTargetUpdate(BaseModel):
    telegram_target: int


# ================= DATABASE =================
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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                telegram_target INTEGER
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

        # For an old database created before telegram_target existed.
        columns = await db.execute_fetchall("PRAGMA table_info(debtors)")
        names = {row[1] for row in columns}
        if "telegram_target" not in names:
            await db.execute(
                "ALTER TABLE debtors ADD COLUMN telegram_target INTEGER"
            )

        await db.commit()

    logger.info("Database ready: %s", DB_PATH)


# ================= TELEGRAM AUTH =================
def verify_telegram_auth(init_data: str) -> dict:
    if not init_data:
        raise HTTPException(status_code=401, detail="Telegram auth yo'q")

    try:
        data = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = data.pop("hash", None)

        if not received_hash:
            raise HTTPException(status_code=401, detail="Hash yo'q")

        check_string = "\n".join(
            f"{key}={data[key]}" for key in sorted(data.keys())
        )

        secret_key = hmac.new(
            b"WebAppData",
            BOT_TOKEN.encode(),
            hashlib.sha256
        ).digest()

        calculated_hash = hmac.new(
            secret_key,
            check_string.encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(calculated_hash, received_hash):
            raise HTTPException(status_code=401, detail="Telegram hash xato")

        user_data = json.loads(data.get("user", "{}"))

        telegram_id = user_data.get("id")
        if not telegram_id:
            raise HTTPException(status_code=401, detail="Telegram user ID topilmadi")

        return {
            "telegram_id": telegram_id,
            "username": user_data.get("username", ""),
            "first_name": user_data.get("first_name", ""),
            "last_name": user_data.get("last_name", "")
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Telegram auth error: %s", exc)
        raise HTTPException(status_code=401, detail="Auth xatosi")


async def get_current_user(request: Request) -> dict:
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    if not init_data:
        init_data = request.query_params.get("initData", "")

    if init_data:
        return verify_telegram_auth(init_data)

    # Only allow the old test user in explicit local development mode.
    if DEV_MODE:
        return {
            "telegram_id": 123456,
            "username": "test",
            "first_name": "Test",
            "last_name": "User"
        }

    raise HTTPException(
        status_code=401,
        detail="Ilovani Telegram ichidan oching"
    )


# ================= APP =================
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
    logger.info("Server started")


# ================= HTML =================
HTML_TEMPLATE = r"""<!DOCTYPE html>
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
:root{--bg-gradient:linear-gradient(135deg,#667eea 0%,#764ba2 50%,#f093fb 100%);--card-bg:rgba(255,255,255,.98);--primary:#667eea;--accent:#f093fb;--success:#10b981;--danger:#ef4444;--warning:#f59e0b;--text:#1f2937;--text-light:#6b7280;--shadow-sm:0 8px 32px rgba(0,0,0,.12)}
[data-theme=dark]{--bg-gradient:linear-gradient(135deg,#0f172a 0%,#1e293b 50%,#312e81 100%);--card-bg:rgba(30,41,59,.98);--text:#f8fafc;--text-light:#94a3b8;--shadow-sm:0 8px 32px rgba(0,0,0,.3)}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:var(--bg-gradient);min-height:100vh;color:var(--text);padding:20px;padding-bottom:110px;overflow-x:hidden}
.container{max-width:600px;margin:0 auto;position:relative;z-index:1}
.header{display:flex;justify-content:space-between;align-items:center;margin-bottom:24px}
.logo{display:flex;align-items:center;gap:14px}.logo-icon{width:52px;height:52px;background:linear-gradient(135deg,var(--primary),var(--accent));border-radius:18px;display:flex;align-items:center;justify-content:center;font-size:26px}
.logo-text h1{font-size:24px;font-weight:900}.logo-text p{font-size:11px;color:var(--text-light)}
.header-actions{display:flex;gap:10px}.icon-btn{width:44px;height:44px;border-radius:50%;background:var(--card-bg);border:0;cursor:pointer;font-size:18px;box-shadow:var(--shadow-sm);color:var(--text)}
.stats-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:14px;margin-bottom:24px}
.stat-card,.debtor-card,.chart-container,.report-box{background:var(--card-bg);backdrop-filter:blur(20px);box-shadow:var(--shadow-sm)}
.stat-card{padding:20px;border-radius:24px}.stat-icon{width:48px;height:48px;border-radius:16px;display:flex;align-items:center;justify-content:center;font-size:22px;margin-bottom:12px;color:#fff}.blue{background:linear-gradient(135deg,#667eea,#764ba2)}.green{background:linear-gradient(135deg,#10b981,#059669)}.orange{background:linear-gradient(135deg,#f59e0b,#d97706)}.purple{background:linear-gradient(135deg,#8b5cf6,#7c3aed)}
.stat-label{font-size:12px;color:var(--text-light);margin-bottom:4px;font-weight:600}.stat-value{font-size:21px;font-weight:900}
.main-btn,.btn-submit{width:100%;padding:17px;background:linear-gradient(135deg,var(--primary),var(--accent));border:0;border-radius:20px;color:#fff;font-size:16px;font-weight:800;cursor:pointer;box-shadow:0 12px 35px rgba(102,126,234,.35)}
.main-btn{margin-bottom:24px;display:flex;align-items:center;justify-content:center;gap:10px}
.section-title{font-size:19px;font-weight:800;margin-bottom:16px;display:flex;align-items:center;gap:10px}.section-title i{color:var(--primary)}
.debtor-card{padding:20px;border-radius:24px;margin-bottom:16px;border-left:5px solid var(--primary)}.debtor-card.overdue{border-left-color:var(--danger)}.debtor-card.paid{border-left-color:var(--success)}
.debtor-header{display:flex;justify-content:space-between;gap:10px;margin-bottom:12px}.debtor-name{font-size:18px;font-weight:800}.debtor-amount{font-size:20px;font-weight:900;color:var(--primary)}
.debtor-info{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:12px;font-size:13px;color:var(--text-light)}.debtor-info span{display:flex;align-items:center;gap:5px}
.debtor-badge{display:inline-block;padding:6px 12px;border-radius:20px;font-size:11px;font-weight:700;margin-bottom:12px}.badge-active{background:rgba(102,126,234,.15);color:var(--primary)}.badge-overdue{background:rgba(239,68,68,.15);color:var(--danger)}.badge-paid{background:rgba(16,185,129,.15);color:var(--success)}
.debtor-details{font-size:12px;color:var(--text-light);margin-bottom:14px;padding:10px;background:rgba(0,0,0,.03);border-radius:12px}.debtor-actions{display:flex;gap:10px}.btn-action{flex:1;padding:13px;border:0;border-radius:14px;font-size:14px;font-weight:700;cursor:pointer}.btn-pay{background:linear-gradient(135deg,var(--success),#059669);color:#fff}.btn-delete{background:rgba(239,68,68,.15);color:var(--danger)}
.empty-state{text-align:center;padding:60px 20px;color:var(--text-light)}.empty-icon{font-size:70px;margin-bottom:16px;opacity:.2}
.page{display:none}.page.active{display:block}.chart-container{border-radius:24px;padding:20px;margin-bottom:20px}.report-box{border-radius:24px;padding:22px}
.nav-bottom{position:fixed;bottom:0;left:0;right:0;background:var(--card-bg);backdrop-filter:blur(20px);display:flex;justify-content:space-around;padding:10px 0 calc(10px + env(safe-area-inset-bottom));box-shadow:0 -8px 32px rgba(0,0,0,.1);z-index:100}.nav-item{display:flex;flex-direction:column;align-items:center;gap:4px;padding:6px 12px;border:0;background:none;cursor:pointer;color:var(--text-light);font-size:11px;font-weight:600}.nav-item i{font-size:20px}.nav-item.active{color:var(--primary)}
.lang-selector{display:flex;gap:8px;margin-bottom:18px}.lang-btn{flex:1;padding:12px;border:2px solid rgba(0,0,0,.1);border-radius:14px;background:var(--card-bg);cursor:pointer;font-weight:700;color:var(--text)}.lang-btn.active{background:linear-gradient(135deg,var(--primary),var(--accent));color:#fff;border-color:transparent}
.modal-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.7);backdrop-filter:blur(10px);z-index:1000;align-items:flex-end;justify-content:center}.modal-overlay.active{display:flex}.modal-content{background:var(--card-bg);width:100%;max-width:600px;border-radius:32px 32px 0 0;padding:30px 24px;max-height:90vh;overflow-y:auto}.modal-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:24px}.modal-title{font-size:24px;font-weight:900;color:var(--primary)}.modal-close{width:38px;height:38px;border-radius:50%;background:rgba(0,0,0,.1);border:0;color:var(--text);cursor:pointer}
.form-group{margin-bottom:20px}.form-label{display:block;font-size:13px;font-weight:700;margin-bottom:8px}.form-input{width:100%;padding:15px;border:2px solid rgba(0,0,0,.1);border-radius:16px;font-size:16px;background:rgba(0,0,0,.03);color:var(--text)}.form-input:focus{outline:none;border-color:var(--primary)}.form-row{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.toast{position:fixed;top:25px;left:50%;transform:translateX(-50%) translateY(-120px);background:var(--success);color:#fff;padding:14px 25px;border-radius:30px;font-weight:700;z-index:2000;transition:.3s;opacity:0}.toast.show{opacity:1;transform:translateX(-50%) translateY(0)}.toast.error{background:var(--danger)}
#confetti-canvas{position:fixed;inset:0;width:100%;height:100%;pointer-events:none;z-index:1500}
@media(max-width:480px){.form-row{grid-template-columns:1fr}}
</style>
</head>
<body>
<canvas id="confetti-canvas"></canvas>
<div id="toast" class="toast">OK</div>

<div class="container">
<div class="header">
<div class="logo"><div class="logo-icon">💎</div><div class="logo-text"><h1 data-i18n="appTitle">Qarz Daftar</h1><p data-i18n="appSubtitle">Premium Edition</p></div></div>
<div class="header-actions"><button class="icon-btn" onclick="toggleLang()"><i class="fas fa-language"></i></button><button class="icon-btn" onclick="toggleTheme()"><i class="fas fa-moon" id="theme-icon"></i></button></div>
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

<div id="page-msg" class="page">
<div class="section-title"><i class="fas fa-paper-plane"></i><span>Telegram xabar yuborish</span></div>
<div style="background:var(--card-bg);padding:16px;border-radius:16px;margin-bottom:16px;font-size:13px;color:var(--text-light)">📲 Qarzdor botga kirib <b>/id</b> yozadi. Keyin olingan ID ni saqlang.</div>
<div class="form-group"><label class="form-label">👤 Qarzdor</label><select class="form-input" id="msg-debtor" onchange="selDebtor()"><option value="">-- Tanlang --</option></select></div>
<div class="form-group"><label class="form-label">🆔 Telegram ID</label><input type="text" class="form-input" id="msg-telegram-id" placeholder="123456789"></div>
<button class="btn-submit" style="margin-bottom:16px" onclick="saveTelegramId()">💾 Telegram ID ni saqlash</button>
<div class="form-group"><label class="form-label">💬 Xabar matni</label><textarea class="form-input" id="msg-text" rows="6" placeholder="Xabar yozing..."></textarea></div>
<div style="font-size:13px;font-weight:700;margin-bottom:10px">💡 Tayyor shablonlar</div>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:16px">
<button class="btn-submit" style="background:#6b7280;padding:12px;font-size:13px" onclick="tpl('eslatma')">💳 Qarz eslatmasi</button>
<button class="btn-submit" style="background:#6b7280;padding:12px;font-size:13px" onclick="tpl('muloyim')">🤝 Muloyim</button>
<button class="btn-submit" style="background:#6b7280;padding:12px;font-size:13px" onclick="tpl('muddat')">📅 Muddat</button>
<button class="btn-submit" style="background:#6b7280;padding:12px;font-size:13px" onclick="tpl('rahmat')">🙏 Rahmat</button>
</div>
<button class="btn-submit" style="background:linear-gradient(135deg,#229ED9,#0088cc)" onclick="sendTelegram()">📤 Telegram orqali yuborish</button>
</div>

<div id="page-report" class="page">
<div class="section-title"><i class="fas fa-file-alt"></i><span data-i18n="reports">Hisobotlar</span></div>
<div class="lang-selector"><button class="lang-btn active" onclick="setReportLang('uz',this)">🇺🇿 O'zbek</button><button class="lang-btn" onclick="setReportLang('ru',this)">🇷🇺 Русский</button><button class="lang-btn" onclick="setReportLang('en',this)">🇬🇧 English</button></div>
<button class="main-btn" onclick="generateReport()"><i class="fas fa-file-alt"></i><span data-i18n="generateReport">Hisobot yaratish</span></button>
<div id="report-preview" class="report-box"></div>
</div>
</div>

<div class="nav-bottom">
<button class="nav-item active" onclick="switchPage('home',this)"><i class="fas fa-home"></i><span data-i18n="navHome">Bosh</span></button>
<button class="nav-item" onclick="switchPage('stats',this)"><i class="fas fa-chart-pie"></i><span data-i18n="navStats">Stat</span></button>
<button class="nav-item" onclick="switchPage('msg',this)"><i class="fas fa-paper-plane"></i><span>Xabar</span></button>
<button class="nav-item" onclick="switchPage('report',this)"><i class="fas fa-file-alt"></i><span data-i18n="navReport">Hisobot</span></button>
</div>

<div id="addModal" class="modal-overlay"><div class="modal-content">
<div class="modal-header"><h2 class="modal-title" data-i18n="newDebtor">Yangi qarzdor</h2><button class="modal-close" onclick="closeModal('addModal')"><i class="fas fa-times"></i></button></div>
<div class="form-group"><label class="form-label" data-i18n="name">Ism familiya *</label><input type="text" class="form-input" id="add-name"></div>
<div class="form-row"><div class="form-group"><label class="form-label" data-i18n="phone">Telefon</label><input type="tel" class="form-input" id="add-phone"></div><div class="form-group"><label class="form-label" data-i18n="amount">Summa *</label><input type="number" class="form-input" id="add-amount" min="0" step="0.01"></div></div>
<div class="form-row"><div class="form-group"><label class="form-label" data-i18n="dueDate">Muddat</label><input type="date" class="form-input" id="add-date"></div><div class="form-group"><label class="form-label" data-i18n="category">Kategoriya</label><select class="form-input" id="add-category"><option>Shaxsiy</option><option>Biznes</option><option>Oila</option><option>Do'st</option></select></div></div>
<div class="form-group"><label class="form-label" data-i18n="note">Izoh</label><textarea class="form-input" id="add-note" rows="2"></textarea></div>
<button class="btn-submit" onclick="addDebtor()"><i class="fas fa-check"></i> <span data-i18n="save">Saqlash</span></button>
</div></div>

<div id="payModal" class="modal-overlay"><div class="modal-content">
<div class="modal-header"><h2 class="modal-title" data-i18n="addPayment">To'lov qo'shish</h2><button class="modal-close" onclick="closeModal('payModal')"><i class="fas fa-times"></i></button></div>
<input type="hidden" id="pay-debtor-id"><div class="form-group"><label class="form-label" data-i18n="amount">Summa</label><input type="number" class="form-input" id="pay-amount" min="0" step="0.01"></div><div class="form-group"><label class="form-label" data-i18n="note">Izoh</label><input type="text" class="form-input" id="pay-note"></div>
<button class="btn-submit" onclick="addPayment()"><i class="fas fa-check"></i> <span data-i18n="confirm">Tasdiqlash</span></button>
</div></div>

<script>
const tg=window.Telegram?.WebApp;
if(tg){tg.ready();tg.expand();}
const headers={'Content-Type':'application/json'};
if(tg?.initData)headers['X-Telegram-Init-Data']=tg.initData;

const translations={
uz:{appTitle:"Qarz Daftar",appSubtitle:"Premium Edition",totalGiven:"Jami berilgan",remaining:"Qolgan qarz",paid:"Tolangan",debtors:"Qarzdorlar",addDebtor:"Yangi qarzdor qo'shish",debtorList:"Qarzdorlar ro'yxati",statistics:"Statistika",reports:"Hisobotlar",generateReport:"Hisobot yaratish",navHome:"Bosh",navStats:"Stat",navReport:"Hisobot",newDebtor:"Yangi qarzdor",name:"Ism familiya *",phone:"Telefon",amount:"Summa *",dueDate:"Muddat",category:"Kategoriya",note:"Izoh",save:"Saqlash",addPayment:"To'lov qo'shish",confirm:"Tasdiqlash",pay:"To'lov",delete:"O'chirish",noDebtors:"Hali qarzdor yo'q",tapAbove:"Yuqoridagi tugmani bosing",debtorAdded:"Qarzdor qo'shildi! ✨",paymentReceived:"To'lov qabul qilindi! 💰🎉",deleted:"O'chirildi! 🗑",confirmDelete:"O'chirishni tasdiqlaysizmi?",nameAmountRequired:"Ism va summa majburiy!",invalidAmount:"Noto'g'ri summa",total:"Jami",paidAmount:"Tolangan",statusActive:"Faol",statusOverdue:"Muddati o'tgan",statusPaid:"To'langan",reportTitle:"QARZ DAFTAR HISOBOTI",reportDate:"Sana",reportTotalDebtors:"Jami qarzdorlar",reportTotalGiven:"Jami berilgan",reportTotalPaid:"Tolangan",reportTotalRemaining:"Qolgan qarz",reportDebtorList:"Qarzdorlar ro'yxati"},
ru:{appTitle:"Долговая Книга",appSubtitle:"Премиум",totalGiven:"Всего выдано",remaining:"Остаток",paid:"Оплачено",debtors:"Должники",addDebtor:"Добавить должника",debtorList:"Список должников",statistics:"Статистика",reports:"Отчёты",generateReport:"Создать отчёт",navHome:"Главная",navStats:"Стат",navReport:"Отчёт",newDebtor:"Новый должник",name:"Имя фамилия *",phone:"Телефон",amount:"Сумма *",dueDate:"Срок",category:"Категория",note:"Примечание",save:"Сохранить",addPayment:"Добавить платёж",confirm:"Подтвердить",pay:"Оплата",delete:"Удалить",noDebtors:"Пока нет должников",tapAbove:"Нажмите кнопку выше",debtorAdded:"Должник добавлен! ✨",paymentReceived:"Платёж принят! 💰",deleted:"Удалено! 🗑",confirmDelete:"Подтвердить удаление?",nameAmountRequired:"Имя и сумма обязательны!",invalidAmount:"Неверная сумма",total:"Всего",paidAmount:"Оплачено",statusActive:"Активен",statusOverdue:"Просрочен",statusPaid:"Оплачен",reportTitle:"ОТЧЁТ ДОЛГОВОЙ КНИГИ",reportDate:"Дата",reportTotalDebtors:"Всего должников",reportTotalGiven:"Всего выдано",reportTotalPaid:"Оплачено",reportTotalRemaining:"Остаток",reportDebtorList:"Список должников"},
en:{appTitle:"Debt Book",appSubtitle:"Premium",totalGiven:"Total Given",remaining:"Remaining",paid:"Paid",debtors:"Debtors",addDebtor:"Add New Debtor",debtorList:"Debtors List",statistics:"Statistics",reports:"Reports",generateReport:"Generate Report",navHome:"Home",navStats:"Stats",navReport:"Report",newDebtor:"New Debtor",name:"Full Name *",phone:"Phone",amount:"Amount *",dueDate:"Due Date",category:"Category",note:"Note",save:"Save",addPayment:"Add Payment",confirm:"Confirm",pay:"Pay",delete:"Delete",noDebtors:"No debtors yet",tapAbove:"Tap the button above",debtorAdded:"Debtor added! ✨",paymentReceived:"Payment received! 💰🎉",deleted:"Deleted! 🗑",confirmDelete:"Confirm deletion?",nameAmountRequired:"Name and amount required!",invalidAmount:"Invalid amount",total:"Total",paidAmount:"Paid",statusActive:"Active",statusOverdue:"Overdue",statusPaid:"Paid",reportTitle:"DEBT BOOK REPORT",reportDate:"Date",reportTotalDebtors:"Total Debtors",reportTotalGiven:"Total Given",reportTotalPaid:"Paid",reportTotalRemaining:"Remaining",reportDebtorList:"Debtors List"}
};

let currentLang=localStorage.getItem('lang')||'uz',reportLang='uz',pieChart=null,barChart=null,msgDebtors=[];

function t(k){return translations[currentLang][k]||k}
function updateTranslations(){document.querySelectorAll('[data-i18n]').forEach(e=>e.textContent=t(e.dataset.i18n))}
function showToast(message,error=false){const el=document.getElementById('toast');el.textContent=message;el.className='toast show'+(error?' error':'');setTimeout(()=>el.classList.remove('show'),3000)}
function formatMoney(n){return new Intl.NumberFormat('uz-UZ').format(Number(n)||0)+" so'm"}
function playClickSound(){try{const c=new(window.AudioContext||window.webkitAudioContext)(),o=c.createOscillator(),g=c.createGain();o.connect(g);g.connect(c.destination);o.frequency.value=800;g.gain.value=.12;o.start();o.stop(c.currentTime+.08)}catch(e){}}
function playSuccessSound(){try{const c=new(window.AudioContext||window.webkitAudioContext)(),o=c.createOscillator(),g=c.createGain();o.connect(g);g.connect(c.destination);o.frequency.setValueAtTime(523,c.currentTime);o.frequency.setValueAtTime(659,c.currentTime+.1);o.frequency.setValueAtTime(784,c.currentTime+.2);g.gain.value=.2;o.start();o.stop(c.currentTime+.45)}catch(e){}}
function launchConfetti(){const cv=document.getElementById('confetti-canvas'),ctx=cv.getContext('2d');cv.width=innerWidth;cv.height=innerHeight;const p=Array.from({length:80},()=>({x:cv.width/2,y:cv.height/2,vx:(Math.random()-.5)*18,vy:-Math.random()*14-3,s:Math.random()*7+3,a:1}));function a(){ctx.clearRect(0,0,cv.width,cv.height);p.forEach(x=>{x.x+=x.vx;x.y+=x.vy;x.vy+=.45;x.a-=.018;ctx.globalAlpha=Math.max(x.a,0);ctx.fillRect(x.x,x.y,x.s,x.s)});if(p.some(x=>x.a>0))requestAnimationFrame(a)}a()}
function toggleLang(){const a=['uz','ru','en'];currentLang=a[(a.indexOf(currentLang)+1)%3];localStorage.setItem('lang',currentLang);updateTranslations();loadData();showToast('🌐 '+currentLang.toUpperCase())}
function toggleTheme(){const dark=document.body.dataset.theme==='dark';document.body.dataset.theme=dark?'light':'dark';localStorage.setItem('theme',dark?'light':'dark');document.getElementById('theme-icon').className=dark?'fas fa-moon':'fas fa-sun'}
function switchPage(page,btn){document.querySelectorAll('.page').forEach(x=>x.classList.remove('active'));document.getElementById('page-'+page).classList.add('active');document.querySelectorAll('.nav-item').forEach(x=>x.classList.remove('active'));btn.classList.add('active');if(page==='stats')loadCharts();if(page==='report')generateReport();if(page==='msg')loadMsgDebtors()}
function openAddModal(){document.getElementById('addModal').classList.add('active')}
function closeModal(id){document.getElementById(id).classList.remove('active')}

async function api(url,options={}){const r=await fetch(url,{...options,headers:{...headers,...(options.headers||{})}});let data={};try{data=await r.json()}catch(e){}if(!r.ok)throw new Error(data.detail||'Server xatosi');return data}

async function loadData(){
try{
const s=await api('/api/stats'),ds=await api('/api/debtors');
document.getElementById('total-given').textContent=formatMoney(s.total_given);document.getElementById('total-remaining').textContent=formatMoney(s.total_remaining);document.getElementById('total-paid').textContent=formatMoney(s.total_paid);document.getElementById('total-count').textContent=s.total_debtors;
const list=document.getElementById('debtors-list');
if(!ds.length){list.innerHTML='<div class="empty-state"><div class="empty-icon">📒</div><p style="font-size:17px;font-weight:600">'+t('noDebtors')+'</p><p style="font-size:13px;margin-top:6px">'+t('tapAbove')+'</p></div>';return}
list.innerHTML=ds.map(d=>{const sc=d.status==='OVERDUE'?'overdue':d.status==='PAID'?'paid':'',bc=d.status==='OVERDUE'?'badge-overdue':d.status==='PAID'?'badge-paid':'badge-active',st=d.status==='OVERDUE'?t('statusOverdue'):d.status==='PAID'?t('statusPaid'):t('statusActive');return `<div class="debtor-card ${sc}"><div class="debtor-header"><div class="debtor-name">${esc(d.name)}</div><div class="debtor-amount">${formatMoney(d.remaining_amount)}</div></div><div class="debtor-info"><span>📞 ${esc(d.phone||'-')}</span><span>🏷 ${esc(d.category||'-')}</span><span>#${d.id}</span>${d.due_date?`<span>📅 ${esc(d.due_date)}</span>`:''}</div><span class="debtor-badge ${bc}">${st}</span><div class="debtor-details"><b>${t('total')}:</b> ${formatMoney(d.total_amount)} | <b>${t('paidAmount')}:</b> ${formatMoney(d.paid_amount)}</div><div class="debtor-actions">${d.status!=='PAID'?`<button class="btn-action btn-pay" onclick="openPayModal(${d.id},${Number(d.remaining_amount)})">💰 ${t('pay')}</button>`:''}<button class="btn-action btn-delete" onclick="deleteDebtor(${d.id})">🗑 ${t('delete')}</button></div></div>`}).join('')
}catch(e){console.error(e);showToast(e.message,true)}
}
function esc(v){return String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[m]))}

async function loadCharts(){try{const s=await api('/api/stats'),ds=await api('/api/debtors');if(typeof Chart==='undefined')return;if(pieChart)pieChart.destroy();pieChart=new Chart(document.getElementById('pieChart'),{type:'doughnut',data:{labels:[t('paid'),t('remaining')],datasets:[{data:[s.total_paid,s.total_remaining],backgroundColor:['#10b981','#ef4444'],borderWidth:0}]},options:{responsive:true,plugins:{legend:{position:'bottom'}}}});if(barChart)barChart.destroy();const top=ds.slice(0,5);barChart=new Chart(document.getElementById('barChart'),{type:'bar',data:{labels:top.map(d=>d.name),datasets:[{label:t('remaining'),data:top.map(d=>d.remaining_amount),backgroundColor:'#667eea',borderRadius:8}]},options:{responsive:true,plugins:{legend:{display:false}},scales:{y:{beginAtZero:true}}}})}catch(e){console.error(e)}}

async function generateReport(){try{const s=await api('/api/stats'),ds=await api('/api/debtors'),tr=translations[reportLang],today=new Date().toLocaleDateString();let h=`<h2 style="text-align:center;color:var(--primary)">${tr.reportTitle}</h2><p style="text-align:center;color:var(--text-light);margin:8px 0 20px">${tr.reportDate}: ${today}</p><div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:20px"><div><small>${tr.reportTotalDebtors}</small><h3>${s.total_debtors}</h3></div><div><small>${tr.reportTotalGiven}</small><h3>${formatMoney(s.total_given)}</h3></div><div><small>${tr.reportTotalPaid}</small><h3>${formatMoney(s.total_paid)}</h3></div><div><small>${tr.reportTotalRemaining}</small><h3>${formatMoney(s.total_remaining)}</h3></div></div><h3>${tr.reportDebtorList}</h3>`;h+=ds.length?ds.map(d=>`<div style="padding:12px;background:rgba(0,0,0,.03);border-radius:10px;margin-top:8px"><b>${esc(d.name)}</b> — ${formatMoney(d.remaining_amount)}<div style="font-size:12px;color:var(--text-light)">${esc(d.phone||'-')} | ${esc(d.category||'-')} | ${esc(d.status)}</div></div>`).join(''):`<p style="padding:25px;text-align:center;color:var(--text-light)">${t('noDebtors')}</p>`;document.getElementById('report-preview').innerHTML=h}catch(e){console.error(e)}}

async function addDebtor(){const data={name:document.getElementById('add-name').value.trim(),phone:document.getElementById('add-phone').value.trim()||null,amount:Number(document.getElementById('add-amount').value),due_date:document.getElementById('add-date').value||null,category:document.getElementById('add-category').value,note:document.getElementById('add-note').value.trim()||null};if(!data.name||!Number.isFinite(data.amount)||data.amount<=0){showToast(t('nameAmountRequired'),true);return}try{await api('/api/debtors',{method:'POST',body:JSON.stringify(data)});playSuccessSound();showToast(t('debtorAdded'));closeModal('addModal');['add-name','add-phone','add-amount','add-date','add-note'].forEach(id=>document.getElementById(id).value='');loadData()}catch(e){showToast(e.message,true)}}
function openPayModal(id,rem){document.getElementById('pay-debtor-id').value=id;document.getElementById('pay-amount').value=rem;document.getElementById('pay-note').value='';document.getElementById('payModal').classList.add('active')}
async function addPayment(){const id=Number(document.getElementById('pay-debtor-id').value),amount=Number(document.getElementById('pay-amount').value),note=document.getElementById('pay-note').value.trim();if(!Number.isFinite(amount)||amount<=0){showToast(t('invalidAmount'),true);return}try{await api('/api/debtors/'+id+'/pay',{method:'PUT',body:JSON.stringify({amount,note})});playSuccessSound();launchConfetti();showToast(t('paymentReceived'));closeModal('payModal');loadData()}catch(e){showToast(e.message,true)}}
async function deleteDebtor(id){if(!confirm(t('confirmDelete')))return;try{await api('/api/debtors/'+id,{method:'DELETE'});showToast(t('deleted'));loadData()}catch(e){showToast(e.message,true)}}

async function loadMsgDebtors(){try{msgDebtors=await api('/api/debtors');const sel=document.getElementById('msg-debtor');sel.innerHTML='<option value="">-- Tanlang --</option>';msgDebtors.forEach(d=>{const o=document.createElement('option');o.value=d.id;o.textContent=d.name+' — '+formatMoney(d.remaining_amount);o.dataset.name=d.name;o.dataset.debt=d.remaining_amount;o.dataset.tg=d.telegram_target||'';sel.appendChild(o)})}catch(e){showToast(e.message,true)}}
function selDebtor(){const sel=document.getElementById('msg-debtor'),o=sel.options[sel.selectedIndex],input=document.getElementById('msg-telegram-id'),text=document.getElementById('msg-text');if(!o?.value){input.value='';text.value='';return}input.value=o.dataset.tg||'';text.value=`Assalomu alaykum, ${o.dataset.name}!\n\nSizdagi qolgan qarz: ${Number(o.dataset.debt||0).toLocaleString('uz-UZ')} so'm.\nIltimos, imkon bo'lsa to'lovni amalga oshiring.\n\nRahmat!`}
function tpl(type){const sel=document.getElementById('msg-debtor'),o=sel.options[sel.selectedIndex];if(!o?.value){showToast("Avval qarzdorni tanlang!",true);return}const n=o.dataset.name,d=Number(o.dataset.debt||0).toLocaleString('uz-UZ');const a={eslatma:`Assalomu alaykum, ${n}!\n\nSizdagi qolgan qarz: ${d} so'm.\nIltimos, imkon bo'lsa to'lovni amalga oshiring.\n\nRahmat!`,muloyim:`Assalomu alaykum, ${n}!\n\nQarz bo'yicha kichik eslatma: ${d} so'm.\nQulay vaqtingizda to'lasangiz xursand bo'lamiz.\n\nRahmat!`,muddat:`Assalomu alaykum, ${n}!\n\nQarz: ${d} so'm.\nKelishilgan muddatda to'lovni amalga oshiring.\n\nRahmat!`,rahmat:`Assalomu alaykum, ${n}!\n\nTo'lovingiz uchun katta rahmat!\nQolgan qarz: ${d} so'm.`};document.getElementById('msg-text').value=a[type]||''}
async function saveTelegramId(){const id=Number(document.getElementById('msg-debtor').value),tgid=document.getElementById('msg-telegram-id').value.trim();if(!id){showToast("Avval qarzdorni tanlang!",true);return}if(!/^[0-9]+$/.test(tgid)){showToast("Telegram ID noto'g'ri!",true);return}try{await api('/api/debtors/'+id+'/telegram',{method:'PUT',body:JSON.stringify({telegram_target:Number(tgid)})});showToast("✅ Telegram ID saqlandi!");loadMsgDebtors()}catch(e){showToast(e.message,true)}}
async function sendTelegram(){const id=Number(document.getElementById('msg-debtor').value),message=document.getElementById('msg-text').value.trim();if(!id){showToast("Avval qarzdorni tanlang!",true);return}if(!message){showToast("Xabar matnini yozing!",true);return}try{await api('/api/send-message',{method:'POST',body:JSON.stringify({debtor_id:id,message})});playSuccessSound();showToast("📤 Telegram orqali yuborildi!")}catch(e){showToast(e.message,true)}}
function setReportLang(l,btn){reportLang=l;document.querySelectorAll('.lang-btn').forEach(x=>x.classList.remove('active'));btn.classList.add('active');generateReport()}

window.addEventListener('load',()=>{const th=localStorage.getItem('theme')||'light';document.body.dataset.theme=th;document.getElementById('theme-icon').className=th==='dark'?'fas fa-sun':'fas fa-moon';updateTranslations();loadData()});
</script>
</body>
</html>"""


# ================= API =================
@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(HTML_TEMPLATE)


@app.get("/api/stats")
async def get_stats(user: dict = Depends(get_current_user)):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            SELECT COUNT(*) AS total_debtors,
                   COALESCE(SUM(total_amount),0) AS total_given,
                   COALESCE(SUM(paid_amount),0) AS total_paid,
                   COALESCE(SUM(remaining_amount),0) AS total_remaining
            FROM debtors
            WHERE user_id=?
        """, (user["telegram_id"],))
        return dict(await cur.fetchone())


@app.get("/api/debtors")
async def get_debtors(user: dict = Depends(get_current_user)):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("""
            SELECT *
            FROM debtors
            WHERE user_id=?
            ORDER BY created_at DESC
        """, (user["telegram_id"],))
        return [dict(row) for row in await cur.fetchall()]


@app.post("/api/debtors")
async def create_debtor(d: DebtorCreate, user: dict = Depends(get_current_user)):
    if d.amount <= 0:
        raise HTTPException(400, "Summa 0 dan katta bo'lishi kerak")

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            INSERT INTO debtors
            (user_id,name,phone,category,note,total_amount,paid_amount,remaining_amount,due_date,status)
            VALUES (?,?,?,?,?,?,0,?,?,?)
        """, (
            user["telegram_id"], d.name.strip(), d.phone, d.category,
            d.note, d.amount, d.amount, d.due_date, "ACTIVE"
        ))
        await db.commit()
        return {"id": cur.lastrowid}


@app.put("/api/debtors/{debtor_id}/pay")
async def add_payment(
    debtor_id: int,
    p: PaymentCreate,
    user: dict = Depends(get_current_user)
):
    if p.amount <= 0:
        raise HTTPException(400, "Summa xato")

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            SELECT total_amount,paid_amount,remaining_amount
            FROM debtors
            WHERE id=? AND user_id=?
        """, (debtor_id, user["telegram_id"]))
        row = await cur.fetchone()

        if not row:
            raise HTTPException(404, "Qarzdor topilmadi")

        if p.amount > row[2]:
            raise HTTPException(400, "To'lov qolgan qarzdan katta")

        new_paid = row[1] + p.amount
        new_remaining = row[2] - p.amount
        new_status = "PAID" if new_remaining <= 0.000001 else "ACTIVE"

        await db.execute("""
            INSERT INTO payments (debtor_id,amount,note)
            VALUES (?,?,?)
        """, (debtor_id, p.amount, p.note))

        await db.execute("""
            UPDATE debtors
            SET paid_amount=?, remaining_amount=?, status=?
            WHERE id=? AND user_id=?
        """, (
            new_paid, max(0, new_remaining), new_status,
            debtor_id, user["telegram_id"]
        ))
        await db.commit()

        return {"remaining": max(0, new_remaining), "status": new_status}


@app.delete("/api/debtors/{debtor_id}")
async def delete_debtor(
    debtor_id: int,
    user: dict = Depends(get_current_user)
):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id FROM debtors WHERE id=? AND user_id=?",
            (debtor_id, user["telegram_id"])
        )
        if not await cur.fetchone():
            raise HTTPException(404, "Qarzdor topilmadi")

        await db.execute(
            "DELETE FROM payments WHERE debtor_id=?",
            (debtor_id,)
        )
        await db.execute(
            "DELETE FROM debtors WHERE id=? AND user_id=?",
            (debtor_id, user["telegram_id"])
        )
        await db.commit()

    return {"ok": True}


@app.put("/api/debtors/{debtor_id}/telegram")
async def update_telegram_target(
    debtor_id: int,
    data: TelegramTargetUpdate,
    user: dict = Depends(get_current_user)
):
    if data.telegram_target <= 0:
        raise HTTPException(400, "Telegram ID xato")

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id FROM debtors WHERE id=? AND user_id=?",
            (debtor_id, user["telegram_id"])
        )
        if not await cur.fetchone():
            raise HTTPException(404, "Qarzdor topilmadi")

        await db.execute("""
            UPDATE debtors
            SET telegram_target=?
            WHERE id=? AND user_id=?
        """, (
            data.telegram_target,
            debtor_id,
            user["telegram_id"]
        ))
        await db.commit()

    return {"ok": True, "telegram_target": data.telegram_target}


@app.post("/api/send-message")
async def send_message(
    data: SendMessage,
    user: dict = Depends(get_current_user)
):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("""
            SELECT name, telegram_target
            FROM debtors
            WHERE id=? AND user_id=?
        """, (data.debtor_id, user["telegram_id"]))
        row = await cur.fetchone()

    if not row:
        raise HTTPException(404, "Qarzdor topilmadi")

    target = row["telegram_target"]
    if not target:
        raise HTTPException(400, "Bu qarzdorga Telegram ID ulanmagan")

    # Escape user text because the bot uses HTML parse mode.
    safe_message = html.escape(data.message)
    sender = html.escape(user.get("first_name") or "Qarz Daftar")

    try:
        await bot.send_message(
            chat_id=int(target),
            text=f"💌 <b>Xabar:</b>\n\n{safe_message}\n\n— {sender}"
        )
        return {"ok": True}
    except Exception as exc:
        logger.exception("Telegram send error: %s", exc)
        raise HTTPException(
            500,
            "Telegramga yuborilmadi. Qarzdor botni ochib /start bosganini tekshiring."
        )


@app.get("/api/export/csv")
async def export_csv(user: dict = Depends(get_current_user)):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            SELECT name,phone,total_amount,paid_amount,
                   remaining_amount,status,due_date
            FROM debtors
            WHERE user_id=?
            ORDER BY created_at DESC
        """, (user["telegram_id"],))
        rows = await cur.fetchall()

    lines = ["Ism;Telefon;Jami;Tolangan;Qolgan;Holat;Muddat"]
    for row in rows:
        lines.append(";".join(
            "" if value is None else str(value) for value in row
        ))

    return Response(
        "\n".join(lines),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition":
                "attachment; filename=qarzlar.csv"
        }
    )


# ================= WEBHOOK =================
@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    update = types.Update.model_validate(data)
    await dp.feed_update(bot, update)
    return {"ok": True}


# ================= BOT =================
@dp.message(Command("id"))
async def cmd_id(message: types.Message):
    await message.answer(
        "🆔 <b>Sizning Telegram ID:</b>\n\n"
        f"<code>{message.from_user.id}</code>\n\n"
        "Shu raqamni Qarz Daftar ilovasidagi "
        "Telegram ID maydoniga kiriting."
    )


@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📒 Ilovani ochish",
                    web_app=WebAppInfo(url=WEBAPP_URL)
                )
            ]
        ]
    )

    await message.answer(
        "<b>💎 Qarz Daftar Pro</b>\n\n"
        "✨ Premium dizayn\n"
        "🎵 Tovush + 🎊 Confetti\n"
        "📊 Grafiklar va statistika\n"
        "📄 Hisobotlar (UZ/RU/EN)\n"
        "🌍 3 til qo'llab-quvvatlanadi\n\n"
        "🆔 Telegram ID olish uchun /id yozing.\n\n"
        "Tugmani bosing 👇",
        reply_markup=keyboard
    )


@dp.message(Command("report"))
async def cmd_report(message: types.Message):
    uid = message.from_user.id

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        cur = await db.execute("""
            SELECT COUNT(*) AS n,
                   COALESCE(SUM(total_amount),0) AS g,
                   COALESCE(SUM(paid_amount),0) AS p,
                   COALESCE(SUM(remaining_amount),0) AS r
            FROM debtors
            WHERE user_id=?
        """, (uid,))
        stats = dict(await cur.fetchone())

        cur = await db.execute("""
            SELECT name,phone,remaining_amount,status
            FROM debtors
            WHERE user_id=?
            ORDER BY created_at DESC
        """, (uid,))
        debtors = await cur.fetchall()

    text = (
        "📊 <b>QARZ DAFTAR HISOBOTI</b>\n"
        f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n"
        f"👥 Qarzdorlar: <b>{stats['n']}</b>\n"
        f"💰 Berilgan: <b>{stats['g']:,.0f} so'm</b>\n"
        f"✅ Tolangan: <b>{stats['p']:,.0f} so'm</b>\n"
        f"⏳ Qolgan: <b>{stats['r']:,.0f} so'm</b>\n\n"
        "<b>📋 Ro'yxat:</b>\n"
    )

    if not debtors:
        text += "\n<i>Hali qarzdor yo'q</i>"
    else:
        for debtor in debtors:
            icon = (
                "✅" if debtor["status"] == "PAID"
                else "⏳" if debtor["status"] == "ACTIVE"
                else "⚠️"
            )
            text += (
                f"\n{icon} <b>{html.escape(debtor['name'])}</b>"
                f" — {debtor['remaining_amount']:,.0f} so'm"
            )

    await message.answer(text)


# ================= START =================
def run_bot():
    try:
        logger.info("🤖 Bot polling started")
        asyncio.run(dp.start_polling(bot))
    except Exception as exc:
        logger.exception("Bot error: %s", exc)


if __name__ == "__main__":
    threading.Thread(target=run_bot, daemon=True).start()
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info"
    )
