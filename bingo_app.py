import sqlite3
import random
from datetime import datetime
import streamlit as st

# =====================================================
# CONFIGURAÇÕES
# =====================================================
DB_PATH = "bingo.db"
APP_TITLE = "Bingo Humano Digital 2.0"
MOD_PIN = st.secrets.get("MOD_PIN", "3535")

# =====================================================
# BANCO DE DADOS
# =====================================================
@st.cache_resource
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db():
    conn = get_conn()
    conn.executescript(
        """
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
        """
    )
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


def get_or_create_player(name: str):
    conn = get_conn()
    now = datetime.utcnow().isoformat()
    try:
        conn.execute(
            "INSERT INTO players(name, created_at) VALUES(?, ?)",
            (name.strip(), now),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    cur = conn.execute("SELECT id FROM players WHERE name=?", (name.strip(),))
    row = cur.fetchone()
    return row[0] if row else None


def upsert_facts(player_id: int, facts: list[str]):
    conn = get_conn()
    conn.execute("DELETE FROM facts WHERE player_id=?", (player_id,))
    for f in facts:
        f = f.strip()
        if f:
            conn.execute("INSERT INTO facts(player_id, text) VALUES(?,?)", (player_id, f))
    conn.commit()


def list_other_players(player_id: int):
    conn = get_conn()
    cur = conn.execute(
        "SELECT id, name FROM players WHERE id != ? ORDER BY name", (player_id,)
    )
    return cur.fetchall()


def list_all_facts_excluding_self(player_id: int):
    conn = get_conn()
    cur = conn.execute(
        """
        SELECT f.id, f.text, f.player_id
        FROM facts f
        WHERE f.player_id != ?
        """,
        (player_id,),
    )
    facts = cur.fetchall()
    random.shuffle(facts)
    return facts


def player_score(player_id: int) -> int:
    conn = get_conn()
    cur = conn.execute(
        "SELECT COUNT(*) FROM guesses WHERE guesser_id=?", (player_id,)
    )
    return cur.fetchone()[0]


def register_guess(guesser_id: int, fact_id: int, guessed_player_id: int):
    now = datetime.utcnow().isoformat()
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO guesses(guesser_id,fact_id,guessed_player_id,created_at)"
            " VALUES(?,?,?,?)",
            (guesser_id, fact_id, guessed_player_id, now),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        pass


def leaderboard():
    conn = get_conn()
    cur = conn.execute(
        """
        SELECT p.name, COUNT(g.id) as guesses
        FROM players p
        LEFT JOIN guesses g ON g.guesser_id = p.id
        GROUP BY p.id
        ORDER BY guesses DESC, p.name ASC
        """
    )
    return cur.fetchall()


# =====================================================
# INTERFACE: JOGADOR
# =====================================================
def page_player():
    st.title("🎯 Bingo Humano Digital 2.0 — Jogador")

    st.session_state.setdefault("player_name", "")
    st.session_state.setdefault("player_id", None)

    # Sincronização de status do jogo
    started = get_setting("started", "0") == "1"
    finished = get_setting("finished", "0") == "1"

    # Etapa 1 — Registro do jogador
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

    # Etapa 2 — Cadastro das curiosidades
    if not started:
        st.info("⏳ Aguarde o moderador iniciar o jogo.")
        with st.form("frm_facts"):
            st.write("✍️ Cadastre 5 curiosidades sobre você:")
            facts = [st.text_input(f"Curiosidade {i+1}") for i in range(5)]
            ok = st.form_submit_button("Salvar minhas 5 frases")
        if ok:
            if any(not f.strip() for f in facts):
                st.error("Por favor, preencha as 5 frases.")
            else:
                upsert_facts(pid, facts)
                st.success("✅ Frases salvas com sucesso. Aguarde o início do jogo.")
        st.stop()

    # Etapa 3 — Jogo em andamento
    if started and not finished:
        st.success("🟢 O jogo está em andamento! Boa sorte!")
        facts = list_all_facts_excluding_self(pid)
        others = list_other_players(pid)
        names = [n for _, n in others]
        name_to_id = {n: i for i, n in others}

        for fact_id, fact_text, _ in facts:
            with st.form(f"frm_{fact_id}"):
                st.markdown(f"**{fact_text}**")
                guess_name = st.selectbox(
                    "Quem é essa pessoa?", [""] + names, index=0
                )
                submit = st.form_submit_button("Confirmar resposta")
            if submit:
                if not guess_name:
                    st.warning("Selecione um nome para confirmar.")
                    st.stop()
                register_guess(pid, fact_id, name_to_id[guess_name])
                st.info("Resposta registrada.")
                st.rerun()

        st.caption("Você pode pular perguntas e responder em qualquer ordem.")
        st.caption("As respostas corretas serão reveladas ao final.")

    # Etapa 4 — Jogo encerrado
    if finished:
        st.warning("⛔ O jogo foi encerrado pelo moderador.")
        st.markdown("### 🏆 Resultado parcial:")
        score = player_score(pid)
        st.metric("Respostas registradas", score)
        st.info("O moderador anunciará o vencedor em breve.")
        st.stop()


# =====================================================
# INTERFACE: MODERADOR
# =====================================================
def page_moderator():
    st.set_page_config(layout="wide")
    st.title("🧭 Painel do Moderador — Bingo Humano Digital 2.0")

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
                st.success("Jogo iniciado!")
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
            conn.executescript(
                "DELETE FROM guesses; DELETE FROM facts; DELETE FROM players; DELETE FROM settings;"
            )
            conn.commit()
            st.warning("Banco limpo. Novo jogo pode começar.")
            st.rerun()

    # Métricas
    conn = get_conn()
    total_players = conn.execute("SELECT COUNT(*) FROM players").fetchone()[0]
    total_facts = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
    total_guesses = conn.execute("SELECT COUNT(*) FROM guesses").fetchone()[0]

    colA, colB, colC = st.columns(3)
    colA.metric("Participantes", total_players)
    colB.metric("Frases cadastradas", total_facts)
    colC.metric("Respostas registradas", total_guesses)

    st.divider()
    st.subheader("🏆 Ranking de acertos")
    data = leaderboard()
    if data:
        st.table({"Participante": [d[0] for d in data], "Respostas": [int(d[1]) for d in data]})
    else:
        st.write("Nenhum participante ainda.")

    st.divider()
    st.subheader("📋 Status dos jogadores")
    cur = conn.execute(
        """
        SELECT p.name,
               (SELECT COUNT(*) FROM facts f WHERE f.player_id=p.id) AS frases,
               (SELECT COUNT(*) FROM guesses g WHERE g.guesser_id=p.id) AS respostas
        FROM players p ORDER BY p.name
        """
    )
    st.dataframe(
        [{"Nome": r[0], "Frases": r[1], "Respostas": r[2]} for r in cur.fetchall()],
        use_container_width=True,
    )


# =====================================================
# MAIN
# =====================================================
def main():
    st.set_page_config(page_title=APP_TITLE, page_icon="🎯")
    init_db()

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
