"""
app.py — ImportAOA Flask Application (versão com SQLite)
"""

from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from functools import wraps
from datetime import date
import os
import database as db

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "importaoa_sqlite_2024_auth_secret_dev")

with app.app_context():
    db.init_db()


# ══ AUTENTICAÇÃO ══════════════════════════════════════════════════════════════

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def calc_taxa_efetiva(taxa_base, comissao):
    return round(taxa_base * (1 + comissao / 100), 2)


def calc_totais_encomenda(enc, taxas_dict, da_pct=20.0, ic_pct=14.0, limiar=200.0, desp=35.0):
    preco       = enc["preco_usd"] * enc["quantidade"]
    frete_ebay  = enc["frete_ebay_usd"]
    frete_redir = enc["frete_redir_usd"]
    seguro      = preco * enc["seguro_pct"] / 100
    outras      = enc.get("outras_taxas", 0) or 0

    cif      = preco + frete_ebay + frete_redir + seguro
    sujeito  = cif > limiar
    da       = round(cif * da_pct / 100, 2) if sujeito else 0
    ic       = round((cif + da) * ic_pct / 100, 2) if sujeito else 0
    despesa  = desp if sujeito else 0
    impostos = da + ic + despesa

    total_usd = round(preco + frete_ebay + frete_redir + seguro + outras + impostos, 2)

    def efetiva(tid):
        t = taxas_dict.get(tid, {})
        return t.get("taxa_base", 0) * (1 + t.get("comissao", 0) / 100)

    return {
        "preco_usd":         round(preco, 2),
        "frete_ebay":        round(frete_ebay, 2),
        "frete_redir":       round(frete_redir, 2),
        "seguro":            round(seguro, 2),
        "cif":               round(cif, 2),
        "sujeito_alfandega": sujeito,
        "da":                da,
        "ic":                ic,
        "despachante":       despesa,
        "total_impostos":    round(impostos, 2),
        "total_usd":         total_usd,
        "total_aoa_airtm":   round(total_usd * efetiva("airtm")),
        "total_aoa_banco":   round(total_usd * efetiva("banco")),
        "total_aoa_bna":     round(total_usd * taxas_dict.get("bna", {}).get("taxa_base", 930)),
        "markup_pct":        round((total_usd - preco) / preco * 100, 1) if preco else 0,
    }


def get_taxas_dict():
    return {t["id"]: t for t in db.get_all_taxas()}


@app.context_processor
def inject_globals():
    td    = get_taxas_dict()
    airtm = td.get("airtm", {})
    user  = db.get_user_by_id(session.get("user_id")) if "user_id" in session else None
    return {
        "taxa_airtm_sidebar":     calc_taxa_efetiva(airtm.get("taxa_base", 970), airtm.get("comissao", 3.5)),
        "taxa_airtm_raw_sidebar": airtm.get("taxa_base", 970),
        "airtm_comissao":         airtm.get("comissao", 3.5),
        "current_user":           user,
    }


@app.route("/")
@login_required
def index():
    stats    = db.get_dashboard_stats()
    recentes = db.get_all_encomendas(ordem="criado_em DESC")[:5]
    td       = get_taxas_dict()
    airtm    = td.get("airtm", {})
    taxa_ef  = calc_taxa_efetiva(airtm.get("taxa_base", 970), airtm.get("comissao", 3.5))
    for enc in recentes:
        enc.update(calc_totais_encomenda(enc, td))
    return render_template("dashboard.html",
        stats=stats, recentes=recentes,
        taxa_airtm=taxa_ef, taxa_airtm_raw=airtm.get("taxa_base", 970), airtm=airtm)


@app.route("/encomendas")
@login_required
def encomendas():
    estado    = request.args.get("estado")
    categoria = request.args.get("categoria")
    td        = get_taxas_dict()
    lista     = db.get_all_encomendas(estado=estado, categoria=categoria)
    for enc in lista:
        enc.update(calc_totais_encomenda(enc, td))
    return render_template("encomendas.html", encomendas=lista,
                           estado_filtro=estado, cat_filtro=categoria)


