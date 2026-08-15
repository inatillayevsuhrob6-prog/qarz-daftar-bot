import os, json, hmac, hashlib, logging, asyncio, threading
from datetime import datetime
from typing import Optional
from urllib.parse import parse_qsl
from io import BytesIO

import aiosqlite, httpx, uvicorn
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
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

BOT_TOKEN = "8651436055:AAH3FgpFyhcnBo4RXXYQpLMv1Wk4qNiCXX0"
WEBAPP_URL = "https://qarz-daftar-bot.onrender.com"
DB_PATH = "qarz_daftar.db"
os.makedirs("images", exist_ok=True)

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
rates_cache = {"data": {}, "time": 0}

class DebtorIn(BaseModel):
    name: str
    phone: Optional[str] = None
    amount: float
    currency: str = "UZS"
    due_date: Optional[str] = None
    category: str = "Shaxsiy"
    note: Optional[str] = None
    rating: int = 3

class PayIn(BaseModel):
    amount: float
    note: Optional[str] = None

class MsgIn(BaseModel):
    debtor_id: int
    message: str

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""CREATE TABLE IF NOT EXISTS debtors(
            id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,name TEXT,phone TEXT,
            category TEXT DEFAULT 'Shaxsiy',note TEXT,total_amount REAL,paid_amount REAL DEFAULT 0,
            remaining_amount REAL,currency TEXT DEFAULT 'UZS',status TEXT DEFAULT 'ACTIVE',
            rating INTEGER DEFAULT 3,image_path TEXT,due_date TEXT,telegram_target INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS payments(
            id INTEGER PRIMARY KEY AUTOINCREMENT,debtor_id INTEGER,amount REAL,note TEXT,
            payment_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        for c,t in [("currency","TEXT DEFAULT 'UZS'"),("rating","INTEGER DEFAULT 3"),("image_path","TEXT"),("telegram_target","INTEGER")]:
            try: await db.execute(f"ALTER TABLE debtors ADD COLUMN {c} {t}")
            except: pass
        await db.commit()

async def get_rates():
    now = datetime.now().timestamp()
    if rates_cache["data"] and now - rates_cache["time"] < 86400:
        return rates_cache["data"]
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = (await c.get("https://open.er-api.com/v6/latest/USD")).json()
            rates_cache["data"] = {"USD":1,"EUR":r["rates"].get("EUR",0.92),"UZS":r["rates"].get("UZS",12500)}
            rates_cache["time"] = now
    except:
        if not rates_cache["data"]:
            rates_cache["data"] = {"USD":1,"EUR":0.92,"UZS":12500}
    return rates_cache["data"]

def conv(amt, f, t, r):
    return (amt / r.get(f,1)) * r.get(t,1)

def verify(init_data):
    if not init_data: raise HTTPException(401)
    try:
        d = dict(parse_qsl(init_data))
        h = d.pop("hash","")
        s = "\n".join(f"{k}={d[k]}" for k in sorted(d))
        sk = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        ch = hmac.new(sk, s.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(ch, h): raise HTTPException(401)
        u = json.loads(d.get("user","{}"))
        return {"telegram_id":u.get("id"),"first_name":u.get("first_name","")}
    except HTTPException: raise
    except: raise HTTPException(401)

async def get_user(r: Request):
    i = r.headers.get("X-Telegram-Init-Data") or r.query_params.get("initData","")
    return verify(i) if i else {"telegram_id":123456,"first_name":"Test"}

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
async def startup():
    await init_db()
    await get_rates()

@app.get("/api/stats")
async def stats(u=Depends(get_user)):
    r = await get_rates()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = [dict(x) for x in await (await db.execute("SELECT * FROM debtors WHERE user_id=?",(u["telegram_id"],))).fetchall()]
    tg=tp=tr=0
    for d in rows:
        c = d.get("currency") or "UZS"
        tg += conv(d["total_amount"],c,"UZS",r)
        tp += conv(d["paid_amount"],c,"UZS",r)
        tr += conv(d["remaining_amount"],c,"UZS",r)
    return {"total_debtors":len(rows),"total_given":tg,"total_paid":tp,"total_remaining":tr,"rates":r}

@app.get("/api/debtors")
async def debtors(u=Depends(get_user)):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        return [dict(x) for x in await (await db.execute("SELECT * FROM debtors WHERE user_id=? ORDER BY id DESC",(u["telegram_id"],))).fetchall()]

@app.get("/api/debtors/top")
async def top(u=Depends(get_user)):
    r = await get_rates()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = [dict(x) for x in await (await db.execute("SELECT * FROM debtors WHERE user_id=? AND remaining_amount>0",(u["telegram_id"],))).fetchall()]
    for d in rows: d["uzs"] = conv(d["remaining_amount"], d.get("currency") or "UZS", "UZS", r)
    rows.sort(key=lambda x: x["uzs"], reverse=True)
    return rows[:10]

@app.get("/api/payments/{did}")
async def payments(did:int, u=Depends(get_user)):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        return [dict(x) for x in await (await db.execute("SELECT p.* FROM payments p JOIN debtors d ON p.debtor_id=d.id WHERE p.debtor_id=? AND d.user_id=? ORDER BY p.payment_date DESC",(did,u["telegram_id"]))).fetchall()]

@app.post("/api/debtors")
async def add_debtor(d:DebtorIn, u=Depends(get_user)):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("INSERT INTO debtors(user_id,name,phone,category,note,total_amount,paid_amount,remaining_amount,currency,rating,due_date) VALUES(?,?,?,?,?,?,0,?,?,?,?,?)",
            (u["telegram_id"],d.name,d.phone,d.category,d.note,d.amount,d.amount,d.currency,d.rating,d.due_date))
        await db.commit()
        return {"id":cur.lastrowid}

@app.put("/api/debtors/{did}/pay")
async def pay(did:int, p:PayIn, u=Depends(get_user)):
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute("SELECT total_amount,paid_amount,remaining_amount FROM debtors WHERE id=? AND user_id=?",(did,u["telegram_id"]))).fetchone()
        if not row: raise HTTPException(404)
        if p.amount<=0 or p.amount>row[2]: raise HTTPException(400)
        np=row[1]+p.amount; nr=row[2]-p.amount; st="PAID" if nr==0 else "ACTIVE"
        await db.execute("INSERT INTO payments(debtor_id,amount,note) VALUES(?,?,?)",(did,p.amount,p.note))
        await db.execute("UPDATE debtors SET paid_amount=?,remaining_amount=?,status=? WHERE id=?",(np,nr,st,did))
        await db.commit()
        return {"remaining":nr}

@app.put("/api/debtors/{did}/rating")
async def rate(did:int, rating:int, u=Depends(get_user)):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE debtors SET rating=? WHERE id=? AND user_id=?",(rating,did,u["telegram_id"]))
        await db.commit()
    return {"ok":True}

@app.delete("/api/debtors/{did}")
async def delete(did:int, u=Depends(get_user)):
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute("SELECT image_path FROM debtors WHERE id=? AND user_id=?",(did,u["telegram_id"]))).fetchone()
        if row and row[0] and os.path.exists(row[0]):
            try: os.remove(row[0])
            except: pass
        await db.execute("DELETE FROM payments WHERE debtor_id=?",(did,))
        await db.execute("DELETE FROM debtors WHERE id=? AND user_id=?",(did,u["telegram_id"]))
        await db.commit()
    return {"ok":True}

@app.post("/api/debtors/{did}/image")
async def upload_img(did:int, file:UploadFile=File(...), u=Depends(get_user)):
    ext = (file.filename or "jpg").split(".")[-1].lower()
    if ext not in ["jpg","jpeg","png","webp"]: ext="jpg"
    path = f"images/{u['telegram_id']}_{did}_{int(datetime.now().timestamp())}.{ext}"
    with open(path,"wb") as f: f.write(await file.read())
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE debtors SET image_path=? WHERE id=? AND user_id=?",(path,did,u["telegram_id"]))
        await db.commit()
    return {"url":f"/{path}"}

@app.get("/images/{fn}")
async def img(fn:str):
    p = f"images/{fn}"
    if not os.path.exists(p): raise HTTPException(404)
    return Response(open(p,"rb").read(), media_type="image/jpeg")

@app.post("/api/send-message")
async def send_msg(m:MsgIn, u=Depends(get_user)):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute("SELECT telegram_target FROM debtors WHERE id=? AND user_id=?",(m.debtor_id,u["telegram_id"]))).fetchone()
    if not row or not row["telegram_target"]:
        raise HTTPException(400,"Qarzdor botga /link yozmagan")
    await bot.send_message(row["telegram_target"], f"💌 Xabar:\n\n{m.message}\n\n— {u['first_name']}")
    return {"ok":True}

