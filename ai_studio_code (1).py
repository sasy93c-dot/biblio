import re
import requests
import sqlite3
import pandas as pd
import streamlit as st
from datetime import date

# --- CONFIGURAZIONE PAGINA ---
st.set_page_config(page_title="Gestionale Biblioteca", layout="wide", page_icon="📚")

# --- MOTORE DI RICERCA ISBN (OTTIMIZZATO) ---
def pulisci_isbn(s):
    return re.sub(r"[^0-9Xx]", "", s or "").upper()

def isbn10_a_13(i):
    if len(i) != 10: 
        return i
    base = "978" + i[:9]
    ctrl = (10 - sum(int(c) * (1 if n % 2 == 0 else 3) for n, c in enumerate(base)) % 10) % 10
    return base + str(ctrl)

def cerca_google(isbn):
    p = {"q": f"isbn:{isbn}"}
    if "GOOGLE_BOOKS_KEY" in st.secrets:
        p["key"] = st.secrets["GOOGLE_BOOKS_KEY"]
    r = requests.get("https://www.googleapis.com/books/v1/volumes", params=p, timeout=10)
    items = r.json().get("items") if r.ok else None
    if not items: 
        return None
    v = items[0]["volumeInfo"]
    cover = (v.get("imageLinks") or {}).get("thumbnail", "").replace("http://", "https://")
    return {
        "titolo": v.get("title", ""), 
        "autore": ", ".join(v.get("authors", [])),
        "editore": v.get("publisher", ""), 
        "copertina": cover
    }

def cerca_openlibrary(isbn):
    r = requests.get("https://openlibrary.org/api/books",
        params={"bibkeys": f"ISBN:{isbn}", "format": "json", "jscmd": "data"}, timeout=10)
    d = r.json().get(f"ISBN:{isbn}") if r.ok else None
    if not d: 
        return None
    return {
        "titolo": d.get("title", ""),
        "autore": ", ".join(a["name"] for a in d.get("authors", [])),
        "editore": ", ".join(p["name"] for p in d.get("publishers", [])),
        "copertina": (d.get("cover") or {}).get("medium", "")
    }

@st.cache_data(ttl=86400)
def cerca_libro(isbn_grezzo):
    isbn = pulisci_isbn(isbn_grezzo)
    if not isbn:
        return None
    for codice in dict.fromkeys([isbn, isbn10_a_13(isbn)]):
        for fonte in (cerca_google, cerca_openlibrary):
            try:
                ris = fonte(codice)
                if ris and ris["titolo"]:
                    if not ris["copertina"]:
                        ris["copertina"] = f"https://covers.openlibrary.org/b/isbn/{codice}-M.jpg?default=false"
                    ris["isbn"] = codice
                    return ris
            except Exception:
                continue
    return None