@app.route("/encomendas/nova", methods=["GET", "POST"])
@login_required
def nova_encomenda():
    redirs = db.get_all_redirecionadoras()
    if request.method == "POST":
        data = {k: request.form.get(k, "") for k in [
            "produto","vendedor","url","categoria","data_compra",
            "estado","tracking","notas"]}
        data["redirecionadora_id"] = request.form.get("redirecionadora", "")
        for f in ["quantidade","preco_usd","frete_ebay_usd","peso_kg",
                  "frete_redir_usd","seguro_pct","outras_taxas"]:
            data[f] = request.form.get(f, 0)
        if not data["data_compra"]:
            data["data_compra"] = str(date.today())
        db.create_encomenda(data)
        return redirect(url_for("encomendas"))
    return render_template("nova_encomenda.html", redirs=redirs)


@app.route("/encomendas/editar/<enc_id>", methods=["GET", "POST"])
@login_required
def editar_encomenda(enc_id):
    redirs = db.get_all_redirecionadoras()
    enc    = db.get_encomenda(enc_id)
    if not enc:
        return redirect(url_for("encomendas"))
    if request.method == "POST":
        data = {k: request.form.get(k, "") for k in [
            "produto","vendedor","url","categoria","data_compra",
            "estado","tracking","notas"]}
        data["redirecionadora_id"] = request.form.get("redirecionadora", "")
        for f in ["quantidade","preco_usd","frete_ebay_usd","peso_kg",
                  "frete_redir_usd","seguro_pct","outras_taxas"]:
            data[f] = request.form.get(f, 0)
        db.update_encomenda(enc_id, data)
        return redirect(url_for("encomendas"))
    return render_template("nova_encomenda.html", redirs=redirs, enc=enc, edit=True)


@app.route("/encomendas/apagar/<enc_id>", methods=["POST"])
@login_required
def apagar_encomenda(enc_id):
    db.delete_encomenda(enc_id)
    return redirect(url_for("encomendas"))


@app.route("/cambio")
@login_required
def cambio():
    taxas    = db.get_all_taxas()
    td       = {t["id"]: t for t in taxas}
    historico = db.get_historico_taxa("airtm", limite=20)
    return render_template("cambio.html", taxas=taxas, taxas_dict=td, historico_airtm=historico)


@app.route("/cambio/atualizar", methods=["POST"])
@login_required
def atualizar_taxa():
    db.update_taxa(
        taxa_id   = request.form.get("id"),
        taxa_base = float(request.form.get("taxa_base", 0)),
        comissao  = float(request.form.get("comissao", 0)),
        obs       = request.form.get("obs", ""),
    )
    return redirect(url_for("cambio"))


@app.route("/calculadora")
@login_required
def calculadora():
    redirs  = db.get_all_redirecionadoras()
    td      = get_taxas_dict()
    airtm   = td.get("airtm", {})
    taxa_ef = calc_taxa_efetiva(airtm.get("taxa_base", 970), airtm.get("comissao", 3.5))
    return render_template("calculadora.html", redirs=redirs, taxa_airtm=taxa_ef, taxas_dict=td)


@app.route("/api/calcular", methods=["POST"])
def api_calcular():
    d  = request.get_json()
    td = get_taxas_dict()
    enc = {
        "preco_usd":       float(d.get("preco_usd", 0)),
        "quantidade":      int(d.get("quantidade", 1)),
        "frete_ebay_usd":  float(d.get("frete_ebay_usd", 0)),
        "peso_kg":         float(d.get("peso_kg", 0)),
        "frete_redir_usd": float(d.get("frete_redir_usd", 0)),
        "seguro_pct":      float(d.get("seguro_pct", 1.5)),
        "outras_taxas":    float(d.get("outras_taxas", 0)),
    }
    return jsonify(calc_totais_encomenda(enc, td,
        da_pct=float(d.get("da_pct", 20)), ic_pct=float(d.get("ic_pct", 14)),
        limiar=float(d.get("limiar", 200)), desp=float(d.get("despachante", 35))))


@app.route("/api/taxa_redir/<redir_id>")
def api_taxa_redir(redir_id):
    r = db.get_redirecionadora(redir_id)
    return jsonify({"tarifa_kg": r["tarifa_kg"] if r else 0})


@app.route("/redirecionadoras")
@login_required
def redirecionadoras():
    return render_template("redirecionadoras.html", redirs=db.get_all_redirecionadoras())


