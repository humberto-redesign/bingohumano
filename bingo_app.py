import sqlite3
from datetime import datetime
from typing import List, Tuple
import streamlit as st

# =========================
# CONFIGURAÇÕES GERAIS
# =========================
DB_PATH = "bingo.db"
APP_TITLE = "RDN Bingo Humano"
MOD_PIN = st.secrets.get("MOD_PIN", "3535")  # definir em .streamlit/secrets.toml se quiser

# =========================
# BANCO DE DADOS
# =========================
@st.cache_resource
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn

def init_db():
    conn = get_conn()
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            FOREIGN KEY(player_id) REFERENCES players(id)
        );
        CREATE TABLE IF NOT EXISTS guesses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guesser_id INTEGER NOT NULL,
            fact_id INTEGER NOT NULL,
            guessed_player_id INTEGER NOT NULL,
            is_correct INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(guesser_id, fact_id),
            FOREIGN KEY(guesser_id) REFERENCES players(id),
            FOREIGN KEY(fact_id) REFERENCES facts(id),
            FOREIGN KEY(guessed_player_id) REFERENCES players(id)
        );
    ''')
    conn.commit()

def set_setting(key: str, value: str):
    conn = get_conn()
    conn.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()

def get_setting(key: str, default: str = "") -> str:
    conn = get_conn()
    cur = conn.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = cur.fetchone()
    return row[0] if row else default

def get_or_create_player(name: str):
    conn = get_conn()
    now = datetime.utcnow().isoformat()
    try:
        conn.execute("INSERT INTO players(name, created_at) VALUES(?, ?)", (name.strip(), now))
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    cur = conn.execute("SELECT id FROM players WHERE name=?", (name.strip(),))
    row = cur.fetchone()
    return row[0] if row else None

def upsert_facts(player_id: int, facts: List[str]):
    conn = get_conn()
    conn.execute("DELETE FROM facts WHERE player_id=?", (player_id,))
    for t in facts:
        t = t.strip()
        if not t:
            continue
        conn.execute("INSERT INTO facts(player_id, text) VALUES(?,?)", (player_id, t))
    conn.commit()

def list_other_players(player_id: int) -> List[Tuple[int, str]]:
    conn = get_conn()
    cur = conn.execute("SELECT id, name FROM players WHERE id != ? ORDER BY name", (player_id,))
    return cur.fetchall()

def list_facts_for_player(player_id: int, limit: int = 20) -> List[Tuple[int, str, int]]:
    conn = get_conn()
    cur = conn.execute('''
        SELECT f.id, f.text, f.player_id
        FROM facts f
        WHERE f.player_id != ?
          AND f.id NOT IN (SELECT fact_id FROM guesses WHERE guesser_id = ? AND is_correct = 1)
        ORDER BY RANDOM() LIMIT ?
    ''', (player_id, player_id, limit))
    return cur.fetchall()

def player_score(player_id: int) -> int:
    conn = get_conn()
    cur = conn.execute("SELECT COUNT(*) FROM guesses WHERE guesser_id=? AND is_correct=1", (player_id,))
    return cur.fetchone()[0]

def register_guess(guesser_id: int, fact_id: int, guessed_player_id: int):
    conn = get_conn()
    cur = conn.execute("SELECT player_id FROM facts WHERE id=?", (fact_id,))
    row = cur.fetchone()
    if not row:
        return False, None
    true_author = row[0]
    is_correct = 1 if (guessed_player_id == true_author) else 0
    now = datetime.utcnow().isoformat()
    try:
        conn.execute(
            "INSERT INTO guesses(guesser_id,fact_id,guessed_player_id,is_correct,created_at) VALUES(?,?,?,?,?)",
            (guesser_id, fact_id, guessed_player_id, is_correct, now),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    return (is_correct == 1), true_author

def leaderboard():
    conn = get_conn()
    cur = conn.execute('''
        SELECT p.name, COALESCE(SUM(g.is_correct),0) as pts
        FROM players p
        LEFT JOIN guesses g ON g.guesser_id = p.id
        GROUP BY p.id
        ORDER BY pts DESC, p.name ASC
    ''')
    return cur.fetchall()

# =========================
# INTERFACE - JOGADOR
# =========================
def page_player():
    st.title(APP_TITLE + " — Jogador")
    st.session_state.setdefault("player_name", "")
    st.session_state.setdefault("player_id", None)

    started = get_setting("started", "0") == "1"
    winner_id = get_setting("winner_id", "")

    if st.session_state["player_id"] is None:
        with st.form("frm_name"):
            name = st.text_input("Seu nome completo", value=st.session_state["player_name"])
            submitted = st.form_submit_button("Entrar")
        if submitted:
            if not name.strip():
                st.warning("Digite seu nome para continuar.")
                st.stop()
            pid = get_or_create_player(name.strip())
            st.session_state["player_name"] = name.strip()
            st.session_state["player_id"] = pid
            st.success(f"Bem-vindo, {name.strip()}!")
            st.rerun()

    if st.session_state["player_id"] is None:
        st.stop()

    pid = st.session_state["player_id"]

    # Etapa 1: Cadastro das curiosidades
    if not started:
        st.info("Etapa 1: Cadastre 5 curiosidades sobre você. O moderador iniciará o jogo em seguida.")
        with st.form("frm_facts"):
            facts = [st.text_input(f"Curiosidade {i+1}") for i in range(5)]
            ok = st.form_submit_button("Salvar minhas 5 frases")
        if ok:
            if any(not f.strip() for f in facts):
                st.error("Por favor, preencha as 5 frases.")
            else:
                upsert_facts(pid, facts)
                st.success("Suas frases foram salvas. Aguarde o moderador iniciar o jogo!")
        st.stop()

    # Etapa 2: Adivinhação
    pts = player_score(pid)
    st.progress(min(pts/5, 1.0), text=f"Pontos: {pts}/5")

    if winner_id:
        wname = get_conn().execute("SELECT name FROM players WHERE id=?", (int(winner_id),)).fetchone()
        if wname:
            st.info(f"🏆 Já temos um vencedor: {wname[0]}. Continue jogando por diversão!")

    facts = list_facts_for_player(pid, limit=10)
    if not facts:
        st.info("Você já validou todas as frases disponíveis. Converse mais com o grupo!")
        st.stop()

    players = list_other_players(pid)
    name_to_id = {n: i for i, n in players}
    names = [n for _, n in players]

    for fact_id, fact_text, _ in facts[:5]:
        with st.form(f"guess_{fact_id}"):
            st.write(f"**Frase:** {fact_text}")
            guess_name = st.selectbox("Quem é essa pessoa?", options=[""] + names, index=0)
            submit = st.form_submit_button("Registrar ponto")
        if submit:
            if not guess_name:
                st.warning("Selecione um nome.")
                st.stop()
            ok, _ = register_guess(pid, fact_id, name_to_id[guess_name])
            if ok:
                st.success("🎯 Acertou!")
                pts = player_score(pid)
                if pts >= 5 and not get_setting("winner_id", ""):
                    set_setting("winner_id", str(pid))
                    st.balloons()
                    st.success("🎉 Você completou 5 acertos! Procure o moderador para receber o prêmio.")
                st.experimental_rerun()
            else:
                st.error("❌ Ainda não! Essa curiosidade não é dessa pessoa.")
                st.experimental_rerun()

# =========================
# INTERFACE - MODERADOR
# =========================
def page_moderator():
    st.set_page_config(page_title="Painel do Moderador", page_icon="🧭", layout="wide")
    st.title("🧭 Painel do Moderador — Bingo Humano Digital")
    st.markdown("### Controle do jogo e visualização em tempo real")
    pin = st.text_input("PIN do moderador", type="password")
    if pin != MOD_PIN:
        st.info("Digite o PIN para administrar o jogo.")
        st.stop()

    started = get_setting("started", "0") == "1"
    winner_id = get_setting("winner_id", "")

    col1, col2, col3 = st.columns(3)
    with col1:
        if not started and st.button("🚀 Iniciar jogo"):
            set_setting("started", "1")
            st.success("Jogo iniciado!")
    with col2:
        if st.button("⏸️ Pausar jogo"):
            set_setting("started", "0")
            st.warning("Jogo pausado.")
    with col3:
        if st.button("🧹 Resetar tudo"):
            conn = get_conn()
            conn.executescript("DELETE FROM guesses; DELETE FROM facts; DELETE FROM players; DELETE FROM settings;")
            conn.commit()
            st.warning("Banco limpo. Novo jogo pode começar.")

    # Painel de status
    conn = get_conn()
    total_players = conn.execute("SELECT COUNT(*) FROM players").fetchone()[0]
    total_facts = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
    total_guesses = conn.execute("SELECT COUNT(*) FROM guesses").fetchone()[0]

    st.metric("Participantes", total_players)
    st.metric("Frases cadastradas", total_facts)
    st.metric("Adivinhações", total_guesses)

    if winner_id:
        wname = conn.execute("SELECT name FROM players WHERE id=?", (int(winner_id),)).fetchone()
        if wname:
            st.success(f"🏆 Vencedor: {wname[0]}")

    st.subheader("Ranking")
    data = leaderboard()
    if data:
        st.table({"Participante": [d[0] for d in data], "Pontos": [int(d[1]) for d in data]})
    else:
        st.write("Sem participantes ainda.")

# =========================
# MAIN
# =========================
def main():
    st.set_page_config(page_title=APP_TITLE, page_icon="🎯", layout="centered")
    init_db()
# Detecta o modo pela URL
params = st.query_params

if "mode" in params:
    mode = params["mode"].lower()
else:
    mode = "player"

if mode == "moderator":
    page_moderator()
else:
    page_player()


if __name__ == "__main__":
    main()
