import streamlit as st
import sqlite3
import random
from datetime import datetime
import time
import pandas as pd

# =====================================================
# CONFIGURAÇÕES
# =====================================================
DB_PATH = "bingo.db"
APP_TITLE = "Bingo Humano Digital 3.0"
MOD_PIN = st.secrets.get("MOD_PIN", "1234")

# =====================================================
# BANCO DE DADOS
# =====================================================
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


def init_db():
    conn = get_conn()
    conn.executescript("""
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
            created_at TEXT NOT NULL,
            UNIQUE(guesser_id, fact_id),
            FOREIGN KEY(guesser_id) REFERENCES players(id),
            FOREIGN KEY(fact_id) REFERENCES facts(id),
            FOREIGN KEY(guessed_player_id) REFERENCES players(id)
        );
    """)
    conn.commit()


def set_setting(key, value):
    conn = get_conn()
    conn.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()


def get_setting(key, default=""):
    conn = get_conn()
    cur = conn.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = cur.fetchone()
    return row[0] if row else default


def get_or_create_player(name):
    conn = get_conn()
    now = datetime.utcnow().isoformat()
    try:
        conn.execute("INSERT INTO players(name, created_at) VALUES(?,?)", (name, now))
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    cur = conn.execute("SELECT id FROM players WHERE name=?", (name,))
    row = cur.fetchone()
    return row[0] if row else None


def upsert_facts(player_id, facts):
    conn = get_conn()
    conn.execute("DELETE FROM facts WHERE player_id=?", (player_id,))
    for f in facts:
        f = f.strip()
        if f:
            conn.execute("INSERT INTO facts(player_id, text) VALUES(?,?)", (player_id, f))
    conn.commit()


def list_other_players(player_id):
    conn = get_conn()
    cur = conn.execute("SELECT id, name FROM players WHERE id != ? ORDER BY name", (player_id,))
    return cur.fetchall()


# =====================================================
# LISTAGEM INTELIGENTE DE CURIOSIDADES (dinâmica)
# =====================================================
def list_all_facts_excluding_self(player_id):
    conn = get_conn()
    total_facts = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]

    # Se o total mudou, recarrega
    if "facts_cache" not in st.session_state or st.session_state.get("facts_total") != total_facts:
        cur = conn.execute("""
            SELECT f.id, f.text, f.player_id
            FROM facts f
            WHERE f.player_id != ?
        """, (player_id,))
        facts = cur.fetchall()
        random.shuffle(facts)
        st.session_state["facts_cache"] = facts
        st.session_state["facts_total"] = total_facts
        st.session_state["facts_order"] = [f[0] for f in facts]
    else:
        facts = st.session_state["facts_cache"]

    facts = sorted(facts, key=lambda x: st.session_state["facts_order"].index(x[0]))
    return facts


# =====================================================
# REGISTRO DE RESPOSTAS (robusto)
# =====================================================
def register_guess(guesser_id, fact_id, guessed_player_id):
    now = datetime.utcnow().isoformat()
    conn = get_conn()
    try:
        # Valida se o fact ainda existe
        cur = conn.execute("SELECT COUNT(*) FROM facts WHERE id=?", (fact_id,))
        if cur.fetchone()[0] == 0:
            st.warning("Essa curiosidade não existe mais (jogo atualizado). Recarregue a página.")
            return

        cur = conn.execute(
            "SELECT id FROM guesses WHERE guesser_id=? AND fact_id=?",
            (guesser_id, fact_id)
        )
        if cur.fetchone():
            conn.execute(
                "UPDATE guesses SET guessed_player_id=?, created_at=? WHERE guesser_id=? AND fact_id=?",
                (guessed_player_id, now, guesser_id, fact_id)
            )
        else:
            conn.execute(
                "INSERT OR IGNORE INTO guesses(guesser_id,fact_id,guessed_player_id,created_at) VALUES(?,?,?,?)",
                (guesser_id, fact_id, guessed_player_id, now)
            )
        conn.commit()
    except sqlite3.OperationalError:
        st.error("⚠️ O banco está ocupado, tente novamente em alguns segundos.")
    except Exception as e:
        st.error(f"Erro ao registrar resposta: {e}")


def leaderboard(limit=5):
    conn = get_conn()
    cur = conn.execute("""
        SELECT p.name, COUNT(g.id) as score
        FROM players p
        LEFT JOIN guesses g ON g.guesser_id = p.id
        GROUP BY p.id
        ORDER BY score DESC, p.name ASC
        LIMIT ?
    """, (limit,))
    return cur.fetchall()

