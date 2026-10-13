import os
import sqlite3
import secrets
from functools import wraps
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash, abort, g

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "malinova.db")
app = Flask(__name__)
app.secret_key = os.environ.get("MALINOVA_SECRET_KEY", "change-moi-avec-une-cle-aleatoire-longue")
ADMIN_PASSWORD = os.environ.get("MALINOVA_ADMIN_PASSWORD", "change-moi-avant-publication")

CATEGORIES = ["Décoration", "Rangement", "Cuisine", "Textiles"]

SEED_PRODUCTS = [
    ("Vase Alba", "Décoration", "Vase décoratif aux lignes douces pour une ambiance naturelle.", 24.90, "https://images.unsplash.com/photo-1578500494198-246f612d3b3d?auto=format&fit=crop&w=1000&q=85", 12),
    ("Panier naturel", "Rangement", "Un panier polyvalent pour organiser plaids, accessoires ou petits objets.", 19.90, "https://images.unsplash.com/photo-1593085260707-5377ba37f868?auto=format&fit=crop&w=1000&q=85", 15),
    ("Bocaux de cuisine", "Cuisine", "Des bocaux sobres pour garder vos ingrédients bien organisés.", 17.50, "https://images.unsplash.com/photo-1603199506016-b9a594b593c0?auto=format&fit=crop&w=1000&q=85", 20),
    ("Coussin doux", "Textiles", "Un coussin décoratif pour ajouter une touche chaleureuse à votre intérieur.", 22.00, "https://images.unsplash.com/photo-1584100936595-c0654b55a2e2?auto=format&fit=crop&w=1000&q=85", 10),
    ("Plateau minimaliste", "Décoration", "Un plateau pratique pour présenter vos objets préférés.", 16.90, "https://images.unsplash.com/photo-1603006905003-be475563bc59?auto=format&fit=crop&w=1000&q=85", 8),
    ("Organiseur malin", "Rangement", "Une solution simple pour garder les petits objets à portée de main.", 12.90, "https://images.unsplash.com/photo-1586023492125-27b2c045efd7?auto=format&fit=crop&w=1000&q=85", 18),
]

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
    db = sqlite3.connect(DB_PATH)
    db.execute("""CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        category TEXT NOT NULL,
        description TEXT NOT NULL,
        price REAL NOT NULL CHECK(price >= 0),
        image_url TEXT NOT NULL DEFAULT '',
        stock INTEGER NOT NULL DEFAULT 0,
        active INTEGER NOT NULL DEFAULT 1
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
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
        created_at TEXT NOT NULL
    )""")
    count = db.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    if count == 0:
        db.executemany(
            "INSERT INTO products (name, category, description, price, image_url, stock) VALUES (?, ?, ?, ?, ?, ?)",
            SEED_PRODUCTS
        )
    db.commit()
    db.close()

init_db()

def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("admin_login", next=request.path))
        return fn(*args, **kwargs)
    return wrapper

def cart_details():
    cart = session.get("cart", {})
    ids = [int(k) for k, qty in cart.items() if str(qty).isdigit() and int(qty) > 0]
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
    return {"cart_count": cart_count, "shop_name": "Malinova Maison", "current_year": datetime.now().year}

@app.route("/")
def index():
    db = get_db()
    products = db.execute("SELECT * FROM products WHERE active=1 ORDER BY id DESC LIMIT 6").fetchall()
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
    qty = request.form.get("qty", "1")
    try:
        qty = max(1, min(99, int(qty)))
    except ValueError:
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

@app.route("/commande", methods=["GET", "POST"])
def checkout():
    items, total, _ = cart_details()
    if not items:
        flash("Votre panier est vide.", "error")
        return redirect(url_for("shop"))
    if request.method == "POST":
        fields = ["name", "email", "phone", "address", "postal_code", "city"]
        data = {f: request.form.get(f, "").strip() for f in fields}
        notes = request.form.get("notes", "").strip()
        if not all(data.values()) or "@" not in data["email"]:
            flash("Merci de renseigner tous les champs obligatoires avec une adresse e-mail valide.", "error")
            return render_template("checkout.html", items=items, total=total, form=request.form)
        item_list = [{
            "id": item["product"]["id"],
            "name": item["product"]["name"],
            "quantity": item["qty"],
            "unit_price": item["product"]["price"],
            "line_total": item["line_total"]
        } for item in items]
        db = get_db()
        db.execute("""INSERT INTO orders
            (customer_name,email,phone,address,postal_code,city,notes,items_json,total,status,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (data["name"], data["email"], data["phone"], data["address"], data["postal_code"],
             data["city"], notes, __import__("json").dumps(item_list, ensure_ascii=False),
             total, "À traiter", datetime.now().isoformat(timespec="minutes")))
        db.commit()
        session["cart"] = {}
        return render_template("order_success.html", order_id=db.execute("SELECT last_insert_rowid()").fetchone()[0])
    return render_template("checkout.html", items=items, total=total, form={})

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
            # Démo : le message n'est pas envoyé par e-mail. Branchez un service mail avant publication.
            flash("Merci pour votre message. Le formulaire de démonstration a bien été validé, mais aucun e-mail n'est envoyé pour le moment.", "success")
            return redirect(url_for("contact"))
        flash("Merci de remplir tous les champs correctement.", "error")
    return render_template("contact.html")

@app.route("/mentions-legales")
def legal():
    return render_template("legal.html")

@app.route("/admin", methods=["GET", "POST"])
def admin_login():
    if session.get("is_admin"):
        return redirect(url_for("admin_dashboard"))
    if request.method == "POST":
        password = request.form.get("password", "")
        if secrets.compare_digest(password, ADMIN_PASSWORD):
            session["is_admin"] = True
            return redirect(url_for("admin_dashboard"))
        flash("Mot de passe incorrect.", "error")
    return render_template("admin_login.html")

@app.route("/admin/deconnexion", methods=["POST"])
def admin_logout():
    session.pop("is_admin", None)
    return redirect(url_for("index"))

@app.route("/admin/tableau-de-bord")
@admin_required
def admin_dashboard():
    db = get_db()
    products = db.execute("SELECT * FROM products ORDER BY id DESC").fetchall()
    orders = db.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 100").fetchall()
    return render_template("admin.html", products=products, orders=orders)

@app.route("/admin/produit/ajouter", methods=["POST"])
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
            "INSERT INTO products (name,category,description,price,image_url,stock) VALUES (?,?,?,?,?,?)",
            (name, category, description, price, image_url, stock)
        )
        get_db().commit()
        flash("Produit ajouté.", "success")
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
    allowed = ["À traiter", "Payée", "Expédiée", "Annulée"]
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
