# Deployment Domain Checklist

Objectif officiel :

```text
https://ghostchessacademy.fr
```

Service Render :

```text
https://ghost-srbt.onrender.com
```

Domaines declares dans Render :

```text
ghostchessacademy.fr
www.ghostchessacademy.fr
```

Etat Render attendu :

```text
Verified
Certificate Issued
```

## Diagnostic actuel

Si `ghostchessacademy.fr` affiche encore la page LWS "Votre domaine a bien ete cree chez LWS", le code n'est pas la cause principale. Le domaine pointe encore vers LWS au niveau DNS ou une redirection/parking LWS est encore actif.

Verification observee avant correction DNS :

```text
ghostchessacademy.fr -> 83.229.19.70
ghostchessacademy.fr -> 2a00:7ee0:8:0:3:3857:0:a4c
www.ghostchessacademy.fr -> alias vers ghostchessacademy.fr
```

Ces valeurs correspondent a LWS, pas a Render.

## DNS attendu cote LWS

### Domaine racine

```text
Type: A
Nom: @
Valeur: 216.24.57.1
TTL: Auto ou 3600
```

### Sous-domaine www

```text
Type: CNAME
Nom: www
Valeur: ghost-srbt.onrender.com
TTL: Auto ou 3600
```

## A supprimer cote LWS

Supprimer les entrees qui maintiennent le domaine chez LWS :

- anciens records `A` vers LWS, par exemple `83.229.19.70`
- records `AAAA` vers LWS, par exemple `2a00:7ee0:8:0:3:3857:0:a4c`
- redirections web LWS
- parking domaine LWS
- `CNAME www` qui ne pointe pas vers Render

## A ne pas supprimer

Ne pas supprimer ces entrees sauf si tu sais exactement pourquoi :

- `NS`
- `MX`
- `TXT` lies aux mails
- `TXT` lies a une verification de domaine
- records SPF/DKIM/DMARC email

## Variables Render attendues

Dans Render, service `ghost-chess-academy`, ajouter ou verifier :

```env
PUBLIC_BASE_URL=https://ghostchessacademy.fr
GHOST_PUBLIC_URL=https://ghostchessacademy.fr
BACKEND_URL=https://ghostchessacademy.fr
```

Garder aussi les variables existantes :

```env
GHOST_DB_BACKEND=supabase
GHOST_SUPABASE_STRICT=1
GHOST_STORAGE_BUCKET=ghost-client-files
SUPABASE_URL=...
SUPABASE_SERVICE_ROLE_KEY=...
GHOST_SECRET_KEY=...
GHOST_ADMIN_USERNAME=...
GHOST_ADMIN_PASSWORD=...
```

## Commandes de test DNS

Depuis Windows :

```powershell
nslookup ghostchessacademy.fr
nslookup www.ghostchessacademy.fr
nslookup ghostchessacademy.fr 8.8.8.8
nslookup www.ghostchessacademy.fr 8.8.8.8
```

Resultat attendu :

```text
ghostchessacademy.fr -> 216.24.57.1
www.ghostchessacademy.fr -> ghost-srbt.onrender.com
```

## Commandes de test HTTP

Quand la propagation DNS est terminee :

```powershell
curl.exe -I https://ghostchessacademy.fr
curl.exe -I https://www.ghostchessacademy.fr
curl.exe -I https://ghostchessacademy.fr/login
```

Resultat attendu :

```text
HTTP/2 200
```

ou une redirection applicative normale vers `/login` selon la page appelee.

## Ordre conseille

1. Corriger les records DNS chez LWS.
2. Supprimer le parking et les redirections web LWS.
3. Attendre la propagation DNS.
4. Verifier avec `nslookup`.
5. Verifier dans Render que les deux domaines restent `Verified` et `Certificate Issued`.
6. Tester `https://ghostchessacademy.fr/login`.

