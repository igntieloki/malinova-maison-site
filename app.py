import os
import sqlite3
from flask import Flask, render_template, request, redirect, url_for, g
import stripe

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "malinova.db")
app = Flask(__name__)
app.secret_key = os.environ.get("MALINOVA_SECRET_KEY", "cle-secrete-par-defaut")

# Configuration de la base de données et produits initiaux
SEED_PRODUCTS = [
    ("Vase Alba", "Décoration", "Vase décoratif aux lignes douces pour une ambiance naturelle.", 24.90, "https://images.unsplash.com/photo-1581783898377-1c85bf937427?auto=format&fit=crop&w=600&q=80"),
    ("Panier naturel", "Rangement", "Un panier polyvalent pour organiser plaids, accessoires ou petits objets.", 17.50, "https://images.unsplash.com/photo-1615874959474-d609969a20ed?auto=format&fit=crop&w=600&q=80"),
]

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    with app.app_context():
        db = get_db()
        db.execute('''
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                description TEXT,
                price REAL NOT NULL,
                image_url TEXT
            )
        ''')
        cursor = db.execute('SELECT COUNT(*) FROM products')
        if cursor.fetchone()[0] == 0:
            db.executemany(
                'INSERT INTO products (name, category, description, price, image_url) VALUES (?, ?, ?, ?, ?)',
                SEED_PRODUCTS
            )
            db.commit()

init_db()

# --- CONFIGURATION STRIPE ---
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")

@app.route('/')
def index():
    db = get_db()
    products = db.execute('SELECT * FROM products').fetchall()
    return render_template('index.html', products=products)

@app.route('/panier')
def panier():
    return render_template('panier.html')

# --- ROUTE DE PAIEMENT STRIPE CHECKOUT ---
@app.route('/creer-paiement', methods=['POST'])
def creer_paiement():
    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'eur',
                    'product_data': {
                        'name': 'Commande - Malinova Maison',
                    },
                    'unit_amount': 2900,  # 29,00 € en centimes
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=url_for('paiement_succes', _external=True),
            cancel_url=url_for('panier', _external=True),
        )
        return redirect(checkout_session.url, code=303)
    except Exception as e:
        return f"Erreur Stripe : {str(e)}", 400

@app.route('/paiement-succes')
def paiement_succes():
    return """
    <div style="text-align:center; padding:50px; font-family:sans-serif;">
        <h1 style="color:green;">🎉 Paiement réussi !</h1>
        <p>Merci pour votre achat chez Malinova Maison.</p>
        <a href="/">Retourner à l'accueil</a>
    </div>
    """

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
