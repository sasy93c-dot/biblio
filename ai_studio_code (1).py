import os
import re
import io
import time
import base64
import random
import sqlite3
import requests
import pandas as pd
import streamlit as st
from datetime import datetime, date

# =============================================================
# CONFIGURAZIONE PAGINA
# =============================================================
st.set_page_config(
    page_title="BookNook - La Mia Tana dei Libri",
    layout="wide",
    page_icon="📖",
    initial_sidebar_state="expanded"
)

DB_FILE = "mia_libreria.db"
STATI = ["Da Iniziare ⏳", "In Lettura 📖", "Letti ✨", "Abbandonati 💤"]
MOODS = [
    "☕ Tisana, coperta & pioggia",
    "😭 Distruzione emotiva garantita",
    "🧠 Cervellotico / Mi farà riflettere",
    "⚡ Colpi di scena adrenalinici",
    "🏖️ Leggero da ombrellone",
    "🪐 Viaggio in altri mondi",
]
LIVELLI = [
    (0, "🌱 Lettore Novizio"),
    (200, "📖 Divoratore di Pagine"),
    (600, "🦉 Gufo Notturno"),
    (1500, "🧙 Mago degli Scaffali"),
    (3500, "🐉 Custode dei Draghi"),
    (7000, "👑 Leggenda della Biblioteca"),
]

