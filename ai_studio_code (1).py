import re
import random
import requests
import sqlite3
import pandas as pd
import streamlit as st
from datetime import datetime

# --- CONFIGURAZIONE PAGINA ---
st.set_page_config(page_title="BookNook - La Mia Tana dei Libri", layout="wide", page_icon="📖")

# --- DATABASE PERSONALE ---
def init_personal_db():
    conn = sqlite3.connect('mia_libreria.db')
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
    return sqlite3.connect('mia_libreria.db')

# --- RICERCA ISBN (Con estrazione automatica Pagine) ---
def cerca_info_libro(isbn_grezzo):
    isbn = re.sub(r"[^0-9Xx]", "", isbn_grezzo or "").upper()
    if not isbn: 
        return None
    
    headers = {'User-Agent': 'Mozilla/5.0'}
    # 1. Google Books
    try:
        url = f"https://www.googleapis.com/books/v1/volumes?q={isbn}"
        res = requests.get(url, headers=headers, timeout=6).json()
        if "items" in res and len(res["items"]) > 0:
            v = res["items"][0]["volumeInfo"]
            cover = (v.get("imageLinks") or {}).get("thumbnail", "").replace("http://", "https://")
            return {
                "isbn": isbn,
                "titolo": v.get("title", ""),
                "autore": ", ".join(v.get("authors", ["Autore Sconosciuto"])),
                "pagine": v.get("pageCount", 0),
                "copertina": cover or f"https://covers.openlibrary.org/b/isbn/{isbn}-M.jpg"
            }
    except Exception:
        pass

    # 2. Open Library Fallback
    try:
        url = f"https://openlibrary.org/api/books?bibkeys=ISBN:{isbn}&format=json&jscmd=data"
        res = requests.get(url, headers=headers, timeout=6).json()
        key = f"ISBN:{isbn}"
        if key in res:
            d = res[key]
            return {
                "isbn": isbn,
                "titolo": d.get("title", ""),
                "autore": ", ".join([a["name"] for a in d.get("authors", [])]) or "Autore Sconosciuto",
                "pagine": d.get("number_of_pages", 0),
                "copertina": (d.get("cover") or {}).get("medium", f"https://covers.openlibrary.org/b/isbn/{isbn}-M.jpg")
            }
    except Exception:
        pass

    return {"isbn": isbn, "titolo": "", "autore": "", "pagine": 0, "copertina": f"https://covers.openlibrary.org/b/isbn/{isbn}-M.jpg"}

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
    "🏆 Sfide & Trofei"
])

conn = get_db()
c = conn.cursor()

# -------------------------------------------------------------
# 1. LA MIA LIBRERIA
# -------------------------------------------------------------
if scelta == "🛋️ La Mia Libreria":
    st.title("📚 I Miei Libri")
    
    filtro_stato = st.selectbox("Filtra per stato:", ["Tutti", "In Lettura 📖", "Da Iniziare ⏳", "Letti ✨", "Abbandonati 💤"])
    
    if filtro_stato == "Tutti":
        query = "SELECT * FROM miei_libri ORDER BY rowid DESC"
        libri = c.execute(query).fetchall()
    else:
        query = "SELECT * FROM miei_libri WHERE stato = ? ORDER BY rowid DESC"
        libri = c.execute(query, (filtro_stato,)).fetchall()
        
    if not libri:
        st.info("Nessun libro trovato in questa sezione. Aggiungine uno nuovo dal menu a sinistra!")
    else:
        # Mostra i libri a griglia (3 per riga)
        colonne = st.columns(3)
        for i, b in enumerate(libri):
            isbn, tit, aut, cover, pag, stato, voto, mood, cit, _ = b
            col = colonne[i % 3]
            with col:
                with st.container():
                    st.markdown('<div class="card-libro">', unsafe_allow_html=True)
                    c_img, c_info = st.columns([1, 2])
                    with c_img:
                        if cover:
                            st.image(cover, use_container_width=True)
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
                    
                    # Espansore rapido per aggiornare lo stato del libro
                    with st.expander("✏️ Aggiorna stato o voto"):
                        nuovo_stato = st.selectbox("Cambia Stato", ["In Lettura 📖", "Letti ✨", "Da Iniziare ⏳", "Abbandonati 💤"], index=0, key=f"st_{isbn}")
                        nuovo_voto = st.slider("Voto (Stelle)", 0, 5, voto, key=f"vt_{isbn}")
                        nuova_cit = st.text_area("Citazione preferita:", value=cit or "", key=f"ct_{isbn}")
                        if st.button("Salva modifiche", key=f"btn_{isbn}"):
                            c.execute("UPDATE miei_libri SET stato = ?, voto = ?, citazione = ? WHERE isbn = ?", (nuovo_stato, nuovo_voto, nuova_cit, isbn))
                            conn.commit()
                            st.rerun()
                            
                    st.markdown('</div>', unsafe_allow_html=True)

