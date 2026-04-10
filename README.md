# Application de gestion des congés (Flask + Supabase)

## Stack
- Frontend : HTML + CSS (templates Flask)
- Backend : Python Flask
- Base de données : Supabase PostgreSQL
- Auth : Supabase Auth
- Déploiement : Vercel compatible (`vercel.json`)

## Structure
```
/project
  /templates
  /static
  app.py
  supabase.sql
  requirements.txt
```

## Installation locale
1. Créer un environnement virtuel et installer les dépendances :
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
2. Copier `.env.example` vers `.env` puis compléter :
   - `SUPABASE_URL`
   - `SUPABASE_ANON_KEY`
   - `SUPABASE_SERVICE_ROLE_KEY`
   - `FLASK_SECRET_KEY`
3. Exécuter le SQL dans Supabase (`supabase.sql`).
4. Lancer l'application :
   ```bash
   flask --app app run --debug
   ```

## Déploiement Vercel
1. Importer le repository dans Vercel.
2. Configurer les variables d'environnement du projet :
   - `SUPABASE_URL`
   - `SUPABASE_ANON_KEY`
   - `SUPABASE_SERVICE_ROLE_KEY` (backend seulement)
   - `FLASK_SECRET_KEY`
3. Vercel détecte `vercel.json` et déploie `app.py` via `@vercel/python`.

## Sécurité
- Ne jamais exposer `SUPABASE_SERVICE_ROLE_KEY` côté frontend.
- Le backend Flask utilise la clé service role pour les opérations sensibles.
- L'auth utilisateur passe par Supabase Auth avec `ANON_KEY`.

## Fonctionnalités
- RBAC (employé, chef_service, direction)
- Workflow multi-niveaux des demandes
- Calcul des jours ouvrés et du solde de congés
- Commentaires publics/privés
- Historique des actions
- Génération PDF du bon de congé après validation finale