# =============================================================
# DATABASE SQLITE
# =============================================================
def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS miei_libri (
                    isbn TEXT PRIMARY KEY,
                    titolo TEXT,
                    autore TEXT,
                    copertina_url TEXT,
                    pagine INTEGER,
                    stato TEXT,
                    voto INTEGER,
                    mood TEXT,
                    citazione TEXT,
                    data_aggiunta TEXT,
                    pagina_corrente INTEGER DEFAULT 0,
                    generi TEXT,
                    trama TEXT,
                    data_fine TEXT)''')
    c.execute("CREATE TABLE IF NOT EXISTS impostazioni (chiave TEXT PRIMARY KEY, valore TEXT)")
    conn.commit()
    conn.close()

init_db()

def get_setting(c, chiave, default):
    r = c.execute("SELECT valore FROM impostazioni WHERE chiave = ?", (chiave,)).fetchone()
    return int(r["valore"]) if r else default

def set_setting(c, conn, chiave, valore):
    c.execute("INSERT OR REPLACE INTO impostazioni (chiave, valore) VALUES (?, ?)", (chiave, str(valore)))
    conn.commit()

def inserisci_libro(c, conn, isbn, titolo, autore="", copertina="", pagine=0, stato="Da Iniziare ⏳",
                    voto=0, mood="", citazione="", generi="", trama=""):
    fine = str(date.today()) if stato == "Letti ✨" else None
    corrente = int(pagine or 0) if stato == "Letti ✨" else 0
    c.execute("""INSERT INTO miei_libri (isbn, titolo, autore, copertina_url, pagine, stato, voto, mood, citazione,
                                         data_aggiunta, pagina_corrente, generi, trama, data_fine)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
              (isbn, titolo.strip(), (autore or "").strip(), copertina or "", int(pagine or 0), stato, voto, mood,
               citazione, str(date.today()), corrente, generi or "", trama or "", fine))
    conn.commit()

# =============================================================
# RICERCA ISBN ONLINE (Google Books + OpenLibrary)
# =============================================================
HEADERS = {"User-Agent": "BookNookApp/3.0"}

def pulisci_isbn(s):
    return re.sub(r"[^0-9Xx]", "", str(s) if s else "").upper()

def _isbn13(i):
    if len(i) != 10:
        return i
    base = "978" + i[:9]
    ctrl = (10 - sum(int(ch) * (1 if n % 2 == 0 else 3) for n, ch in enumerate(base)) % 10) % 10
    return base + str(ctrl)

def _cover_valida(url):
    try:
        r = requests.head(url, headers=HEADERS, timeout=5, allow_redirects=True)
        return r.ok and "image" in r.headers.get("content-type", "")
    except Exception:
        return False

def _google(q, isbn):
    params = {"q": q, "maxResults": 5, "country": "IT"}
    try:
        if "GOOGLE_BOOKS_KEY" in st.secrets:
            params["key"] = st.secrets["GOOGLE_BOOKS_KEY"]
    except Exception:
        pass
    try:
        r = requests.get("https://www.googleapis.com/books/v1/volumes", params=params, headers=HEADERS, timeout=8)
        if not r.ok:
            return None
        items = r.json().get("items") or []
        scelto = None
        for it in items:
            ids = [x.get("identifier", "") for x in it["volumeInfo"].get("industryIdentifiers", [])]
            if isbn in ids:
                scelto = it
                break
        if not scelto and items and q.startswith("isbn:"):
            scelto = items[0]
        if not scelto:
            return None
        v = scelto["volumeInfo"]
        cover = (v.get("imageLinks") or {}).get("thumbnail", "").replace("http://", "https://").replace("&edge=curl", "")
        return {
            "titolo": v.get("title", ""),
            "autore": ", ".join(v.get("authors", [])),
            "copertina": cover,
            "pagine": v.get("pageCount") or 0,
            "generi": ", ".join(v.get("categories", [])[:3]),
            "trama": (v.get("description") or "")[:800]
        }
    except Exception:
        return None

def _openlibrary(isbn):
    try:
        url = f"https://openlibrary.org/api/books?bibkeys=ISBN:{isbn}&format=json&jscmd=data"
        r = requests.get(url, headers=HEADERS, timeout=8)
        if not r.ok:
            return None
        data = r.json().get(f"ISBN:{isbn}")
        if not data:
            return None
        cov = (data.get("cover") or {}).get("large") or (data.get("cover") or {}).get("medium", "")
        return {
            "titolo": data.get("title", ""),
            "autore": ", ".join(a["name"] for a in data.get("authors", [])),
            "copertina": cov,
            "pagine": data.get("number_of_pages") or 0,
            "generi": ", ".join(s["name"] for s in data.get("subjects", [])[:3]),
            "trama": ""
        }
    except Exception:
        return None

def cerca_info_libro(isbn_grezzo):
    isbn = pulisci_isbn(isbn_grezzo)
    if not isbn:
        return None
    codici = [isbn]
    i13 = _isbn13(isbn)
    if i13 not in codici:
        codici.append(i13)

    totale = {"isbn": isbn, "titolo": "", "autore": "", "copertina": "", "pagine": 0, "generi": "", "trama": ""}

    for cod in codici:
        for f in (lambda: _google(f"isbn:{cod}", cod), lambda: _openlibrary(cod), lambda: _google(cod, cod)):
            try:
                res = f()
                if res:
                    for k in ("titolo", "autore", "copertina", "pagine", "generi", "trama"):
                        if not totale[k] and res.get(k):
                            totale[k] = res[k]
                if totale["titolo"] and totale["copertina"] and totale["pagine"]:
                    break
            except Exception:
                continue
        if totale["titolo"]:
            break

    if not totale["titolo"]:
        return None

    if not totale["copertina"]:
        alt = f"https://covers.openlibrary.org/b/isbn/{isbn}-M.jpg?default=false"
        if _cover_valida(alt):
            totale["copertina"] = alt

    return totale

# =============================================================
# DECODIFICA CODICI A BARRE POTENZIATA (ROBUSTA)
# =============================================================
def decodifica_barcode(img_bytes):
    """Analizza l'immagine applicando correzioni di contrasto, nitidezza e rotazioni."""
    try:
        from PIL import Image, ImageOps, ImageEnhance
    except ImportError:
        return None, "Libreria Pillow non installata."

    has_zxing = False
    try:
        import zxingcpp
        has_zxing = True
    except ImportError:
        pass

    has_pyzbar = False
    try:
        from pyzbar import pyzbar
        has_pyzbar = True
    except ImportError:
        pass

    if not has_zxing and not has_pyzbar:
        return None, "ERRORE_LIBRERIE"

    try:
        raw_img = Image.open(io.BytesIO(img_bytes))
        img = ImageOps.exif_transpose(raw_img).convert("RGB")
    except Exception as e:
        return None, f"Errore apertura immagine: {e}"

    # Ridimensionamento intelligente per evitare immagini giganti o minuscole
    img.thumbnail((2000, 2000))
    w, h = img.size
