# GHOST Chess Platform — V18 Tournois & Déploiement

Port local : **5031**

Version orientée test élèves : Supabase/PostgreSQL, espace coach sécurisé, portail élève enrichi, offres FCFA, paiements, tournois, échanges, onboarding, responsive et persistance d’onglets.

## Lancement local

```bash
pip install -r requirements.txt
python app.py
```

Puis ouvrir :

```text
http://127.0.0.1:5031
```

Copie ton fichier `.env` de la version précédente dans ce dossier.

## Variables `.env`

```env
GHOST_DB_BACKEND=supabase
GHOST_SUPABASE_STRICT=1
SUPABASE_URL=https://TON-PROJET.supabase.co
SUPABASE_SERVICE_ROLE_KEY=sb_secret_...
GHOST_STORAGE_BUCKET=ghost-client-files
GHOST_SECRET_KEY=une-longue-cle-aleatoire
GHOST_ADMIN_USERNAME=coach
GHOST_ADMIN_PASSWORD=mot-de-passe-coach
PUBLIC_BASE_URL=https://ghostchessacademy.fr
GHOST_PUBLIC_URL=https://ghostchessacademy.fr
BACKEND_URL=https://ghostchessacademy.fr
LOCAL_DEV_URL=http://127.0.0.1:5031
```

## Supabase

1. Lance `supabase_schema.sql` dans SQL Editor.
2. Crée le bucket Storage `ghost-client-files`.
3. Mets les variables `.env`.
4. Ouvre `/health` pour vérifier que le backend est `supabase`.

## Nouveautés V18

- Offres FCFA corrigées :
  - Séance découverte : 2 000 FCFA, 30 minutes, 1 séance.
  - Séance standard : 3 500 FCFA, 1 heure, 1 séance.
  - Pack progression : 9 000 FCFA, 5 séances mensuelles.
  - Préparation tournoi : 10 000 FCFA, 4 séances ciblées.
  - Offre Ghost : 20 000 FCFA, accompagnement premium mensuel, base 8 séances / à la demande.
- Fenêtre de paiement avec Arthur Simo — (+237) 694054282.
- L’élève clique “Paiement effectué”, le coach reçoit une notification cliquable.
- Finances en FCFA avec encaissements validés et en attente.
- Onboarding élève non intrusif, affiché une seule fois.
- Logo/icone fantôme remplacé par un cavalier sobre animé.
- Haki visibles côté élève.
- Tournois : création coach, envoi à tous ou sélection, affichage côté élèves.
- Échanges élève ↔ binôme : messages, liens, FEN/PGN.
- Réinitialisation mot de passe via demande élève + action coach.
- Export sauvegarde JSON.
- Persistance de l’onglet élève et du contenu iframe après refresh.
- Responsive mobile renforcé.

## Déploiement

Cette app est une app Flask serveur. Pour un test réel avec élèves, privilégie Render ou Railway avec :

```bash
gunicorn app:app
```

Le fichier `render.yaml` est inclus pour faciliter Render. Netlify seul est plus adapté aux sites statiques/frontends et aux fonctions serverless JS/TS/Go ; pour cette version Flask complète, Render/Railway est plus simple et fiable.


## Deploiement domaine personnalise

Domaine officiel :

```text
https://ghostchessacademy.fr
```

Service Render :

```text
https://ghost-srbt.onrender.com
```

Variables d'environnement a verifier dans Render :

```env
PUBLIC_BASE_URL=https://ghostchessacademy.fr
GHOST_PUBLIC_URL=https://ghostchessacademy.fr
BACKEND_URL=https://ghostchessacademy.fr
```

Les liens internes de l'application doivent rester en chemins relatifs quand c'est possible :

```text
/login
/client
/api/...
```

DNS attendu cote LWS :

```text
ghostchessacademy.fr      A      216.24.57.1
www.ghostchessacademy.fr  CNAME  ghost-srbt.onrender.com
```

Commandes de test :

```powershell
nslookup ghostchessacademy.fr
nslookup www.ghostchessacademy.fr
```

Resultat attendu :

```text
ghostchessacademy.fr -> 216.24.57.1
www.ghostchessacademy.fr -> ghost-srbt.onrender.com
```

Si le domaine affiche encore la page LWS "Votre domaine a bien ete cree chez LWS", le probleme est DNS/LWS : supprimer les anciens records `A`/`AAAA` LWS, le parking domaine et les redirections web LWS. Ne pas supprimer les records `NS`, `MX` ou `TXT` lies aux mails/verifications. Voir `DEPLOYMENT_DOMAIN_CHECKLIST.md`.

### V18
- Réponses aux tournois plus fluides côté élève avec état visuel et possibilité de se rétracter.
- Bannière tournois sur le dashboard coach avec participants confirmés / invités.
- Suppression de tournoi côté coach.
- Notifications tournois conservées vers la section concernée.
