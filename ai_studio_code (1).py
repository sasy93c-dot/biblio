import re
import io
import base64
import random
import sqlite3
import requests
import streamlit as st
from datetime import datetime

# --- CONFIGURAZIONE PAGINA ---
st.set_page_config(page_title="BookNook - La Mia Tana dei Libri", layout="wide", page_icon="📖")

STATI = ["Da Iniziare ⏳", "In Lettura 📖", "Letti ✨", "Abbandonati 💤"]
MOODS = [
    "☕ Tisana, coperta & pioggia",
    "😭 Distruzione emotiva garantita",
    "🧠 Cervellotico / Mi farà riflettere",
    "⚡ Colpi di scena adrenalinici",
    "🏖️ Leggero da ombrellone",
    "🪐 Viaggio in altri mondi",
]

# --- DATABASE PERSONALE ---
def init_personal_db():
    conn = sqlite3.connect("mia_libreria.db")
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
    conn.commit()
    conn.close()

init_personal_db()

def get_db():
    return sqlite3.connect("mia_libreria.db")

# --- RICERCA ISBN (Google Books + Open Library, con pagine e copertina) ---
HEADERS = {"User-Agent": "BookNook/1.0 (streamlit)"}

def _get(url, params=None):
    r = requests.get(url, params=params, headers=HEADERS, timeout=12)
    r.raise_for_status()
    return r.json()

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
    if "GOOGLE_BOOKS_KEY" in st.secrets:
        p["key"] = st.secrets["GOOGLE_BOOKS_KEY"]
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
            "copertina": cover, "pagine": v.get("pageCount") or 0}

def _openlibrary(isbn):
    d = _get("https://openlibrary.org/api/books",
             {"bibkeys": f"ISBN:{isbn}", "format": "json", "jscmd": "data"}).get(f"ISBN:{isbn}")
    if not d:
        return None
    return {"titolo": d.get("title", ""),
            "autore": ", ".join(a["name"] for a in d.get("authors", [])),
            "copertina": (d.get("cover") or {}).get("medium", ""),
            "pagine": d.get("number_of_pages") or 0}

@st.cache_data(ttl=86400, show_spinner=False)
def _cerca_online(isbn):
    # Se non trova nulla solleva un errore: l'esito negativo NON viene messo in cache.
    errori = []
    fonti = (("google_isbn", lambda i: _google(f"isbn:{i}", i)),
             ("google_testo", lambda i: _google(i, i)),
             ("openlibrary", _openlibrary))
    for nome, f in fonti:
        try:
            ris = f(isbn)
            if ris and ris["titolo"]:
                if not ris["copertina"]:
                    alt = f"https://covers.openlibrary.org/b/isbn/{isbn}-M.jpg?default=false"
                    ris["copertina"] = alt if _cover_valida(alt) else ""
                return ris
            errori.append(f"{nome}: nessun risultato")
        except Exception as e:
            errori.append(f"{nome}: {e}")
    raise LookupError(" | ".join(errori))

def cerca_info_libro(isbn_grezzo):
    isbn = re.sub(r"[^0-9Xx]", "", isbn_grezzo or "").upper()
    st.session_state["ultimo_errore_isbn"] = ""
    if not isbn:
        return None
    for codice in dict.fromkeys([isbn, _isbn13(isbn)]):
        try:
            return dict(_cerca_online(codice))
        except LookupError as e:
            st.session_state["ultimo_errore_isbn"] = str(e)
    return None

# --- COPERTINE ---
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
        st.caption("Copertina non disponibile")

def _leggi_zx(img):
    import zxingcpp
    for kw in ({"formats": zxingcpp.BarcodeFormat.EAN13, "try_rotate": True, "try_downscale": True},
               {"try_rotate": True, "try_downscale": True}, {}):
        try:
            for r in zxingcpp.read_barcodes(img, **kw):
                t = re.sub(r"[^0-9Xx]", "", r.text).upper()
                if len(t) in (10, 13):
                    return t
        except TypeError:
            continue
    return None

def leggi_barcode(img_bytes):
    """Legge un ISBN da una foto provando ritagli, scale e filtri diversi."""
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

# --- STILI GRAFICI ---
st.markdown("""
<style>
    .card-libro {
        background-color: rgba(255, 255, 255, 0.05);
        border-radius: 12px;
        padding: 15px;
        margin-bottom: 20px;
        border: 1px solid rgba(255, 255, 255, 0.1);
    }
</style>
""", unsafe_allow_html=True)

# --- MENU LATERALE ---
st.sidebar.title("☕ BookNook")
st.sidebar.caption("Il tuo rifugio letterario personale")
scelta = st.sidebar.radio("Esplora:", [
    "🛋️ La Mia Libreria",
    "➕ Aggiungi un Libro",
    "🔮 L'Oracolo della Lettura",
    "🏆 Sfide & Trofei",
])

# Backup del database (su Streamlit Cloud il file si cancella al riavvio)
try:
    with open("mia_libreria.db", "rb") as f:
        st.sidebar.download_button("💾 Scarica backup", f.read(), "mia_libreria.db")
