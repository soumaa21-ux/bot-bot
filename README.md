# Blue Guardian Bot : version « premium » corrigée

Bot MT5 pour prop firm (Blue Guardian, modèle Instant forex). Données H1, périmètre USD. Objectif : ne jamais être éliminé
par une erreur technique ou un dépassement de risque, et ne parier sur une stratégie qu'après
validation hors échantillon.

> Aucun bot ne garantit de passer un challenge. Le code est testé, la stratégie de départ
> n'est **pas** validée. Ne payez aucun challenge tant que la walk-forward et le holdout ne sont pas
> positifs après coûts.

## Structure

```
config/
  rules_firm.yaml     règles de la firme (sourcées, voir plus bas)
  settings.yaml       risque, stratégie, sessions, coûts
  symbols.yaml        spécifications par symbole (remplacées par MT5 en live)
core/
  risk_engine.py      SEUL décideur : sizing, drawdown journalier/trailing, cascade, exposition
  strategy.py         MA cross + filtre tendance EMA200 + RSI + ADX (5 paramètres libres)
  indicators.py       EMA, RSI, ATR, ADX (causaux)
  regime_filter.py    filtre de régime (ADX)
  filters.py          session, news, spread
  state_store.py      état persistant SQLite (baseline journalière, HWM, verrous, journal)
  execution.py        adaptateur MT5 : filling mode, ordre vérifié, SL obligatoire
backtest/
  data.py             chargement CSV (MT5 ou générique), resample M1 -> M15
  engine.py           backtest portefeuille réaliste (même RiskEngine que le live)
  metrics.py          profit factor net, DD, pire jour, série de pertes
  walk_forward.py     optimisation par fenêtres + holdout 20 % évalué une seule fois
  monte_carlo.py      distribution du drawdown, probabilité de rupture
  run_backtest.py     CLI, rapport Markdown dans reports/
monitoring/
  logger.py           journal JSON Lines (logs/bot.jsonl)
  alerts.py           Telegram optionnel
tests/                38 tests (risque, sizing, backtest, exécution, stratégie, filtres)
tools/make_synthetic.py   génère des données factices pour tester le pipeline
run_bot.py            bot live MT5 (Windows)
```

## Règles de la firme : ce qui est vérifié

Source principale : blueguardian.com/forex (offre Instant 100K, valeurs officielles) :
- drawdown journalier : 3 000 $ (3 %) sur 100K, mesuré depuis le solde de début de journée ;
- drawdown total : 6 000 $ (6 %) trailing ;
- levier maximum 1:30 ; EA autorisés ; week-end et overnight autorisés ; news : **non** (modèle Instant).

Sources tierces (à confirmer dans votre tableau de bord) : reset journalier à 17h New York ;
trailing depuis le plus haut solde ; règle de holding minimum de 2 minutes ; blocage de ±5 min
autour des news sur les comptes financés. Le bot applique la version la plus stricte de chaque règle.

## Corrections appliquées

| # | Problème | Statut |
|---|---|---|
| 1 | Décision sur bougie en cours | Corrigé : bougies fermées (position 1), signal à la clôture i-1, exécution à l'ouverture de i |
| 2 | Pip codé en dur (US500, JPY) | Corrigé : sizing sur tick_size / tick_value, lu depuis MT5 en live |
| 3 | Lot sizing approximatif | Corrigé : arrondi vers le bas au volume_step, min/max, plafond de levier |
| 4 | Baseline perdue au redémarrage | Corrigé : baseline persistée en SQLite |
| 5 | Heure machine locale | Corrigé : reset calculé en fuseau de la firme (New York 17h), jamais l'horloge machine |
| 6 | Positions non fermées au circuit breaker | Corrigé : fermeture forcée au niveau 85 % |
| 7 | Pas de drawdown total | Corrigé : plus haut solde, verrou manuel |
| 8 | Pas de limite d'exposition | Corrigé : positions max et risque ouvert max (1 %) |
| 9 | `None` non gérés | Corrigé : positions_get, symbol_info, tick, order_check |
| 10 | Pas de filtre spread/news/horaires | Corrigé : les trois, en backtest comme en live |
| 11 | Filling mode figé | Corrigé : détecté par symbole |
| 12 | Pas de reconnexion | Partiel : nouvelles tentatives avec backoff, aucun ordre tant que déconnecté |
| 13 | Aucun test | Corrigé : 37 tests |
| 14 | Backtest sans coûts | Corrigé : spread, commission, slippage, pessimisme intrabar, équité flottante |
| 15 | Un seul actif | Partiel : multi-actifs avec solde partagé ; un seul timeframe à la fois |

Corrections de la roadmap non couvertes : tableau de bord, rapport quotidien, corrélation entre
actifs, cooldown par symbole, arrêt par Telegram, comparaison live/backtest sur démo, portefeuille
de 2 à 3 stratégies. Elles viennent après la validation de l'edge.

## Utilisation

### 1. Backtest

```bash
pip install -r requirements.txt
# Un fichier par symbole (export MT5 ou CSV avec colonne time)
python -m backtest.run_backtest --csv EURUSD=data/EURUSD_M15.csv --csv GBPUSD=data/GBPUSD_M15.csv \
       --csv US500=data/US500_M15.csv --walk-forward --montecarlo
# Données M1 : agrégation en M15 et annonces à fort impact (colonne time, UTC)
python -m backtest.run_backtest --csv EURUSD=data/EURUSD_M1.csv --resample 15min --news data/news.csv --walk-forward
```

Le rapport est écrit dans `reports/`. Lisez en priorité la section **walk-forward** et le **holdout**.
Le résultat « en échantillon » ne prouve rien. Le stress des coûts doublés est inclus.