@app.get("/api/export/pdf")
async def pdf(u=Depends(get_user), lang:str="uz"):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        s = dict(await (await db.execute("SELECT COUNT(*) n,SUM(total_amount) g,SUM(paid_amount) p,SUM(remaining_amount) r FROM debtors WHERE user_id=?",(u["telegram_id"],))).fetchone())
        ds = [dict(x) for x in await (await db.execute("SELECT * FROM debtors WHERE user_id=? ORDER BY remaining_amount DESC",(u["telegram_id"],))).fetchall()]
    tr = {"uz":{"t":"QARZ DAFTAR","d":"Sana","n":"Ism","p":"Tel","g":"Jami","r":"Qolgan","s":"Holat"},
          "ru":{"t":"ОТЧЕТ","d":"Дата","n":"Имя","p":"Тел","g":"Всего","r":"Остаток","s":"Статус"},
          "en":{"t":"REPORT","d":"Date","n":"Name","p":"Phone","g":"Total","r":"Remaining","s":"Status"}}.get(lang,{"t":"REPORT","d":"Date","n":"Name","p":"Phone","g":"Total","r":"Remaining","s":"Status"})
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4)
    els = [Paragraph(tr["t"], getSampleStyleSheet()['Heading1']), Spacer(1,12)]
    data = [[tr["n"],tr["p"],tr["g"],tr["r"],tr["s"]]] + [[d["name"],d["phone"] or "-",f"{d['total_amount']:.0f}",f"{d['remaining_amount']:.0f}",d["status"]] for d in ds]
    els.append(Table(data, colWidths=[140,100,100,100,80]))
    doc.build(els)
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/pdf", headers={"Content-Disposition":f"attachment; filename=report_{lang}.pdf"})

