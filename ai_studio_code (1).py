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
import streamlit.components.v1 as components
from datetime import datetime, date

# =============================================================
# CONFIGURAZIONE PAGINA
# =============================================================
st.set_page_config(
    page_title="BookNook - La Mia Tana dei Libri",
    layout="wide",
    page_icon="📖",
    initial_sidebar_state="collapsed"
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
HEADERS = {"User-Agent": "BookNookMobile/2.0"}

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
        r = requests.head(url, headers=HEADERS, timeout=4, allow_redirects=True)
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
        r = requests.get("https://www.googleapis.com/books/v1/volumes", params=params, headers=HEADERS, timeout=7)
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
        r = requests.get(url, headers=HEADERS, timeout=7)
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

def mostra_cover(src, **kw):
    try:
        if src and src.startswith("data:image"):
            st.image(base64.b64decode(src.split(",", 1)[1]), **kw)
        elif src:
            st.image(src, **kw)
        else:
            st.caption("📕 Nessuna copertina")
    except Exception:
        st.caption("📕 Copertina non disponibile")

# =============================================================
# STILE INTERFACCIA
# =============================================================
st.markdown("""
<style>
  .hero{background:linear-gradient(135deg,#7b2ff7 0%,#f107a3 55%,#ff9a3c 100%);color:#fff;border-radius:18px;
        padding:20px;margin-bottom:18px;box-shadow:0 8px 24px rgba(123,47,247,.25)}
  .hero h1{margin:0;font-size:1.7rem;color:#fff}
  .hero p{margin:.3rem 0 0;font-size:0.95rem;opacity:.95;color:#fff}
  .quote{border-left:5px solid #ffcf5c;padding:12px 16px;border-radius:0 14px 14px 0;
         background:rgba(255,207,92,.12);font-style:italic}
  div[data-testid="stMetric"]{background:rgba(179,107,255,.09);border:1px solid rgba(179,107,255,.22);
         padding:10px 14px;border-radius:14px}
  .stButton>button{border-radius:12px;font-weight:600}
  .badge-on{background:linear-gradient(135deg,rgba(255,207,92,.22),rgba(241,7,163,.18));
            border:1px solid rgba(255,207,92,.65);border-radius:14px;padding:10px;text-align:center;margin-bottom:8px}
  .badge-off{background:rgba(255,255,255,.03);border:1px dashed rgba(255,255,255,.2);
             border-radius:14px;padding:10px;text-align:center;opacity:.55;margin-bottom:8px}
</style>
""", unsafe_allow_html=True)

# =============================================================
# CALCOLO LIVELLO, XP E TROFEI
# =============================================================
def calcola_xp(c):
    rows = c.execute("SELECT stato, pagine, pagina_corrente, citazione FROM miei_libri").fetchall()
    xp = 0
    for r in rows:
        if r["stato"] == "Letti ✨":
            xp += (r["pagine"] or 0) + 50
        elif r["stato"] == "In Lettura 📖":
            xp += (r["pagina_corrente"] or 0)
        if r["citazione"]:
            xp += 15
    return xp

def livello(xp):
    idx = 0
    for i, (soglia, _) in enumerate(LIVELLI):
        if xp >= soglia:
            idx = i
    nome = LIVELLI[idx][1]
    if idx + 1 < len(LIVELLI):
        prossimo = LIVELLI[idx + 1]
        base = LIVELLI[idx][0]
        frac = (xp - base) / (prossimo[0] - base)
        return nome, min(max(frac, 0.0), 1.0), prossimo[0] - xp, prossimo[1]
    return nome, 1.0, 0, None

def statistiche(c):
    letti = c.execute("SELECT COUNT(*) n, COALESCE(SUM(pagine),0) p FROM miei_libri WHERE stato = 'Letti ✨'").fetchone()
    tot = c.execute("SELECT COUNT(*) FROM miei_libri").fetchone()[0]
    mood_letti = c.execute("SELECT COUNT(DISTINCT mood) FROM miei_libri WHERE stato = 'Letti ✨' AND mood != ''").fetchone()[0]
    cit = c.execute("SELECT COUNT(*) FROM miei_libri WHERE citazione IS NOT NULL AND citazione != ''").fetchone()[0]
    cinque = c.execute("SELECT COUNT(*) FROM miei_libri WHERE voto = 5").fetchone()[0]
    grosso = c.execute("SELECT COUNT(*) FROM miei_libri WHERE stato = 'Letti ✨' AND pagine >= 500").fetchone()[0]
    return {
        "letti": letti["n"], "pagine": letti["p"], "totale": tot,
        "mood": mood_letti, "citazioni": cit, "cinque": cinque, "mattoni": grosso
    }

TROFEI = [
    ("🌱", "Primo Passo", "Finisci il tuo 1° libro", lambda s: s["letti"] >= 1),
    ("📚", "Topo di Biblioteca", "Finisci 5 libri", lambda s: s["letti"] >= 5),
    ("🐲", "Signore dei Draghi", "Finisci 15 libri", lambda s: s["letti"] >= 15),
    ("⚡", "Macina-Pagine", "Leggi 1.000 pagine", lambda s: s["pagine"] >= 1000),
    ("🏔️", "Alpinista di Pagine", "Leggi 5.000 pagine", lambda s: s["pagine"] >= 5000),
    ("🧱", "Domatore di Mattoni", "Finisci un libro da 500+ pag.", lambda s: s["mattoni"] >= 1),
    ("🧭", "Esploratore di Vibes", "Libri con 3 mood diversi", lambda s: s["mood"] >= 3),
    ("💬", "Collezionista di Frasi", "Salva 5 citazioni", lambda s: s["citazioni"] >= 5),
    ("🌟", "Cuore a 5 Stelle", "Assegna 5 stelle a un libro", lambda s: s["cinque"] >= 1),
    ("🏛️", "Bibliotecario", "Cataloga almeno 25 volumi", lambda s: s["totale"] >= 25),
]

# =============================================================
# CONNESSIONE E SIDEBAR
# =============================================================
conn = get_db()
c = conn.cursor()

xp_tot = calcola_xp(c)
lv_nome, lv_frac, lv_mancano, lv_prox = livello(xp_tot)

st.sidebar.title("☕ BookNook")
st.sidebar.caption("Tana dei Libri")
st.sidebar.markdown(f"**{lv_nome}**")
st.sidebar.progress(lv_frac)
st.sidebar.caption(f"⭐ **{xp_tot} XP**" + (f" · Mancano {lv_mancano} XP per {lv_prox}" if lv_prox else " · Livello Max!"))

scelta = st.sidebar.radio("Menu:", [
    "⚡ Scansione Veloce",
    "🏠 Il Mio Rifugio",
    "🛋️ La Mia Libreria",
    "➕ Aggiungi a Mano",
    "🔮 L'Oracolo",
    "📊 Statistiche",
    "🏆 Sfide & Trofei",
])

# =============================================================
# 1. SCANSIONE VELOCE (OTTIMIZZATA PER CODICI EAN-13)
# =============================================================
if scelta == "⚡ Scansione Veloce":
    st.markdown('<div class="hero"><h1>⚡ Scansione Posteriore</h1>'
                '<p>Tieni il codice a barre dentro il riquadro giallo (a 15-20 cm). Salva da sola senza toccare nulla.</p></div>',
                unsafe_allow_html=True)

    for k, default in [("log_scan", []), ("non_trovati", [])]:
        if k not in st.session_state:
            st.session_state[k] = default

    stato_raffica = st.selectbox("Salva nuovi libri come:", STATI, index=0)

    # Cattura ISBN reindirizzato
    isbn_catturato = None
    try:
        if "isbn_auto" in st.query_params:
            isbn_catturato = pulisci_isbn(st.query_params["isbn_auto"])
            st.query_params.clear()
    except Exception:
        pass

    # SCANNER HTML5 SPECIFICO PER CODICI A BARRE (con Viewfinder e Html5Qrcode)
    HTML_SCANNER_EAN13 = """
    <!DOCTYPE html>
    <html>
    <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
    <!-- Libreria specializzata in codici a barre per mobile -->
    <script src="https://unpkg.com/html5-qrcode@2.3.8/html5-qrcode.min.js"></script>
    <style>
      body { margin: 0; background: transparent; font-family: -apple-system, system-ui, sans-serif; color: #fff; }
      #reader-box { background: #0c081c; border-radius: 18px; overflow: hidden; box-shadow: 0 8px 24px rgba(0,0,0,0.5); border: 1px solid rgba(255,255,255,0.12); }
      #reader { width: 100%; min-height: 350px; background: #000; }
      #start-panel { text-align: center; padding: 30px 15px; }
      .btn-go { background: linear-gradient(135deg,#7b2ff7,#f107a3); color: #fff; border: none; padding: 15px 30px; border-radius: 14px; font-size: 16px; font-weight: bold; cursor: pointer; box-shadow: 0 4px 15px rgba(123,47,247,0.4); }
      #status-info { padding: 12px; background: #1c152d; font-size: 13px; text-align: center; border-left: 4px solid #7b2ff7; }
      /* Stile del viewfinder mirino */
      #reader__scan_region { border: 3px solid #ffcf5c !important; border-radius: 12px !important; }
      #reader__dashboard_section_csr button { background: #2b2342; color: #fff; border: 1px solid #7b2ff7; padding: 6px 12px; border-radius: 8px; }
    </style>
    </head>
    <body>
    <div id="reader-box">
      <div id="start-panel">
        <div style="font-size:3.5rem;margin-bottom:10px;">📷</div>
        <h3 style="margin:0 0 8px;">Scanner Posteriore EAN-13</h3>
        <p style="margin:0 0 18px;color:#bbb;font-size:13px;">Inquadra il codice a barre sul retro del libro a 15-20 cm di distanza.</p>
        <button id="btn-activate" class="btn-go">▶️ Avvia Fotocamera Posteriore</button>
      </div>

      <div id="reader" style="display:none;"></div>
      <div id="status-info">In attesa di avvio scanner...</div>
    </div>

    <script>
    (function(){
      var startPanel = document.getElementById("start-panel");
      var readerDiv = document.getElementById("reader");
      var statusInfo = document.getElementById("status-info");
      var btnActivate = document.getElementById("btn-activate");
      var html5QrCode = null;
      var lastCode = "";
      var lastTime = 0;

      function setStatus(t, err) {
        statusInfo.textContent = t;
        statusInfo.style.borderLeftColor = err ? "#ff4d6d" : "#7b2ff7";
      }

      function beep() {
        try {
          var ctx = new (window.AudioContext || window.webkitAudioContext)();
          var o = ctx.createOscillator();
          o.type = "sine";
          o.frequency.value = 950;
          o.connect(ctx.destination);
          o.start();
          setTimeout(function(){ o.stop(); ctx.close(); }, 140);
        } catch(e){}
      }

      function onBarcodeFound(decodedText) {
        var clean = String(decodedText).replace(/[^0-9Xx]/g, "").toUpperCase();
        if (!((clean.length === 13 && /^97[89]/.test(clean)) || clean.length === 10)) return;

        var now = Date.now();
        if (clean === lastCode && (now - lastTime) < 4500) return;
        lastCode = clean;
        lastTime = now;

        beep();
        if (navigator.vibrate) navigator.vibrate(140);
        setStatus("✅ Codice ISBN Rilevato: " + clean + " — Salvataggio in corso...", false);

        if (html5QrCode) {
          try { html5QrCode.pause(); } catch(e){}
        }

        try {
          var pUrl = new URL(window.parent.location.href);
          pUrl.searchParams.set("isbn_auto", clean);
          window.parent.location.href = pUrl.href;
        } catch(e) {
          window.parent.location.search = "?isbn_auto=" + clean;
        }
      }

      async function startScanner() {
        startPanel.style.display = "none";
        readerDiv.style.display = "block";
        setStatus("Avvio fotocamera posteriore con mirino laser...", false);

        html5QrCode = new Html5Qrcode("reader");

        // Configuriamo il mirino specifico per codici a barre orizzontali (280x140px)
        var config = {
          fps: 20,
          qrbox: { width: 280, height: 140 },
          aspectRatio: 1.0,
          formatsToSupport: [
            Html5QrcodeSupportedFormats.EAN_13,
            Html5QrcodeSupportedFormats.EAN_8,
            Html5QrcodeSupportedFormats.UPC_A,
            Html5QrcodeSupportedFormats.UPC_E,
            Html5QrcodeSupportedFormats.CODE_128
          ]
        };

        try {
          // Ricerca specifica della fotocamera posteriore tra i dispositivi
          var devices = await Html5Qrcode.getCameras();
          var targetCameraId = null;

          if (devices && devices.length > 0) {
            for (var i = 0; i < devices.length; i++) {
              var lbl = (devices[i].label || "").toLowerCase();
              if (lbl.includes("back") || lbl.includes("rear") || lbl.includes("posteriore") || lbl.includes("environment") || lbl.includes("0")) {
                targetCameraId = devices[i].id;
                break;
              }
            }
            if (!targetCameraId && devices.length > 1) {
              targetCameraId = devices[devices.length - 1].id;
            }
          }

          var cameraSetting = targetCameraId ? targetCameraId : { facingMode: "environment" };

          await html5QrCode.start(
            cameraSetting,
            config,
            onBarcodeFound,
            function(err) { /* Scansione fotogramma in corso */ }
          );

          setStatus("🟢 Inquadra il codice a barre dentro il riquadro giallo (distanza 15-20 cm).", false);

        } catch(err) {
          setStatus("Errore avvio fotocamera: " + err + ". Verifica i permessi o la connessione HTTPS.", true);
        }
      }

      btnActivate.addEventListener("click", startScanner);
    })();
    </script>
    </body>
    </html>
    """

    components.html(HTML_SCANNER_EAN13, height=440)

    st.caption("💡 **Consiglio di lettura su smartphone**:")
    st.caption("- Tieni il libro a circa **15-20 cm** dall'obiettivo (se sei troppo vicino, la fotocamera non riesce a mettere a fuoco).")
    st.caption("- Posiziona le barre nere orizzontali e ben illuminate dentro il rettangolo.")

    # Inserimento manuale rapido / barcode gun
    with st.expander("⌨️ Oppure digita l'ISBN a mano:"):
        with st.form("form_pistola", clear_on_submit=True):
            input_pistola = st.text_input("Codice ISBN:", placeholder="Es. 9788804668237")
            if st.form_submit_button("Aggiungi Subito") and input_pistola.strip():
                isbn_catturato = pulisci_isbn(input_pistola)

    # Processamento automatico libro rilevato
    if isbn_catturato:
        isbn = isbn_catturato
        esiste = c.execute("SELECT titolo FROM miei_libri WHERE isbn = ?", (isbn,)).fetchone()
        if esiste:
            st.toast(f"Già presente: {esiste['titolo']}", icon="📚")
            st.session_state.log_scan.insert(0, f"📚 Già presente: **{esiste['titolo']}** ({isbn})")
        else:
            with st.spinner(f"Cerco informazioni per ISBN {isbn}..."):
                info = cerca_info_libro(isbn)
            if info:
                inserisci_libro(c, conn, info["isbn"], info["titolo"], info["autore"], info["copertina"],
                                info["pagine"], stato_raffica, generi=info.get("generi", ""),
                                trama=info.get("trama", ""))
                pg_str = f"{info['pagine']} pag." if info["pagine"] else "pagine non indicate"
                st.toast(f"Aggiunto: {info['titolo']}", icon="✅")
                st.session_state.log_scan.insert(0, f"✅ **{info['titolo']}** — {info['autore']} ({pg_str})")
            else:
                if isbn not in st.session_state.non_trovati:
                    st.session_state.non_trovati.append(isbn)
                st.toast(f"ISBN {isbn} non trovato online", icon="⚠️")
                st.session_state.log_scan.insert(0, f"⚠️ ISBN {isbn} non trovato online: salvalo qui sotto")

    st.subheader("📋 Registro Scansioni Recenti")
    if st.session_state.log_scan:
        for riga in st.session_state.log_scan[:6]:
            st.markdown(riga)
    else:
        st.caption("I libri scansionati compariranno qui in tempo reale.")

    for isbn_mancante in list(st.session_state.non_trovati):
        with st.expander(f"⚠️ Salva manualmente: {isbn_mancante}", expanded=True):
            nt = st.text_input("Titolo del volume", key=f"nt_{isbn_mancante}")
            na = st.text_input("Autore", key=f"na_{isbn_mancante}")
            np = st.number_input("Pagine", min_value=0, key=f"np_{isbn_mancante}")
            if st.button("Salva nella Libreria", key=f"ns_{isbn_mancante}"):
                if nt.strip():
                    try:
                        inserisci_libro(c, conn, isbn_mancante, nt, na, "", np, stato_raffica)
                    except sqlite3.IntegrityError:
                        pass
                    st.session_state.non_trovati.remove(isbn_mancante)
                    st.rerun()
                else:
                    st.error("Il titolo è obbligatorio.")

# =============================================================
# 2. IL MIO RIFUGIO (HOME)
# =============================================================
elif scelta == "🏠 Il Mio Rifugio":
    ora = datetime.now().hour
    saluto = "Buongiorno" if ora < 12 else "Buon pomeriggio" if ora < 18 else "Buonasera"
    st.markdown(f'<div class="hero"><h1>{saluto}, lettore! 📖</h1>'
                f'<p>{lv_nome} · {xp_tot} Punti Esperienza</p></div>', unsafe_allow_html=True)

    s = statistiche(c)
    in_corso_n = c.execute("SELECT COUNT(*) FROM miei_libri WHERE stato = 'In Lettura 📖'").fetchone()[0]
    m1, m2 = st.columns(2)
    m1.metric("Libri Catalogati 📚", s["totale"])
    m2.metric("Completati ✨", s["letti"])
    m3, m4 = st.columns(2)
    m3.metric("In Lettura Ora 📖", in_corso_n)
    m4.metric("Pagine Lette 📄", s["pagine"])

    st.subheader("📖 Attualmente In Lettura")
    in_lettura = c.execute("SELECT * FROM miei_libri WHERE stato = 'In Lettura 📖' ORDER BY rowid DESC").fetchall()
    if not in_lettura:
        st.info("Non stai leggendo nessun libro al momento. Scegline uno dalla libreria o chiedi a **L'Oracolo** 🔮!")
    else:
        for b in in_lettura:
            with st.container(border=True):
                ci, cd = st.columns([1, 4])
                with ci:
                    if b["copertina_url"]:
                        mostra_cover(b["copertina_url"], use_container_width=True)
                with cd:
                    pag = b["pagine"] or 0
                    cur = b["pagina_corrente"] or 0
                    st.markdown(f"### {b['titolo']}")
                    st.caption(f"di **{b['autore']}**")
                    if pag > 0:
                        prog = min(cur / pag, 1.0)
                        st.progress(prog, text=f"Pag. {cur} di {pag} ({int(prog * 100)}%)")
                    else:
                        st.caption(f"Pagina {cur}")

                    b1, b2, b3 = st.columns(3)
                    if b1.button("+10 pag.", key=f"p10_{b['isbn']}"):
                        nuovo = cur + 10
                        if pag:
                            nuovo = min(nuovo, pag)
                        c.execute("UPDATE miei_libri SET pagina_corrente = ? WHERE isbn = ?", (nuovo, b["isbn"]))
                        conn.commit()
                        st.rerun()
                    if b2.button("+25 pag.", key=f"p25_{b['isbn']}"):
                        nuovo = cur + 25
                        if pag:
                            nuovo = min(nuovo, pag)
                        c.execute("UPDATE miei_libri SET pagina_corrente = ? WHERE isbn = ?", (nuovo, b["isbn"]))
                        conn.commit()
                        st.rerun()
                    if b3.button("🏁 Finito!", key=f"fin_{b['isbn']}"):
                        c.execute("UPDATE miei_libri SET stato = 'Letti ✨', pagina_corrente = ?, data_fine = ? WHERE isbn = ?",
                                  (pag or cur, str(date.today()), b["isbn"]))
                        conn.commit()
                        st.balloons()
                        st.toast("Complimenti!", icon="🏆")
                        st.rerun()

    st.subheader("💬 Citazione del Giorno")
    citazioni = c.execute("SELECT citazione, titolo FROM miei_libri WHERE citazione IS NOT NULL AND citazione != ''").fetchall()
    if citazioni:
        q = random.Random(str(date.today())).choice(citazioni)
        st.markdown(f'<div class="quote">“{q["citazione"]}”<br><br><small>— Da <b>{q["titolo"]}</b></small></div>', unsafe_allow_html=True)
    else:
        st.caption("Salva qualche citazione dai tuoi libri preferiti per vederla apparire qui ogni giorno.")

# =============================================================
# 3. LA MIA LIBRERIA
# =============================================================
elif scelta == "🛋️ La Mia Libreria":
    st.markdown('<div class="hero"><h1>🛋️ I Miei Scaffali</h1><p>Tutti i tuoi volumi</p></div>', unsafe_allow_html=True)

    filtro_stato = st.selectbox("Filtra per stato:", ["Tutti"] + STATI)
    cerca = st.text_input("🔎 Cerca titolo o autore:")

    sql = "SELECT * FROM miei_libri WHERE 1=1"
    params = []
    if filtro_stato != "Tutti":
        sql += " AND stato = ?"
        params.append(filtro_stato)
    if cerca:
        sql += " AND (titolo LIKE ? OR autore LIKE ?)"
        params += [f"%{cerca}%", f"%{cerca}%"]

    libri = c.execute(sql + " ORDER BY rowid DESC", params).fetchall()

    if not libri:
        st.info("Nessun libro trovato.")
    else:
        for b in libri:
            isbn = b["isbn"]
            voto = b["voto"] or 0
            pag = b["pagine"] or 0
            with st.container(border=True):
                c_img, c_txt = st.columns([1, 3])
                with c_img:
                    if b["copertina_url"]:
                        mostra_cover(b["copertina_url"], use_container_width=True)
                with c_txt:
                    st.subheader(b["titolo"])
                    st.caption(f"✍️ {b['autore']}")
                    st.write(f"**{b['stato']}**")
                    if voto > 0:
                        st.write("⭐" * voto)
                    if pag > 0:
                        st.caption(f"📄 {pag} pag.")
                    if b["mood"]:
                        st.caption(f"🏷️ `{b['mood']}`")

                with st.expander("✏️ Modifica"):
                    idx_stato = STATI.index(b["stato"]) if b["stato"] in STATI else 0
                    n_stato = st.selectbox("Stato", STATI, index=idx_stato, key=f"st_{isbn}")
                    n_pag = st.number_input("Pagine totali", min_value=0, value=pag, key=f"pg_{isbn}")
                    n_voto = st.slider("Voto", 0, 5, voto, key=f"vt_{isbn}")
                    n_cit = st.text_area("Citazione memorabile", value=b["citazione"] or "", key=f"ct_{isbn}")
                    
                    bs1, bs2 = st.columns(2)
                    if bs1.button("💾 Salva", key=f"save_{isbn}"):
                        fine = b["data_fine"]
                        if n_stato == "Letti ✨":
                            fine = fine or str(date.today())
                        c.execute("UPDATE miei_libri SET stato=?, voto=?, citazione=?, pagine=?, data_fine=? WHERE isbn=?",
                                  (n_stato, n_voto, n_cit, n_pag, fine, isbn))
                        conn.commit()
                        st.success("Modifiche salvate!")
                        st.rerun()
                    if bs2.button("🗑️ Rimuovi", key=f"del_{isbn}"):
                        c.execute("DELETE FROM miei_libri WHERE isbn = ?", (isbn,))
                        conn.commit()
                        st.rerun()

# =============================================================
# 4. AGGIUNGI A MANO
# =============================================================
elif scelta == "➕ Aggiungi a Mano":
    st.markdown('<div class="hero"><h1>✨ Aggiungi Volume</h1><p>Compilazione manuale o ricerca per ISBN</p></div>', unsafe_allow_html=True)

    isbn_digitato = st.text_input("Codice ISBN:", placeholder="Es. 9788804668237")
    if st.button("🔍 Cerca Informazioni Online"):
        if isbn_digitato.strip():
            with st.spinner("Ricerca nei cataloghi online..."):
                trovato = cerca_info_libro(isbn_digitato)
            if trovato:
                st.session_state["m_tit"] = trovato["titolo"]
                st.session_state["m_aut"] = trovato["autore"]
                st.session_state["m_cov"] = trovato["copertina"]
                st.session_state["m_pag"] = int(trovato["pagine"] or 0)
                st.session_state["m_gen"] = trovato.get("generi", "")
                st.session_state["m_tra"] = trovato.get("trama", "")
                st.success("Dati libro trovati!")
            else:
                st.warning("Nessun libro trovato con questo codice.")

    titolo = st.text_input("Titolo:", value=st.session_state.get("m_tit", ""))
    autore = st.text_input("Autore:", value=st.session_state.get("m_aut", ""))
    pagine = st.number_input("Pagine:", min_value=0, value=st.session_state.get("m_pag", 0))
    stato = st.selectbox("Stato:", STATI)
    mood = st.selectbox("Vibes:", MOODS)
    copertina = st.text_input("URL Copertina:", value=st.session_state.get("m_cov", ""))

    if st.button("📥 Salva nella Libreria", type="primary"):
        if not titolo.strip():
            st.error("Il titolo è obbligatorio!")
        else:
            isbn_finale = pulisci_isbn(isbn_digitato) or f"BOOK-{random.randint(100000, 999999)}"
            try:
                inserisci_libro(c, conn, isbn_finale, titolo, autore, copertina, pagine, stato, 0, mood,
                                "", st.session_state.get("m_gen", ""), st.session_state.get("m_tra", ""))
                st.success(f"«{titolo}» è stato aggiunto alla tua libreria!")
                st.balloons()
            except sqlite3.IntegrityError:
                st.error("Questo libro è già registrato nel database!")

# =============================================================
# 5. L'ORACOLO
# =============================================================
elif scelta == "🔮 L'Oracolo":
    st.markdown('<div class="hero"><h1>🔮 L\'Oracolo dei Libri</h1><p>Lascia che il destino scelga la tua prossima lettura.</p></div>', unsafe_allow_html=True)
    pila = c.execute("SELECT * FROM miei_libri WHERE stato = 'Da Iniziare ⏳'").fetchall()
    if not pila:
        st.warning("Nessun libro nella pila 'Da Iniziare'.")
    else:
        st.write(f"Ci sono **{len(pila)}** libri in attesa.")
        if st.button("✨ Interroga l'Oracolo ✨", type="primary"):
            scelto = random.choice(pila)
            st.snow()
            st.subheader(f"📖 {scelto['titolo']}")
            st.caption(f"di {scelto['autore']} · {scelto['pagine'] or '?'} pag.")
            if scelto["copertina_url"]:
                mostra_cover(scelto["copertina_url"], width=200)
            if st.button("Lo inizio ora!", key="start_ora"):
                c.execute("UPDATE miei_libri SET stato = 'In Lettura 📖' WHERE isbn = ?", (scelto["isbn"],))
                conn.commit()
                st.success("Spostato 'In Lettura'!")

# =============================================================
# 6. STATISTICHE
# =============================================================
elif scelta == "📊 Statistiche":
    st.markdown('<div class="hero"><h1>📊 Statistiche di Lettura</h1></div>', unsafe_allow_html=True)
    df = pd.read_sql_query("SELECT * FROM miei_libri", conn)
    if df.empty:
        st.info("Nessun libro presente.")
    else:
        letti = df[df["stato"] == "Letti ✨"]
        st.metric("Pagine Lette Totali", int(letti["pagine"].fillna(0).sum()))
        st.metric("Libri Finiti", len(letti))
        st.bar_chart(df["stato"].value_counts())

# =============================================================
# 7. SFIDE & TROFEI
# =============================================================
elif scelta == "🏆 Sfide & Trofei":
    st.markdown('<div class="hero"><h1>🏆 Sfide & Trofei</h1></div>', unsafe_allow_html=True)
    s = statistiche(c)
    st.subheader(f"🎯 Obiettivo {datetime.now().year}")
    obj_anno = st.slider("Libri da leggere quest'anno:", 1, 100, get_setting(c, "obiettivo_anno", 12))
    set_setting(c, conn, "obiettivo_anno", obj_anno)
    st.progress(min(s["letti"] / obj_anno, 1.0), text=f"{s['letti']} su {obj_anno} libri")

    st.divider()
    st.subheader("🎖️ Trofei")
    cols = st.columns(2)
    for idx, (emoji, nome, desc, cond) in enumerate(TROFEI):
        with cols[idx % 2]:
            if cond(s):
                st.markdown(f'<div class="badge-on">{emoji} <b>{nome}</b><br><small>{desc}</small></div>', unsafe_allow_html=True)
            else:
                st.markdown(f'<div class="badge-off">🔒 <b>{nome}</b><br><small>{desc}</small></div>', unsafe_allow_html=True)

conn.close()
