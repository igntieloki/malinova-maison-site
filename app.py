import os
import sqlite3
import secrets
import json
from functools import wraps
from datetime import datetime

import stripe
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, abort, g
)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.environ.get("MALINOVA_DB_PATH", os.path.join(BASE_DIR, "malinova.db"))

app = Flask(__name__)
# Render est derrière un proxy HTTPS : on lui fait confiance pour construire les bonnes URLs.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

app.secret_key = os.environ.get("MALINOVA_SECRET_KEY")
if not app.secret_key:
    # Convenience for local testing only. Set MALINOVA_SECRET_KEY in Render before public use.
    app.secret_key = "LOCAL-TEST-ONLY-" + secrets.token_hex(32)

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "1") == "1",
    PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 14,
)

ADMIN_PASSWORD = os.environ.get("MALINOVA_ADMIN_PASSWORD", "")

# ---------------------------------------------------------------------------
# Stripe
# ---------------------------------------------------------------------------
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
# Sécurité : les vraies clés (sk_live_...) sont refusées tant que cette variable n'est pas à "1".
ALLOW_LIVE_PAYMENTS = os.environ.get("ALLOW_LIVE_PAYMENTS", "0") == "1"

STATUS_PENDING = "En attente de paiement"
STATUS_PAID = "Payée"
STATUS_SHIPPED = "Expédiée"
STATUS_CANCELLED = "Annulée"

CATEGORIES = ["Décoration", "Rangement", "Cuisine", "Textiles"]

SEED_PRODUCTS = [
    ("Vase Alba", "Décoration", "Vase décoratif aux lignes douces pour une ambiance naturelle.", 24.90, "https://images.unsplash.com/photo-1578500494198-246f612d3b3d?auto=format&fit=crop&w=1000&q=85", 12),
    ("Panier naturel", "Rangement", "Un panier polyvalent pour organiser plaids, accessoires ou petits objets.", 19.90, "https://images.unsplash.com/photo-1593085260707-5377ba37f868?auto=format&fit=crop&w=1000&q=85", 15),
    ("Bocaux de cuisine", "Cuisine", "Des bocaux sobres pour garder vos ingrédients bien organisés.", 17.50, "https://images.unsplash.com/photo-1603199506016-b9a594b593c0?auto=format&fit=crop&w=1000&q=85", 20),
    ("Coussin doux", "Textiles", "Un coussin décoratif pour ajouter une touche chaleureuse à votre intérieur.", 22.00, "https://images.unsplash.com/photo-1584100936595-c0654b55a2e2?auto=format&fit=crop&w=1000&q=85", 10),
    ("Plateau minimaliste", "Décoration", "Un plateau pratique pour présenter vos objets préférés.", 16.90, "https://images.unsplash.com/photo-1603006905003-be475563bc59?auto=format&fit=crop&w=1000&q=85", 8),
    ("Organiseur malin", "Rangement", "Une solution simple pour garder les petits objets à portée de main.", 12.90, "https://images.unsplash.com/photo-1586023492125-27b2c045efd7?auto=format&fit=crop&w=1000&q=85", 18),
]

app.jinja_env.filters['from_json'] = lambda value: json.loads(value or '[]')


def to_cents(amount):
    """Convertit un prix en euros en centimes (entier), comme l'exige Stripe."""
    return int(round(float(amount) * 100))


def stripe_ready():
    """Vérifie que la clé Stripe est présente et autorisée. Retourne (ok, message)."""
    key = stripe.api_key or ""
    if not key.startswith(("sk_test_", "sk_live_", "rk_test_", "rk_live_")):
        return False, "Le paiement n'est pas encore configuré sur ce site."
    if key.startswith(("sk_live_", "rk_live_")) and not ALLOW_LIVE_PAYMENTS:
        return False, "Les paiements réels ne sont pas encore activés sur ce site."
    return True, ""


# ---------------------------------------------------------------------------
# Base de données
# ---------------------------------------------------------------------------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db

