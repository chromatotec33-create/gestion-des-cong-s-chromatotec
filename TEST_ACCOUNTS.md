# Faux comptes par catégorie (jeu de test)

Utilise ces comptes pour couvrir tout le workflow de validation (`employe` ➜ `chef_service` ➜ `direction`).

## 1) Comptes à créer dans Supabase Auth

Dans **Supabase > Authentication > Users**, crée ces utilisateurs (mot de passe unique de test recommandé: `Test1234!`).

| Catégorie | Nom | Email |
|---|---|---|
| Direction | Nadia Direction | direction.test@chromatotec.local |
| Chef service (Production) | Karim Chef Prod | chef.prod.test@chromatotec.local |
| Chef service (Qualité) | Ines Chef Qualite | chef.qualite.test@chromatotec.local |
| Employé (Production) | Samir Employe Prod | employe.prod1.test@chromatotec.local |
| Employé (Production) | Leila Employe Prod | employe.prod2.test@chromatotec.local |
| Employé (Qualité) | Youssef Employe Qualite | employe.qualite1.test@chromatotec.local |

---

## 2) Insérer les profils applicatifs et les services

Une fois les comptes Auth créés, exécute ce SQL dans l'éditeur SQL Supabase :

```sql
-- Services
insert into public.services (nom)
values ('Production'), ('Qualité')
on conflict (nom) do nothing;

-- Profils utilisateurs (table public.users) reliés à auth.users
insert into public.users (id, nom, email, role, service_id, date_embauche)
select
  au.id,
  seed.nom,
  seed.email,
  seed.role::public.user_role,
  s.id,
  seed.date_embauche
from (
  values
    ('Nadia Direction', 'direction.test@chromatotec.local', 'direction', null::text, date '2022-01-10'),
    ('Karim Chef Prod', 'chef.prod.test@chromatotec.local', 'chef_service', 'Production', date '2022-09-01'),
    ('Ines Chef Qualite', 'chef.qualite.test@chromatotec.local', 'chef_service', 'Qualité', date '2023-02-01'),
    ('Samir Employe Prod', 'employe.prod1.test@chromatotec.local', 'employe', 'Production', date '2024-02-15'),
    ('Leila Employe Prod', 'employe.prod2.test@chromatotec.local', 'employe', 'Production', date '2025-01-10'),
    ('Youssef Employe Qualite', 'employe.qualite1.test@chromatotec.local', 'employe', 'Qualité', date '2024-11-05')
) as seed(nom, email, role, service_name, date_embauche)
join auth.users au on au.email = seed.email
left join public.services s on s.nom = seed.service_name
on conflict (id) do update
set
  nom = excluded.nom,
  email = excluded.email,
  role = excluded.role,
  service_id = excluded.service_id,
  date_embauche = excluded.date_embauche,
  updated_at = now();
```

---

## 3) Matrice de test rapide par catégorie

- **Employé** : créer une demande, consulter son propre solde et historique.
- **Chef service** : valider/refuser les demandes de son service uniquement.
- **Direction** : valider/refuser toutes les demandes, générer le bon PDF final.

## 4) Scénarios recommandés

1. `employe.prod1.test@chromatotec.local` crée une demande.
2. `chef.prod.test@chromatotec.local` valide la demande.
3. `direction.test@chromatotec.local` valide la demande pour activer la génération du PDF.
4. Refaire un second scénario avec refus chef ou refus direction pour couvrir les statuts finaux.