### 2. Tests

```bash
python -m pytest -q tests/
```

### 3. Bot live (Windows, MT5 installé, compte démo)

```bat
pip install MetaTrader5 pandas numpy pyyaml
set MT5_LOGIN=12345678
set MT5_PASSWORD=xxxx
set MT5_SERVER=BlueGuardian-Demo
python run_bot.py
```

Arrêt d'urgence : créer un fichier `STOP` dans le dossier du bot. Le bot ferme toutes ses positions et quitte.

## Critères avant tout argent réel (roadmap, section 10)

1. Walk-forward positive hors échantillon après coûts (profit factor net > 1,3 selon la roadmap).
2. Holdout non contaminé également positif ; le holdout n'est évalué qu'une fois.
3. Monte Carlo : probabilité de rupture du trailing < 5 %.
4. Stress des coûts doublés : résultat encore acceptable.
5. 2 à 3 mois de démo cohérents avec le backtest, zéro incident technique.

## Limites connues

- Les spécifications de `symbols.yaml` (surtout US500 : tick_value, contract_size) sont des hypothèses. Le bot live les lit depuis MT5 ; vérifiez-les avant le backtest.
- Le backtest suppose une devise de cotation égale à la devise du compte (USD).
- Le drawdown « pire journée » du backtest est calculé par jour civil UTC ; la firme reset à 17h New York.
- Le moteur de risque garde le verrou trailing même si le solde remonte (conservateur, non modélisé : le verrou de la firme après +6 %).
- Le bot live n'a pas été exécuté contre un vrai MT5 dans cet environnement (Linux) : seules les parties testées avec un faux module MT5 ont été vérifiées.
- Aucune stratégie n'est validée. La stratégie de départ est une hypothèse à tester.


---

## Étude H1 : données fournies (12 fichiers, 2 ans)

Lancer : `python tools/run_h1_study.py` (rapport dans `reports/study_h1_*.md`).

**Périmètre testé** (devise de cotation USD) : EURUSD, US500, US100, US30, XAUUSD, XAGUSD, USOIL.

**Exclus** :
- BTCUSD, ETHUSD : cryptos, non supportées par Blue Guardian selon les sources tierces (à confirmer).
- GER40 (DAX), FRA40 (CAC40) : cotées en EUR. JP225 (Nikkei) : cotée en JPY. La conversion de devise n'est pas implémentée.

**Données** : bougies H1 ; ~3 450 bougies pour les indices, ~11 000 à 12 000 pour FX, or, argent, pétrole.
Contrôle de qualité : pas de doublon, pas de OHLC incohérent, pas de valeur nulle. Les sauts de plus de 5 % sont
cohérents avec des mouvements réels (avril 2025, mars 2026) ; ils sont conservés.

### Deux volets distincts

- **Volet A, edge** : mode recherche (`enforce_firm_limits=False`). Les trades sont mesurés sans que la firme gèle le compte.
- **Volet B, challenge** : compte réel simulé avec toutes les règles, départs tous les 45 jours, horizon 90 jours.

### Pourquoi deux volets

Un premier passage avec les règles de la firme appliquées à la walk-forward gelait le compte après 4 à 5 % de drawdown.
La walk-forward mesurait alors seulement le début de l'échantillon. Le mode recherche corrige cela ; le volet B garde la
règle de la firme pour la question « aurais-je passé le challenge ? ».

### Résultats

- **Edge, portefeuille, hors échantillon** : 179 trades, profit factor 1,03, espérance +0,02 R. Essentiellement nulle.
- **Holdout (20 % final, jamais utilisé pour choisir les paramètres)** : 54 trades, profit factor 0,66, espérance -0,23 R, rendement -5,8 %.
- **Monte Carlo** : probabilité de dépasser le trailing 6 % = 88 % (5 000 simulations sur les trades hors échantillon).
- **Stress des coûts x2** : pratiquement inchangé (PF 1,02), mais le signal de départ est déjà absent.
- **Volet B, challenge** : 14 départs, aucune rupture formelle, mais le compte est gelé dès -4,2 % (blocage à 70 % du trailing) ;
  rendement médian de l'horizon -2,3 %.

Par symbole, quelques PF hors échantillon dépassent 1,5 (XAGUSD, US100, US30, US500). Chaque symbole n'a que 9 à 43 trades
hors échantillon, et les holdouts sont trop faibles (3 à 15 trades). Ce n'est pas statistiquement exploitable.

### Conclusion

La stratégie actuelle (MA cross + filtres) **n'a pas d'edge démontrable** sur ces données. Selon la règle de la roadmap
(section 10), on ne passe pas à la phase suivante, et on ne paie pas de challenge.

### Limites de cette étude

- 2 ans de H1 donnent 3 000 à 12 000 bougies par symbole, soit trop peu de trades pour conclure. La roadmap demande 5 à 10 ans.
- Les contrats (tick_value, contract_size) des indices et matières premières sont des hypothèses dans `symbols.yaml`.
  Les % et les R y sont peu sensibles, mais les tailles minimales et le levier le sont.
- Pas de calendrier économique fourni : le filtre news est désactivé. Blue Guardian ne permet pas le trading news sur le modèle Instant.
- La walk-forward optimise 3 paramètres sur 8 combinaisons seulement.

### Prochaine étape recommandée

1. Fournir un historique plus long (5 ans minimum, idéalement H1 ou M15 sur les mêmes symboles).
2. Choisir une hypothèse de marché précise (roadmap, section 5.1) plutôt que d'optimiser la stratégie actuelle.
3. Ajouter un calendrier économique avant tout trading réel.