@app.teardown_appcontext
def close_db(error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()

def init_db():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("""CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        category TEXT NOT NULL,
        description TEXT NOT NULL,
        price REAL NOT NULL CHECK(price >= 0),
        image_url TEXT NOT NULL DEFAULT '',
        stock INTEGER NOT NULL DEFAULT 0,
        active INTEGER NOT NULL DEFAULT 0
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS customers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT NOT NULL COLLATE NOCASE UNIQUE,
        password_hash TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER,
        customer_name TEXT NOT NULL,
        email TEXT NOT NULL,
        phone TEXT NOT NULL,
        address TEXT NOT NULL,
        postal_code TEXT NOT NULL,
        city TEXT NOT NULL,
        notes TEXT DEFAULT '',
        items_json TEXT NOT NULL,
        total REAL NOT NULL,
        status TEXT NOT NULL DEFAULT 'À traiter',
        created_at TEXT NOT NULL,
        stripe_session_id TEXT,
        FOREIGN KEY(customer_id) REFERENCES customers(id) ON DELETE SET NULL
    )""")
    # Migrations pour une base créée par une version précédente.
    order_columns = {row[1] for row in db.execute("PRAGMA table_info(orders)").fetchall()}
    if "customer_id" not in order_columns:
        db.execute("ALTER TABLE orders ADD COLUMN customer_id INTEGER REFERENCES customers(id)")
    if "stripe_session_id" not in order_columns:
        db.execute("ALTER TABLE orders ADD COLUMN stripe_session_id TEXT")
    db.execute("CREATE INDEX IF NOT EXISTS idx_orders_stripe_session ON orders(stripe_session_id)")
    count = db.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    if count == 0:
        db.executemany(
            "INSERT INTO products (name, category, description, price, image_url, stock, active) VALUES (?, ?, ?, ?, ?, ?, 0)",
            SEED_PRODUCTS
        )
    db.commit()
    db.close()

init_db()


# ---------------------------------------------------------------------------
# Accès
# ---------------------------------------------------------------------------
def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("admin_login", next=request.path))
        return fn(*args, **kwargs)
    return wrapper