# --- INIZIALIZZAZIONE DATABASE ---
def init_db():
    conn = sqlite3.connect('biblioteca.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS libri (
                    isbn TEXT PRIMARY KEY,
                    titolo TEXT,
                    autore TEXT,
                    editore TEXT,
                    copie_totali INTEGER,
                    copie_disponibili INTEGER,
                    copertina_url TEXT)''')

    try:
        c.execute("ALTER TABLE libri ADD COLUMN copertina_url TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE libri ADD COLUMN editore TEXT")
    except sqlite3.OperationalError:
        pass

    c.execute('''CREATE TABLE IF NOT EXISTS utenti (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nome TEXT,
                    cognome TEXT,
                    email TEXT,
                    telefono TEXT)''')

    c.execute('''CREATE TABLE IF NOT EXISTS prestiti (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    isbn TEXT,
                    utente_id INTEGER,
                    data_prestito TEXT,
                    data_restituzione TEXT,
                    stato TEXT,
                    FOREIGN KEY(isbn) REFERENCES libri(isbn),
                    FOREIGN KEY(utente_id) REFERENCES utenti(id))''')
    conn.commit()
    conn.close()

init_db()

def get_connection():
    return sqlite3.connect('biblioteca.db')

# --- INTERFACCIA PRINCIPALE ---
st.title("📚 Banco Biblioteca - Gestione Prestiti")

menu = st.sidebar.radio("Sezioni:", [
    "📖 Catalogo Libri (ISBN)", 
    "📋 Registra Prestito", 
    "🔄 Rientro Prestito", 
    "👥 Anagrafica Utenti", 
    "📊 Storico Prestiti"
])

conn = get_connection()
c = conn.cursor()

# -------------------------------------------------------------
# 1. CATALOGO LIBRI (AGGIUNTA + MODIFICA QUANTITÀ)
# -------------------------------------------------------------
if menu == "📖 Catalogo Libri (ISBN)":
    tab_nuovo, tab_modifica = st.tabs(["➕ Aggiungi Nuovo Libro", "✏️ Modifica Quantità / Rimuovi"])

    # --- TAB 1: INSERIMENTO NUOVO LIBRO (CON SUPPORTO BARCODE) ---
    with tab_nuovo:
        st.subheader("Carica Libro tramite ISBN (o Lettore Barcode)")

        for k in ["titolo_temp", "autore_temp", "editore_temp", "cover_temp", "isbn_temp", "ultimo_isbn_cercato"]:
            if k not in st.session_state:
                st.session_state[k] = ""

        col_isbn, col_btn = st.columns([3, 1])
        
        # Campo di testo reattivo: premendo Invio o sparando con il barcode fa partire la ricerca
        isbn_inserito = col_isbn.text_input(
            "Codice ISBN (spara col lettore o premi Invio):", 
            value=st.session_state.isbn_temp,
            key="input_isbn"
        )
        tasto_cerca = col_btn.button("🔍 Cerca Dati")

        # Ricerca automatica se il codice cambia o se si preme il pulsante
        isbn_pulito = pulisci_isbn(isbn_inserito)
        if (isbn_pulito and isbn_pulito != st.session_state.ultimo_isbn_cercato) or tasto_cerca:
            if isbn_pulito:
                with st.spinner("Ricerca nei database in corso..."):
                    dati = cerca_libro(isbn_pulito)
                    st.session_state.ultimo_isbn_cercato = isbn_pulito
                    if dati:
                        st.session_state.titolo_temp = dati["titolo"]
                        st.session_state.autore_temp = dati["autore"]
                        st.session_state.editore_temp = dati["editore"]
                        st.session_state.cover_temp = dati["copertina"]
                        st.session_state.isbn_temp = dati["isbn"]
                        st.success(f"Trovato: {dati['titolo']}")
                        st.rerun()
                    else:
                        st.session_state.isbn_temp = isbn_pulito
                        st.warning("Nessun dato trovato online. Puoi inserire i campi a mano.")
            else:
                st.error("Inserisci prima un codice ISBN.")

        # Campi dati e copertina
        col_dati, col_anteprima = st.columns([3, 1])
        with col_dati:
            titolo_finale = st.text_input("Titolo:", value=st.session_state.titolo_temp)
            autore_finale = st.text_input("Autore:", value=st.session_state.autore_temp)
            editore_finale = st.text_input("Editore:", value=st.session_state.editore_temp)
            copertina_url = st.text_input("URL Copertina:", value=st.session_state.cover_temp)
            copie = st.number_input("Quantità copie da inserire:", min_value=1, value=1, step=1)

        with col_anteprima:
            if st.session_state.cover_temp:
                st.image(st.session_state.cover_temp, caption="Anteprima Copertina", width=140)
            else:
                st.info("Nessuna copertina")

        if st.button("💾 Salva nel Catalogo", type="primary"):
            isbn_db = pulisci_isbn(isbn_inserito)
            if not isbn_db:
                st.error("L'ISBN è obbligatorio.")
            elif not titolo_finale.strip():
                st.error("Il Titolo è obbligatorio.")
            else:
                try:
                    c.execute("SELECT copie_totali, copie_disponibili FROM libri WHERE isbn = ?", (isbn_db,))
                    esiste = c.fetchone()

                    if esiste:
                        c.execute("""UPDATE libri 
                                     SET copie_totali = copie_totali + ?, 
                                         copie_disponibili = copie_disponibili + ?, 
                                         titolo = ?, autore = ?, editore = ?, copertina_url = ? 
                                     WHERE isbn = ?""",
                                  (copie, copie, titolo_finale.strip(), autore_finale.strip(), editore_finale.strip(), copertina_url, isbn_db))
                        st.success(f"Aggiunte {copie} copie al libro '{titolo_finale}'.")
                    else:
                        c.execute("""INSERT INTO libri (isbn, titolo, autore, editore, copie_totali, copie_disponibili, copertina_url) 
                                     VALUES (?, ?, ?, ?, ?, ?, ?)""",
                              (isbn_db, titolo_finale.strip(), autore_finale.strip(), editore_finale.strip(), copie, copie, copertina_url))
                        st.success(f"Libro '{titolo_finale}' inserito a catalogo!")

                    conn.commit()
                    # Reset stati per prossimo inserimento
                    for k in ["titolo_temp", "autore_temp", "editore_temp", "cover_temp", "isbn_temp", "ultimo_isbn_cercato"]:
                        st.session_state[k] = ""
                    st.rerun()
                except Exception as e:
                    st.error(f"Errore durante il salvataggio: {e}")

    # --- TAB 2: MODIFICA QUANTITÀ O ELIMINA ---
    with tab_modifica:
        st.subheader("Aggiorna Disponibilità o Rimuovi Libro")
        libri_presenti = c.execute("SELECT isbn, titolo, autore, copie_totali, copie_disponibili, copertina_url FROM libri").fetchall()

        if not libri_presenti:
            st.info("Nessun libro a catalogo da modificare.")
        else:
            diz_mod = {f"{l[1]} ({l[2]}) - ISBN: {l[0]}": l for l in libri_presenti}
            libro_selezionato = st.selectbox("Seleziona il libro da modificare:", list(diz_mod.keys()))
            dati_sel = diz_mod[libro_selezionato]

            isbn_sel = dati_sel[0]
            tit_sel = dati_sel[1]
            totali_attuali = dati_sel[3]
            disp_attuali = dati_sel[4]
            in_prestito = totali_attuali - disp_attuali
            cover_sel = dati_sel[5]

            c_mod1, c_mod2 = st.columns([3, 1])
            with c_mod1:
                st.write(f"📌 **Riepilogo:**")
                st.write(f"- Copie Totali Possedute: **{totali_attuali}**")
                st.write(f"- Copie attualmente in Prestito: **{in_prestito}**")
                st.write(f"- Copie Disponibili al banco: **{disp_attuali}**")

                nuovo_totale = st.number_input(
                    "Imposta Nuovo Totale Copie Possedute:",
                    min_value=in_prestito,
                    value=totali_attuali,
                    step=1,
                    help=f"Non puoi scendere sotto {in_prestito} perché quelle copie sono attualmente fuori in prestito."
                )

            with c_mod2:
                if cover_sel:
                    st.image(cover_sel, width=120)

            col_aggiorna, col_cancella = st.columns([2, 1])
            if col_aggiorna.button("💾 Applica Nuova Quantità", type="primary"):
                nuove_disp = nuovo_totale - in_prestito
                c.execute("UPDATE libri SET copie_totali = ?, copie_disponibili = ? WHERE isbn = ?",
                          (nuovo_totale, nuove_disp, isbn_sel))
                conn.commit()
                st.success(f"Copie aggiornate con successo! Nuove disponibili: {nuove_disp}")
                st.rerun()

            if col_cancella.button("🗑️ Rimuovi Libro dal Catalogo"):
                if in_prestito > 0:
                    st.error(f"Impossibile eliminare: ci sono {in_prestito} copie attualmente in prestito!")
                else:
                    c.execute("DELETE FROM libri WHERE isbn = ?", (isbn_sel,))
                    conn.commit()
                    st.success(f"'{tit_sel}' è stato rimosso dal catalogo.")
                    st.rerun()

    st.divider()
    st.subheader("📚 Catalogo Libri Completo")
    catalogo = c.execute("SELECT copertina_url, isbn, titolo, autore, editore, copie_disponibili, copie_totali FROM libri").fetchall()
    if catalogo:
        df = pd.DataFrame(catalogo, columns=["Copertina", "ISBN", "Titolo", "Autore", "Editore", "Disp.", "Totali"])
        st.dataframe(
            df,
            column_config={
                "Copertina": st.column_config.ImageColumn("Copertina", width="small")
            },
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info("Nessun libro a catalogo.")

# -------------------------------------------------------------
# 2. REGISTRA PRESTITO
# -------------------------------------------------------------
elif menu == "📋 Registra Prestito":
    st.subheader("Registra Uscita Libro")

    utenti = c.execute("SELECT id, nome, cognome FROM utenti").fetchall()
    libri_disp = c.execute("SELECT isbn, titolo, autore, copie_disponibili, copertina_url FROM libri WHERE copie_disponibili > 0").fetchall()

    if not utenti:
        st.warning("Nessun utente registrato. Vai in 'Anagrafica Utenti'.")
    elif not libri_disp:
        st.warning("Nessun libro disponibile per il prestito.")
    else:
        diz_utenti = {f"{u[1]} {u[2]} (ID: {u[0]})": u[0] for u in utenti}
        diz_libri = {f"{b[1]} - {b[2]} (Copie: {b[3]})": (b[0], b[4]) for b in libri_disp}

        col1, col2 = st.columns([3, 1])
        with col1:
            utente_sel = st.selectbox("Seleziona Utente:", list(diz_utenti.keys()))
            libro_sel = st.selectbox("Seleziona Libro:", list(diz_libri.keys()))
            data_inizio = st.date_input("Data Prestito:", date.today())

        with col2:
            copertina_sel = diz_libri[libro_sel][1]
            if copertina_sel:
                st.image(copertina_sel, width=130, caption="Libro selezionato")

        if st.button("Conferma Prestito", type="primary"):
            id_utente = diz_utenti[utente_sel]
            isbn_libro = diz_libri[libro_sel][0]

            c.execute("INSERT INTO prestiti (isbn, utente_id, data_prestito, stato) VALUES (?, ?, ?, 'IN CORSO')",
                      (isbn_libro, id_utente, str(data_inizio)))
            c.execute("UPDATE libri SET copie_disponibili = copie_disponibili - 1 WHERE isbn = ?", (isbn_libro,))
            conn.commit()

            st.success("Prestito registrato!")
            st.rerun()

# -------------------------------------------------------------
# 3. RIENTRO PRESTITO
# -------------------------------------------------------------
elif menu == "🔄 Rientro Prestito":
    st.subheader("Registra Rientro Libro")

    query = '''SELECT p.id, l.titolo, u.nome, u.cognome, p.data_prestito, p.isbn, l.copertina_url
               FROM prestiti p
               JOIN libri l ON p.isbn = l.isbn
               JOIN utenti u ON p.utente_id = u.id
               WHERE p.stato = 'IN CORSO' '''
    in_corso = c.execute(query).fetchall()

    if not in_corso:
        st.info("Nessun prestito attivo al momento.")
    else:
        prestiti_attivi = {f"'{p[1]}' preso da {p[2]} {p[3]} (il {p[4]})": (p[0], p[5], p[6]) for p in in_corso}
        selezione = st.selectbox("Seleziona prestito da chiudere:", list(prestiti_attivi.keys()))

        id_prestito, isbn_libro, cover = prestiti_attivi[selezione]
        if cover:
            st.image(cover, width=120)

        if st.button("Segna come Restituito", type="primary"):
            oggi = str(date.today())
            c.execute("UPDATE prestiti SET stato = 'RESTITUITO', data_restituzione = ? WHERE id = ?", (oggi, id_prestito))
            c.execute("UPDATE libri SET copie_disponibili = copie_disponibili + 1 WHERE isbn = ?", (isbn_libro,))
            conn.commit()

            st.success("Libro restituito!")
            st.rerun()

# -------------------------------------------------------------
# 4. ANAGRAFICA UTENTI
# -------------------------------------------------------------
elif menu == "👥 Anagrafica Utenti":
    st.subheader("Aggiungi Nuovo Utente")
    col_n, col_c = st.columns(2)
    nome = col_n.text_input("Nome:")
    cognome = col_c.text_input("Cognome:")
    col_e, col_t = st.columns(2)
    email = col_e.text_input("Email (opzionale):")
    telefono = col_t.text_input("Telefono (opzionale):")

    if st.button("Registra Utente", type="primary"):
        if nome.strip() and cognome.strip():
            c.execute("INSERT INTO utenti (nome, cognome, email, telefono) VALUES (?, ?, ?, ?)",
                      (nome.strip(), cognome.strip(), email.strip(), telefono.strip()))
            conn.commit()
            st.success(f"Utente '{nome} {cognome}' registrato!")
            st.rerun()
        else:
            st.error("Nome e Cognome sono obbligatori.")

    st.divider()
    st.subheader("Utenti Registrati")
    lista_utenti = c.execute("SELECT id, nome, cognome, email, telefono FROM utenti").fetchall()
    if lista_utenti:
        df_u = pd.DataFrame(lista_utenti, columns=["ID", "Nome", "Cognome", "Email", "Telefono"])
        st.dataframe(df_u, use_container_width=True, hide_index=True)
    else:
        st.info("Nessun utente registrato.")

# -------------------------------------------------------------
# 5. STORICO PRESTITI
# -------------------------------------------------------------
elif menu == "📊 Storico Prestiti":
    st.subheader("Riepilogo Totale Movimenti")
    query = '''SELECT l.copertina_url, l.titolo, p.isbn, u.nome || ' ' || u.cognome as utente, 
                      p.data_prestito, p.data_restituzione, p.stato 
               FROM prestiti p
               JOIN libri l ON p.isbn = l.isbn
               JOIN utenti u ON p.utente_id = u.id
               ORDER BY p.id DESC'''
    storico = c.execute(query).fetchall()
    if storico:
        df_s = pd.DataFrame(storico, columns=["Copertina", "Titolo", "ISBN", "Utente", "Data Prestito", "Data Reso", "Stato"])
        st.dataframe(
            df_s,
            column_config={
                "Copertina": st.column_config.ImageColumn("Copertina", width="small")
            },
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info("Nessuna movimentazione registrata.")

conn.close()