# -------------------------------------------------------------
# 2. AGGIUNGI LIBRO
# -------------------------------------------------------------
import re, requests, streamlit as st

HEADERS = {"User-Agent": "TanaLibri/1.0 (streamlit)"}

def _get(url, params=None):
    r = requests.get(url, params=params, headers=HEADERS, timeout=12)
    r.raise_for_status()
    return r.json()

def _isbn13(i):
    if len(i) != 10:
        return i
    base = "978" + i[:9]
    ctrl = (10 - sum(int(c) * (1 if n % 2 == 0 else 3) for n, c in enumerate(base)) % 10) % 10
    return base + str(ctrl)

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
    errori = []
    fonti = (("google_isbn", lambda i: _google(f"isbn:{i}", i)),
             ("google_testo", lambda i: _google(i, i)),
             ("openlibrary", _openlibrary))
    for nome, f in fonti:
        try:
            ris = f(isbn)
            if ris and ris["titolo"]:
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
# -------------------------------------------------------------
# 3. L'ORACOLO DELLA LETTURA (FUN FEATURE)
# -------------------------------------------------------------
elif scelta == "🔮 L'Oracolo della Lettura":
    st.title("🔮 L'Oracolo del Prossimo Libro")
    st.caption("Blocco del lettore? Lascia che sia il destino a scegliere cosa leggerai stasera.")
    
    da_leggere = c.execute("SELECT titolo, autore, copertina_url, mood, pagine FROM miei_libri WHERE stato = 'Da Iniziare ⏳'").fetchall()
    
    if not da_leggere:
        st.warning("La tua pila di libri 'Da Iniziare' è vuota! Aggiungi qualche libro prima di interrogare l'Oracolo.")
    else:
        st.write(f"Attualmente hai **{len(da_leggere)}** libri in attesa sulla tua pila.")
        
        frasi_oracolo = [
            "Le stelle hanno parlato: questo libro ti terrà sveglio fino alle 3 del mattino.",
            "Prepara una tisana calda, spegni il telefono e apri questa pagina.",
            "Il destino esige che tu legga proprio questo volume adesso.",
            "Niente scuse, la tua prossima avventura è qui!"
        ]
        
        if st.button("✨ Invoca l'Oracolo ✨", type="primary"):
            libro_scelto = random.choice(da_leggere)
            st.snow()
            
            st.divider()
            c_img, c_desc = st.columns([1, 2])
            with c_img:
                if libro_scelto[2]:
                    st.image(libro_scelto[2], width=200)
            with c_desc:
                st.subheader(f"📖 {libro_scelto[0]}")
                st.write(f"di **{libro_scelto[1]}**")
                st.info(f"🔮 Responso: *{random.choice(frasi_oracolo)}*")
                st.markdown(f"**Vibes:** `{libro_scelto[3]}`")
                if libro_scelto[4] > 0:
                    st.caption(f"Lunghezza: {libro_scelto[4]} pagine")

# -------------------------------------------------------------
# 4. SFIDE & TROFEI
# -------------------------------------------------------------
elif scelta == "🏆 Sfide & Trofei":
    st.title("🏆 I Tuoi Traguardi Letterari")
    
    # Statistiche rapide
    letti = c.execute("SELECT COUNT(*), SUM(pagine) FROM miei_libri WHERE stato = 'Letti ✨'").fetchone()
    num_letti = letti[0] or 0
    pag_lette = letti[1] or 0
    
    in_corso = c.execute("SELECT COUNT(*) FROM miei_libri WHERE stato = 'In Lettura 📖'").fetchone()[0]
    
    col1, col2, col3 = st.columns(3)
    col1.metric("Libri Finiti ✨", num_letti)
    col2.metric("In Lettura Ora 📖", in_corso)
    col3.metric("Pagine Divorate 📄", pag_lette)
    
    st.divider()
    
    # Sfida Annuale
    st.subheader("🎯 Sfida di Lettura 2025")
    obiettivo = st.slider("Il tuo obiettivo annuale di libri:", min_value=1, max_value=100, value=12)
    percentuale = min(num_letti / obiettivo, 1.0)
    st.progress(percentuale)
    st.write(f"Hai letto **{num_letti}** su **{obiettivo}** libri ({int(percentuale*100)}% completato).")
    
    st.divider()
    
    # Badge Sbloccabili
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