def customer_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("customer_id"):
            flash("Connectez-vous pour accéder à votre espace client.", "error")
            return redirect(url_for("login", next=request.path))
        return fn(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# Panier (les prix viennent TOUJOURS de la base de données, jamais du navigateur)
# ---------------------------------------------------------------------------
def cart_details():
    cart = session.get("cart", {})
    ids = [int(k) for k, qty in cart.items() if str(k).isdigit() and str(qty).isdigit() and int(qty) > 0]
    if not ids:
        return [], 0.0, 0
    placeholders = ",".join("?" for _ in ids)
    rows = get_db().execute(
        f"SELECT * FROM products WHERE active=1 AND id IN ({placeholders})", ids
    ).fetchall()
    items, total, count = [], 0.0, 0
    for product in rows:
        qty = min(int(cart.get(str(product["id"]), 1)), 99)
        line_total = round(product["price"] * qty, 2)
        items.append({"product": product, "qty": qty, "line_total": line_total})
        total += line_total
        count += qty
    return items, round(total, 2), count

@app.context_processor
def inject_globals():
    _, _, cart_count = cart_details()
    return {
        "cart_count": cart_count,
        "shop_name": "Malinova Maison",
        "current_year": datetime.now().year,
        "customer_logged_in": bool(session.get("customer_id")),
        "customer_name": session.get("customer_name", ""),
        "is_admin": bool(session.get("is_admin")),
    }


# ---------------------------------------------------------------------------
# Pages boutique
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    products = get_db().execute(
        "SELECT * FROM products WHERE active=1 ORDER BY id DESC LIMIT 6"
    ).fetchall()
    return render_template("index.html", products=products)

@app.route("/boutique")
def shop():
    q = request.args.get("q", "").strip()
    category = request.args.get("categorie", "").strip()
    sql = "SELECT * FROM products WHERE active=1"
    params = []
    if q:
        sql += " AND (name LIKE ? OR description LIKE ?)"
        params.extend([f"%{q}%", f"%{q}%"])
    if category in CATEGORIES:
        sql += " AND category=?"
        params.append(category)
    sql += " ORDER BY name"
    products = get_db().execute(sql, params).fetchall()
    return render_template("shop.html", products=products, categories=CATEGORIES, q=q, selected_category=category)

@app.route("/produit/<int:product_id>")
def product_detail(product_id):
    product = get_db().execute("SELECT * FROM products WHERE id=? AND active=1", (product_id,)).fetchone()
    if not product:
        abort(404)
    return render_template("product.html", product=product)

@app.post("/panier/ajouter/<int:product_id>")
def add_to_cart(product_id):
    product = get_db().execute("SELECT * FROM products WHERE id=? AND active=1", (product_id,)).fetchone()
    if not product:
        abort(404)
    try:
        qty = max(1, min(99, int(request.form.get("qty", "1"))))
    except (ValueError, TypeError):
        qty = 1
    cart = session.get("cart", {})
    cart[str(product_id)] = min(99, int(cart.get(str(product_id), 0)) + qty)
    session["cart"] = cart
    flash("Produit ajouté au panier.", "success")
    return redirect(request.referrer or url_for("shop"))

@app.route("/panier")
def cart():
    items, total, _ = cart_details()
    return render_template("cart.html", items=items, total=total)

@app.post("/panier/mettre-a-jour")
def update_cart():
    new_cart = {}
    for key, value in request.form.items():
        if key.startswith("qty_"):
            product_id = key[4:]
            try:
                qty = max(0, min(99, int(value)))
                if qty:
                    new_cart[product_id] = qty
            except ValueError:
                continue
    session["cart"] = new_cart
    flash("Panier mis à jour.", "success")
    return redirect(url_for("cart"))


# ---------------------------------------------------------------------------
# Comptes clients
# ---------------------------------------------------------------------------
@app.route("/inscription", methods=["GET", "POST"])
def register():
    if session.get("customer_id"):
        return redirect(url_for("account"))
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        if not name or len(name) > 100 or "@" not in email or len(email) > 254:
            flash("Vérifiez votre nom et votre adresse e-mail.", "error")
        elif len(password) < 10:
            flash("Choisissez un mot de passe d'au moins 10 caractères.", "error")
        elif password != confirm:
            flash("Les deux mots de passe ne correspondent pas.", "error")
        else:
            try:
                db = get_db()
                cursor = db.execute(
                    "INSERT INTO customers (name,email,password_hash,created_at) VALUES (?,?,?,?)",
                    (name, email, generate_password_hash(password), datetime.now().isoformat(timespec="minutes"))
                )
                db.commit()
                session.clear()
                session["customer_id"] = cursor.lastrowid
                session["customer_name"] = name
                session.permanent = True
                flash("Votre compte a été créé. Bienvenue chez Malinova Maison !", "success")
                return redirect(url_for("account"))
            except sqlite3.IntegrityError:
                flash("Un compte existe déjà avec cette adresse e-mail.", "error")
    return render_template("register.html")

@app.route("/connexion", methods=["GET", "POST"])
def login():
    if session.get("customer_id"):
        return redirect(url_for("account"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        customer = get_db().execute(
            "SELECT * FROM customers WHERE email=? COLLATE NOCASE", (email,)
        ).fetchone()
        if customer and check_password_hash(customer["password_hash"], password):
            session.clear()
            session["customer_id"] = customer["id"]
            session["customer_name"] = customer["name"]
            session.permanent = True
            flash("Vous êtes connecté.", "success")
            next_url = request.args.get("next", "")
            if next_url.startswith("/") and not next_url.startswith("//"):
                return redirect(next_url)
            return redirect(url_for("account"))
        flash("Adresse e-mail ou mot de passe incorrect.", "error")
    return render_template("login.html")

@app.post("/deconnexion")
def customer_logout():
    session.clear()
    flash("Vous êtes déconnecté.", "success")
    return redirect(url_for("index"))

@app.route("/mon-compte")
@customer_required
def account():
    customer = get_db().execute(
        "SELECT id,name,email,created_at FROM customers WHERE id=?",
        (session["customer_id"],)
    ).fetchone()
    if not customer:
        session.clear()
        return redirect(url_for("login"))
    orders = get_db().execute(
        "SELECT id,total,status,created_at,items_json FROM orders WHERE customer_id=? ORDER BY id DESC",
        (customer["id"],)
    ).fetchall()
    return render_template("account.html", customer=customer, orders=orders)


# ---------------------------------------------------------------------------
# Commande + paiement Stripe Checkout
# ---------------------------------------------------------------------------
def cancel_pending_order(order_id):
    """Annule une commande uniquement si elle attend encore son paiement."""
    if not order_id:
        return
    db = get_db()
    db.execute(
        "UPDATE orders SET status=? WHERE id=? AND status=?",
        (STATUS_CANCELLED, order_id, STATUS_PENDING)
    )
    db.commit()


def mark_order_paid(order_id, stripe_session_id, amount_total):
    """
    Marque une commande comme payée, uniquement si Stripe a confirmé le paiement
    et si la session et le montant correspondent à la commande.
    Peut être appelée plusieurs fois sans danger (le stock n'est retiré qu'une fois).
    Retourne True si la commande est payée (ou déjà expédiée).
    """
    db = get_db()
    order = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not order or not stripe_session_id or order["stripe_session_id"] != stripe_session_id:
        return False
    if amount_total is None or int(amount_total) != to_cents(order["total"]):
        app.logger.error("Montant Stripe différent de la commande %s", order_id)
        return False
    cursor = db.execute(
        "UPDATE orders SET status=? WHERE id=? AND status IN (?, ?)",
        (STATUS_PAID, order_id, STATUS_PENDING, STATUS_CANCELLED)
    )
    if cursor.rowcount == 1:
        for line in json.loads(order["items_json"] or "[]"):
            db.execute(
                "UPDATE products SET stock = MAX(stock - ?, 0) WHERE id=?",
                (int(line.get("quantity", 0)), int(line.get("id", 0)))
            )
    db.commit()
    status = db.execute("SELECT status FROM orders WHERE id=?", (order_id,)).fetchone()["status"]
    return status in (STATUS_PAID, STATUS_SHIPPED)


@app.route("/commande", methods=["GET", "POST"])
def checkout():
    items, total, _ = cart_details()
    if not items:
        flash("Votre panier est vide.", "error")
        return redirect(url_for("shop"))
    customer = None
    if session.get("customer_id"):
        customer = get_db().execute(
            "SELECT * FROM customers WHERE id=?", (session["customer_id"],)
        ).fetchone()
    initial = {}
    if customer:
        initial = {"name": customer["name"], "email": customer["email"]}

    if request.method == "POST":
        fields = ["name", "email", "phone", "address", "postal_code", "city"]
        data = {f: request.form.get(f, "").strip() for f in fields}
        notes = request.form.get("notes", "").strip()
        if not all(data.values()) or "@" not in data["email"]:
            flash("Merci de renseigner tous les champs obligatoires avec une adresse e-mail valide.", "error")
            return render_template("checkout.html", items=items, total=total, form=request.form)

        ready, message = stripe_ready()
        if not ready:
            flash(message, "error")
            return render_template("checkout.html", items=items, total=total, form=request.form)

        for item in items:
            if item["qty"] > item["product"]["stock"]:
                flash(f"Stock insuffisant pour « {item['product']['name']} ».", "error")
                return redirect(url_for("cart"))

        if to_cents(total) < 50:
            flash("Le montant minimum pour un paiement par carte est de 0,50 €.", "error")
            return redirect(url_for("cart"))

        # Si le client revient après avoir abandonné un paiement, on annule l'ancienne commande en attente.
        cancel_pending_order(session.get("pending_order_id"))

        item_list = [{
            "id": item["product"]["id"],
            "name": item["product"]["name"],
            "quantity": item["qty"],
            "unit_price": item["product"]["price"],
            "line_total": item["line_total"]
        } for item in items]

        db = get_db()
        cursor = db.execute("""INSERT INTO orders
            (customer_id,customer_name,email,phone,address,postal_code,city,notes,items_json,total,status,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (customer["id"] if customer else None, data["name"], data["email"], data["phone"],
             data["address"], data["postal_code"], data["city"], notes,
             json.dumps(item_list, ensure_ascii=False), total, STATUS_PENDING,
             datetime.now().isoformat(timespec="minutes")))
        db.commit()
        order_id = cursor.lastrowid

        # Les montants envoyés à Stripe sont recalculés ici, depuis la base de données.
        line_items = [{
            "price_data": {
                "currency": "eur",
                "unit_amount": to_cents(item["product"]["price"]),
                "product_data": {"name": item["product"]["name"]},
            },
            "quantity": item["qty"],
        } for item in items]

        try:
            checkout_session = stripe.checkout.Session.create(
                mode="payment",
                line_items=line_items,
                client_reference_id=str(order_id),
                customer_email=data["email"],
                metadata={"order_id": str(order_id)},
                locale="fr",
                success_url=url_for("payment_success", _external=True) + "?session_id={CHECKOUT_SESSION_ID}",
                cancel_url=url_for("payment_cancelled", _external=True),
            )
        except stripe.StripeError:
            app.logger.exception("Erreur Stripe à la création de la session de paiement")
            cancel_pending_order(order_id)
            flash("Le paiement est momentanément indisponible. Réessayez dans quelques instants.", "error")
            return render_template("checkout.html", items=items, total=total, form=request.form)

        db.execute("UPDATE orders SET stripe_session_id=? WHERE id=?", (checkout_session.id, order_id))
        db.commit()
        session["pending_order_id"] = order_id
        return redirect(checkout_session.url, code=303)

    return render_template("checkout.html", items=items, total=total, form=initial)


@app.route("/paiement/succes")
def payment_success():
    session_id = request.args.get("session_id", "")
    if not session_id:
        abort(404)
    order = get_db().execute(
        "SELECT * FROM orders WHERE stripe_session_id=?", (session_id,)
    ).fetchone()
    if not order:
        abort(404)
    try:
        checkout_session = stripe.checkout.Session.retrieve(session_id)
    except stripe.StripeError:
        app.logger.exception("Erreur Stripe à la vérification du paiement")
        flash("Impossible de vérifier votre paiement pour le moment. Si vous avez été débité, contactez-nous.", "error")
        return redirect(url_for("shop"))

    if checkout_session.payment_status == "paid":
        if mark_order_paid(order["id"], checkout_session.id, checkout_session.amount_total):
            session["cart"] = {}
            session.pop("pending_order_id", None)
            return render_template("order_success.html", order_id=order["id"])
        flash("Le paiement n'a pas pu être rapproché de votre commande. Contactez-nous.", "error")
        return redirect(url_for("shop"))

    flash("Votre paiement est en cours de vérification. Vous recevrez une confirmation dès qu'il sera validé.", "success")
    return redirect(url_for("shop"))


@app.route("/paiement/annule")
def payment_cancelled():
    cancel_pending_order(session.pop("pending_order_id", None))
    flash("Paiement annulé. Votre panier a été conservé.", "error")
    return redirect(url_for("cart"))


@app.post("/webhook/stripe")
def stripe_webhook():
    """Stripe nous prévient ici (signature vérifiée) même si le client ferme son navigateur."""
    if not STRIPE_WEBHOOK_SECRET:
        return "Webhook non configuré", 503
    payload = request.get_data()
    signature = request.headers.get("Stripe-Signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, signature, STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.SignatureVerificationError):
        return "Signature invalide", 400

    obj = event.data.object
    order_id = getattr(obj, "client_reference_id", None)
    if order_id and event.type in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        if getattr(obj, "payment_status", "") == "paid":
            mark_order_paid(order_id, getattr(obj, "id", None), getattr(obj, "amount_total", None))
    elif order_id and event.type in ("checkout.session.expired", "checkout.session.async_payment_failed"):
        cancel_pending_order(order_id)
    return "", 200


# ---------------------------------------------------------------------------
# Pages d'information
# ---------------------------------------------------------------------------
@app.route("/a-propos")
def about():
    return render_template("about.html")

@app.route("/contact", methods=["GET", "POST"])
def contact():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        message = request.form.get("message", "").strip()
        if name and "@" in email and message:
            flash("Merci pour votre message. Le formulaire de démonstration a bien été validé, mais aucun e-mail n'est envoyé pour le moment.", "success")
            return redirect(url_for("contact"))
        flash("Merci de remplir tous les champs correctement.", "error")
    return render_template("contact.html")

@app.route("/mentions-legales")
def legal():
    return render_template("legal.html")


# ---------------------------------------------------------------------------
# Administration
# ---------------------------------------------------------------------------
@app.route("/admin", methods=["GET", "POST"])
def admin_login():
    if session.get("is_admin"):
        return redirect(url_for("admin_dashboard"))
    if request.method == "POST":
        password = request.form.get("password", "")
        if ADMIN_PASSWORD and secrets.compare_digest(password, ADMIN_PASSWORD):
            session["is_admin"] = True
            session.permanent = True
            return redirect(url_for("admin_dashboard"))
        flash("Mot de passe incorrect. Vérifiez que MALINOVA_ADMIN_PASSWORD est configuré sur Render.", "error")
    return render_template("admin_login.html")

@app.post("/admin/deconnexion")
def admin_logout():
    session.pop("is_admin", None)
    return redirect(url_for("index"))

@app.route("/admin/tableau-de-bord")
@admin_required
def admin_dashboard():
    db = get_db()
    products = db.execute("SELECT * FROM products ORDER BY id DESC").fetchall()
    orders = db.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 100").fetchall()
    customers = db.execute("SELECT id,name,email,created_at FROM customers ORDER BY id DESC LIMIT 100").fetchall()
    return render_template("admin.html", products=products, orders=orders, customers=customers)

@app.post("/admin/produit/ajouter")
@admin_required
def admin_add_product():
    try:
        name = request.form.get("name", "").strip()
        category = request.form.get("category", "Décoration")
        description = request.form.get("description", "").strip()
        price = float(request.form.get("price", "0"))
        image_url = request.form.get("image_url", "").strip()
        stock = max(0, int(request.form.get("stock", "0")))
        if not name or not description or price < 0 or category not in CATEGORIES:
            raise ValueError()
        get_db().execute(
            "INSERT INTO products (name,category,description,price,image_url,stock,active) VALUES (?,?,?,?,?,?,0)",
            (name, category, description, price, image_url, stock)
        )
        get_db().commit()
        flash("Produit ajouté et gardé masqué pour le moment.", "success")
    except (ValueError, TypeError):
        flash("Vérifie les champs du produit.", "error")
    return redirect(url_for("admin_dashboard"))

@app.post("/admin/produit/<int:product_id>/visibilite")
@admin_required
def admin_toggle_product(product_id):
    db = get_db()
    db.execute("UPDATE products SET active=CASE active WHEN 1 THEN 0 ELSE 1 END WHERE id=?", (product_id,))
    db.commit()
    return redirect(url_for("admin_dashboard"))

@app.post("/admin/commande/<int:order_id>/statut")
@admin_required
def admin_order_status(order_id):
    status = request.form.get("status", "À traiter")
    allowed = [STATUS_PENDING, "À traiter", STATUS_PAID, STATUS_SHIPPED, STATUS_CANCELLED]
    if status not in allowed:
        abort(400)
    db = get_db()
    db.execute("UPDATE orders SET status=? WHERE id=?", (status, order_id))
    db.commit()
    return redirect(url_for("admin_dashboard"))

@app.errorhandler(404)
def not_found(error):
    return render_template("404.html"), 404

if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1", host="127.0.0.1", port=5000)