@app.get("/api/export/csv")
async def csv(u=Depends(get_user)):
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (await db.execute("SELECT name,phone,total_amount,currency,paid_amount,remaining_amount,status FROM debtors WHERE user_id=?",(u["telegram_id"],))).fetchall()
    lines = ["Ism;Tel;Jami;Valyuta;Tolangan;Qolgan;Holat"] + [";".join(str(x) if x is not None else "" for x in r) for r in rows]
    return Response("\n".join(lines), media_type="text/csv")

@app.get("/api/remind/{did}")
async def remind(did:int, u=Depends(get_user)):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        d = await (await db.execute("SELECT name,remaining_amount,currency FROM debtors WHERE id=? AND user_id=?",(did,u["telegram_id"]))).fetchone()
    if not d: raise HTTPException(404)
    return {"text":f"Assalomu alaykum, {d['name']}!\n\nSizda {d['remaining_amount']:,.0f} {d['currency'] or 'UZS'} qarz bor.\nIltimos to'lang.\n\nRahmat!"}

@app.post("/webhook")
async def webhook(r:Request):
    await dp.feed_update(bot, types.Update(**await r.json()))
    return {"ok":True}

HTML = """<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Qarz Daftar</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:sans-serif;background:linear-gradient(135deg,#667eea,#764ba2);min-height:100vh;padding:20px;padding-bottom:100px;color:#333}
.c{max-width:600px;margin:0 auto}
.h{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;color:#fff}
.h h1{font-size:24px}
.btn{padding:10px 20px;border:none;border-radius:10px;cursor:pointer;font-weight:bold}
.card{background:#fff;padding:15px;border-radius:15px;margin-bottom:12px;box-shadow:0 4px 15px rgba(0,0,0,.1)}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:20px}
.stat{background:#fff;padding:15px;border-radius:15px;text-align:center}
.stat b{font-size:20px;display:block;margin-top:5px}
.nav{position:fixed;bottom:0;left:0;right:0;background:#fff;display:flex;justify-content:space-around;padding:10px;box-shadow:0 -4px 20px rgba(0,0,0,.1)}
.nav button{border:none;background:none;cursor:pointer;padding:8px 12px;font-size:12px}
.nav button.active{color:#667eea;font-weight:bold}
.modal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:100;align-items:flex-end}
.modal.show{display:flex}
.modal-content{background:#fff;width:100%;max-width:600px;border-radius:20px 20px 0 0;padding:20px;max-height:80vh;overflow-y:auto}
input,select,textarea{width:100%;padding:12px;border:2px solid #ddd;border-radius:10px;margin-bottom:10px;font-size:16px}
.toast{position:fixed;top:20px;left:50%;transform:translateX(-50%);background:#10b981;color:#fff;padding:12px 24px;border-radius:20px;z-index:200;display:none}
.toast.show{display:block}
.page{display:none}.page.active{display:block}
</style></head><body>
<div class="toast" id="toast"></div>
<div class="c">
<div class="h"><h1>💎 Qarz Daftar</h1><div><button class="btn" onclick="toggleLang()" style="background:#fff">🌐</button></div></div>

<div id="p-home" class="page active">
<div class="grid">
<div class="stat">Jami<b id="s1">0</b></div>
<div class="stat">Qolgan<b id="s2">0</b></div>
<div class="stat">Tolangan<b id="s3">0</b></div>
<div class="stat">Soni<b id="s4">0</b></div>
</div>
<button class="btn" style="width:100%;background:#fff;color:#667eea;margin-bottom:20px" onclick="openAdd()">➕ Yangi qarzdor</button>
<div id="list"></div>
</div>

<div id="p-top" class="page"><h2 style="color:#fff;margin-bottom:15px">🏆 TOP</h2><div id="toplist"></div></div>
<div id="p-stats" class="page"><h2 style="color:#fff;margin-bottom:15px">📊 Statistika</h2><div class="card"><canvas id="chart"></canvas></div></div>
<div id="p-pdf" class="page"><h2 style="color:#fff;margin-bottom:15px">📄 Hisobot</h2>
<button class="btn" style="width:100%;background:#fff;color:#667eea;margin-bottom:10px" onclick="dlPDF('uz')">🇺 PDF O'zbek</button>
<button class="btn" style="width:100%;background:#fff;color:#667eea;margin-bottom:10px" onclick="dlPDF('ru')">🇷🇺 PDF Русский</button>
<button class="btn" style="width:100%;background:#fff;color:#667eea" onclick="dlCSV()">📊 CSV</button></div>
<div id="p-msg" class="page"><h2 style="color:#fff;margin-bottom:15px">💌 Xabar</h2><p style="color:#fff;margin-bottom:15px">Qarzdor botga /link ID yozishi kerak</p><div id="msglist"></div></div>
</div>

<div class="nav">
<button class="active" onclick="sw('home',this)">🏠 Bosh</button>
<button onclick="sw('top',this)">🏆 TOP</button>
<button onclick="sw('stats',this)">📊 Stat</button>
<button onclick="sw('pdf',this)">📄 PDF</button>
<button onclick="sw('msg',this)">💌 Xabar</button>
</div>

<div class="modal" id="m-add"><div class="modal-content">
<h2>Yangi qarzdor</h2>
<input id="a-name" placeholder="Ism *">
<input id="a-phone" placeholder="Telefon">
<input id="a-amt" type="number" placeholder="Summa *">
<select id="a-cur"><option>UZS</option><option>USD</option><option>EUR</option></select>
<input id="a-date" type="date">
<select id="a-cat"><option>Shaxsiy</option><option>Biznes</option><option>Oila</option></select>
<textarea id="a-note" placeholder="Izoh" rows="2"></textarea>
<button class="btn" style="width:100%;background:#667eea;color:#fff" onclick="addDebtor()">Saqlash</button>
<button class="btn" style="width:100%;background:#ddd;margin-top:10px" onclick="closeM('m-add')">Bekor</button>
</div></div>

<div class="modal" id="m-pay"><div class="modal-content">
<h2>To'lov</h2>
<input type="hidden" id="p-id">
<input id="p-amt" type="number" placeholder="Summa">
<input id="p-note" placeholder="Izoh">
<button class="btn" style="width:100%;background:#10b981;color:#fff" onclick="payDebtor()">Tasdiqlash</button>
<button class="btn" style="width:100%;background:#ddd;margin-top:10px" onclick="closeM('m-pay')">Bekor</button>
</div></div>

<div class="modal" id="m-hist"><div class="modal-content">
<h2>Tarix</h2>
<div id="hist"></div>
<button class="btn" style="width:100%;background:#ddd;margin-top:10px" onclick="closeM('m-hist')">Yopish</button>
</div></div>

<div class="modal" id="m-msg"><div class="modal-content">
<h2>Xabar yuborish</h2>
<input type="hidden" id="m-id">
<textarea id="m-txt" rows="5" placeholder="Xabar..."></textarea>
<button class="btn" style="width:100%;background:#8b5cf6;color:#fff" onclick="sendMsg()">Yuborish</button>
<button class="btn" style="width:100%;background:#3b82f6;color:#fff;margin-top:10px" onclick="copyMsg()">Nusxalash</button>
<button class="btn" style="width:100%;background:#ddd;margin-top:10px" onclick="closeM('m-msg')">Bekor</button>
</div></div>

<script>
const tg=window.Telegram?.WebApp;if(tg)tg.expand();
const H={'Content-Type':'application/json'};if(tg?.initData)H['X-Telegram-Init-Data']=tg.initData;
let LANG='uz',chart=null;

function toast(m){const t=document.getElementById('toast');t.textContent=m;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),2000)}
function sw(p,b){document.querySelectorAll('.page').forEach(x=>x.classList.remove('active'));document.getElementById('p-'+p).classList.add('active');document.querySelectorAll('.nav button').forEach(x=>x.classList.remove('active'));b.classList.add('active');if(p==='top')loadTop();if(p==='stats')loadChart();if(p==='msg')loadMsg()}
function toggleLang(){LANG=LANG==='uz'?'ru':'uz';toast(LANG.toUpperCase());load()}

async function load(){
const s=await(await fetch('/api/stats',{headers:H})).json();
document.getElementById('s1').textContent=Math.round(s.total_given).toLocaleString();
document.getElementById('s2').textContent=Math.round(s.total_remaining).toLocaleString();
document.getElementById('s3').textContent=Math.round(s.total_paid).toLocaleString();
document.getElementById('s4').textContent=s.total_debtors;
const ds=await(await fetch('/api/debtors',{headers:H})).json();
const l=document.getElementById('list');
if(!ds.length){l.innerHTML='<div class="card" style="text-align:center">Hali qarzdor yo\'q</div>';return}
l.innerHTML=ds.map(d=>`<div class="card">
<div style="display:flex;justify-content:space-between;margin-bottom:8px"><b>${d.name}</b><b style="color:#667eea">${Math.round(d.remaining_amount).toLocaleString()} ${d.currency||'UZS'}</b></div>
<div style="font-size:13px;color:#666;margin-bottom:8px">${d.phone||'-'} | ${d.category} | #${d.id}</div>
<div style="font-size:12px;color:#999;margin-bottom:10px">Jami: ${Math.round(d.total_amount).toLocaleString()} | Tolangan: ${Math.round(d.paid_amount).toLocaleString()}</div>
<div style="display:flex;gap:6px;flex-wrap:wrap">
${d.status!=='PAID'?`<button class="btn" style="background:#10b981;color:#fff;padding:8px 12px;font-size:13px" onclick="openPay(${d.id},${d.remaining_amount})">💰 To'lov</button>`:''}
${d.phone?`<button class="btn" style="background:#25D366;color:#fff;padding:8px 12px;font-size:13px" onclick="wa('${d.phone.replace(/[^0-9]/g,'')}')">📱 WA</button>`:''}
<button class="btn" style="background:#8b5cf6;color:#fff;padding:8px 12px;font-size:13px" onclick="openMsg(${d.id})">💌</button>
<button class="btn" style="background:#3b82f6;color:#fff;padding:8px 12px;font-size:13px" onclick="rem(${d.id})">🔔</button>
<button class="btn" style="background:#8b5cf6;color:#fff;padding:8px 12px;font-size:13px" onclick="hist(${d.id})">📜</button>
<button class="btn" style="background:#ef4444;color:#fff;padding:8px 12px;font-size:13px" onclick="del(${d.id})">🗑</button>
</div></div>`).join('')}

async function loadTop(){
const ds=await(await fetch('/api/debtors/top',{headers:H})).json();
document.getElementById('toplist').innerHTML=ds.length?ds.map((d,i)=>`<div class="card"><b>${i===0?'🥇':i===1?'🥈':i===2?'🥉':'#'+(i+1)} ${d.name}</b> — ${Math.round(d.remaining_amount).toLocaleString()} ${d.currency||'UZS'}</div>`).join(''):'<div class="card">Yo\'q</div>'}

async function loadChart(){
const s=await(await fetch('/api/stats',{headers:H})).json();
if(chart)chart.destroy();
chart=new Chart(document.getElementById('chart'),{type:'doughnut',data:{labels:['Tolangan','Qolgan'],datasets:[{data:[s.total_paid,s.total_remaining],backgroundColor:['#10b981','#ef4444']}]}})}

async function loadMsg(){
const ds=await(await fetch('/api/debtors',{headers:H})).json();
document.getElementById('msglist').innerHTML=ds.map(d=>`<div class="card"><b>${d.name}</b> (#${d.id})<br><button class="btn" style="background:#8b5cf6;color:#fff;margin-top:8px" onclick="openMsg(${d.id})">💌 Xabar</button></div>`).join('')}

function dlPDF(l){window.open('/api/export/pdf?lang='+l+'&initData='+encodeURIComponent(tg?.initData||''))}
function dlCSV(){window.open('/api/export/csv?initData='+encodeURIComponent(tg?.initData||''))}
function wa(p){window.open('https://wa.me/'+p)}

function openAdd(){document.getElementById('m-add').classList.add('show')}
function closeM(id){document.getElementById(id).classList.remove('show')}

async function addDebtor(){
const d={name:a_name.value,phone:a_phone.value,amount:parseFloat(a_amt.value),currency:a_cur.value,due_date:a_date.value,category:a_cat.value,note:a_note.value};
if(!d.name||!d.amount){toast('Ism va summa kerak');return}
await fetch('/api/debtors',{method:'POST',headers:H,body:JSON.stringify(d)});
toast('Qo\'shildi!');closeM('m-add');load()}

function openPay(id,rem){p_id.value=id;p_amt.value=rem;p_note.value='';document.getElementById('m-pay').classList.add('show')}
async function payDebtor(){
const r=await fetch('/api/debtors/'+p_id.value+'/pay',{method:'PUT',headers:H,body:JSON.stringify({amount:parseFloat(p_amt.value),note:p_note.value})});
if(r.ok){toast('Qabul qilindi!');closeM('m-pay');load()}else toast('Xato')}

async function hist(id){
const ps=await(await fetch('/api/payments/'+id,{headers:H})).json();
document.getElementById('hist').innerHTML=ps.length?ps.map(p=>`<div style="padding:10px;background:#f5f5f5;margin-bottom:8px;border-radius:8px"><b>${Math.round(p.amount).toLocaleString()}</b> — ${p.note||'-'}<br><small>${new Date(p.payment_date).toLocaleDateString()}</small></div>`).join(''):'<p>Yo\'q</p>';
document.getElementById('m-hist').classList.add('show')}

function openMsg(id){m_id.value=id;m_txt.value='';document.getElementById('m-msg').classList.add('show')}
async function sendMsg(){
const r=await fetch('/api/send-message',{method:'POST',headers:H,body:JSON.stringify({debtor_id:parseInt(m_id.value),message:m_txt.value})});
if(r.ok){toast('Yuborildi!');closeM('m-msg')}else{const e=await r.json();toast(e.detail||'Xato')}}
function copyMsg(){navigator.clipboard.writeText(m_txt.value);toast('Nusxalandi')}

async function rem(id){
const r=await(await fetch('/api/remind/'+id,{headers:H})).json();
navigator.clipboard.writeText(r.text);toast('Nusxalandi')}

async function del(id){if(!confirm('O\'chirilsinmi?'))return;await fetch('/api/debtors/'+id,{method:'DELETE',headers:H});toast('O\'chirildi');load()}

window.onload=load;
</script></body></html>"""

