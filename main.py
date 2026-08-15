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

# ============ KONFIGURATSIYA ============
BOT_TOKEN = "8651436055:AAH3FgpFyhcnBo4RXXYQpLMv1Wk4qNiCXX0"  # O'zingizning tokenni qo'ying
WEBAPP_URL = "http://localhost:8000"  # Deploy qilinganda o'zgartirasiz
DB_PATH = "qarz_daftar.db"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger("qarz_daftar")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()


# ============ HTML TEMPLATE (Dizayn + JS) ============
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="uz">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>Qarz Daftar Pro</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}
:root{
  --bg:linear-gradient(135deg,#0f172a 0%,#1e293b 50%,#312e81 100%);
  --card:rgba(30,41,59,.65);
  --border:rgba(255,255,255,.1);
  --text:#f8fafc;--muted:#94a3b8;
  --primary:#6366f1;--accent:#ec4899;
  --success:#10b981;--danger:#ef4444;--warn:#f59e0b;
  --grad:linear-gradient(135deg,#6366f1 0%,#8b5cf6 50%,#ec4899 100%);
  --shadow:0 10px 40px rgba(99,102,241,.25);
}
[data-theme=light]{
  --bg:linear-gradient(135deg,#f1f5f9 0%,#e0e7ff 50%,#fce7f3 100%);
  --card:rgba(255,255,255,.75);
  --border:rgba(0,0,0,.08);
  --text:#0f172a;--muted:#64748b;
  --shadow:0 10px 40px rgba(99,102,241,.15);
}
html,body{min-height:100vh;background:var(--bg);background-attachment:fixed;color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;overflow-x:hidden;padding-bottom:100px}
body::before{content:'';position:fixed;top:-50%;left:-50%;width:200%;height:200%;background:radial-gradient(circle at 20% 50%,rgba(99,102,241,.15) 0%,transparent 50%),radial-gradient(circle at 80% 80%,rgba(236,72,153,.1) 0%,transparent 50%);pointer-events:none;z-index:0}
.container{position:relative;z-index:1;max-width:600px;margin:0 auto;padding:16px}
.glass{background:var(--card);backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);border:1px solid var(--border);border-radius:20px;box-shadow:var(--shadow)}
header{display:flex;justify-content:space-between;align-items:center;padding:8px 0 16px}
h1{font-size:1.6rem;font-weight:800;background:var(--grad);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;letter-spacing:-.5px}
.theme-btn{width:40px;height:40px;border-radius:50%;background:var(--card);border:1px solid var(--border);display:flex;align-items:center;justify-content:center;cursor:pointer;color:var(--text);transition:.3s}
.theme-btn:active{transform:scale(.9)}
.stats{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:20px}
.stat{padding:16px;position:relative;overflow:hidden;transition:.3s}
.stat::after{content:'';position:absolute;top:-50%;right:-50%;width:200%;height:200%;background:var(--grad);opacity:.05;border-radius:50%;pointer-events:none}
.stat-icon{width:36px;height:36px;border-radius:10px;background:var(--grad);display:flex;align-items:center;justify-content:center;color:#fff;font-size:1rem;margin-bottom:8px}
.stat-label{font-size:.75rem;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;font-weight:600}
.stat-val{font-size:1.3rem;font-weight:800;margin-top:4px}
h2{font-size:1.1rem;font-weight:700;margin:8px 0 14px;display:flex;align-items:center;gap:8px}
.list{display:flex;flex-direction:column;gap:12px}
.card{padding:16px;position:relative;overflow:hidden;transition:.3s;animation:slideIn .4s ease}
.card:active{transform:scale(.98)}
.card::before{content:'';position:absolute;left:0;top:0;bottom:0;width:4px;background:var(--primary);border-radius:4px 0 0 4px}
.card.overdue::before{background:var(--danger)}
.card.paid::before{background:var(--success)}
.card.soon::before{background:var(--warn)}
.card-top{display:flex;justify-content:space-between;align-items:start;gap:12px;margin-bottom:8px}
.card-name{font-size:1rem;font-weight:700}
.card-meta{font-size:.8rem;color:var(--muted);display:flex;gap:12px;flex-wrap:wrap;margin-top:4px}
.card-meta i{margin-right:3px;color:var(--primary)}
.amount{text-align:right}
.amount-main{font-size:1.2rem;font-weight:800;background:var(--grad);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}
.amount-sub{font-size:.72rem;color:var(--muted);margin-top:2px}
.badges{display:flex;gap:6px;margin-top:10px;flex-wrap:wrap}
.badge{font-size:.7rem;padding:4px 10px;border-radius:20px;background:rgba(99,102,241,.15);color:var(--primary);font-weight:600}
.badge.overdue{background:rgba(239,68,68,.15);color:var(--danger)}
.badge.paid{background:rgba(16,185,129,.15);color:var(--success)}
.badge.soon{background:rgba(245,158,11,.15);color:var(--warn)}
.card-actions{display:flex;gap:8px;margin-top:12px}
.btn{padding:10px 16px;border-radius:12px;border:none;font-weight:600;cursor:pointer;font-size:.85rem;display:inline-flex;align-items:center;gap:6px;transition:.2s}
.btn:active{transform:scale(.95)}
.btn-p{background:var(--grad);color:#fff;flex:1}
.btn-d{background:rgba(239,68,68,.15);color:var(--danger)}
.btn-w{background:rgba(255,255,255,.05);color:var(--text);border:1px solid var(--border)}
[data-theme=light] .btn-w{background:rgba(0,0,0,.03)}
.search{margin-bottom:14px}
.search input,.search-box{width:100%;padding:14px 18px;background:var(--card);border:1px solid var(--border);border-radius:14px;color:var(--text);font-size:.95rem;outline:none;transition:.2s}
.search input:focus{border-color:var(--primary);box-shadow:0 0 0 3px rgba(99,102,241,.15)}
.chips{display:flex;gap:8px;overflow-x:auto;padding-bottom:8px;margin-bottom:14px;scrollbar-width:none}
.chips::-webkit-scrollbar{display:none}
.chip{padding:8px 16px;border-radius:20px;background:var(--card);border:1px solid var(--border);white-space:nowrap;font-size:.82rem;cursor:pointer;transition:.2s;font-weight:500}
.chip.active{background:var(--grad);color:#fff;border-color:transparent}
.empty{text-align:center;padding:40px 20px;color:var(--muted)}
.empty i{font-size:3rem;opacity:.3;margin-bottom:12px}
.nav{position:fixed;bottom:0;left:0;right:0;padding:12px 16px;backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);background:var(--card);border-top:1px solid var(--border);display:flex;justify-content:space-around;align-items:center;z-index:100;padding-bottom:calc(12px + env(safe-area-inset-bottom))}
.nav-item{flex:1;text-align:center;color:var(--muted);cursor:pointer;padding:6px 0;transition:.2s;font-size:.7rem;font-weight:600}
.nav-item i{font-size:1.3rem;display:block;margin-bottom:3px;transition:.2s}
.nav-item.active{color:var(--primary)}
.nav-item.active i{transform:translateY(-3px)}
.nav-add{background:var(--grad);width:54px;height:54px;border-radius:50%;display:flex;align-items:center;justify-content:center;color:#fff;font-size:1.4rem;box-shadow:0 8px 20px rgba(99,102,241,.5);margin-top:-20px;border:3px solid var(--card);cursor:pointer}
.nav-add:active{transform:scale(.9)}
.modal{position:fixed;inset:0;background:rgba(0,0,0,.7);backdrop-filter:blur(4px);z-index:200;display:none;align-items:flex-end;justify-content:center}
.modal.open{display:flex;animation:fadeIn .2s}
.modal-content{width:100%;max-width:600px;background:var(--bg);border-radius:28px 28px 0 0;padding:24px;max-height:90vh;overflow-y:auto;animation:slideUp .3s}
.modal h3{font-size:1.2rem;font-weight:700;margin-bottom:18px}
.form-group{margin-bottom:14px}
.form-group label{display:block;margin-bottom:6px;color:var(--muted);font-size:.82rem;font-weight:600}
.form-group input,.form-group select,.form-group textarea{width:100%;padding:13px 16px;background:var(--card);border:1px solid var(--border);border-radius:12px;color:var(--text);font-size:.95rem;outline:none;transition:.2s;font-family:inherit}
.form-group input:focus,.form-group select:focus,.form-group textarea:focus{border-color:var(--primary)}
.form-row{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.toast{position:fixed;top:30px;left:50%;transform:translateX(-50%) translateY(-100px);background:var(--grad);color:#fff;padding:12px 24px;border-radius:30px;font-weight:600;z-index:300;box-shadow:0 10px 30px rgba(0,0,0,.3);opacity:0;transition:.3s}
.toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
.page{display:none;animation:fadeIn .3s}
.page.active{display:block}
.profile-avatar{width:80px;height:80px;border-radius:50%;background:var(--grad);display:flex;align-items:center;justify-content:center;font-size:2rem;color:#fff;margin:0 auto 16px;box-shadow:var(--shadow)}
.profile-info{text-align:center;margin-bottom:20px}
.profile-name{font-size:1.2rem;font-weight:700}
.profile-user{color:var(--muted);font-size:.85rem;margin-top:4px}
.profile-stats{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-bottom:20px}
.profile-stat{padding:14px;text-align:center}
.profile-stat .v{font-size:1.1rem;font-weight:800;background:var(--grad);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}
.profile-stat .l{font-size:.72rem;color:var(--muted);text-transform:uppercase;margin-top:3px}
.menu-item{padding:16px;display:flex;align-items:center;gap:12px;cursor:pointer;transition:.2s;margin-bottom:8px}
.menu-item:active{background:rgba(99,102,241,.1)}
.menu-item i{width:36px;height:36px;border-radius:10px;background:var(--grad);display:flex;align-items:center;justify-content:center;color:#fff}
.menu-item span{flex:1;font-weight:600}
.menu-item .chev{color:var(--muted)}
@keyframes fadeIn{from{opacity:0}to{opacity:1}}
@keyframes slideUp{from{transform:translateY(100%)}to{transform:translateY(0)}}
@keyframes slideIn{from{opacity:0;transform:translateX(-10px)}to{opacity:1;transform:translateX(0)}}
</style>
</head>
<body>
<div id="toast" class="toast">OK</div>
<div class="container">
<header>
<h1>💎 Qarz Daftar</h1>
<div class="theme-btn" onclick="toggleTheme()"><i class="fa-solid fa-circle-half-stroke"></i></div>
</header>

<div id="page-home" class="page active">
<div class="stats">
<div class="glass stat"><div class="stat-icon"><i class="fa-solid fa-wallet"></i></div><div class="stat-label">Jami berilgan</div><div class="stat-val" id="s-total">0</div></div>
<div class="glass stat"><div class="stat-icon"><i class="fa-solid fa-clock"></i></div><div class="stat-label">Qolgan qarz</div><div class="stat-val" id="s-rem" style="color:var(--danger)">0</div></div>
<div class="glass stat"><div class="stat-icon"><i class="fa-solid fa-check-circle"></i></div><div class="stat-label">Tolangan</div><div class="stat-val" id="s-paid" style="color:var(--success)">0</div></div>
<div class="glass stat"><div class="stat-icon"><i class="fa-solid fa-users"></i></div><div class="stat-label">Qarzdorlar</div><div class="stat-val" id="s-cnt">0</div></div>
</div>
<h2><i class="fa-solid fa-bolt"></i> So'nggi qarzdorlar</h2>
<div id="home-list" class="list"></div>
</div>

<div id="page-debtors" class="page">
<div class="search"><input type="text" id="q" placeholder="🔍 Ism yoki telefon..." oninput="debounceSearch()"></div>
<div class="chips">
<div class="chip active" onclick="setFilter('ALL',this)">Barchasi</div>
<div class="chip" onclick="setFilter('ACTIVE',this)">Faol</div>
<div class="chip" onclick="setFilter('OVERDUE',this)">Muddati o'tgan</div>
<div class="chip" onclick="setFilter('PAID',this)">To'langan</div>
</div>
<div id="d-list" class="list"></div>
</div>

<div id="page-stats" class="page">
<h2><i class="fa-solid fa-chart-pie"></i> Statistika</h2>
<div class="glass" style="padding:20px;margin-bottom:14px">
<div class="profile-stats">
<div class="profile-stat"><div class="v" id="p-given">0</div><div class="l">Berilgan</div></div>
<div class="profile-stat"><div class="v" id="p-paid">0</div><div class="l">Olingan</div></div>
<div class="profile-stat"><div class="v" id="p-rem">0</div><div class="l">Qolgan</div></div>
</div>
</div>
<div class="glass menu-item" onclick="exportCSV()"><i class="fa-solid fa-file-csv"></i><span>CSV eksport</span><i class="fa-solid fa-chevron-right chev"></i></div>
</div>

<div id="page-profile" class="page">
<div class="profile-avatar" id="avatar">👤</div>
<div class="profile-info">
<div class="profile-name" id="p-name">Foydalanuvchi</div>
<div class="profile-user" id="p-user">@user</div>
</div>
<div class="profile-stats">
<div class="profile-stat"><div class="v" id="pc-total">0</div><div class="l">Qarzdor</div></div>
<div class="profile-stat"><div class="v" id="pc-rem">0</div><div class="l">Qolgan</div></div>
<div class="profile-stat"><div class="v" id="pc-over">0</div><div class="l">O'tgan</div></div>
</div>
<div class="glass menu-item" onclick="toggleTheme()"><i class="fa-solid fa-palette"></i><span>Mavzu o'zgartirish</span><i class="fa-solid fa-chevron-right chev"></i></div>
<div class="glass menu-item" onclick="exportCSV()"><i class="fa-solid fa-download"></i><span>Ma'lumotlarni yuklash</span><i class="fa-solid fa-chevron-right chev"></i></div>
<div class="glass menu-item" style="color:var(--muted);font-size:.85rem;justify-content:center;cursor:default"><i class="fa-solid fa-circle-info" style="background:transparent;color:var(--muted)"></i><span>Qarz Daftar Pro v2.0</span></div>
</div>
</div>

<nav class="nav">
<div class="nav-item active" onclick="go('home',this)"><i class="fa-solid fa-house"></i>Bosh</div>
<div class="nav-item" onclick="go('debtors',this)"><i class="fa-solid fa-users"></i>Qarzdorlar</div>
<div class="nav-add" onclick="openAdd()"><i class="fa-solid fa-plus"></i></div>
<div class="nav-item" onclick="go('stats',this)"><i class="fa-solid fa-chart-line"></i>Stat</div>
<div class="nav-item" onclick="go('profile',this)"><i class="fa-solid fa-user"></i>Profil</div>
</nav>

<div id="m-add" class="modal"><div class="modal-content">
<h3>➕ Yangi qarzdor</h3>
<div class="form-group"><label>Ism familiya *</label><input id="f-name" placeholder="Akramov Jasur"></div>
<div class="form-row">
<div class="form-group"><label>Telefon</label><input id="f-phone" placeholder="+998..."></div>
<div class="form-group"><label>Summa *</label><input id="f-amt" type="number" placeholder="1000000"></div>
</div>
<div class="form-row">
<div class="form-group"><label>Muddat</label><input id="f-date" type="date"></div>
<div class="form-group"><label>Kategoriya</label><select id="f-cat"><option>Shaxsiy</option><option>Biznes</option><option>Oila</option><option>Do'st</option></select></div>
</div>
<div class="form-group"><label>Izoh</label><textarea id="f-note" rows="2" placeholder="Qo'shimcha ma'lumot..."></textarea></div>
<div style="display:flex;gap:10px;margin-top:18px">
<button class="btn btn-p" onclick="submitAdd()"><i class="fa-solid fa-check"></i> Saqlash</button>
<button class="btn btn-w" onclick="closeM('m-add')">Bekor</button>
</div>
</div></div>

<div id="m-pay" class="modal"><div class="modal-content">
<h3>💰 To'lov qo'shish</h3>
<div id="pay-info" style="padding:12px;background:var(--card);border-radius:12px;margin-bottom:16px;font-size:.9rem"></div>
<input type="hidden" id="pay-id">
<div class="form-group"><label>Summa</label><input id="pay-amt" type="number"></div>
<div class="form-group"><label>Izoh</label><input id="pay-note" placeholder="Naqd / Karta..."></div>
<div style="display:flex;gap:10px;margin-top:18px">
<button class="btn btn-p" onclick="submitPay()"><i class="fa-solid fa-check"></i> Tasdiqlash</button>
<button class="btn btn-w" onclick="closeM('m-pay')">Bekor</button>
</div>
</div></div>

<script>
const tg=window.Telegram?.WebApp;
if(tg){tg.expand();tg.ready()}
const H={'Content-Type':'application/json'};
if(tg&&tg.initData)H['X-Telegram-Init-Data']=tg.initData;
const fmt=n=>new Intl.NumberFormat('uz-UZ').format(n||0);
let filter='ALL',searchTO;

async function loadStats(){
try{
const d=await fetch('/api/stats',{headers:H}).then(r=>r.json());
document.getElementById('s-total').textContent=fmt(d.total_given);
document.getElementById('s-rem').textContent=fmt(d.total_remaining);
document.getElementById('s-paid').textContent=fmt(d.total_paid);
document.getElementById('s-cnt').textContent=d.total_debtors;
document.getElementById('p-given').textContent=fmt(d.total_given);
document.getElementById('p-paid').textContent=fmt(d.total_paid);
document.getElementById('p-rem').textContent=fmt(d.total_remaining);
document.getElementById('pc-total').textContent=d.total_debtors;
document.getElementById('pc-rem').textContent=fmt(d.total_remaining);
document.getElementById('pc-over').textContent=d.overdue_count||0;
const recent=await fetch('/api/debtors?limit=5',{headers:H}).then(r=>r.json());
document.getElementById('home-list').innerHTML=recent.length?recent.map(render).join(''):'<div class="empty"><i class="fa-solid fa-inbox"></i><div>Hali qarzdor yo\'q</div><div style="font-size:.85rem;margin-top:4px">+ tugmasini bosing</div></div>';
}catch(e){console.error(e)}
}

async function loadDebtors(){
const list=await fetch(`/api/debtors?status=${filter}&search=${encodeURIComponent(document.getElementById('q')?.value||'')}`,{headers:H}).then(r=>r.json());
const el=document.getElementById('d-list');
el.innerHTML=list.length?list.map(render,true).join(''):'<div class="empty"><i class="fa-solid fa-search"></i><div>Hech narsa topilmadi</div></div>';
}

function render(d,actions){
const today=new Date().toISOString().split('T')[0];
const soon=d.due_date&&d.due_date>today&&d.due_date<=(new Date(Date.now()+3*86400000).toISOString().split('T')[0]);
const cls=d.status==='OVERDUE'?'overdue':d.status==='PAID'?'paid':soon?'soon':'';
const bcls=d.status==='OVERDUE'?'overdue':d.status==='PAID'?'paid':soon?'soon':'';
const stTxt=d.status==='OVERDUE'?'Muddati o\\'tgan':d.status==='PAID'?'To\\'langan':soon?'Tez orada':'Faol';
const acts=actions&&d.status!=='PAID'?`<div class="card-actions"><button class="btn btn-p" onclick="openPay(${d.id},'${d.name.replace(/'/g,"\\\\'")}',${d.remaining_amount})"><i class="fa-solid fa-money-bill"></i> To'lov</button><button class="btn btn-d" onclick="delD(${d.id})"><i class="fa-solid fa-trash"></i></button></div>`:'';
return`<div class="glass card ${cls}"><div class="card-top"><div style="flex:1"><div class="card-name">${d.name}</div><div class="card-meta"><span><i class="fa-solid fa-phone"></i>${d.phone||'-'}</span>${d.due_date?`<span><i class="fa-regular fa-calendar"></i>${d.due_date}</span>`:''}</div><div class="badges"><span class="badge ${bcls}">${stTxt}</span>${d.category?`<span class="badge">${d.category}</span>`:''}</div>${acts}</div><div class="amount"><div class="amount-main">${fmt(d.remaining_amount)}</div><div class="amount-sub">Jami: ${fmt(d.total_amount)}</div></div></div></div>`;
}

function go(p,el){
document.querySelectorAll('.page').forEach(x=>x.classList.remove('active'));
document.getElementById('page-'+p).classList.add('active');
document.querySelectorAll('.nav-item').forEach(x=>x.classList.remove('active'));
if(el)el.classList.add('active');
if(p==='home')loadStats();
if(p==='debtors')loadDebtors();
if(p==='stats'||p==='profile')loadStats();
}
function openAdd(){document.getElementById('m-add').classList.add('open')}
function openPay(id,name,rem){
document.getElementById('pay-id').value=id;
document.getElementById('pay-info').innerHTML=`<b>${name}</b><br><span style="color:var(--muted)">Qolgan: <b style="color:var(--text)">${fmt(rem)} so'm</b></span>`;
document.getElementById('pay-amt').value=rem;
document.getElementById('pay-note').value='';
document.getElementById('m-pay').classList.add('open');
}
function closeM(id){document.getElementById(id).classList.remove('open')}
async function submitAdd(){
const b={name:f_name.value.trim(),phone:f_phone.value.trim(),amount:parseFloat(f_amt.value)||0,due_date:f_date.value,category:f_cat.value,note:f_note.value.trim()};
if(!b.name||!b.amount)return toast('Ism va summa majburiy!',1);
await fetch('/api/debtors',{method:'POST',headers:H,body:JSON.stringify(b)});
closeM('m-add');toast('Qarzdor qo\\'shildi');
['f-name','f-phone','f-amt','f-note','f-date'].forEach(i=>document.getElementById(i).value='');
loadStats();loadDebtors();
}
async function submitPay(){
const id=pay_id.value,amt=parseFloat(pay_amt.value)||0,note=pay_note.value;
if(amt<=0)return toast('Noto\\'g\\'ri summa',1);
const r=await fetch(`/api/debtors/${id}/pay`,{method:'PUT',headers:H,body:JSON.stringify({amount:amt,note})});
if(r.ok){closeM('m-pay');toast('To\\'lov qabul qilindi');loadStats();loadDebtors()}
else toast('Xatolik',1);
}
async function delD(id){
if(!confirm('O\\'chirilsinmi?'))return;
await fetch(`/api/debtors/${id}`,{method:'DELETE',headers:H});
toast('O\\'chirildi');loadStats();loadDebtors();
}
function setFilter(s,el){
filter=s;
document.querySelectorAll('.chip').forEach(c=>c.classList.remove('active'));
el.classList.add('active');
loadDebtors();
}
function debounceSearch(){clearTimeout(searchTO);searchTO=setTimeout(loadDebtors,300)}
function exportCSV(){window.open('/api/export/csv?initData='+(tg?.initData||''),'_blank');toast('Yuklanmoqda...')}
function toggleTheme(){
const c=document.documentElement.getAttribute('data-theme');
const n=c==='light'?'dark':'light';
document.documentElement.setAttribute('data-theme',n);
localStorage.setItem('theme',n);
}
function toast(m,err){
const t=document.getElementById('toast');
t.textContent=m;
t.style.background=err?'var(--danger)':'var(--grad)';
t.classList.add('show');
setTimeout(()=>t.classList.remove('show'),2500);
}
window.onload=()=>{
document.documentElement.setAttribute('data-theme',localStorage.getItem('theme')||'dark');
if(tg?.initDataUnsafe?.user){
const u=tg.initDataUnsafe.user;
document.getElementById('p-name').textContent=(u.first_name||'')+(u.last_name?' '+u.last_name:'');
document.getElementById('p-user').textContent='@'+(u.username||'user'+u.id);
document.getElementById('avatar').textContent=(u.first_name||'U').charAt(0).toUpperCase();
}
loadStats();
};
</script>
</body>
</html>"""


# ============ MODELLAR ============
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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            debtor_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            note TEXT,
            payment_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        await db.commit()
    logger.info("✅ Database tayyor")


# ============ AUTH ============
def verify_init_data(init_data: str) -> dict:
    if not init_data:
        raise HTTPException(401, "Telegramdan oching")
    try:
        data = dict(parse_qsl(init_data))
        h = data.pop("hash", "")
        s = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        check_str = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
        if hmac.new(s, check_str.encode(), hashlib.sha256).hexdigest() != h:
            raise HTTPException(401, "Hash xato")
        user = json.loads(data.get("user", "{}"))
        return {"telegram_id": user.get("id", 0), "username": user.get("username", "")}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(401, "Auth xato")


async def get_current_user(request: Request) -> dict:
    init_data = request.headers.get("X-Telegram-Init-Data") or request.query_params.get("initData", "")
    return verify_init_data(init_data)


# ============ FASTAPI ============
@asynccontextmanager
async def lifespan(a: FastAPI):
    await init_db()
    task = asyncio.create_task(notification_scheduler())
    logger.info("🚀 Server ishga tushdi")
    yield
    task.cancel()


app = FastAPI(title="Qarz Daftar", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(HTML_TEMPLATE)


@app.get("/api/stats")
async def stats(user: dict = Depends(get_current_user)):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("""
            SELECT COUNT(*) AS total_debtors,
                   COALESCE(SUM(total_amount),0) AS total_given,
                   COALESCE(SUM(paid_amount),0) AS total_paid,
                   COALESCE(SUM(remaining_amount),0) AS total_remaining,
                   COALESCE(SUM(CASE WHEN status='OVERDUE' THEN 1 ELSE 0 END),0) AS overdue_count
            FROM debtors WHERE user_id=?
        """, (user["telegram_id"],))
        return dict(await cur.fetchone())


@app.get("/api/debtors")
async def debtors(search: str = "", status: str = "ALL", limit: int = 50, user: dict = Depends(get_current_user)):
    q = "SELECT * FROM debtors WHERE user_id=?"
    p = [user["telegram_id"]]
    if search:
        q += " AND (name LIKE ? OR phone LIKE ?)"
        p += [f"%{search}%", f"%{search}%"]
    if status != "ALL":
        q += " AND status=?"
        p.append(status)
    q += " ORDER BY created_at DESC LIMIT ?"
    p.append(limit)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(q, p)
        return [dict(r) for r in await cur.fetchall()]


@app.post("/api/debtors")
async def add(d: DebtorCreate, user: dict = Depends(get_current_user)):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            INSERT INTO debtors (user_id,name,phone,category,note,total_amount,paid_amount,remaining_amount,due_date)
            VALUES (?,?,?,?,?,?,0,?,?)
        """, (user["telegram_id"], d.name, d.phone, d.category, d.note, d.amount, d.amount, d.due_date))
        await db.commit()
        return {"id": cur.lastrowid}


@app.put("/api/debtors/{i}/pay")
async def pay(i: int, p: PaymentCreate, user: dict = Depends(get_current_user)):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT total_amount,paid_amount FROM debtors WHERE id=? AND user_id=?",
                               (i, user["telegram_id"]))
        r = await cur.fetchone()
        if not r:
            raise HTTPException(404, "Topilmadi")
        rem = r[0] - r[1]
        if p.amount <= 0 or p.amount > rem:
            raise HTTPException(400, f"Summa xato (qolgan: {rem})")
        np = r[1] + p.amount
        nr = rem - p.amount
        st = "PAID" if nr == 0 else "ACTIVE"
        await db.execute("INSERT INTO payments (debtor_id,amount,note) VALUES (?,?,?)", (i, p.amount, p.note))
        await db.execute("UPDATE debtors SET paid_amount=?,remaining_amount=?,status=? WHERE id=?", (np, nr, st, i))
        await db.commit()
        return {"remaining": nr}


@app.delete("/api/debtors/{i}")
async def delete(i: int, user: dict = Depends(get_current_user)):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM payments WHERE debtor_id=?", (i,))
        await db.execute("DELETE FROM debtors WHERE id=? AND user_id=?", (i, user["telegram_id"]))
        await db.commit()
        return {"ok": True}


@app.get("/api/export/csv")
async def export_csv(user: dict = Depends(get_current_user)):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            SELECT name,phone,total_amount,paid_amount,remaining_amount,status,due_date
            FROM debtors WHERE user_id=?
        """, (user["telegram_id"],))
        rows = await cur.fetchall()
    lines = ["Ism;Telefon;Jami;Tolangan;Qolgan;Holat;Muddat"]
    for r in rows:
        lines.append(";".join("" if x is None else str(x) for x in r))
    return Response("\n".join(lines), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=qarzlar.csv"})


# ============ ESLATMALAR ============
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
        await db.execute("""
            UPDATE debtors SET status='OVERDUE'
            WHERE status='ACTIVE' AND remaining_amount>0
              AND due_date IS NOT NULL AND due_date!='' AND due_date<?
        """, (today.isoformat(),))
        cur = await db.execute("""
            SELECT user_id,name,remaining_amount,due_date,status FROM debtors
            WHERE remaining_amount>0 AND due_date IS NOT NULL AND due_date!=''
              AND ((status='OVERDUE' AND due_date=?)
                   OR (status='ACTIVE' AND due_date BETWEEN ? AND ?))
        """, ((today - timedelta(days=1)).isoformat(),
              today.isoformat(), (today + timedelta(days=3)).isoformat()))
        rows = await cur.fetchall()
        await db.commit()
    for uid, name, rem, dt, st in rows:
        text = (f"⚠️ <b>MUDDATI O'TDI!</b>\n👤 {name}\n💰 {rem:,.0f} so'm\n📅 {dt}"
                if st == "OVERDUE" else
                f"⏳ <b>Eslatma</b>\n👤 {name}\n💰 {rem:,.0f} so'm\n📅 Muddat: {dt}")
        try:
            await bot.send_message(uid, text)
        except Exception as e:
            logger.warning(f"Xabar xatosi: {e}")


# ============ BOT ============
@dp.message(CommandStart())
async def start(m: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📒 Ilovani ochish", web_app=WebAppInfo(url=WEBAPP_URL))]])
    await m.answer(
        "<b>💎 Qarz Daftar Pro</b>\n\n"
        "✅ Qarzdorlar ro'yxati\n"
        "✅ To'lovlar va statistika\n"
        "✅ Avtomatik eslatmalar\n"
        "✅ Chiroyli dizayn\n\n"
        "Tugmani bosing 👇",
        reply_markup=kb
    )


@dp.message(Command("app"))
async def appcmd(m: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📒 Ilovani ochish", web_app=WebAppInfo(url=WEBAPP_URL))]])
    await m.answer("Mini App:", reply_markup=kb)


# ============ START ============
def run_bot():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(dp.start_polling(bot))


if __name__ == "__main__":
    threading.Thread(target=run_bot, daemon=True).start()
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")