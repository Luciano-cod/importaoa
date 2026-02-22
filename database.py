"""
database.py — Camada de base de dados SQLite para o ImportAOA

Toda a lógica de BD fica aqui isolada do resto da aplicação.
A app.py nunca escreve SQL directamente — chama sempre funções deste módulo.
Isto chama-se "separação de responsabilidades" e torna o código muito mais fácil
de manter e testar.

Estrutura das tabelas:
  taxas_cambio       — taxas BNA, AirTM, banco, etc.
  redirecionadoras   — MyUS, Stackry, etc.
  encomendas         — cada compra no eBay
  historico_taxas    — registo histórico de alterações de câmbio (bonus!)
"""

import sqlite3
import os
from datetime import date, datetime
from contextlib import contextmanager
from werkzeug.security import generate_password_hash, check_password_hash

# O ficheiro .db fica no disco persistente do Render (/var/data)
# ou na pasta da aplicação em desenvolvimento local.
import os
_DATA_DIR = os.environ.get("RENDER_DATA_DIR", os.path.dirname(__file__))
# Cria a pasta automaticamente se não existir (necessário no Render)
os.makedirs(_DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(_DATA_DIR, "importaoa.db")


# ─── GESTÃO DE CONEXÃO ──────────────────────────────────────────────────────

@contextmanager
def get_db():
    """
    Context manager para conexões à base de dados.

    Uso:
        with get_db() as db:
            db.execute("SELECT ...")

    O 'with' garante que a conexão é sempre fechada, mesmo se ocorrer
    um erro a meio. É o equivalente ao try/finally mas muito mais limpo.

    row_factory = sqlite3.Row faz com que as linhas se comportem como
    dicionários — podes aceder a colunas por nome (row["nome"]) em vez
    de por índice (row[0]).
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")  # Activar chaves estrangeiras
    conn.execute("PRAGMA journal_mode = WAL")  # Melhor performance em leitura
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()  # Se algo correr mal, desfaz tudo — é tudo ou nada
        raise
    finally:
        conn.close()


# ─── CRIAÇÃO DO SCHEMA ─────────────────────────────────────────────────────

def init_db():
    """
    Cria todas as tabelas se não existirem ainda.
    Chamado uma vez quando a aplicação arranca.

    'CREATE TABLE IF NOT EXISTS' é idempotente — podes chamar isto
    quantas vezes quiseres sem destruir dados existentes.
    """
    with get_db() as db:
        db.executescript("""
            -- Taxas de câmbio (BNA, AirTM, banco, etc.)
            CREATE TABLE IF NOT EXISTS taxas_cambio (
                id          TEXT PRIMARY KEY,      -- 'airtm', 'bna', etc.
                nome        TEXT NOT NULL,
                taxa_base   REAL NOT NULL DEFAULT 0,
                comissao    REAL NOT NULL DEFAULT 0,  -- percentagem, ex: 3.5
                obs         TEXT DEFAULT '',
                atualizado  TEXT DEFAULT (date('now'))
            );

            -- Redirecionadoras (MyUS, Stackry, etc.)
            CREATE TABLE IF NOT EXISTS redirecionadoras (
                id            TEXT PRIMARY KEY,
                nome          TEXT NOT NULL,
                website       TEXT DEFAULT '',
                pais          TEXT DEFAULT '',
                cidade        TEXT DEFAULT '',
                estado        TEXT DEFAULT '',
                cep           TEXT DEFAULT '',
                tarifa_kg     REAL NOT NULL DEFAULT 0,  -- USD por kg
                consolidacao  INTEGER DEFAULT 1,         -- 0=Não, 1=Sim
                avaliacao     INTEGER DEFAULT 3,
                obs           TEXT DEFAULT ''
            );

            -- Encomendas — tabela principal da aplicação
            -- As colunas 'redirecionadora_id' são chaves estrangeiras:
            -- garantem que não podes ter uma encomenda a apontar para
            -- uma redirecionadora que não existe.
            CREATE TABLE IF NOT EXISTS encomendas (
                id                TEXT PRIMARY KEY,
                produto           TEXT NOT NULL,
                vendedor          TEXT DEFAULT '',
                url               TEXT DEFAULT '',
                categoria         TEXT DEFAULT 'Outro',
                data_compra       TEXT DEFAULT (date('now')),
                redirecionadora_id TEXT REFERENCES redirecionadoras(id),
                quantidade        INTEGER NOT NULL DEFAULT 1,
                preco_usd         REAL NOT NULL DEFAULT 0,
                frete_ebay_usd    REAL DEFAULT 0,
                peso_kg           REAL DEFAULT 0,
                frete_redir_usd   REAL DEFAULT 0,
                seguro_pct        REAL DEFAULT 1.5,
                outras_taxas      REAL DEFAULT 0,
                estado            TEXT DEFAULT 'Processando',
                tracking          TEXT DEFAULT '',
                notas             TEXT DEFAULT '',
                criado_em         TEXT DEFAULT (datetime('now'))
            );

            -- Histórico de taxas — cada vez que alguém actualiza uma taxa,
            -- guardamos o valor antigo aqui. Assim tens um historial completo
            -- de como o AOA/USD evoluiu ao longo do tempo. Isto é uma funcionalidade
            -- que os ficheiros JSON simplesmente não conseguem fazer facilmente.
            CREATE TABLE IF NOT EXISTS historico_taxas (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                taxa_id     TEXT NOT NULL,
                taxa_base   REAL NOT NULL,
                comissao    REAL NOT NULL,
                registado_em TEXT DEFAULT (datetime('now'))
            );

            -- Utilizadores — autenticação e gestão de contas
            CREATE TABLE IF NOT EXISTS utilizadores (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                nome         TEXT NOT NULL,
                apelido      TEXT NOT NULL DEFAULT '',
                email        TEXT NOT NULL UNIQUE,
                senha_hash   TEXT NOT NULL,
                criado_em    TEXT DEFAULT (datetime('now'))
            );
        """)

    # Inserir dados iniciais se as tabelas estiverem vazias
    _seed_initial_data()


def _seed_initial_data():
    """Popula as tabelas com dados de exemplo na primeira vez."""
    with get_db() as db:
        # Só insere se ainda não houver dados
        if db.execute("SELECT COUNT(*) FROM taxas_cambio").fetchone()[0] > 0:
            return

        db.executemany(
            "INSERT INTO taxas_cambio (id, nome, taxa_base, comissao, obs) VALUES (?,?,?,?,?)",
            [
                ("bna",      "BNA (Banco Nacional)",        930.0,  0.0, "Taxa oficial — difícil aceder para particulares"),
                ("banco",    "Banco Comercial",              940.0,  2.5, "Taxa do banco + spread bancário embutido"),
                ("airtm",   "AirTM",                        970.0,  3.5, "Taxa AirTM varia conforme oferta/procura. Comissão ~3-5%"),
                ("informal", "Mercado Informal (ref.)",     1020.0,  0.0, "Referência apenas — risco legal"),
                ("wise",     "Wise / Remessa Internacional", 950.0,  1.8, "Para receber de fora — referência útil"),
            ]
        )

        db.executemany(
            """INSERT INTO redirecionadoras
               (id, nome, website, pais, cidade, estado, cep, tarifa_kg, consolidacao, avaliacao, obs)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            [
                ("myus",      "MyUS",      "myus.com",      "EUA (Florida)",         "Miami",     "FL", "33126", 28.62, 1, 5, "Muito popular, boa app móvel"),
                ("stackry",   "Stackry",   "stackry.com",   "EUA (New Hampshire)",   "Nashua",    "NH", "03060", 20.90, 1, 4, "Sem taxas de adesão, sem IVA em NH"),
                ("shipito",   "Shipito",   "shipito.com",   "EUA (Oregon)",          "Portland",  "OR", "97218", 19.25, 1, 4, "Planos premium com descontos"),
                ("shipto",    "ShipTo",    "ship.to",       "EUA (Delaware)",        "Wilmington","DE", "19801", 22.44, 1, 4, "Interface moderna, bons parceiros"),
                ("parcelbee", "ParcelBee", "parcelbee.com", "EUA (Nova York)",       "New York",  "NY", "10017", 25.96, 1, 4, "Interface em português"),
            ]
        )

        db.executemany(
            """INSERT INTO encomendas
               (id, produto, vendedor, categoria, data_compra, redirecionadora_id,
                quantidade, preco_usd, frete_ebay_usd, peso_kg, frete_redir_usd,
                seguro_pct, outras_taxas, estado, tracking, notas)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                ("ENC-001","Apple AirPods Pro 2nd Gen","tech_store_usa","Eletrónicos","2024-02-01","myus",     1, 199.99,15.0, 0.5, 28.62,1.5,0,"Entregue",     "1Z999AA10123456784",""),
                ("ENC-002","Nike Air Max 270 - Size 44","sneakers_world","Calçado",  "2024-02-05","stackry",  1, 120.00,12.0, 1.2, 42.00,1.0,0,"Em Trânsito",  "9400111899223456781",""),
                ("ENC-003","Vitamix 5200 Blender",      "kitchen_goods_co","Casa & Jardim","2024-02-08","shipto",1,389.00,22.0, 4.5, 95.00,2.0,0,"Aguarda Recolha","","Volume grande"),
                ("ENC-004","Lego Technic 42156 PEUGEOT","toy_kingdom_intl","Brinquedos","2024-02-10","myus",  2, 219.95, 9.0, 3.0, 34.00,1.0,0,"Processando",  "","2 unidades"),
                ("ENC-005","Samsung 65\" QLED QN90C TV","electronics_direct","Eletrónicos","2024-02-12","shipito",1,1299.00,55.0,32.0,320.00,2.5,0,"Problema","7816400000000000","Retenção alfandegária"),
            ]
        )


# ─── QUERIES — TAXAS ───────────────────────────────────────────────────────

def get_all_taxas():
    """Devolve todas as taxas com a taxa_efetiva calculada em SQL."""
    with get_db() as db:
        rows = db.execute("""
            SELECT *,
                   ROUND(taxa_base * (1 + comissao / 100.0), 2) AS taxa_efetiva
            FROM taxas_cambio
            ORDER BY CASE id
                WHEN 'bna'      THEN 1
                WHEN 'banco'    THEN 2
                WHEN 'airtm'   THEN 3
                WHEN 'wise'     THEN 4
                ELSE 5
            END
        """).fetchall()
        return [dict(r) for r in rows]


def get_taxa(taxa_id):
    with get_db() as db:
        row = db.execute(
            "SELECT *, ROUND(taxa_base*(1+comissao/100.0),2) AS taxa_efetiva FROM taxas_cambio WHERE id=?",
            (taxa_id,)
        ).fetchone()
        return dict(row) if row else None


def update_taxa(taxa_id, taxa_base, comissao, obs):
    """
    Actualiza uma taxa e guarda o valor anterior no histórico.
    Isto são duas operações — ou ambas acontecem, ou nenhuma (transacção).
    """
    with get_db() as db:
        # 1. Guardar o valor actual no histórico ANTES de actualizar
        atual = db.execute(
            "SELECT taxa_base, comissao FROM taxas_cambio WHERE id=?", (taxa_id,)
        ).fetchone()
        if atual:
            db.execute(
                "INSERT INTO historico_taxas (taxa_id, taxa_base, comissao) VALUES (?,?,?)",
                (taxa_id, atual["taxa_base"], atual["comissao"])
            )

        # 2. Actualizar o valor actual
        db.execute(
            "UPDATE taxas_cambio SET taxa_base=?, comissao=?, obs=?, atualizado=date('now') WHERE id=?",
            (taxa_base, comissao, obs, taxa_id)
        )


def get_historico_taxa(taxa_id, limite=30):
    """Últimas N alterações de uma taxa — para um gráfico de evolução."""
    with get_db() as db:
        rows = db.execute("""
            SELECT taxa_base, comissao,
                   ROUND(taxa_base*(1+comissao/100.0),2) AS taxa_efetiva,
                   registado_em
            FROM historico_taxas
            WHERE taxa_id = ?
            ORDER BY registado_em DESC
            LIMIT ?
        """, (taxa_id, limite)).fetchall()
        return [dict(r) for r in rows]


# ─── QUERIES — REDIRECIONADORAS ────────────────────────────────────────────

def get_all_redirecionadoras():
    with get_db() as db:
        rows = db.execute("SELECT * FROM redirecionadoras ORDER BY nome").fetchall()
        return [dict(r) for r in rows]


def get_redirecionadora(rid):
    with get_db() as db:
        row = db.execute("SELECT * FROM redirecionadoras WHERE id=?", (rid,)).fetchone()
        return dict(row) if row else None


def update_redirecionadora(rid, nome, tarifa_kg, obs):
    with get_db() as db:
        db.execute(
            "UPDATE redirecionadoras SET nome=?, tarifa_kg=?, obs=? WHERE id=?",
            (nome, tarifa_kg, obs, rid)
        )


# ─── QUERIES — ENCOMENDAS ──────────────────────────────────────────────────

def get_all_encomendas(estado=None, categoria=None, ordem="criado_em DESC"):
    """
    Devolve encomendas com JOIN para trazer o nome da redirecionadora.

    Um JOIN une duas tabelas numa só consulta. Aqui, em vez de fazer
    duas queries separadas (uma para encomendas, outra para redirecionadoras),
    fazemos tudo de uma vez.

    LEFT JOIN significa que queremos a encomenda MESMO se não tiver
    redirecionadora associada — ao contrário do INNER JOIN que a descartaria.
    """
    where_clauses = []
    params = []

    if estado:
        where_clauses.append("e.estado = ?")
        params.append(estado)
    if categoria:
        where_clauses.append("e.categoria = ?")
        params.append(categoria)

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    with get_db() as db:
        rows = db.execute(f"""
            SELECT e.*,
                   r.nome AS redir_nome,
                   r.tarifa_kg AS redir_tarifa
            FROM encomendas e
            LEFT JOIN redirecionadoras r ON e.redirecionadora_id = r.id
            {where_sql}
            ORDER BY e.{ordem}
        """, params).fetchall()
        return [dict(r) for r in rows]


def get_encomenda(enc_id):
    with get_db() as db:
        row = db.execute("""
            SELECT e.*, r.nome AS redir_nome
            FROM encomendas e
            LEFT JOIN redirecionadoras r ON e.redirecionadora_id = r.id
            WHERE e.id = ?
        """, (enc_id,)).fetchone()
        return dict(row) if row else None


def create_encomenda(data: dict) -> str:
    """
    Cria uma nova encomenda e devolve o ID gerado.
    O ID é gerado automaticamente no formato ENC-001.
    """
    with get_db() as db:
        # Contar quantas já existem para gerar o próximo ID
        count = db.execute("SELECT COUNT(*) FROM encomendas").fetchone()[0]
        enc_id = f"ENC-{count + 1:03d}"

        db.execute("""
            INSERT INTO encomendas
            (id, produto, vendedor, url, categoria, data_compra, redirecionadora_id,
             quantidade, preco_usd, frete_ebay_usd, peso_kg, frete_redir_usd,
             seguro_pct, outras_taxas, estado, tracking, notas)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            enc_id,
            data.get("produto", ""),
            data.get("vendedor", ""),
            data.get("url", ""),
            data.get("categoria", "Outro"),
            data.get("data_compra", str(date.today())),
            data.get("redirecionadora_id") or None,
            int(data.get("quantidade", 1)),
            float(data.get("preco_usd", 0)),
            float(data.get("frete_ebay_usd", 0)),
            float(data.get("peso_kg", 0)),
            float(data.get("frete_redir_usd", 0)),
            float(data.get("seguro_pct", 1.5)),
            float(data.get("outras_taxas", 0)),
            data.get("estado", "Processando"),
            data.get("tracking", ""),
            data.get("notas", ""),
        ))
        return enc_id