@app.get("/", response_class=HTMLResponse)
async def index(): return HTMLResponse(HTML)

@dp.message(CommandStart())
async def start(m:types.Message):
    await m.answer(f"<b>💎 Qarz Daftar</b>\n\nID: <code>{m.from_user.id}</code>\n\n/link <debtor_id> — bog'lash\n/backup — zaxira\n/report — hisobot\n\n👇",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📒 Ilova",web_app=WebAppInfo(url=WEBAPP_URL))]]))

@dp.message(Command("link"))
async def link(m:types.Message):
    args=m.text.split()
    if len(args)<2: await m.answer("/link <id>"); return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE debtors SET telegram_target=? WHERE id=?",(m.from_user.id,int(args[1])))
        await db.commit()
    await m.answer("✅ Bog'landi!")

@dp.message(Command("backup"))
async def backup(m:types.Message):
    async with aiosqlite.connect(DB_PATH) as db:
        rows=await (await db.execute("SELECT name,phone,total_amount,currency,paid_amount,remaining_amount,status FROM debtors WHERE user_id=?",(m.from_user.id,))).fetchall()
    lines=["Ism;Tel;Jami;Valyuta;Tolangan;Qolgan;Holat"]+[";".join(str(x) if x else "" for x in r) for r in rows]
    await m.answer_document(FSInputFile(BytesIO("\n".join(lines).encode()),filename="backup.csv"))

@dp.message(Command("report"))
async def report(m:types.Message):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory=aiosqlite.Row
        s=dict(await (await db.execute("SELECT COUNT(*) n,SUM(total_amount) g,SUM(paid_amount) p,SUM(remaining_amount) r FROM debtors WHERE user_id=?",(m.from_user.id,))).fetchone())
    await m.answer(f"📊 Qarzdorlar: {s['n']}\n💰 Jami: {s['g'] or 0:,.0f}\n✅ Tolangan: {s['p'] or 0:,.0f}\n⏳ Qolgan: {s['r'] or 0:,.0f}")

def run_bot():
    try:
        loop=asyncio.new_event_loop();asyncio.set_event_loop(loop)
        loop.add_signal_handler=lambda*a,**k:None
        loop.run_until_complete(dp.start_polling(bot))
    except Exception as e: print(e)

if __name__=="__main__":
    threading.Thread(target=run_bot,daemon=True).start()
    uvicorn.run(app,host="0.0.0.0",port=int(os.getenv("PORT",8000)))