except Exception:
    pass

conn = get_db()
c = conn.cursor()

# -------------------------------------------------------------
# 1. LA MIA LIBRERIA
# -------------------------------------------------------------
if scelta == "🛋️ La Mia Libreria":
    st.title("📚 I Miei Libri")

    filtro_stato = st.selectbox("Filtra per stato:", ["Tutti"] + STATI)

    if filtro_stato == "Tutti":
        libri = c.execute("SELECT * FROM miei_libri ORDER BY rowid DESC").fetchall()
    else:
        libri = c.execute("SELECT * FROM miei_libri WHERE stato = ? ORDER BY rowid DESC", (filtro_stato,)).fetchall()

    if not libri:
        st.info("Nessun libro trovato in questa sezione. Aggiungine uno nuovo dal menu a sinistra!")
    else:
        colonne = st.columns(3)
        for i, b in enumerate(libri):
            isbn, tit, aut, cover, pag, stato, voto, mood, cit, _ = b
            voto = voto or 0
            pag = pag or 0
            with colonne[i % 3]:
                with st.container():
                    st.markdown('<div class="card-libro">', unsafe_allow_html=True)
                    c_img, c_info = st.columns([1, 2])
                    with c_img:
                        if cover:
                            mostra_cover(cover, use_container_width=True)
                    with c_info:
                        st.subheader(tit)
                        st.caption(f"✍️ {aut}")
                        st.markdown(f"🏷️ `{mood or 'Nessun Mood'}`")
                        st.write(f"Stato: **{stato}**")
                        if voto > 0:
                            st.write("⭐" * voto)
                        if pag > 0:
                            st.caption(f"📄 {pag} pagine")

                    if cit:
                        st.info(f"💬 *\"{cit}\"*")

                    with st.expander("✏️ Aggiorna stato o voto"):
                        idx = STATI.index(stato) if stato in STATI else 0
                        nuovo_stato = st.selectbox("Cambia Stato", STATI, index=idx, key=f"st_{isbn}")
                        nuovo_voto = st.slider("Voto (Stelle)", 0, 5, voto, key=f"vt_{isbn}")
                        nuova_cit = st.text_area("Citazione preferita:", value=cit or "", key=f"ct_{isbn}")
                        col_s, col_d = st.columns(2)
                        if col_s.button("Salva modifiche", key=f"btn_{isbn}"):
                            c.execute("UPDATE miei_libri SET stato = ?, voto = ?, citazione = ? WHERE isbn = ?",
                                      (nuovo_stato, nuovo_voto, nuova_cit, isbn))
                            conn.commit()
                            st.rerun()
                        if col_d.button("🗑️ Elimina", key=f"del_{isbn}"):
                            c.execute("DELETE FROM miei_libri WHERE isbn = ?", (isbn,))
                            conn.commit()
                            st.rerun()

                    st.markdown('</div>', unsafe_allow_html=True)

