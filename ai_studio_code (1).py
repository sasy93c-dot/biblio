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
# CONFIGURAZIONE
# =============================================================
st.set_page_config(page_title="BookNook - La Mia Tana dei Libri", layout="wide", page_icon="📖")

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

SCANNER_HTML = r"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body{margin:0;font-family:system-ui,-apple-system,sans-serif;color:#f3eefc;background:transparent}
  #wrap{position:relative;border-radius:16px;overflow:hidden;background:#0b0816;min-height:120px}
  video{width:100%;display:block;max-height:55vh;object-fit:cover}
  #frame{position:absolute;left:8%;right:8%;top:28%;bottom:28%;border:3px solid #ffcf5c;border-radius:14px;
         box-shadow:0 0 0 999px rgba(0,0,0,.38);pointer-events:none;display:none}
  #line{position:absolute;left:10%;right:10%;height:2px;background:#ff4d6d;top:50%;display:none;
        animation:sweep 1.6s ease-in-out infinite alternate;pointer-events:none}
  @keyframes sweep{from{top:34%}to{top:66%}}
  #bar{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap}
  button{flex:1;padding:12px 14px;border:0;border-radius:12px;font-size:16px;font-weight:600;cursor:pointer;
         background:linear-gradient(135deg,#7b2ff7,#f107a3);color:#fff}
  button.sec{background:#2b2342;color:#f3eefc}
  #msg{margin-top:10px;padding:10px 12px;border-radius:12px;background:#2b2342;font-size:15px}
</style>
<script>
  // Dice subito a Streamlit che il componente e' pronto e gli da un'altezza minima.
  window.parent.postMessage({isStreamlitMessage:true, type:"streamlit:componentReady", apiVersion:1}, "*");
  window.parent.postMessage({isStreamlitMessage:true, type:"streamlit:setFrameHeight", height:230}, "*");
</script>
</head>
<body>
<div id="wrap">
  <video id="v" playsinline muted></video>
  <div id="frame"></div><div id="line"></div>
</div>
<div id="bar">
  <button id="go">📷 Avvia scanner</button>
  <button id="torch" class="sec" hidden>🔦 Luce</button>
  <button id="stop" class="sec" hidden>⏹ Stop</button>
</div>
<div id="msg">Premi «Avvia scanner» e inquadra il codice a barre sul retro del libro.</div>

<script>
(function(){
  function send(type, data){ window.parent.postMessage(Object.assign({isStreamlitMessage:true, type:type}, data||{}), "*"); }
  function fit(){ send("streamlit:setFrameHeight", {height: Math.max(document.body.scrollHeight + 10, 230)}); }
  fit();

  var v=document.getElementById("v"), msg=document.getElementById("msg");
  var go=document.getElementById("go"), torchBtn=document.getElementById("torch"), stopBtn=document.getElementById("stop");
  var frame=document.getElementById("frame"), line=document.getElementById("line");
  var stream=null, track=null, running=false, last="", lastT=0, zx=null, torchOn=false;

  function setMsg(t){ msg.textContent=t; fit(); }
  function beep(){ try{ var a=new (window.AudioContext||window.webkitAudioContext)(); var o=a.createOscillator();
    o.frequency.value=880; o.connect(a.destination); o.start(); setTimeout(function(){o.stop();a.close();},130);}catch(e){} }
  function valid(d){ return (d.length===13 && /^97[89]/.test(d)) || d.length===10; }

  function found(txt){
    var d=String(txt).replace(/[^0-9Xx]/g,"").toUpperCase();
    if(!valid(d)) return;
    var now=Date.now();
    if(d===last && now-lastT<5000) return;
    last=d; lastT=now;
    beep(); if(navigator.vibrate) navigator.vibrate(90);
    setMsg("✅ Letto: "+d+" — pronto per il prossimo!");
    send("streamlit:setComponentValue", {value:{isbn:d, t:now}, dataType:"json"});
  }


  function loadZX(){
    return new Promise(function(resolve){
      if(window.ZXing) return resolve(true);
      var sc=document.createElement("script");
      sc.src="https://cdn.jsdelivr.net/npm/@zxing/library@0.21.3/umd/index.min.js";
      sc.onload=function(){ resolve(true); };
      sc.onerror=function(){ resolve(false); };
      setTimeout(function(){ resolve(!!window.ZXing); }, 8000);
      document.head.appendChild(sc);
    });
  }

  async function start(){
    try{
      stream = await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:"environment"}, width:{ideal:1920}, height:{ideal:1080}}, audio:false});
    }catch(e){ setMsg("Fotocamera non disponibile ("+e.name+"). Controlla i permessi del browser."); return; }
    v.srcObject=stream; await v.play();
    track=stream.getVideoTracks()[0];
    try{
      var caps=track.getCapabilities ? track.getCapabilities() : {};
      if(caps.focusMode && caps.focusMode.indexOf("continuous")>-1) await track.applyConstraints({advanced:[{focusMode:"continuous"}]});
      if(caps.torch) torchBtn.hidden=false;
    }catch(e){}
    running=true; go.hidden=true; stopBtn.hidden=false; frame.style.display="block"; line.style.display="block";
    setMsg("Inquadra il codice a barre dentro il riquadro…");

    var useNative=false;
    if("BarcodeDetector" in window){
      try{ var fm=await BarcodeDetector.getSupportedFormats(); useNative = fm.indexOf("ean_13")>-1; }catch(e){}
    }
    if(useNative){
      var det=new BarcodeDetector({formats:["ean_13"]});
      (function loop(){
        if(!running) return;
        det.detect(v).then(function(r){ if(r.length) found(r[0].rawValue); }).catch(function(){}).then(function(){ setTimeout(loop,110); });
      })();
    }else if(await loadZX() && window.ZXing){
      var hints=new Map();
      hints.set(ZXing.DecodeHintType.POSSIBLE_FORMATS,[ZXing.BarcodeFormat.EAN_13]);
      hints.set(ZXing.DecodeHintType.TRY_HARDER,true);
      zx=new ZXing.BrowserMultiFormatReader(hints);
      zx.decodeFromVideoElement(v, function(res){ if(res) found(res.getText()); });
    }else{ setMsg("Non riesco a caricare il lettore di codici. Usa «Aggiungi a Mano» con una foto."); }
  }

  function stop(){
    running=false;
    if(zx){ try{zx.reset();}catch(e){} zx=null; }
    if(stream){ stream.getTracks().forEach(function(t){t.stop();}); stream=null; }
    v.srcObject=null; go.hidden=false; stopBtn.hidden=true; torchBtn.hidden=true;
    frame.style.display="none"; line.style.display="none"; setMsg("Scanner fermo.");
  }

  go.addEventListener("click", start);
  stopBtn.addEventListener("click", stop);
  torchBtn.addEventListener("click", async function(){
    torchOn=!torchOn;
    try{ await track.applyConstraints({advanced:[{torch:torchOn}]}); }catch(e){ setMsg("Luce non supportata."); }
  });
})();
</script>
</body>
</html>
"""

def _prepara_scanner():
    """Crea da solo la cartella del componente: non serve piu' caricarla su GitHub."""
    import tempfile
    for base in (os.path.dirname(os.path.abspath(__file__)), tempfile.gettempdir()):
        try:
            cartella = os.path.join(base, "scanner_component_auto")
            os.makedirs(cartella, exist_ok=True)
            with open(os.path.join(cartella, "index.html"), "w", encoding="utf-8") as f:
                f.write(SCANNER_HTML)
            return cartella
        except Exception:
            continue
    return None

