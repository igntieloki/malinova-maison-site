# Malinova Maison — boutique Python

Boutique de démonstration en Flask + SQLite : catalogue, recherche, catégories, panier, formulaire de commande et tableau de bord administrateur.

## 1. Prérequis
- Python 3.10 ou plus récent
- Une connexion Internet uniquement pour charger les images distantes dans les exemples

## 2. Installation
Ouvre un terminal dans ce dossier, puis exécute :

### Windows
```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:MALINOVA_SECRET_KEY = "remplace-par-une-cle-aleatoire-longue"
$env:MALINOVA_ADMIN_PASSWORD = "choisis-un-mot-de-passe-admin-solide"
python app.py
```

### macOS / Linux
```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
export MALINOVA_SECRET_KEY="remplace-par-une-cle-aleatoire-longue"
export MALINOVA_ADMIN_PASSWORD="choisis-un-mot-de-passe-admin-solide"
python app.py
```

Puis ouvre http://127.0.0.1:5000

## 3. Administration
Va sur http://127.0.0.1:5000/admin et utilise le mot de passe défini dans `MALINOVA_ADMIN_PASSWORD`.
Tu peux ajouter des produits, masquer/afficher les produits et changer le statut des commandes.

**Important :** si tu ne définis pas les variables d'environnement, les valeurs de démonstration ne sont pas sûres. Ne publie pas le site avant de les remplacer.

## 4. Personnaliser les produits
Les six produits d'exemple sont dans `SEED_PRODUCTS` au début de `app.py`. La base `malinova.db` est créée au premier lancement. Si tu changes les produits de départ après cette création, les nouveaux produits ne seront pas ajoutés automatiquement : supprime `malinova.db` uniquement si tu veux repartir de zéro (cela efface commandes et produits ajoutés).

Les images sont des URL distantes d'exemple. Utilise des photos que tu possèdes ou que tu as le droit d'utiliser.

## 5. Avant une vraie mise en vente
Cette base est un prototype, pas une boutique prête à encaisser des paiements. Avant la production :
- configure une clé secrète forte et un mot de passe administrateur fort ;
- ajoute une protection CSRF, des limites de tentatives et une configuration sécurisée des cookies ;
- connecte un prestataire de paiement reconnu (ne stocke jamais les données de carte dans cette application) ;
- vérifie stock, livraison, retours, remboursements et e-mails de confirmation ;
- rédige des mentions légales, CGV, politique de confidentialité et politique de cookies adaptées à ton activité ;
- configure HTTPS, sauvegardes et hébergement de production (ne lance pas le serveur de développement Flask en production) ;
- vérifie les obligations françaises et européennes applicables à la vente en ligne.
Le formulaire de contact est une démo : il ne transmet pas d'e-mail. Le formulaire de commande enregistre une commande mais ne prélève aucun paiement.
