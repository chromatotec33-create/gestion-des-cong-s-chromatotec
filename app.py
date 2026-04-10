import os
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from functools import wraps
from io import BytesIO

from dotenv import load_dotenv
from flask import Flask, abort, flash, jsonify, redirect, render_template, request, send_file, session, url_for
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from supabase import Client, create_client

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-me-in-production")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

if not SUPABASE_URL or not SUPABASE_ANON_KEY or not SUPABASE_SERVICE_ROLE_KEY:
    raise RuntimeError("SUPABASE_URL, SUPABASE_ANON_KEY et SUPABASE_SERVICE_ROLE_KEY sont requis.")

supabase_public: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
supabase_admin: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

ROLE_EMPLOYE = "employe"
ROLE_CHEF = "chef_service"
ROLE_DIRECTION = "direction"

FINAL_STATUSES = {"valide_direction", "refuse_chef", "refuse_direction"}
TEST_ACCOUNTS = [
    {"label": "Direction", "email": "direction.test@chromatotec.local"},
    {"label": "Chef service (Production)", "email": "chef.prod.test@chromatotec.local"},
    {"label": "Chef service (Qualité)", "email": "chef.qualite.test@chromatotec.local"},
    {"label": "Employé (Production)", "email": "employe.prod1.test@chromatotec.local"},
    {"label": "Employé (Production)", "email": "employe.prod2.test@chromatotec.local"},
    {"label": "Employé (Qualité)", "email": "employe.qualite1.test@chromatotec.local"},
]


def today_utc() -> date:
    return datetime.utcnow().date()