SCANNER_DIR = _prepara_scanner()
scanner = components.declare_component("isbn_scanner", path=SCANNER_DIR) if SCANNER_DIR else None

# =============================================================
# DATABASE
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
                    data_aggiunta TEXT)''')
    esistenti = {r["name"] for r in c.execute("PRAGMA table_info(miei_libri)")}
    for col, ddl in [("pagina_corrente", "INTEGER DEFAULT 0"), ("generi", "TEXT"),
                     ("trama", "TEXT"), ("data_fine", "TEXT")]:
        if col not in esistenti:
            c.execute(f"ALTER TABLE miei_libri ADD COLUMN {col} {ddl}")
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
# RICERCA ISBN (unisce piu' fonti: titolo, autore, COPERTINA e PAGINE)
# =============================================================
HEADERS = {"User-Agent": "BookNook/2.0 (streamlit)"}

def _get(url, params=None):
    r = requests.get(url, params=params, headers=HEADERS, timeout=12)
    r.raise_for_status()
    return r.json()

def pulisci_isbn(s):
    return re.sub(r"[^0-9Xx]", "", s or "").upper()

def _isbn13(i):
    if len(i) != 10:
        return i
    base = "978" + i[:9]
    ctrl = (10 - sum(int(ch) * (1 if n % 2 == 0 else 3) for n, ch in enumerate(base)) % 10) % 10
    return base + str(ctrl)

def _cover_valida(url):
    try:
        r = requests.head(url, headers=HEADERS, timeout=8, allow_redirects=True)
        return r.ok and "image" in r.headers.get("content-type", "")
    except Exception:
        return False

def _google(q, isbn):
    p = {"q": q, "maxResults": 5, "country": "IT"}
    try:
        p["key"] = st.secrets["GOOGLE_BOOKS_KEY"]
    except Exception:
        pass  # nessuna chiave configurata: si usa Google senza chiave
    items = _get("https://www.googleapis.com/books/v1/volumes", p).get("items") or []
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
    return {"titolo": v.get("title", ""), "autore": ", ".join(v.get("authors", [])),
            "copertina": cover, "pagine": v.get("pageCount") or 0,
            "generi": ", ".join(v.get("categories", [])[:3]),
            "trama": (v.get("description") or "")[:700]}

def _google_isbn(isbn):
    return _google(f"isbn:{isbn}", isbn)

def _google_testo(isbn):
    return _google(isbn, isbn)

def _ol_dati(isbn):
    d = _get("https://openlibrary.org/api/books",
             {"bibkeys": f"ISBN:{isbn}", "format": "json", "jscmd": "data"}).get(f"ISBN:{isbn}")
    if not d:
        return None
    return {"titolo": d.get("title", ""),
            "autore": ", ".join(a["name"] for a in d.get("authors", [])),
            "copertina": (d.get("cover") or {}).get("medium", ""),
            "pagine": d.get("number_of_pages") or 0,
            "generi": ", ".join(s["name"] for s in d.get("subjects", [])[:3]),
            "trama": ""}

def _ol_edizione(isbn):
    r = requests.get(f"https://openlibrary.org/isbn/{isbn}.json", headers=HEADERS, timeout=12)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    d = r.json()
    pagine = d.get("number_of_pages") or 0
    if not pagine and d.get("pagination"):
        m = re.search(r"\d+", str(d["pagination"]))
        pagine = int(m.group()) if m else 0
    return {"titolo": d.get("title", ""), "autore": "", "copertina": "", "pagine": pagine, "generi": "", "trama": ""}

CAMPI = ("titolo", "autore", "copertina", "pagine", "generi", "trama")
ESSENZIALI = ("titolo", "autore", "copertina", "pagine")

def _vuoto(v):
    return v in ("", 0, None)

@st.cache_data(ttl=43200, show_spinner=False)
def _cerca_online(isbn):
    # Se non trova il titolo solleva un errore: l'esito negativo NON viene memorizzato.
    tot = {k: ("" if k != "pagine" else 0) for k in CAMPI}
    errori = []
    for nome, f in (("google_isbn", _google_isbn), ("ol_dati", _ol_dati),
                    ("ol_edizione", _ol_edizione), ("google_testo", _google_testo)):
        try:
            ris = f(isbn)
            if not ris:
                errori.append(f"{nome}: nessun risultato")
            else:
                for k in CAMPI:
                    if _vuoto(tot[k]) and not _vuoto(ris.get(k)):
                        tot[k] = ris[k]
        except Exception as e:
            errori.append(f"{nome}: {e}")
        if all(not _vuoto(tot[k]) for k in ESSENZIALI):
            break
    if not tot["titolo"]:
        raise LookupError(" | ".join(errori) or "nessuna fonte ha questo ISBN")
    if not tot["copertina"]:
        alt = f"https://covers.openlibrary.org/b/isbn/{isbn}-M.jpg?default=false"
        tot["copertina"] = alt if _cover_valida(alt) else ""
    return tot

def cerca_info_libro(isbn_grezzo):
    isbn = pulisci_isbn(isbn_grezzo)
    st.session_state["ultimo_errore_isbn"] = ""
    if not isbn:
        return None
    for codice in dict.fromkeys([isbn, _isbn13(isbn)]):
        try:
            ris = dict(_cerca_online(codice))
            ris["isbn"] = codice
            return ris
        except LookupError as e:
            st.session_state["ultimo_errore_isbn"] = str(e)
    return None

# =============================================================
# COPERTINE E LETTURA CODICE DA FOTO
# =============================================================
def immagine_a_data_uri(file):
    from PIL import Image
    img = Image.open(file).convert("RGB")
    img.thumbnail((400, 600))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()

def mostra_cover(src, **kw):
    try:
        if src.startswith("data:image"):
            st.image(base64.b64decode(src.split(",", 1)[1]), **kw)
        else:
            st.image(src, **kw)
    except Exception:
        st.caption("📕 Copertina non disponibile")

def _leggi_zx(img):
    import zxingcpp
    for kw in ({"formats": zxingcpp.BarcodeFormat.EAN13, "try_rotate": True, "try_downscale": True},
               {"try_rotate": True, "try_downscale": True}, {}):
        try:
            for r in zxingcpp.read_barcodes(img, **kw):
                t = pulisci_isbn(r.text)
                if len(t) in (10, 13):
                    return t
        except TypeError:
            continue
    return None

def leggi_barcode(img_bytes):
    from PIL import Image, ImageOps, ImageFilter
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(img_bytes))).convert("RGB")
    img.thumbnail((2400, 2400))
    w, h = img.size
    regioni = [img,
               img.crop((int(w * .1), int(h * .2), int(w * .9), int(h * .8))),
               img.crop((int(w * .2), int(h * .3), int(w * .8), int(h * .7)))]
    for reg in regioni:
        g = ImageOps.autocontrast(ImageOps.grayscale(reg), cutoff=2)
        for scala in (1.0, 0.6, 0.4, 1.6):
            v = g if scala == 1.0 else g.resize((max(1, int(g.width * scala)), max(1, int(g.height * scala))), Image.LANCZOS)
            for variante in (v, v.filter(ImageFilter.SHARPEN), v.point(lambda p: 255 if p > 128 else 0)):
                t = _leggi_zx(variante)
                if t:
                    return t
    return None

# =============================================================
# STILE
# =============================================================
st.markdown("""
<style>
  .hero{background:linear-gradient(135deg,#7b2ff7 0%,#f107a3 55%,#ff9a3c 100%);color:#fff;border-radius:20px;
        padding:22px 28px;margin-bottom:18px;box-shadow:0 10px 28px rgba(123,47,247,.28)}
  .hero h1{margin:0;font-size:1.9rem;color:#fff}
  .hero p{margin:.3rem 0 0;font-size:1.05rem;opacity:.95;color:#fff}
  .quote{border-left:5px solid #ffcf5c;padding:10px 16px;border-radius:0 12px 12px 0;
         background:rgba(255,207,92,.10);font-style:italic}
  div[data-testid="stMetric"]{background:rgba(179,107,255,.10);border:1px solid rgba(179,107,255,.25);
         padding:12px 16px;border-radius:16px}
  div[data-testid="stVerticalBlockBorderWrapper"]{transition:transform .2s ease, box-shadow .2s ease}
  div[data-testid="stVerticalBlockBorderWrapper"]:hover{transform:translateY(-3px);box-shadow:0 8px 22px rgba(0,0,0,.25)}
  .stButton>button{border-radius:12px;font-weight:600}
  .badge-on{background:linear-gradient(135deg,rgba(255,207,92,.25),rgba(241,7,163,.18));
            border:1px solid rgba(255,207,92,.6);border-radius:16px;padding:12px;margin-bottom:10px}
  .badge-off{background:rgba(255,255,255,.04);border:1px dashed rgba(255,255,255,.2);border-radius:16px;
             padding:12px;margin-bottom:10px;opacity:.55}
</style>
""", unsafe_allow_html=True)

# =============================================================
# PUNTI ESPERIENZA E TROFEI
# =============================================================
def calcola_xp(c):
    rows = c.execute("SELECT stato, pagine, pagina_corrente, citazione FROM miei_libri").fetchall()
    xp = 0
    for r in rows:
        if r["stato"] == "Letti ✨":
            xp += (r["pagine"] or 0) + 50
        elif r["stato"] == "In Lettura 📖":
            xp += r["pagina_corrente"] or 0
        if r["citazione"]:
            xp += 10
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
        return nome, min(max(frac, 0), 1), prossimo[0] - xp, prossimo[1]
    return nome, 1.0, 0, None

def statistiche(c):
    letti = c.execute("SELECT COUNT(*) n, COALESCE(SUM(pagine),0) p FROM miei_libri WHERE stato = 'Letti ✨'").fetchone()
    tot = c.execute("SELECT COUNT(*) FROM miei_libri").fetchone()[0]
    mood_letti = c.execute("SELECT COUNT(DISTINCT mood) FROM miei_libri WHERE stato = 'Letti ✨' AND mood != ''").fetchone()[0]
    cit = c.execute("SELECT COUNT(*) FROM miei_libri WHERE citazione IS NOT NULL AND citazione != ''").fetchone()[0]
    cinque = c.execute("SELECT COUNT(*) FROM miei_libri WHERE voto = 5").fetchone()[0]
    grosso = c.execute("SELECT COUNT(*) FROM miei_libri WHERE stato = 'Letti ✨' AND pagine >= 500").fetchone()[0]
    return {"letti": letti["n"], "pagine": letti["p"], "totale": tot, "mood": mood_letti,
            "citazioni": cit, "cinque": cinque, "mattoni": grosso}

TROFEI = [
    ("🌱", "Primo Passo", "Finisci il tuo 1° libro", lambda s: s["letti"] >= 1),
    ("📚", "Topo di Biblioteca", "Finisci 5 libri", lambda s: s["letti"] >= 5),
    ("🐲", "Signore dei Draghi", "Finisci 15 libri", lambda s: s["letti"] >= 15),
    ("⚡", "Macina-Pagine", "Leggi 1.000 pagine", lambda s: s["pagine"] >= 1000),
    ("🏔️", "Alpinista di Pagine", "Leggi 5.000 pagine", lambda s: s["pagine"] >= 5000),
    ("🧱", "Domatore di Mattoni", "Finisci un libro da 500+ pagine", lambda s: s["mattoni"] >= 1),
    ("🧭", "Esploratore di Vibes", "Finisci libri con 3 mood diversi", lambda s: s["mood"] >= 3),
    ("💬", "Collezionista di Frasi", "Salva 5 citazioni", lambda s: s["citazioni"] >= 5),
    ("🌟", "Cuore a 5 Stelle", "Dai 5 stelle a un libro", lambda s: s["cinque"] >= 1),
    ("🏛️", "Bibliotecario", "Cataloga 25 libri", lambda s: s["totale"] >= 25),
]

# =============================================================
# CELEBRAZIONI (dopo un rerun)
# =============================================================
if st.session_state.pop("festa", False):
    st.balloons()
    st.toast("🎉 Libro finito! Che traguardo!", icon="🏆")

# =============================================================
# MENU
# =============================================================
conn = get_db()
c = conn.cursor()

xp_tot = calcola_xp(c)
lv_nome, lv_frac, lv_mancano, lv_prox = livello(xp_tot)

st.sidebar.title("☕ BookNook")
st.sidebar.caption("Il tuo rifugio letterario personale")
st.sidebar.markdown(f"**{lv_nome}**")
st.sidebar.progress(lv_frac)
st.sidebar.caption(f"{xp_tot} XP" + (f" · {lv_mancano} XP a {lv_prox}" if lv_prox else " · livello massimo!"))

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
        st.sidebar.download_button("💾 Scarica backup", f.read(), DB_FILE)
except Exception:
    pass

# =============================================================
# 1. IL MIO RIFUGIO (HOME)
# =============================================================
if scelta == "🏠 Il Mio Rifugio":
    ora = datetime.now().hour
    saluto = "Buongiorno" if ora < 12 else "Buon pomeriggio" if ora < 18 else "Buonasera"
    st.markdown(f'<div class="hero"><h1>{saluto}, lettore! 📖</h1>'
                f'<p>{lv_nome} · {xp_tot} XP</p></div>', unsafe_allow_html=True)

    s = statistiche(c)
    in_corso_n = c.execute("SELECT COUNT(*) FROM miei_libri WHERE stato = 'In Lettura 📖'").fetchone()[0]
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Libri in libreria 📚", s["totale"])
    m2.metric("Finiti ✨", s["letti"])
    m3.metric("In lettura 📖", in_corso_n)
    m4.metric("Pagine divorate 📄", s["pagine"])

    st.subheader("📖 Stai leggendo")
    in_lettura = c.execute("SELECT * FROM miei_libri WHERE stato = 'In Lettura 📖' ORDER BY rowid DESC").fetchall()
    if not in_lettura:
        st.info("Nessun libro in lettura. Scegline uno dalla libreria o fatti consigliare dall'Oracolo 🔮")
    for b in in_lettura:
        with st.container(border=True):
            ci, cd = st.columns([1, 5])
            with ci:
                if b["copertina_url"]:
                    mostra_cover(b["copertina_url"], width="stretch")
            with cd:
                pag = b["pagine"] or 0
                cur = b["pagina_corrente"] or 0
                st.markdown(f"**{b['titolo']}** — {b['autore']}")
                if pag:
                    st.progress(min(cur / pag, 1.0), text=f"Pagina {cur} di {pag} ({int(min(cur / pag, 1) * 100)}%)")
                else:
                    st.caption(f"Pagina {cur} (numero di pagine sconosciuto: aggiornalo in libreria)")
                bc1, bc2, bc3, bc4 = st.columns(4)
                for col, n in zip((bc1, bc2, bc3), (10, 25, 50)):
                    if col.button(f"+{n} pag.", key=f"p{n}_{b['isbn']}"):
                        nuovo = cur + n
                        if pag:
                            nuovo = min(nuovo, pag)
                        c.execute("UPDATE miei_libri SET pagina_corrente = ? WHERE isbn = ?", (nuovo, b["isbn"]))
                        conn.commit()
                        st.rerun()
                if bc4.button("🏁 Finito!", key=f"fin_{b['isbn']}"):
                    c.execute("UPDATE miei_libri SET stato = 'Letti ✨', pagina_corrente = ?, data_fine = ? WHERE isbn = ?",
                              (pag or cur, str(date.today()), b["isbn"]))
                    conn.commit()
                    st.session_state["festa"] = True
                    st.rerun()

    colq, colb = st.columns(2)
    with colq:
        st.subheader("💬 Citazione del giorno")
        citazioni = c.execute("SELECT citazione, titolo FROM miei_libri WHERE citazione IS NOT NULL AND citazione != ''").fetchall()
        if citazioni:
            q = random.Random(str(date.today())).choice(citazioni)
            st.markdown(f'<div class="quote">“{q["citazione"]}”<br><small>— {q["titolo"]}</small></div>', unsafe_allow_html=True)
        else:
            st.caption("Salva le tue frasi preferite dalla libreria e compariranno qui ogni giorno.")
    with colb:
        st.subheader("🎲 Il libro di oggi")
        pila = c.execute("SELECT * FROM miei_libri WHERE stato = 'Da Iniziare ⏳'").fetchall()
        if pila:
            b = random.Random("b" + str(date.today())).choice(pila)
            ci, cd = st.columns([1, 2])
            with ci:
                if b["copertina_url"]:
                    mostra_cover(b["copertina_url"], width="stretch")
            with cd:
                st.markdown(f"**{b['titolo']}**")
                st.caption(f"{b['autore']} · {b['pagine'] or '?'} pag.")
        else:
            st.caption("La pila 'Da Iniziare' è vuota: scansiona qualche libro!")

# =============================================================
# 2. LA MIA LIBRERIA
# =============================================================
elif scelta == "🛋️ La Mia Libreria":
    st.markdown('<div class="hero"><h1>📚 I Miei Libri</h1><p>Tutta la tua collezione, in un colpo d\'occhio</p></div>',
                unsafe_allow_html=True)

    f1, f2, f3 = st.columns([1, 1, 2])
    filtro_stato = f1.selectbox("Stato:", ["Tutti"] + STATI)
    filtro_mood = f2.selectbox("Vibes:", ["Tutte"] + MOODS)
    cerca = f3.text_input("🔎 Cerca per titolo o autore:")

    sql = "SELECT * FROM miei_libri WHERE 1=1"
    par = []
    if filtro_stato != "Tutti":
        sql += " AND stato = ?"
        par.append(filtro_stato)
    if filtro_mood != "Tutte":
        sql += " AND mood = ?"
        par.append(filtro_mood)
    if cerca:
        sql += " AND (titolo LIKE ? OR autore LIKE ?)"
        par += [f"%{cerca}%", f"%{cerca}%"]
    libri = c.execute(sql + " ORDER BY rowid DESC", par).fetchall()

    if not libri:
        st.info("Nessun libro trovato. Aggiungine uno con la scansione veloce ⚡")
    else:
        st.caption(f"{len(libri)} libri")
        colonne = st.columns(3)
        for i, b in enumerate(libri):
            isbn = b["isbn"]
            voto = b["voto"] or 0
            pag = b["pagine"] or 0
            with colonne[i % 3]:
                with st.container(border=True):
                    c_img, c_info = st.columns([1, 2])
                    with c_img:
                        if b["copertina_url"]:
                            mostra_cover(b["copertina_url"], width="stretch")
                    with c_info:
                        st.subheader(b["titolo"])
                        st.caption(f"✍️ {b['autore']}")
                        if b["mood"]:
                            st.markdown(f"🏷️ `{b['mood']}`")
                        st.write(f"**{b['stato']}**")
                        if voto > 0:
                            st.write("⭐" * voto)
                        st.caption(f"📄 {pag} pagine" if pag else "📄 pagine non indicate")
                    if b["generi"]:
                        st.caption(f"🎭 {b['generi']}")
                    if b["citazione"]:
                        st.info(f"💬 *\"{b['citazione']}\"*")

                    with st.expander("✏️ Modifica"):
                        if b["trama"]:
                            st.caption(b["trama"])
                        idx = STATI.index(b["stato"]) if b["stato"] in STATI else 0
                        nuovo_stato = st.selectbox("Stato", STATI, index=idx, key=f"st_{isbn}")
                        nuove_pagine = st.number_input("Pagine totali", min_value=0, value=pag, key=f"pg_{isbn}")
                        nuova_cur = st.number_input("Pagina a cui sei arrivato", min_value=0,
                                                    value=int(b["pagina_corrente"] or 0), key=f"cu_{isbn}")
                        nuovo_mood = st.selectbox("Vibes", [""] + MOODS,
                                                  index=([""] + MOODS).index(b["mood"]) if b["mood"] in MOODS else 0,
                                                  key=f"md_{isbn}")
                        nuovo_voto = st.slider("Voto", 0, 5, voto, key=f"vt_{isbn}")
                        nuova_cit = st.text_area("Citazione preferita", value=b["citazione"] or "", key=f"ct_{isbn}")
                        cs, cd_ = st.columns(2)
                        if cs.button("💾 Salva", key=f"btn_{isbn}"):
                            fine = b["data_fine"]
                            if nuovo_stato == "Letti ✨":
                                nuova_cur = nuove_pagine or nuova_cur
                                fine = fine or str(date.today())
                            c.execute("""UPDATE miei_libri SET stato=?, voto=?, citazione=?, pagine=?, pagina_corrente=?,
                                         mood=?, data_fine=? WHERE isbn=?""",
                                      (nuovo_stato, nuovo_voto, nuova_cit, nuove_pagine, nuova_cur, nuovo_mood, fine, isbn))
                            conn.commit()
                            if nuovo_stato == "Letti ✨" and b["stato"] != "Letti ✨":
                                st.session_state["festa"] = True
                            st.rerun()
                        if cd_.button("🗑️ Elimina", key=f"del_{isbn}"):
                            c.execute("DELETE FROM miei_libri WHERE isbn = ?", (isbn,))
                            conn.commit()
                            st.rerun()

# =============================================================
# 3. SCANSIONE VELOCE (fotocamera dal vivo, aggiunta automatica)
# =============================================================
elif scelta == "⚡ Scansione Veloce":
    st.markdown('<div class="hero"><h1>⚡ Scansione Veloce</h1>'
                '<p>Inquadra il codice a barre: titolo, autore, copertina e pagine si compilano da soli.</p></div>',
                unsafe_allow_html=True)

    for k, default in [("log_scan", []), ("non_trovati", []), ("last_scan_t", None)]:
        if k not in st.session_state:
            st.session_state[k] = default

    stato_raffica = st.selectbox("Aggiungi i libri scansionati come:", STATI, index=0)

    if scanner is None:
        st.error("Non riesco a preparare lo scanner. "
                 "Usa 'Aggiungi a Mano' con la foto del codice.")
    else:
        val = scanner(key="isbn_live", default=None)
        st.caption("Non vedi il pulsante «Avvia scanner» qui sopra? Aggiorna la pagina (o ricarica senza cache) e prova di nuovo.")
        if val and val.get("t") != st.session_state.last_scan_t:
            st.session_state.last_scan_t = val["t"]
            isbn = pulisci_isbn(val.get("isbn", ""))
            esiste = c.execute("SELECT titolo FROM miei_libri WHERE isbn = ?", (isbn,)).fetchone()
            if esiste:
                st.toast(f"Già in libreria: {esiste['titolo']}", icon="📚")
                st.session_state.log_scan.insert(0, f"📚 Già presente: **{esiste['titolo']}**")
            else:
                with st.spinner(f"Cerco {isbn}..."):
                    info = cerca_info_libro(isbn)
                if info:
                    inserisci_libro(c, conn, info["isbn"], info["titolo"], info["autore"], info["copertina"],
                                    info["pagine"], stato_raffica, generi=info.get("generi", ""),
                                    trama=info.get("trama", ""))
                    pg = f"{info['pagine']} pagine" if info["pagine"] else "pagine non trovate"
                    st.toast(f"{info['titolo']} · {pg}", icon="✅")
                    st.session_state.log_scan.insert(0, f"✅ **{info['titolo']}** — {info['autore']} · {pg}")
                else:
                    if isbn not in st.session_state.non_trovati:
                        st.session_state.non_trovati.append(isbn)
                    st.toast(f"ISBN {isbn} non trovato", icon="⚠️")
                    st.session_state.log_scan.insert(0, f"⚠️ ISBN {isbn} non trovato: completalo qui sotto")

    st.subheader("Ultimi libri scansionati")
    if st.session_state.log_scan:
        for riga in st.session_state.log_scan[:10]:
            st.markdown(riga)
    else:
        st.caption("Premi «Avvia scanner» e inquadra un libro. Ogni lettura viene salvata subito.")

    for isbn in list(st.session_state.non_trovati):
        with st.expander(f"⚠️ Completa a mano: {isbn}", expanded=True):
            t = st.text_input("Titolo", key=f"nt_{isbn}")
            a = st.text_input("Autore", key=f"na_{isbn}")
            p = st.number_input("Pagine", min_value=0, key=f"np_{isbn}")
            if st.button("Salva", key=f"ns_{isbn}"):
                if t.strip():
                    try:
                        inserisci_libro(c, conn, isbn, t, a, "", p, stato_raffica)
                    except sqlite3.IntegrityError:
                        pass
                    st.session_state.non_trovati.remove(isbn)
                    st.rerun()
                else:
                    st.error("Il titolo è obbligatorio.")

    with st.expander("💡 Consigli e alternative"):
        st.markdown(
            "- Tieni il codice dentro il riquadro giallo, con buona luce e senza riflessi.\n"
            "- Usa il pulsante **🔦 Luce** se sei in una stanza buia.\n"
            "- Se la fotocamera non parte, controlla i permessi del browser, oppure usa **Aggiungi a Mano** con una foto.\n"
            "- Un **lettore di codici a barre USB/Bluetooth** scrive l'ISBN direttamente nel campo di «Aggiungi a Mano».")

# =============================================================
# 4. AGGIUNGI A MANO
# =============================================================
elif scelta == "➕ Aggiungi a Mano":
    st.markdown('<div class="hero"><h1>✨ Aggiungi un Volume</h1>'
                '<p>Digita l\'ISBN, scatta una foto del codice o compila tutto a mano.</p></div>', unsafe_allow_html=True)

    for k, default in [("p_tit", ""), ("p_aut", ""), ("p_cov", ""), ("p_pag", 0), ("p_gen", ""), ("p_tra", "")]:
        if k not in st.session_state:
            st.session_state[k] = default

    def applica(info):
        st.session_state.p_tit = info["titolo"]
        st.session_state.p_aut = info["autore"]
        st.session_state.p_cov = info["copertina"]
        st.session_state.p_pag = int(info["pagine"] or 0)
        st.session_state.p_gen = info.get("generi", "")
        st.session_state.p_tra = info.get("trama", "")

    with st.expander("📷 Leggi il codice da una foto"):
        st.caption("Su telefono il primo riquadro apre la fotocamera (qualità migliore). Avvicinati, luce uniforme, senza riflessi.")
        foto_file = st.file_uploader("📱 Scatta o carica una foto del codice a barre",
                                     type=["jpg", "jpeg", "png", "webp"], key="scan_file")
        foto_cam = st.camera_input("...oppure usa la webcam", key="scan_cam")
        foto = foto_file or foto_cam
        if foto:
            try:
                codice = leggi_barcode(foto.getvalue())
            except Exception as e:
                codice = None
                st.error(f"Lettura non riuscita: {e}")
            if codice:
                if codice != st.session_state.get("isbn_scan"):
                    st.session_state.isbn_scan = codice
                    with st.spinner("Cerco il libro..."):
                        info = cerca_info_libro(codice)
                    if info:
                        applica(info)
                st.success(f"Codice letto: {codice}")
            else:
                st.warning("Non riesco a leggere il codice. Riprova più da vicino o digita l'ISBN.")

    col_isbn, col_btn = st.columns([3, 1])
    isbn_input = col_isbn.text_input("Codice ISBN:", value=st.session_state.get("isbn_scan", ""))
    col_btn.write("")
    if col_btn.button("🔍 Trova Magia"):
        if not isbn_input:
            st.error("Inserisci prima un codice ISBN.")
        else:
            with st.spinner("Cerco il libro..."):
                info = cerca_info_libro(isbn_input)
            if info:
                applica(info)
                st.success(f"Trovato: {info['titolo']}!" + ("" if info["pagine"] else " (pagine non disponibili, inseriscile a mano)"))
            else:
                st.warning("ISBN non trovato nei database, compila i dettagli a mano.")
                with st.expander("Dettagli tecnici"):
                    st.write(st.session_state.get("ultimo_errore_isbn", ""))

    c1, c2 = st.columns([2, 1])
    with c1:
        titolo = st.text_input("Titolo:", value=st.session_state.p_tit)
        autore = st.text_input("Autore:", value=st.session_state.p_aut)
        pagine = st.number_input("Numero di Pagine:", min_value=0, value=int(st.session_state.p_pag or 0))
        stato = st.selectbox("Stato Iniziale:", STATI)
        mood = st.selectbox("Che tipo di 'vibes' ha questo libro?", MOODS)
        voto = st.slider("Il tuo voto (se lo hai già letto):", 0, 5, 0)
        citazione = st.text_area("Una frase che ti ha colpito (opzionale):")
    with c2:
        copertina = st.text_input("URL Copertina:", value=st.session_state.p_cov)
        file_cover = st.file_uploader("...oppure carica la copertina", type=["jpg", "jpeg", "png", "webp"])
        if file_cover:
            copertina = immagine_a_data_uri(file_cover)
        if copertina:
            mostra_cover(copertina, width=160, caption="Copertina")

    if st.button("📥 Aggiungi alla Mia Tana", type="primary"):
        if not titolo.strip():
            st.error("Il titolo è obbligatorio!")
        else:
            isbn_salva = pulisci_isbn(isbn_input) or f"LIB-{random.randint(100000, 999999)}"
            try:
                inserisci_libro(c, conn, isbn_salva, titolo, autore, copertina, pagine, stato, voto, mood, citazione,
                                st.session_state.p_gen, st.session_state.p_tra)
                for k, d in [("p_tit", ""), ("p_aut", ""), ("p_cov", ""), ("p_pag", 0), ("p_gen", ""), ("p_tra", "")]:
                    st.session_state[k] = d
                st.session_state.pop("isbn_scan", None)
                st.success(f"'{titolo}' è entrato a far parte della tua libreria!")
                st.balloons()
            except sqlite3.IntegrityError:
                st.error("Questo ISBN è già nella tua libreria.")

# =============================================================
# 5. L'ORACOLO
# =============================================================
elif scelta == "🔮 L'Oracolo":
    st.markdown('<div class="hero"><h1>🔮 L\'Oracolo del Prossimo Libro</h1>'
                '<p>Blocco del lettore? Lascia che sia il destino a scegliere.</p></div>', unsafe_allow_html=True)

    vibes = st.selectbox("Che vibes hai stasera?", ["Sorprendimi (qualsiasi)"] + MOODS)
    if vibes.startswith("Sorprendimi"):
        pila = c.execute("SELECT * FROM miei_libri WHERE stato = 'Da Iniziare ⏳'").fetchall()
    else:
        pila = c.execute("SELECT * FROM miei_libri WHERE stato = 'Da Iniziare ⏳' AND mood = ?", (vibes,)).fetchall()

    if not pila:
        st.warning("Nessun libro 'Da Iniziare' con queste vibes. Aggiungine qualcuno o cambia umore!")
    else:
        st.write(f"Hai **{len(pila)}** libri in attesa per questa serata.")
        frasi = [
            "Le stelle hanno parlato: questo libro ti terrà sveglio fino alle 3 del mattino.",
            "Prepara una tisana calda, spegni il telefono e apri questa pagina.",
            "Il destino esige che tu legga proprio questo volume adesso.",
            "Niente scuse, la tua prossima avventura è qui!",
            "Un libro ti sta aspettando da troppo tempo sulla pila. Oggi è il suo giorno.",
        ]
        if st.button("✨ Invoca l'Oracolo ✨", type="primary"):
            slot = st.empty()
            titoli = [b["titolo"] for b in pila]
            for _ in range(10):
                slot.markdown(f"### 🎰 {random.choice(titoli)}")
                time.sleep(0.18)
            scelto = random.choice(pila)
            slot.empty()
            st.snow()
            st.divider()
            ci, cd = st.columns([1, 2])
            with ci:
                if scelto["copertina_url"]:
                    mostra_cover(scelto["copertina_url"], width=200)
            with cd:
                st.subheader(f"📖 {scelto['titolo']}")
                st.write(f"di **{scelto['autore']}**")
                st.info(f"🔮 Responso: *{random.choice(frasi)}*")
                if scelto["mood"]:
                    st.markdown(f"**Vibes:** `{scelto['mood']}`")
                if scelto["pagine"]:
                    st.caption(f"Lunghezza: {scelto['pagine']} pagine (~{max(1, round(scelto['pagine'] / 40))} ore di lettura)")
                if scelto["trama"]:
                    st.caption(scelto["trama"])
                if st.button("📖 Lo inizio adesso!", key="inizio_oracolo"):
                    c.execute("UPDATE miei_libri SET stato = 'In Lettura 📖' WHERE isbn = ?", (scelto["isbn"],))
                    conn.commit()
                    st.success("Buona lettura! Lo trovi nel tuo Rifugio.")

# =============================================================
# 6. STATISTICHE
# =============================================================
elif scelta == "📊 Statistiche":
    st.markdown('<div class="hero"><h1>📊 Le Tue Statistiche</h1><p>I numeri della tua vita da lettore</p></div>',
                unsafe_allow_html=True)
    df = pd.read_sql_query("SELECT * FROM miei_libri", conn)
    if df.empty:
        st.info("Aggiungi qualche libro per vedere le statistiche.")
    else:
        letti = df[df["stato"] == "Letti ✨"]
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Pagine lette", int(letti["pagine"].fillna(0).sum()))
        k2.metric("Voto medio", f"{letti[letti['voto'] > 0]['voto'].mean():.1f} ⭐" if (letti["voto"] > 0).any() else "—")
        k3.metric("Libro più lungo", int(letti["pagine"].max()) if not letti.empty else 0)
        k4.metric("Autori diversi", df["autore"].nunique())

        g1, g2 = st.columns(2)
        with g1:
            st.subheader("Per stato")
            st.bar_chart(df["stato"].value_counts())
        with g2:
            st.subheader("Per vibes")
            mood_counts = df[df["mood"] != ""]["mood"].value_counts()
            if mood_counts.empty:
                st.caption("Nessun mood assegnato.")
            else:
                st.bar_chart(mood_counts)

        st.subheader("Libri finiti per mese")
        fin = letti.dropna(subset=["data_fine"]).copy()
        if fin.empty:
            st.caption("Segna qualche libro come finito per vedere il grafico.")
        else:
            fin["mese"] = fin["data_fine"].str[:7]
            st.bar_chart(fin.groupby("mese").size())

        st.subheader("Autori più presenti")
        top = df[df["autore"] != ""]["autore"].value_counts().head(5)
        if not top.empty:
            st.dataframe(top.rename("Libri"), width="stretch")

# =============================================================
# 7. SFIDE & TROFEI
# =============================================================
elif scelta == "🏆 Sfide & Trofei":
    st.markdown('<div class="hero"><h1>🏆 Sfide & Trofei</h1><p>Ogni pagina ti avvicina al prossimo traguardo</p></div>',
                unsafe_allow_html=True)
    s = statistiche(c)

    st.subheader(f"🎯 Sfida {datetime.now().year}")
    obiettivo = st.slider("Il tuo obiettivo annuale di libri:", 1, 100, get_setting(c, "obiettivo_anno", 12))
    set_setting(c, conn, "obiettivo_anno", obiettivo)
    anno = str(datetime.now().year)
    letti_anno = c.execute("SELECT COUNT(*) FROM miei_libri WHERE stato = 'Letti ✨' AND (data_fine LIKE ? OR data_fine IS NULL)",
                           (f"{anno}%",)).fetchone()[0]
    perc = min(letti_anno / obiettivo, 1.0)
    st.progress(perc, text=f"{letti_anno} su {obiettivo} libri ({int(perc * 100)}%)")
    if perc >= 1:
        st.success("🎉 Obiettivo annuale raggiunto!")

    st.subheader("📅 Sfida del mese")
    mese = datetime.now().strftime("%Y-%m")
    pag_mese = c.execute("SELECT COALESCE(SUM(pagine),0) FROM miei_libri WHERE stato = 'Letti ✨' AND data_fine LIKE ?",
                         (f"{mese}%",)).fetchone()[0]
    obj_mese = st.slider("Pagine da leggere questo mese:", 100, 3000, get_setting(c, "obiettivo_mese", 800), step=50)
    set_setting(c, conn, "obiettivo_mese", obj_mese)
    st.progress(min(pag_mese / obj_mese, 1.0), text=f"{pag_mese} / {obj_mese} pagine")

    st.divider()
    st.subheader("🎖️ Trofei")
    sbloccati = sum(1 for _, _, _, cond in TROFEI if cond(s))
    st.caption(f"Sbloccati: {sbloccati} / {len(TROFEI)}")
    cols = st.columns(5)
    for i, (emoji, nome, desc, cond) in enumerate(TROFEI):
        with cols[i % 5]:
            if cond(s):
                st.markdown(f'<div class="badge-on"><div style="font-size:2rem">{emoji}</div><b>{nome}</b><br><small>{desc}</small></div>',
                            unsafe_allow_html=True)
            else:
                st.markdown(f'<div class="badge-off"><div style="font-size:2rem">🔒</div><b>{nome}</b><br><small>{desc}</small></div>',
                            unsafe_allow_html=True)

conn.close()