def update_encomenda(enc_id: str, data: dict):
    with get_db() as db:
        db.execute("""
            UPDATE encomendas SET
                produto=?, vendedor=?, url=?, categoria=?, data_compra=?,
                redirecionadora_id=?, quantidade=?, preco_usd=?, frete_ebay_usd=?,
                peso_kg=?, frete_redir_usd=?, seguro_pct=?, outras_taxas=?,
                estado=?, tracking=?, notas=?
            WHERE id=?
        """, (
            data.get("produto", ""),
            data.get("vendedor", ""),
            data.get("url", ""),
            data.get("categoria", "Outro"),
            data.get("data_compra", ""),
            data.get("redirecionadora_id") or None,
            int(data.get("quantidade", 1)),
            float(data.get("preco_usd", 0)),
            float(data.get("frete_ebay_usd", 0)),
            float(data.get("peso_kg", 0)),
            float(data.get("frete_redir_usd", 0)),
            float(data.get("seguro_pct", 1.5)),
            float(data.get("outras_taxas", 0)),
            data.get("estado", "Processando"),
            data.get("tracking", ""),
            data.get("notas", ""),
            enc_id,
        ))


def delete_encomenda(enc_id: str):
    with get_db() as db:
        db.execute("DELETE FROM encomendas WHERE id=?", (enc_id,))