def to_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def round2(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def calculate_working_days(date_debut: date, date_fin: date, holidays: set | None = None) -> int:
    """
    Calcule les jours ouvrés entre 2 dates incluses.
    Exclut week-ends et prévoit extension jours fériés via `holidays`.
    """
    if date_fin < date_debut:
        return 0

    holidays = holidays or set()
    total = 0
    current = date_debut
    while current <= date_fin:
        if current.weekday() < 5 and current not in holidays:
            total += 1
        current += timedelta(days=1)
    return total


def leave_period_bounds(reference: date) -> tuple[date, date]:
    """Période légale du 1er juin au 31 mai."""
    start_year = reference.year if reference.month >= 6 else reference.year - 1
    start = date(start_year, 6, 1)
    end = date(start_year + 1, 5, 31)
    return start, end


def months_between(start: date, end: date) -> Decimal:
    """Approximation proratisée au jour pour le calcul d'acquisition."""
    if end <= start:
        return Decimal("0")
    days = Decimal((end - start).days + 1)
    return days / Decimal("30.4375")


def calculate_leave_balance(user: dict) -> dict:
    """
    Calcule le solde théorique avec deux cas :
    - Cas 1 acquisition mensuelle 2.0833
    - Cas 2 forfait annuel 25 jours si ancienneté >= 1 an
    Prend en compte la proratisation, arrondi 2 décimales, et congés validés déjà pris.
    """
    hire_date = to_date(user["date_embauche"])
    now = today_utc()
    period_start, period_end = leave_period_bounds(now)

    accrual_start = max(hire_date, period_start)
    accrual_end = min(now, period_end)

    monthly_rate = Decimal("2.0833")
    acquired_case_1 = months_between(accrual_start, accrual_end) * monthly_rate

    has_one_year = (now - hire_date).days >= 365
    acquired_case_2 = Decimal("25") if has_one_year else Decimal("0")

    acquired = max(acquired_case_1, acquired_case_2)

    taken_result = (
        supabase_admin.table("demandes_conges")
        .select("nb_jours")
        .eq("user_id", user["id"])
        .eq("statut", "valide_direction")
        .gte("date_debut", period_start.isoformat())
        .lte("date_fin", period_end.isoformat())
        .execute()
    )
    taken = sum((row.get("nb_jours") or 0) for row in (taken_result.data or []))

    remaining = Decimal(str(acquired)) - Decimal(str(taken))
    remaining = max(remaining, Decimal("0"))

    return {
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "acquired_case_1": round2(Decimal(acquired_case_1)),
        "acquired_case_2": round2(acquired_case_2),
        "acquired": round2(Decimal(acquired)),
        "taken": round2(Decimal(str(taken))),
        "remaining": round2(remaining),
        "method": "forfait_annuel" if acquired_case_2 >= acquired_case_1 else "acquisition_mensuelle",
    }


def current_user() -> dict | None:
    user_id = session.get("user_id")
    if not user_id:
        return None
    response = supabase_admin.table("users").select("*").eq("id", user_id).single().execute()
    return response.data if response.data else None


def log_action(user_id: str, action: str, commentaire: str = "") -> None:
    supabase_admin.table("historique_actions").insert(
        {
            "user_id": user_id,
            "action": action,
            "commentaire": commentaire,
            "date": datetime.utcnow().isoformat(),
        }
    ).execute()


def check_database_connection() -> tuple[bool, str]:
    try:
        supabase_admin.table("services").select("id").limit(1).execute()
        return True, "Connecté à la base de données"
    except Exception:
        return False, "Base de données indisponible"


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def roles_required(*allowed):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = current_user()
            if not user or user["role"] not in allowed:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


def check_overlap(user_id: str, start: date, end: date) -> bool:
    response = (
        supabase_admin.table("demandes_conges")
        .select("id")
        .eq("user_id", user_id)
        .in_("statut", ["en_attente", "valide_chef", "valide_direction"])
        .lte("date_debut", end.isoformat())
        .gte("date_fin", start.isoformat())
        .limit(1)
        .execute()
    )
    return bool(response.data)


def visible_requests_for(user: dict) -> list[dict]:
    query = (
        supabase_admin.table("demandes_conges")
        .select("id,user_id,date_debut,date_fin,nb_jours,statut,created_at,users(id,nom,email,role,service_id)")
        .order("created_at", desc=True)
    )
    if user["role"] == ROLE_EMPLOYE:
        query = query.eq("user_id", user["id"])
    elif user["role"] == ROLE_CHEF:
        query = query.eq("users.service_id", user["service_id"])
    return query.execute().data or []


def generate_leave_pdf(demande: dict, user: dict) -> bytes:
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    pdf.setTitle(f"Bon de congé {demande['id']}")

    y = 800
    lines = [
        "BON DE CONGÉ",
        f"Date génération : {today_utc().isoformat()}",
        "",
        f"Employé : {user['nom']} ({user['email']})",
        f"ID demande : {demande['id']}",
        f"Période : {demande['date_debut']} au {demande['date_fin']}",
        f"Nombre de jours ouvrés : {demande['nb_jours']}",
        f"Statut final : {demande['statut']}",
        "",
        "Validation finale Direction : ACCORDÉ",
    ]

    for line in lines:
        pdf.drawString(72, y, line)
        y -= 22

    pdf.showPage()
    pdf.save()
    buffer.seek(0)
    return buffer.read()


@app.route("/")
def index():
    return redirect(url_for("dashboard"))


@app.route("/login", methods=["GET", "POST"])
def login():
    db_connected, db_message = check_database_connection()

    if request.method == "POST":
        if not db_connected:
            flash("Erreur de connexion à la base de données. Réessayez plus tard.", "error")
            return render_template("login.html", test_accounts=TEST_ACCOUNTS, db_connected=False, db_message=db_message)

        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        try:
            auth = supabase_public.auth.sign_in_with_password({"email": email, "password": password})
        except Exception:
            flash("Connexion impossible pour le moment. Vérifiez vos identifiants ou réessayez.", "error")
            return render_template(
                "login.html", test_accounts=TEST_ACCOUNTS, db_connected=db_connected, db_message=db_message
            )

        if not auth.user:
            flash("Connexion invalide.", "error")
            return render_template(
                "login.html", test_accounts=TEST_ACCOUNTS, db_connected=db_connected, db_message=db_message
            )

        try:
            profile = (
                supabase_admin.table("users")
                .select("id,nom,email,role,service_id,date_embauche")
                .eq("id", auth.user.id)
                .single()
                .execute()
                .data
            )
        except Exception:
            flash("Erreur lors de la récupération du profil utilisateur.", "error")
            return render_template(
                "login.html", test_accounts=TEST_ACCOUNTS, db_connected=db_connected, db_message=db_message
            )

        if not profile:
            flash("Profil utilisateur introuvable.", "error")
            return render_template(
                "login.html", test_accounts=TEST_ACCOUNTS, db_connected=db_connected, db_message=db_message
            )

        session["user_id"] = profile["id"]
        session["role"] = profile["role"]
        log_action(profile["id"], "login", "Connexion utilisateur")
        flash("Connexion réussie.", "success")
        return redirect(url_for("dashboard"))

    return render_template("login.html", test_accounts=TEST_ACCOUNTS, db_connected=db_connected, db_message=db_message)


@app.route("/api/db-status")
def db_status():
    connected, message = check_database_connection()
    return jsonify({"connected": connected, "message": message}), (200 if connected else 503)


@app.route("/logout")
def logout():
    user_id = session.get("user_id")
    if user_id:
        log_action(user_id, "logout", "Déconnexion utilisateur")
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    user = current_user()
    demandes = visible_requests_for(user)
    balance = calculate_leave_balance(user)
    return render_template("dashboard.html", user=user, demandes=demandes, balance=balance)


@app.route("/demandes")
@login_required
def demandes():
    user = current_user()
    data = visible_requests_for(user)
    return render_template("demandes.html", user=user, demandes=data)


@app.route("/create", methods=["GET", "POST"])
@login_required
@roles_required(ROLE_EMPLOYE, ROLE_CHEF, ROLE_DIRECTION)
def create_demande():
    user = current_user()
    if request.method == "POST":
        date_debut = to_date(request.form["date_debut"])
        date_fin = to_date(request.form["date_fin"])
        commentaire_public = request.form.get("commentaire_public", "").strip()
        commentaire_prive = request.form.get("commentaire_prive", "").strip()

        if date_fin < date_debut:
            flash("La date de fin doit être après la date de début.", "error")
            return render_template("create.html", user=user)

        nb_jours = calculate_working_days(date_debut, date_fin)
        if nb_jours <= 0:
            flash("La demande doit contenir au moins 1 jour ouvré.", "error")
            return render_template("create.html", user=user)

        if check_overlap(user["id"], date_debut, date_fin):
            flash("Une demande chevauche déjà cette période.", "error")
            return render_template("create.html", user=user)

        balance = calculate_leave_balance(user)
        if balance["remaining"] < nb_jours:
            flash("Solde insuffisant.", "error")
            return render_template("create.html", user=user)

        demande = (
            supabase_admin.table("demandes_conges")
            .insert(
                {
                    "user_id": user["id"],
                    "date_debut": date_debut.isoformat(),
                    "date_fin": date_fin.isoformat(),
                    "nb_jours": nb_jours,
                    "statut": "en_attente",
                }
            )
            .execute()
            .data[0]
        )

        if commentaire_public:
            supabase_admin.table("commentaires_demandes").insert(
                {
                    "demande_id": demande["id"],
                    "auteur_id": user["id"],
                    "type_commentaire": "public",
                    "contenu": commentaire_public,
                }
            ).execute()

        if commentaire_prive and user["role"] in {ROLE_CHEF, ROLE_DIRECTION}:
            supabase_admin.table("commentaires_demandes").insert(
                {
                    "demande_id": demande["id"],
                    "auteur_id": user["id"],
                    "type_commentaire": "prive",
                    "contenu": commentaire_prive,
                }
            ).execute()

        log_action(user["id"], "create_demande", f"Demande {demande['id']} créée")
        flash("Demande créée avec succès.", "success")
        return redirect(url_for("dashboard"))

    return render_template("create.html", user=user)


@app.route("/validate", methods=["POST"])
@login_required
@roles_required(ROLE_CHEF, ROLE_DIRECTION)
def validate_demande():
    user = current_user()
    demande_id = request.form.get("demande_id")
    commentaire = request.form.get("commentaire", "").strip()

    demande = (
        supabase_admin.table("demandes_conges")
        .select("*,users(id,service_id,nom,email)")
        .eq("id", demande_id)
        .single()
        .execute()
        .data
    )
    if not demande:
        abort(404)

    new_status = None
    if user["role"] == ROLE_CHEF and demande["statut"] == "en_attente":
        if user["service_id"] != demande["users"]["service_id"]:
            abort(403)
        new_status = "valide_chef"
    elif user["role"] == ROLE_DIRECTION and demande["statut"] in {"en_attente", "valide_chef"}:
        new_status = "valide_direction"
    else:
        abort(403)

    supabase_admin.table("demandes_conges").update({"statut": new_status}).eq("id", demande_id).execute()

    if commentaire:
        supabase_admin.table("commentaires_demandes").insert(
            {
                "demande_id": demande_id,
                "auteur_id": user["id"],
                "type_commentaire": "public",
                "contenu": commentaire,
            }
        ).execute()

    if new_status == "valide_direction":
        pdf_bytes = generate_leave_pdf({**demande, "statut": new_status}, demande["users"])
        file_name = f"bon_conge_{demande_id}.pdf"
        os.makedirs("generated_pdfs", exist_ok=True)
        with open(os.path.join("generated_pdfs", file_name), "wb") as f:
            f.write(pdf_bytes)
        log_action(user["id"], "validation_finale", f"PDF généré: {file_name}")

    log_action(user["id"], "validate_demande", f"Demande {demande_id} -> {new_status}")
    flash("Demande validée.", "success")
    return redirect(url_for("demandes"))


@app.route("/refuse", methods=["POST"])
@login_required
@roles_required(ROLE_CHEF, ROLE_DIRECTION)
def refuse_demande():
    user = current_user()
    demande_id = request.form.get("demande_id")
    commentaire = request.form.get("commentaire", "").strip()

    demande = (
        supabase_admin.table("demandes_conges")
        .select("*,users(service_id)")
        .eq("id", demande_id)
        .single()
        .execute()
        .data
    )
    if not demande:
        abort(404)

    if user["role"] == ROLE_CHEF:
        if user["service_id"] != demande["users"]["service_id"]:
            abort(403)
        new_status = "refuse_chef"
    else:
        new_status = "refuse_direction"

    supabase_admin.table("demandes_conges").update({"statut": new_status}).eq("id", demande_id).execute()

    if commentaire:
        supabase_admin.table("commentaires_demandes").insert(
            {
                "demande_id": demande_id,
                "auteur_id": user["id"],
                "type_commentaire": "prive" if user["role"] != ROLE_EMPLOYE else "public",
                "contenu": commentaire,
            }
        ).execute()

    log_action(user["id"], "refuse_demande", f"Demande {demande_id} -> {new_status}")
    flash("Demande refusée.", "success")
    return redirect(url_for("demandes"))


@app.route("/pdf/<demande_id>")
@login_required
def get_pdf(demande_id: str):
    file_path = os.path.join("generated_pdfs", f"bon_conge_{demande_id}.pdf")
    if not os.path.exists(file_path):
        abort(404)
    return send_file(file_path, mimetype="application/pdf", as_attachment=True)


@app.context_processor
def inject_globals():
    return {"ROLE_EMPLOYE": ROLE_EMPLOYE, "ROLE_CHEF": ROLE_CHEF, "ROLE_DIRECTION": ROLE_DIRECTION}


if __name__ == "__main__":
    app.run(debug=True)