@app.route("/redirecionadoras/atualizar", methods=["POST"])
@login_required
def atualizar_redir():
    db.update_redirecionadora(
        rid=request.form.get("id"),
        nome=request.form.get("nome", ""),
        tarifa_kg=float(request.form.get("tarifa_kg", 0)),
        obs=request.form.get("obs", ""),
    )
    return redirect(url_for("redirecionadoras"))


@app.route("/relatorios")
@login_required
def relatorios():
    return render_template("relatorios.html", **db.get_relatorio_completo())


# ══ ROTAS DE AUTENTICAÇÃO ═════════════════════════════════════════════════════

@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        senha = request.form.get("senha", "")
        user  = db.verify_password(email, senha)
        if user:
            session["user_id"]   = user["id"]
            session["user_nome"] = user["nome"]
            return redirect(url_for("index"))
        else:
            error = "Email ou senha incorrectos. Tente novamente."
    return render_template("login.html", error=error)


@app.route("/cadastro", methods=["GET", "POST"])
def cadastro():
    if "user_id" in session:
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        nome    = request.form.get("nome", "").strip()
        apelido = request.form.get("apelido", "").strip()
        email   = request.form.get("email", "").strip()
        senha   = request.form.get("senha", "")
        senha2  = request.form.get("senha2", "")
        if not nome or not email or not senha:
            error = "Preencha todos os campos obrigatórios."
        elif len(senha) < 8:
            error = "A senha deve ter pelo menos 8 caracteres."
        elif senha != senha2:
            error = "As senhas não coincidem."
        else:
            user = db.create_user(nome, apelido, email, senha)
            if user:
                session["user_id"]   = user["id"]
                session["user_nome"] = user["nome"]
                return redirect(url_for("index"))
            else:
                error = "Este email já está registado. Tente fazer login."
    return render_template("cadastro.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ══ ROTA: Service Worker (precisa estar na raiz / para ter scope global) ══
# O Service Worker só consegue controlar páginas no mesmo scope (pasta).
# Se servíssemos o SW de /static/, ele só controlaria /static/* — inútil.
# Por isso servimo-lo directamente de /, mesmo estando fisicamente em /static/.
@app.route('/service-worker.js')
def service_worker():
    from flask import send_from_directory
    response = send_from_directory('static', 'service-worker.js')
    # Header crítico: diz ao browser para não cachear o SW ele próprio.
    # O SW tem a sua própria lógica de cache — se o browser o cacheasse,
    # nunca conseguiríamos actualizar a aplicação.
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Service-Worker-Allowed'] = '/'
    return response


if __name__ == "__main__":
    import os

    # ── OPÇÃO 1: Rede local (PC + telemóvel no mesmo Wi-Fi) ─────────────
    # host="0.0.0.0" significa "aceita ligações de qualquer endereço de rede",
    # não apenas do próprio computador (localhost/127.0.0.1).
    # Para aceder do telemóvel: abre o browser e vai a http://IP_DO_PC:5000
    # Para saber o IP do PC: no Windows corre "ipconfig", no Mac/Linux "ifconfig"
    #
    # ── OPÇÃO 2: Cloud (Railway, Render, etc.) ──────────────────────────
    # As plataformas cloud definem a variável de ambiente PORT automaticamente.
    # O int(os.environ.get("PORT", 5000)) usa a porta da plataforma se existir,
    # ou 5000 como fallback para desenvolvimento local.
    # debug=False em produção: nunca expor informação de erro ao público.
    #
    # ── OPÇÃO 3: PWA ────────────────────────────────────────────────────
    # A PWA não requer nenhuma mudança aqui — é tudo gerida pelo browser
    # através do manifest.json e service-worker.js que já servimos acima.
    # Para HTTPS local (necessário para PWA em alguns browsers), usa:
    # app.run(ssl_context='adhoc') com "pip install pyopenssl"

    is_production = os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RENDER")
    port = int(os.environ.get("PORT", 5000))

    if is_production:
        print(f"🚀 ImportAOA — Produção (cloud) — porta {port}")
        app.run(host="0.0.0.0", port=port, debug=False)
    else:
        print("🚀 ImportAOA — Desenvolvimento")
        print(f"   Local:       http://127.0.0.1:{port}")
        print(f"   Rede local:  http://SEU_IP:{port}  (telemóvel no mesmo Wi-Fi)")
        print(f"   PWA:         Abre no Chrome/Edge e clica 'Instalar'")
        app.run(host="0.0.0.0", port=port, debug=True)
