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
HEADERS = {"User-Agent": "BookNookApp/7.0"}

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

# =============================================================
# GESTIONE COPERTINE
# =============================================================
def immagine_a_data_uri(file):
    from PIL import Image
    img = Image.open(file).convert("RGB")
    img.thumbnail((450, 650))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=82)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()

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
        padding:22px 28px;margin-bottom:20px;box-shadow:0 8px 24px rgba(123,47,247,.25)}
  .hero h1{margin:0;font-size:1.85rem;color:#fff}
  .hero p{margin:.3rem 0 0;font-size:1.02rem;opacity:.95;color:#fff}
  .quote{border-left:5px solid #ffcf5c;padding:12px 18px;border-radius:0 14px 14px 0;
         background:rgba(255,207,92,.12);font-style:italic}
  div[data-testid="stMetric"]{background:rgba(179,107,255,.09);border:1px solid rgba(179,107,255,.22);
         padding:12px 16px;border-radius:16px}
  .stButton>button{border-radius:12px;font-weight:600}
  .badge-on{background:linear-gradient(135deg,rgba(255,207,92,.22),rgba(241,7,163,.18));
            border:1px solid rgba(255,207,92,.65);border-radius:16px;padding:12px;text-align:center;margin-bottom:8px}
  .badge-off{background:rgba(255,255,255,.03);border:1px dashed rgba(255,255,255,.2);
             border-radius:16px;padding:12px;text-align:center;opacity:.55;margin-bottom:8px}

  /* Nasconde il tasto manuale 'Take Photo' di Streamlit */
  div[data-testid="stCameraInput"] button {
    display: none !important;
  }
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
st.sidebar.caption("Il tuo rifugio letterario personale")
st.sidebar.markdown(f"**{lv_nome}**")
st.sidebar.progress(lv_frac)
st.sidebar.caption(f"⭐ **{xp_tot} XP**" + (f" · Mancano {lv_mancano} XP per {lv_prox}" if lv_prox else " · Livello Massimo!"))

scelta = st.sidebar.radio("Esplora:", [
    "🏠 Il Mio Rifugio",
    "🛋️ La Mia Libreria",
    "⚡ Scansione Veloce",
    "➕ Aggiungi a Mano",
    "🔮 L'Oracolo",
    "📊 Statistiche",
    "🏆 Sfide & Trofei",
])

try:
    with open(DB_FILE, "rb") as f:
        st.sidebar.download_button("💾 Esporta Backup Database", f.read(), file_name="mia_libreria_backup.db")
except Exception:
    pass

# =============================================================
# 1. IL MIO RIFUGIO (HOME)
# =============================================================
if scelta == "🏠 Il Mio Rifugio":
    ora = datetime.now().hour
    saluto = "Buongiorno" if ora < 12 else "Buon pomeriggio" if ora < 18 else "Buonasera"
    st.markdown(f'<div class="hero"><h1>{saluto}, lettore! 📖</h1>'
                f'<p>{lv_nome} · {xp_tot} Punti Esperienza accumulati</p></div>', unsafe_allow_html=True)

    s = statistiche(c)
    in_corso_n = c.execute("SELECT COUNT(*) FROM miei_libri WHERE stato = 'In Lettura 📖'").fetchone()[0]
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Libri Catalogati 📚", s["totale"])
    m2.metric("Completati ✨", s["letti"])
    m3.metric("In Lettura Ora 📖", in_corso_n)
    m4.metric("Pagine Lette 📄", s["pagine"])

    st.subheader("📖 Attualmente In Lettura")
    in_lettura = c.execute("SELECT * FROM miei_libri WHERE stato = 'In Lettura 📖' ORDER BY rowid DESC").fetchall()
    if not in_lettura:
        st.info("Non stai leggendo nessun libro al momento. Scegline uno dalla libreria o chiedi a **L'Oracolo** 🔮!")
    else:
        for b in in_lettura:
            with st.container(border=True):
                ci, cd = st.columns([1, 5])
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
                        st.progress(prog, text=f"Pagina {cur} di {pag} ({int(prog * 100)}%)")
                    else:
                        st.caption(f"Pagina {cur} (totale pagine non indicato)")

                    b1, b2, b3, b4 = st.columns(4)
                    for col, n in zip((b1, b2, b3), (10, 25, 50)):
                        if col.button(f"+{n} pag.", key=f"p{n}_{b['isbn']}"):
                            nuovo = cur + n
                            if pag:
                                nuovo = min(nuovo, pag)
                            c.execute("UPDATE miei_libri SET pagina_corrente = ? WHERE isbn = ?", (nuovo, b["isbn"]))
                            conn.commit()
                            st.rerun()
                    if b4.button("🏁 Finito!", key=f"fin_{b['isbn']}"):
                        c.execute("UPDATE miei_libri SET stato = 'Letti ✨', pagina_corrente = ?, data_fine = ? WHERE isbn = ?",
                                  (pag or cur, str(date.today()), b["isbn"]))
                        conn.commit()
                        st.balloons()
                        st.toast("Complimenti per il traguardo!", icon="🏆")
                        st.rerun()

    cq, cb = st.columns(2)
    with cq:
        st.subheader("💬 Citazione del Giorno")
        citazioni = c.execute("SELECT citazione, titolo FROM miei_libri WHERE citazione IS NOT NULL AND citazione != ''").fetchall()
        if citazioni:
            q = random.Random(str(date.today())).choice(citazioni)
            st.markdown(f'<div class="quote">“{q["citazione"]}”<br><br><small>— Da <b>{q["titolo"]}</b></small></div>', unsafe_allow_html=True)
        else:
            st.caption("Salva qualche citazione dai tuoi libri preferiti per vederla apparire qui ogni giorno.")
    with cb:
        st.subheader("🎲 Il Consiglio della Tana")
        pila = c.execute("SELECT * FROM miei_libri WHERE stato = 'Da Iniziare ⏳'").fetchall()
        if pila:
            scelto_oggi = random.Random("rec" + str(date.today())).choice(pila)
            c_c1, c_c2 = st.columns([1, 2])
            with c_c1:
                if scelto_oggi["copertina_url"]:
                    mostra_cover(scelto_oggi["copertina_url"], use_container_width=True)
            with c_c2:
                st.markdown(f"**{scelto_oggi['titolo']}**")
                st.caption(f"{scelto_oggi['autore']} · {scelto_oggi['pagine'] or '?'} pagine")
                if scelto_oggi["mood"]:
                    st.caption(f"Vibes: `{scelto_oggi['mood']}`")
        else:
            st.caption("La pila 'Da Iniziare' è vuota. Aggiungi qualche nuovo volume!")

# =============================================================
# 2. LA MIA LIBRERIA
# =============================================================
elif scelta == "🛋️ La Mia Libreria":
    st.markdown('<div class="hero"><h1>🛋️ I Miei Scaffali</h1><p>Tutti i tuoi volumi custoditi in un unico posto</p></div>',
                unsafe_allow_html=True)

    f1, f2, f3 = st.columns([1, 1, 2])
    filtro_stato = f1.selectbox("Filtra per stato:", ["Tutti"] + STATI)
    filtro_mood = f2.selectbox("Filtra per vibes:", ["Tutte"] + MOODS)
    cerca = f3.text_input("🔎 Cerca per titolo o autore:")

    sql = "SELECT * FROM miei_libri WHERE 1=1"
    params = []
    if filtro_stato != "Tutti":
        sql += " AND stato = ?"
        params.append(filtro_stato)
    if filtro_mood != "Tutte":
        sql += " AND mood = ?"
        params.append(filtro_mood)
    if cerca:
        sql += " AND (titolo LIKE ? OR autore LIKE ?)"
        params += [f"%{cerca}%", f"%{cerca}%"]

    libri = c.execute(sql + " ORDER BY rowid DESC", params).fetchall()

    if not libri:
        st.info("Nessun libro trovato.")
    else:
        st.caption(f"Mostrando **{len(libri)}** volumi")
        cols = st.columns(3)
        for i, b in enumerate(libri):
            isbn = b["isbn"]
            voto = b["voto"] or 0
            pag = b["pagine"] or 0
            with cols[i % 3]:
                with st.container(border=True):
                    c_img, c_txt = st.columns([1, 2])
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
                    if b["citazione"]:
                        st.info(f"💬 “{b['citazione']}”")

                    with st.expander("✏️ Modifica Dettagli"):
                        if b["trama"]:
                            st.caption(b["trama"][:350] + ("..." if len(b["trama"]) > 350 else ""))
                        idx_stato = STATI.index(b["stato"]) if b["stato"] in STATI else 0
                        n_stato = st.selectbox("Stato", STATI, index=idx_stato, key=f"st_{isbn}")
                        n_pag = st.number_input("Pagine totali", min_value=0, value=pag, key=f"pg_{isbn}")
                        n_cur = st.number_input("Pagina corrente", min_value=0, value=int(b["pagina_corrente"] or 0), key=f"cu_{isbn}")
                        mood_lista = [""] + MOODS
                        idx_mood = mood_lista.index(b["mood"]) if b["mood"] in mood_lista else 0
                        n_mood = st.selectbox("Vibes", mood_lista, index=idx_mood, key=f"md_{isbn}")
                        n_voto = st.slider("Voto", 0, 5, voto, key=f"vt_{isbn}")
                        n_cit = st.text_area("Citazione memorabile", value=b["citazione"] or "", key=f"ct_{isbn}")
                        
                        bs1, bs2 = st.columns(2)
                        if bs1.button("💾 Salva", key=f"save_{isbn}"):
                            fine = b["data_fine"]
                            if n_stato == "Letti ✨":
                                n_cur = n_pag or n_cur
                                fine = fine or str(date.today())
                            c.execute("""UPDATE miei_libri SET stato=?, voto=?, citazione=?, pagine=?, pagina_corrente=?,
                                         mood=?, data_fine=? WHERE isbn=?""",
                                      (n_stato, n_voto, n_cit, n_pag, n_cur, n_mood, fine, isbn))
                            conn.commit()
                            st.success("Modifiche salvate!")
                            st.rerun()
                        if bs2.button("🗑️ Rimuovi", key=f"del_{isbn}"):
                            c.execute("DELETE FROM miei_libri WHERE isbn = ?", (isbn,))
                            conn.commit()
                            st.rerun()

# =============================================================
# 3. SCANSIONE VELOCE (FORZATURA FOTOCAMERA POSTERIORE)
# =============================================================
elif scelta == "⚡ Scansione Veloce":
    st.markdown('<div class="hero"><h1>⚡ Scansione Posteriore Continua</h1>'
                '<p>Inquadra il codice a barre sul retro del libro: la fotocamera posteriore legge e registra da sola.</p></div>',
                unsafe_allow_html=True)

    for k, default in [("log_scan", []), ("non_trovati", [])]:
        if k not in st.session_state:
            st.session_state[k] = default

    c_conf1, c_conf2 = st.columns([1, 1])
    stato_raffica = c_conf1.selectbox("Salva nuovi libri come:", STATI, index=0)

    # Verifica se un codice è arrivato in automatico
    isbn_catturato = None
    try:
        if "isbn_auto" in st.query_params:
            isbn_catturato = pulisci_isbn(st.query_params["isbn_auto"])
            st.query_params.clear()
    except Exception:
        pass

    # 1. Elemento video nativo autorizzato da Streamlit
    cam_dummy = st.camera_input("Scansione Posteriore", key="cam_live_feed")

    # 2. Sensore Ottico con forzatura Hardware della Fotocamera Posteriore
    JS_ATTIVA_POSTERIORE = """
    <script src="https://cdn.jsdelivr.net/npm/@zxing/library@0.21.3/umd/index.min.js"></script>
    <script>
    (function(){
      var lastFound = "";
      var lastTime = 0;
      var curTrack = null;
      var currentFacing = "environment";

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

      function onDetected(code) {
        var d = String(code).replace(/[^0-9Xx]/g, "").toUpperCase();
        if (!((d.length === 13 && /^97[89]/.test(d)) || d.length === 10)) return;

        var now = Date.now();
        if (d === lastFound && (now - lastTime) < 5000) return;
        lastFound = d;
        lastTime = now;

        beep();
        if (navigator.vibrate) navigator.vibrate(140);

        try {
          var parentUrl = new URL(window.parent.location.href);
          parentUrl.searchParams.set("isbn_auto", d);
          window.parent.location.href = parentUrl.href;
        } catch(e) {
          window.parent.postMessage({isbn_auto: d}, "*");
        }
      }

      // Funzione che individua l'ID hardware della fotocamera posteriore
      async function forceRearCamera(video) {
        var nav = window.parent.navigator || navigator;
        if (!nav.mediaDevices || !nav.mediaDevices.enumerateDevices) return;

        try {
          // 1. Ferma la fotocamera frontale e rilascia il sensore del telefono
          if (video.srcObject) {
            video.srcObject.getTracks().forEach(function(t){ t.stop(); });
            video.srcObject = null;
          }

          // Attendi 150ms per far rilasciare il sensore dal chip dello smartphone
          await new Promise(function(res){ setTimeout(res, 150); });

          // 2. Cerca la fotocamera posteriore nell'elenco dispositivi hardware
          var devices = await nav.mediaDevices.enumerateDevices();
          var videoDevices = devices.filter(function(d){ return d.kind === "videoinput"; });
          var backDevice = null;

          // Cerca etichette tipiche del retro ("back", "rear", "posteriore", "environment", "0")
          for (var i = 0; i < videoDevices.length; i++) {
            var lbl = (videoDevices[i].label || "").toLowerCase();
            if (lbl.indexOf("back") > -1 || lbl.indexOf("rear") > -1 || lbl.indexOf("posteriore") > -1 || lbl.indexOf("environment") > -1) {
              backDevice = videoDevices[i];
              break;
            }
          }

          // Se il telefono non mostra le etichette, seleziona l'ultima fotocamera (è quasi sempre il retro)
          if (!backDevice && videoDevices.length > 1) {
            backDevice = videoDevices[videoDevices.length - 1];
          }

          var constraints = {
            audio: false,
            video: backDevice
              ? { deviceId: { exact: backDevice.deviceId }, width: { ideal: 1920 }, height: { ideal: 1080 } }
              : { facingMode: { exact: "environment" }, width: { ideal: 1920 }, height: { ideal: 1080 } }
          };

          var stream;
          try {
            stream = await nav.mediaDevices.getUserMedia(constraints);
          } catch(e1) {
            // Fallback con vincolo flessibile se exact fallisce
            stream = await nav.mediaDevices.getUserMedia({
              video: { facingMode: { ideal: "environment" }, width: { ideal: 1920 }, height: { ideal: 1080 } },
              audio: false
            });
          }

          video.srcObject = stream;
          video.setAttribute("playsinline", "true");
          video.setAttribute("autoplay", "true");
          video.muted = true;
          await video.play();

          curTrack = stream.getVideoTracks()[0];
          currentFacing = "environment";

          // Abilita la messa a fuoco continua per codici a barre
          if (curTrack && curTrack.getCapabilities) {
            var caps = curTrack.getCapabilities();
            if (caps.focusMode && caps.focusMode.indexOf("continuous") > -1) {
              curTrack.applyConstraints({ advanced: [{ focusMode: "continuous" }] }).catch(function(){});
            }
          }
        } catch(err) {
          console.warn("Avviso fotocamera posteriore:", err);
        }
      }

      function setupScanner() {
        var doc = window.parent.document;
        var video = doc.querySelector("div[data-testid='stCameraInput'] video") || doc.querySelector("video");

        if (!video) {
          setTimeout(setupScanner, 300);
          return;
        }

        // Tenta l'attivazione immediata della fotocamera posteriore
        forceRearCamera(video);

        // Aggiungi pulsanti touch e mirino laser
        var container = video.parentElement;
        if (container && !doc.getElementById("booknook-rear-controls")) {
          container.style.position = "relative";
          var ui = doc.createElement("div");
          ui.id = "booknook-rear-controls";
          ui.style.cssText = "position:absolute;top:0;left:0;right:0;bottom:0;pointer-events:none;z-index:99;";
          ui.innerHTML = `
            <div style="position:absolute;left:8%;right:8%;top:20%;bottom:20%;border:3px solid #ffcf5c;border-radius:14px;box-shadow:0 0 0 9999px rgba(0,0,0,0.38);">
              <div style="position:absolute;left:5%;right:5%;height:3px;background:#ff4d6d;top:50%;box-shadow:0 0 10px #ff4d6d;animation:scanlaser 1.6s infinite alternate ease-in-out;"></div>
            </div>
            <div style="position:absolute;top:10px;left:0;right:0;display:flex;justify-content:center;gap:8px;pointer-events:auto;">
              <button id="btn-force-back" style="background:#7b2ff7;color:#fff;border:none;padding:10px 16px;border-radius:12px;font-weight:bold;font-size:14px;box-shadow:0 4px 12px rgba(0,0,0,0.4);cursor:pointer;">🔄 Passa a Fotocamera Posteriore</button>
            </div>
            <style>
              @keyframes scanlaser { from { top: 22%; } to { top: 78%; } }
            </style>
          `;
          container.appendChild(ui);

          // Cliccando il pulsante forzi esplicitamente la lente posteriore tramite gesto touch
          doc.getElementById("btn-force-back").addEventListener("click", function(e){
            e.preventDefault();
            e.stopPropagation();
            forceRearCamera(video);
          });
        }

        // Riconoscimento codici hardware nativo continuo
        if ("BarcodeDetector" in window) {
          try {
            var detector = new BarcodeDetector({ formats: ["ean_13"] });
            function loopDetector() {
              if (video.readyState >= 2) {
                detector.detect(video).then(function(barcodes){
                  if (barcodes.length > 0) onDetected(barcodes[0].rawValue);
                }).catch(function(){});
              }
              setTimeout(loopDetector, 75);
            }
            loopDetector();
            return;
          } catch(e){}
        }

        // Fallback continuo con libreria ZXing
        if (window.ZXing) {
          var hints = new Map();
          hints.set(ZXing.DecodeHintType.POSSIBLE_FORMATS, [ZXing.BarcodeFormat.EAN_13]);
          var reader = new ZXing.BrowserMultiFormatReader(hints);
          reader.decodeFromVideoElement(video, function(res){
            if (res) onDetected(res.getText());
          });
        }
      }

      setTimeout(setupScanner, 400);
    })();
    </script>
    """
    components.html(JS_ATTIVA_POSTERIORE, height=0)

    # Inserimento opzionale manuale o con pistola USB
    with st.expander("⌨️ Oppure digita l'ISBN a mano o usa una pistola barcode USB:"):
        with st.form("form_pistola", clear_on_submit=True):
            input_pistola = st.text_input("Codice ISBN (premi Invio per salvare):", placeholder="Es. 9788804668237")
            if st.form_submit_button("Aggiungi Subito") and input_pistola.strip():
                isbn_catturato = pulisci_isbn(input_pistola)

    # Se un libro è stato catturato automaticamente
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
                st.session_state.log_scan.insert(0, f"⚠️ ISBN {isbn} non trovato online: completalo qui sotto")

    st.subheader("📋 Registro Scansioni Recenti")
    if st.session_state.log_scan:
        for riga in st.session_state.log_scan[:8]:
            st.markdown(riga)
    else:
        st.caption("I libri scansionati appariranno qui man mano che li inquadri.")

    for isbn_mancante in list(st.session_state.non_trovati):
        with st.expander(f"⚠️ Completa manualmente: {isbn_mancante}", expanded=True):
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
# 4. AGGIUNGI A MANO
# =============================================================
elif scelta == "➕ Aggiungi a Mano":
    st.markdown('<div class="hero"><h1>✨ Aggiungi un Volume</h1>'
                '<p>Inserisci manualmente i dettagli o cerca tramite codice ISBN.</p></div>', unsafe_allow_html=True)

    for k in ["man_tit", "man_aut", "man_cov", "man_pag", "man_gen", "man_tra"]:
        if k not in st.session_state:
            st.session_state[k] = "" if "pag" not in k else 0

    c_isbn, c_trova = st.columns([3, 1])
    isbn_digitato = c_isbn.text_input("Codice ISBN (10 o 13 cifre):", placeholder="Es. 9788804668237")
    c_trova.write("")
    if c_trova.button("🔍 Trova Magia", use_container_width=True):
        if not isbn_digitato.strip():
            st.error("Inserisci prima un codice ISBN.")
        else:
            with st.spinner("Ricerca nei cataloghi online..."):
                trovato = cerca_info_libro(isbn_digitato)
            if trovato:
                st.session_state.man_tit = trovato["titolo"]
                st.session_state.man_aut = trovato["autore"]
                st.session_state.man_cov = trovato["copertina"]
                st.session_state.man_pag = int(trovato["pagine"] or 0)
                st.session_state.man_gen = trovato.get("generi", "")
                st.session_state.man_tra = trovato.get("trama", "")
                st.success(f"Trovato: **{trovato['titolo']}**!")
            else:
                st.warning("Nessun libro trovato con questo codice. Puoi compilare i dettagli a mano.")

    c1, c2 = st.columns([2, 1])
    with c1:
        titolo = st.text_input("Titolo:", value=st.session_state.man_tit)
        autore = st.text_input("Autore:", value=st.session_state.man_aut)
        pagine = st.number_input("Numero di Pagine:", min_value=0, value=int(st.session_state.man_pag or 0))
        stato = st.selectbox("Stato Iniziale:", STATI)
        mood = st.selectbox("Che 'vibes' ha questo libro?", MOODS)
        voto = st.slider("Il tuo voto:", 0, 5, 0)
        citazione = st.text_area("Citazione preferita (opzionale):")
    with c2:
        copertina = st.text_input("URL Copertina:", value=st.session_state.man_cov)
        carica_img = st.file_uploader("Oppure carica immagine copertina:", type=["jpg", "png", "webp"])
        if carica_img:
            copertina = immagine_a_data_uri(carica_img)
        if copertina:
            mostra_cover(copertina, width=170)

    if st.button("📥 Salva Volume nella Tana", type="primary", use_container_width=True):
        if not titolo.strip():
            st.error("Il titolo è obbligatorio!")
        else:
            isbn_finale = pulisci_isbn(isbn_digitato) or f"BOOK-{random.randint(100000, 999999)}"
            try:
                inserisci_libro(c, conn, isbn_finale, titolo, autore, copertina, pagine, stato, voto, mood,
                                citazione, st.session_state.man_gen, st.session_state.man_tra)
                for k in ["man_tit", "man_aut", "man_cov", "man_pag", "man_gen", "man_tra"]:
                    st.session_state[k] = "" if "pag" not in k else 0
                st.success(f"«{titolo}» è stato aggiunto alla tua libreria!")
                st.balloons()
            except sqlite3.IntegrityError:
                st.error("Questo libro è già registrato nella tua libreria!")

# =============================================================
# 5. L'ORACOLO
# =============================================================
elif scelta == "🔮 L'Oracolo":
    st.markdown('<div class="hero"><h1>🔮 L\'Oracolo dei Libri</h1>'
                '<p>Non sai cosa leggere stasera? Lascia che sia il destino a scegliere per te.</p></div>',
                unsafe_allow_html=True)

    scelta_mood = st.selectbox("Che umore hai stasera?", ["Sorprendimi (qualsiasi)"] + MOODS)
    if scelta_mood.startswith("Sorprendimi"):
        pila = c.execute("SELECT * FROM miei_libri WHERE stato = 'Da Iniziare ⏳'").fetchall()
    else:
        pila = c.execute("SELECT * FROM miei_libri WHERE stato = 'Da Iniziare ⏳' AND mood = ?", (scelta_mood,)).fetchall()

    if not pila:
        st.warning("Nessun libro 'Da Iniziare' trovato per questo umore. Aggiungi qualche nuovo volume o cambia vibes!")
    else:
        st.write(f"Ci sono **{len(pila)}** libri in attesa adatti alla tua serata.")
        responsi = [
            "Le stelle hanno deciso: spegni il telefono e apriti a questo mondo.",
            "Questo libro ti terrà sveglio fino alle prime ore del mattino.",
            "Prepara una tazza calda, è il momento perfetto per questa storia.",
            "La pila ringrazia: ecco il prescelto del destino!"
        ]
        if st.button("✨ Interroga l'Oracolo ✨", type="primary", use_container_width=True):
            slot = st.empty()
            titoli = [b["titolo"] for b in pila]
            for _ in range(8):
                slot.markdown(f"### 🎰 *{random.choice(titoli)}*")
                time.sleep(0.15)
            scelto = random.choice(pila)
            slot.empty()
            st.snow()
            st.divider()

            c_ocov, c_otxt = st.columns([1, 2])
            with c_ocov:
                if scelto["copertina_url"]:
                    mostra_cover(scelto["copertina_url"], width=220)
            with c_otxt:
                st.subheader(f"📖 {scelto['titolo']}")
                st.caption(f"di **{scelto['autore']}**")
                st.info(f"🔮 *{random.choice(responsi)}*")
                if scelto["mood"]:
                    st.write(f"🏷️ Vibes: `{scelto['mood']}`")
                if scelto["pagine"]:
                    st.caption(f"Lunghezza: {scelto['pagine']} pagine (~{max(1, round(scelto['pagine'] / 45))} ore di lettura)")
                if scelto["trama"]:
                    st.caption(scelto["trama"])
                if st.button("📖 Inizialo subito!", key="start_ora"):
                    c.execute("UPDATE miei_libri SET stato = 'In Lettura 📖' WHERE isbn = ?", (scelto["isbn"],))
                    conn.commit()
                    st.success("Il libro è stato spostato 'In Lettura'. Lo trovi nel tuo Rifugio!")

# =============================================================
# 6. STATISTICHE
# =============================================================
elif scelta == "📊 Statistiche":
    st.markdown('<div class="hero"><h1>📊 La Tua Lettura in Cifre</h1>'
                '<p>Panoramica completa delle tue abitudini di lettura</p></div>', unsafe_allow_html=True)
    df = pd.read_sql_query("SELECT * FROM miei_libri", conn)
    if df.empty:
        st.info("Nessun dato disponibile. Inizia catalogando qualche libro!")
    else:
        letti = df[df["stato"] == "Letti ✨"]
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Pagine Lette Totali", int(letti["pagine"].fillna(0).sum()))
        voto_medio = letti[letti["voto"] > 0]["voto"].mean()
        k2.metric("Media Voto", f"{voto_medio:.1f} ⭐" if not pd.isna(voto_medio) else "—")
        k3.metric("Volume Più Lungo", f"{int(letti['pagine'].max())} pag." if not letti.empty and letti['pagine'].max() > 0 else "—")
        k4.metric("Autori Differenti", df["autore"].replace("", None).nunique())

        c_g1, c_g2 = st.columns(2)
        with c_g1:
            st.subheader("Ripartizione per Stato")
            st.bar_chart(df["stato"].value_counts())
        with c_g2:
            st.subheader("Vibes & Mood")
            mc = df[df["mood"] != ""]["mood"].value_counts()
            if not mc.empty:
                st.bar_chart(mc)
            else:
                st.caption("Nessun mood assegnato finora.")

        st.subheader("Libri Conclusi per Mese")
        fin = letti.dropna(subset=["data_fine"]).copy()
        if not fin.empty:
            fin["mese"] = fin["data_fine"].str[:7]
            st.bar_chart(fin.groupby("mese").size())
        else:
            st.caption("Termina qualche volume per visualizzare l'andamento nel tempo.")

# =============================================================
# 7. SFIDE & TROFEI
# =============================================================
elif scelta == "🏆 Sfide & Trofei":
    st.markdown('<div class="hero"><h1>🏆 Sfide & Trofei</h1>'
                '<p>Raggiungi i tuoi traguardi e sblocca riconoscimenti</p></div>', unsafe_allow_html=True)
    s = statistiche(c)

    st.subheader(f"🎯 Sfida di Lettura {datetime.now().year}")
    obj_anno = st.slider("Quanti libri vuoi leggere quest'anno?", 1, 100, get_setting(c, "obiettivo_anno", 12))
    set_setting(c, conn, "obiettivo_anno", obj_anno)
    anno_cur = str(datetime.now().year)
    letti_anno = c.execute("SELECT COUNT(*) FROM miei_libri WHERE stato = 'Letti ✨' AND (data_fine LIKE ? OR data_fine IS NULL)",
                           (f"{anno_cur}%",)).fetchone()[0]
    p_anno = min(letti_anno / obj_anno, 1.0)
    st.progress(p_anno, text=f"{letti_anno} su {obj_anno} libri completati ({int(p_anno * 100)}%)")
    if p_anno >= 1.0:
        st.success("🎉 Complimenti! Hai raggiunto il tuo obiettivo annuale!")

    st.subheader("📅 Sfida Pagine del Mese")
    mese_cur = datetime.now().strftime("%Y-%m")
    pag_mese = c.execute("SELECT COALESCE(SUM(pagine),0) FROM miei_libri WHERE stato = 'Letti ✨' AND data_fine LIKE ?",
                         (f"{mese_cur}%",)).fetchone()[0]
    obj_mese = st.slider("Pagine da leggere questo mese:", 100, 3000, get_setting(c, "obiettivo_mese", 600), step=50)
    set_setting(c, conn, "obiettivo_mese", obj_mese)
    st.progress(min(pag_mese / obj_mese, 1.0), text=f"{pag_mese} su {obj_mese} pagine")

    st.divider()
    st.subheader("🎖️ Bacheca dei Trofei")
    sbloccati = sum(1 for _, _, _, cond in TROFEI if cond(s))
    st.caption(f"Sbloccati **{sbloccati}** su **{len(TROFEI)}** trofei")
    cols = st.columns(5)
    for idx, (emoji, nome, desc, cond) in enumerate(TROFEI):
        with cols[idx % 5]:
            if cond(s):
                st.markdown(f'<div class="badge-on"><div style="font-size:2.2rem">{emoji}</div>'
                            f'<b>{nome}</b><br><small>{desc}</small></div>', unsafe_allow_html=True)
            else:
                st.markdown(f'<div class="badge-off"><div style="font-size:2.2rem">🔒</div>'
                            f'<b>{nome}</b><br><small>{desc}</small></div>', unsafe_allow_html=True)

conn.close()