# ─── QUERIES — DASHBOARD & RELATÓRIOS ─────────────────────────────────────

def get_dashboard_stats():
    """
    Uma única query que devolve todos os números do dashboard.
    Fazemos tudo em SQL — é muito mais eficiente do que carregar todos os
    registos para Python e contar lá.
    """
    with get_db() as db:
        # Contagens por estado — GROUP BY agrupa os resultados por valor único
        estados = db.execute("""
            SELECT estado, COUNT(*) as n
            FROM encomendas
            GROUP BY estado
        """).fetchall()
        por_estado = {r["estado"]: r["n"] for r in estados}

        total = db.execute("SELECT COUNT(*) FROM encomendas").fetchone()[0]

        # Soma de todos os valores de produto (sem impostos — isso é calculado em Python)
        soma = db.execute("""
            SELECT COALESCE(SUM(preco_usd * quantidade), 0) as total_prod,
                   COALESCE(SUM(frete_ebay_usd + frete_redir_usd), 0) as total_frete
            FROM encomendas
        """).fetchone()

        # Gastos por categoria
        por_cat = db.execute("""
            SELECT categoria,
                   COUNT(*) as count,
                   SUM(preco_usd * quantidade) as usd
            FROM encomendas
            GROUP BY categoria
            ORDER BY usd DESC
        """).fetchall()

        # Gastos por mês — substr extrai os primeiros 7 caracteres da data (YYYY-MM)
        por_mes = db.execute("""
            SELECT substr(data_compra, 1, 7) as mes,
                   SUM(preco_usd * quantidade) as usd,
                   COUNT(*) as count
            FROM encomendas
            GROUP BY mes
            ORDER BY mes
        """).fetchall()

        return {
            "total": total,
            "em_transito": por_estado.get("Em Trânsito", 0),
            "entregues":   por_estado.get("Entregue", 0),
            "problemas":   por_estado.get("Problema", 0) + por_estado.get("Devolvido", 0),
            "processando": por_estado.get("Processando", 0),
            "aguarda":     por_estado.get("Aguarda Recolha", 0),
            "total_prod_usd": round(soma["total_prod"], 2),
            "total_frete_usd": round(soma["total_frete"], 2),
            "por_categoria": [dict(r) for r in por_cat],
            "por_mes":       [dict(r) for r in por_mes],
        }


def get_relatorio_completo():
    """Dados agregados para a página de Relatórios."""
    with get_db() as db:
        por_cat = db.execute("""
            SELECT categoria,
                   COUNT(*) as count,
                   ROUND(SUM(preco_usd * quantidade + frete_ebay_usd + frete_redir_usd), 2) as usd
            FROM encomendas
            GROUP BY categoria ORDER BY usd DESC
        """).fetchall()

        por_redir = db.execute("""
            SELECT r.nome, r.id, COUNT(e.id) as count,
                   ROUND(SUM(e.preco_usd * e.quantidade), 2) as usd
            FROM encomendas e
            LEFT JOIN redirecionadoras r ON e.redirecionadora_id = r.id
            GROUP BY e.redirecionadora_id
            ORDE