# -------------------------------------------------------------
# 2. AGGIUNGI LIBRO
# -------------------------------------------------------------
elif scelta == "➕ Aggiungi un Libro":
    st.title("✨ Aggiungi un Volume alla Collezione")

    for k, default in [("p_tit", ""), ("p_aut", ""), ("p_cov", ""), ("p_pag", 0)]:
        if k not in st.session_state:
            st.session_state[k] = default

    with st.expander("📷 Scansiona il codice a barre con la fotocamera"):
        st.caption("Consiglio: su telefono usa il primo riquadro, che apre la fotocamera del telefono (messa a fuoco e qualita' migliori). "
                   "Avvicinati finche' il codice a barre occupa buona parte della foto, con luce uniforme e senza riflessi.")
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
                        st.session_state.p_tit = info["titolo"]
                        st.session_state.p_aut = info["autore"]
                        st.session_state.p_cov = info["copertina"]
                        st.session_state.p_pag = int(info["pagine"] or 0)
                st.success(f"Codice letto: {codice}")
            else:
                st.warning("Non riesco a leggere il codice. Avvicinati, evita i riflessi e riprova, oppure digita l'ISBN.")

    col_isbn, col_btn = st.columns([3, 1])
    isbn_input = col_isbn.text_input("Codice ISBN da scansionare o digitare:",
                                     value=st.session_state.get("isbn_scan", ""))

    if col_btn.button("🔍 Trova Magia"):
        if not isbn_input:
            st.error("Inserisci prima un codice ISBN.")
        else:
            with st.spinner("Cerco il libro..."):
                info = cerca_info_libro(isbn_input)
            if info:
                st.session_state.p_tit = info["titolo"]
                st.session_state.p_aut = info["autore"]
                st.session_state.p_cov = info["copertina"]
                st.session_state.p_pag = int(info["pagine"] or 0)
                st.success(f"Trovato: {info['titolo']}!")
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
            isbn_salva = re.sub(r"[^0-9Xx]", "", isbn_input or "").upper() or f"LIB-{random.randint(100000, 999999)}"
            try:
                c.execute("""INSERT INTO miei_libri (isbn, titolo, autore, copertina_url, pagine, stato, voto, mood, citazione, data_aggiunta)
                             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                          (isbn_salva, titolo.strip(), autore.strip(), copertina, int(pagine), stato, voto, mood,
                           citazione, str(datetime.now().date())))
                conn.commit()
                st.session_state.p_tit = ""
                st.session_state.p_aut = ""
                st.session_state.p_cov = ""
                st.session_state.p_pag = 0
                st.success(f"'{titolo}' è entrato a far parte della tua libreria!")
                st.balloons()
            except sqlite3.IntegrityError:
                st.error("Questo ISBN è già nella tua libreria.")

# -------------------------------------------------------------
# 3. L'ORACOLO DELLA LETTURA
# -------------------------------------------------------------
elif scelta == "🔮 L'Oracolo della Lettura":
    st.title("🔮 L'Oracolo del Prossimo Libro")
    st.caption("Blocco del lettore? Lascia che sia il destino a scegliere cosa leggerai stasera.")

    da_leggere = c.execute(
        "SELECT titolo, autore, copertina_url, mood, pagine FROM miei_libri WHERE stato = 'Da Iniziare ⏳'").fetchall()

    if not da_leggere:
        st.warning("La tua pila di libri 'Da Iniziare' è vuota! Aggiungi qualche libro prima di interrogare l'Oracolo.")
    else:
        st.write(f"Attualmente hai **{len(da_leggere)}** libri in attesa sulla tua pila.")

        frasi_oracolo = [
            "Le stelle hanno parlato: questo libro ti terrà sveglio fino alle 3 del mattino.",
            "Prepara una tisana calda, spegni il telefono e apri questa pagina.",
            "Il destino esige che tu legga proprio questo volume adesso.",
            "Niente scuse, la tua prossima avventura è qui!",
        ]

        if st.button("✨ Invoca l'Oracolo ✨", type="primary"):
            libro_scelto = random.choice(da_leggere)
            st.snow()

            st.divider()
            c_img, c_desc = st.columns([1, 2])
            with c_img:
                if libro_scelto[2]:
                    mostra_cover(libro_scelto[2], width=200)
            with c_desc:
                st.subheader(f"📖 {libro_scelto[0]}")
                st.write(f"di **{libro_scelto[1]}**")
                st.info(f"🔮 Responso: *{random.choice(frasi_oracolo)}*")
                st.markdown(f"**Vibes:** `{libro_scelto[3]}`")
                if (libro_scelto[4] or 0) > 0:
                    st.caption(f"Lunghezza: {libro_scelto[4]} pagine")

# -------------------------------------------------------------
# 4. SFIDE & TROFEI
# -------------------------------------------------------------
elif scelta == "🏆 Sfide & Trofei":
    st.title("🏆 I Tuoi Traguardi Letterari")

    letti = c.execute("SELECT COUNT(*), SUM(pagine) FROM miei_libri WHERE stato = 'Letti ✨'").fetchone()
    num_letti = letti[0] or 0
    pag_lette = letti[1] or 0

    in_corso = c.execute("SELECT COUNT(*) FROM miei_libri WHERE stato = 'In Lettura 📖'").fetchone()[0]

    col1, col2, col3 = st.columns(3)
    col1.metric("Libri Finiti ✨", num_letti)
    col2.metric("In Lettura Ora 📖", in_corso)
    col3.metric("Pagine Divorate 📄", pag_lette)

    st.divider()

    st.subheader(f"🎯 Sfida di Lettura {datetime.now().year}")
    obiettivo = st.slider("Il tuo obiettivo annuale di libri:", min_value=1, max_value=100, value=12)
    percentuale = min(num_letti / obiettivo, 1.0)
    st.progress(percentuale)
    st.write(f"Hai letto **{num_letti}** su **{obiettivo}** libri ({int(percentuale * 100)}% completato).")

    st.divider()

    st.subheader("🎖️ Trofei Personali")

    b1, b2, b3, b4 = st.columns(4)
    with b1:
        if num_letti >= 1:
            st.success("🌱 **Primo Passo**\nHai letto il tuo 1° libro!")
        else:
            st.caption("🔒 *Primo Passo*\n(Leggi 1 libro)")
    with b2:
        if num_letti >= 5:
            st.success("📚 **Topo di Biblioteca**\n5 libri completati!")
        else:
            st.caption("🔒 *Topo di Biblioteca*\n(Leggi 5 libri)")
    with b3:
        if pag_lette >= 1000:
            st.success("⚡ **Macina-Pagine**\nOltre 1.000 pagine lette!")
        else:
            st.caption("🔒 *Macina-Pagine*\n(Raggiungi 1.000 pagine)")
    with b4:
        if num_letti >= 15:
            st.success("🐲 **Signore dei Draghi**\n15+ libri letti!")
        else:
            st.caption("🔒 *Signore dei Draghi*\n(Leggi 15 libri)")

conn.close()
