# Checklist pré-déploiement Ghost Chess Academy — V26

## Flux coach
- Connexion coach avec `.env` copiée.
- Dashboard visible sans données de test inutiles.
- Centre Ghosts : inscriptions, paiements, RDV, devoirs, parties reçues, tournois.
- Paiements / accès : les Ghosts avec seulement l’accès app de base ne doivent plus apparaître.
- Suppression paiement/forfait : remet le Ghost à l’accès de base.
- Suppression lignes d’encaissement : retire les paiements de test des totaux.

## Flux Ghost
- Inscription Ghost 5 000 FCFA : demande → validation coach → code → compte actif.
- Accès Ghost de base : pas de forfait actif, pas de consommation 0/1.
- Choix formule : modal de paiement Arthur Simo (+237) 694054282.
- Paiement signalé → notification coach → validation → forfait actif.
- Forfait terminé : invitation à renouveler, jamais dette.
- Notifications Ghost : liste déroulante, clic = lu + redirection.

## Cohérence tarifs
- Inscription : 5 000 FCFA.
- Découverte : 2 000 FCFA / 30 min / 1 séance.
- Standard : 3 500 FCFA / 1h / 1 séance.
- Progression : 9 000 FCFA / 5 séances.
- Préparation tournoi : 10 000 FCFA / 4 séances.
- Offre Ghost : 20 000 FCFA / base 8 séances + adaptation à la demande.

## Tests rapides avant partage
1. Créer une demande d’inscription Ghost.
2. Valider et utiliser le code côté Ghost.
3. Choisir une formule, signaler paiement, valider côté coach.
4. Envoyer une partie, répondre avec feedback.
5. Créer un tournoi, répondre côté Ghost, supprimer tournoi.
6. Rafraîchir sur plusieurs onglets : rester sur la bonne zone.
7. Tester sur mobile ou fenêtre étroite.