# =====================================================
# INTERFACE JOGADOR
# =====================================================
def page_player():
    st.title("🎯 Bingo Humano Digital — Jogador")

    st.session_state.setdefault("player_name", "")
    st.session_state.setdefault("player_id", None)
    st.session_state.setdefault("facts_loaded", False)
    st.session_state.setdefault("ready_to_play", False)

    started = get_setting("started", "0") == "1"
    finished = get_setting("finished", "0") == "1"

    if st.session_state["player_id"] is None:
        with st.form("frm_name"):
            name = st.text_input("Digite seu nome completo")
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

    if not st.session_state["facts_loaded"]:
        st.info("✍️ Cadastre 3 curiosidades sobre você.")
        with st.form("frm_facts"):
            facts = [st.text_input(f"Curiosidade {i+1}") for i in range(3)]
            ok = st.form_submit_button("Salvar minhas 3 frases")
        if ok:
            if any(not f.strip() for f in facts):
                st.error("Por favor, preencha as 3 frases.")
            else:
                upsert_facts(pid, facts)
                st.session_state["facts_loaded"] = True
                st.success("✅ Frases salvas!")
                if started:
                    st.session_state["ready_to_play"] = True
                    st.toast("O jogo já está em andamento! Boa sorte 🎯")
                    st.rerun()
                else:
                    st.rerun()
        st.stop()

    if not started and st.session_state["facts_loaded"]:
        st.info("⏳ Aguardando o moderador iniciar o jogo...")
        time.sleep(5)
        st.rerun()

    if started and not st.session_state["ready_to_play"] and not finished:
        st.markdown("<div class='banner'>🚀 O moderador iniciou o jogo! Clique abaixo para começar.</div>", unsafe_allow_html=True)
        if st.button("🎯 Iniciar o Jogo!", use_container_width=True):
            st.session_state["ready_to_play"] = True
            st.toast("Boa sorte! O jogo começou 🎉")
            st.rerun()
        st.stop()

    if started and st.session_state["ready_to_play"] and not finished:
        st.success("🟢 O jogo está em andamento!")
        facts = list_all_facts_excluding_self(pid)
        others = list_other_players(pid)
        names = [n for _, n in others]
        name_to_id = {n: i for i, n in others}

        conn = get_conn()
        cur = conn.execute("SELECT fact_id FROM guesses WHERE guesser_id=?", (pid,))
        answered = {row[0] for row in cur.fetchall()}

        try:
            with open("style.css") as f:
                st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)
        except FileNotFoundError:
            st.warning("⚠️ Arquivo de estilo não encontrado (style.css).")

        for fact_id, fact_text, _ in facts:
            answered_flag = fact_id in answered
            card_class = "card answered" if answered_flag else "card"
            st.markdown(f"<div class='{card_class}'><b>{fact_text}</b></div>", unsafe_allow_html=True)
            with st.form(f"frm_{fact_id}"):
                guess_name = st.selectbox("Quem é essa pessoa?", [""] + names, key=f"guess_{fact_id}")
                submit = st.form_submit_button("Confirmar resposta")
            if submit:
                if not guess_name:
                    st.warning("Selecione um nome para confirmar.")
                    st.stop()
                register_guess(pid, fact_id, name_to_id[guess_name])
                st.toast("Resposta registrada! 👏")
                st.rerun()

    if finished:
        st.warning("⛔ O jogo foi encerrado pelo moderador.")
        st.subheader("🏆 Top 5 jogadores")
        data = leaderboard()
        for i, (name, score) in enumerate(data, start=1):
            st.markdown(f"<div class='rank rank{i}'>🥇 {name} — {score} pontos</div>", unsafe_allow_html=True)

# =====================================================
# INTERFACE MODERADOR
# =====================================================
def page_moderator():
    st.title("🧭 Painel do Moderador — Bingo Humano")

    pin = st.text_input("PIN do moderador", type="password")
    if pin != MOD_PIN:
        st.info("Digite o PIN para acessar o painel.")
        st.stop()

    started = get_setting("started", "0") == "1"
    finished = get_setting("finished", "0") == "1"

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        if not started:
            if st.button("🚀 Iniciar jogo"):
                set_setting("started", "1")
                set_setting("finished", "0")
                st.toast("O jogo foi iniciado!")
                st.rerun()
        else:
            st.success("🟢 Jogo em andamento")
    with col2:
        if st.button("⛔ Encerrar jogo"):
            set_setting("finished", "1")
            set_setting("started", "0")
            st.warning("Jogo encerrado.")
            st.rerun()
    with col3:
        if st.button("🔄 Atualizar métricas"):
            st.rerun()
    with col4:
        if st.button("🧹 Resetar tudo"):
            conn = get_conn()
            conn.executescript("DELETE FROM guesses; DELETE FROM facts; DELETE FROM players; DELETE FROM settings;")
            conn.commit()
            st.warning("Banco limpo.")
            st.rerun()

    conn = get_conn()
    total_players = conn.execute("SELECT COUNT(*) FROM players").fetchone()[0]
    total_facts = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
    total_guesses = conn.execute("SELECT COUNT(*) FROM guesses").fetchone()[0]

    colA, colB, colC = st.columns(3)
    colA.metric("Participantes", total_players)
    colB.metric("Curiosidades", total_facts)
    colC.metric("Respostas", total_guesses)

    st.subheader("📋 Jogadores e Curiosidades Cadastradas")
    df_players = pd.read_sql_query("""
        SELECT p.name AS Jogador, COUNT(f.id) AS Curiosidades
        FROM players p
        LEFT JOIN facts f ON p.id = f.player_id
        GROUP BY p.id
        ORDER BY p.name
    """, conn)
    st.dataframe(df_players, use_container_width=True)

    st.subheader("🎯 Jogadores e Respostas Dadas (Engajamento)")
    df_guesses = pd.read_sql_query("""
        SELECT p.name AS Jogador, COUNT(g.id) AS Respostas
        FROM players p
        LEFT JOIN guesses g ON p.id = g.guesser_id
        GROUP BY p.id
        ORDER BY Respostas DESC, p.name
    """, conn)
    st.dataframe(df_guesses, use_container_width=True)

    st.subheader("🏆 Ranking Top 5")
    data = leaderboard()
    for i, (name, score) in enumerate(data, start=1):
        st.markdown(f"<div class='rank rank{i}'>🥇 {name} — {score} pontos</div>", unsafe_allow_html=True)

# =====================================================
# MAIN
# =====================================================
def main():
    st.set_page_config(page_title=APP_TITLE, page_icon="🎯")
    init_db()
    params = st.query_params
    mode = params["mode"].lower() if "mode" in params else "player"
    if mode == "moderator":
        page_moderator()
    else:
        page_player()

if __name__ == "__main__":
    main()
