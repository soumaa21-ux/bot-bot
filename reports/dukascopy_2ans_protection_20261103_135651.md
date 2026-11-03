# Dukascopy H1 (nov. 2024 - nov. 2026) : test de protection du compte

Source : GitHub Actions, 32 jobs (4 marchés x 8 trimestres). Fichiers : data_dukascopy/merged/.
Bougies H1 à l'heure pile (convention broker). yfinance : bougies décalées de 30 min sur les indices.
EURUSD, USOIL, XAGUSD : données yfinance (non téléchargées par Dukascopy).

Mode réel, configuration actuelle (blocage 85 %, budgets pondérés 1,5 % indices / 0,9 % autres, reprise B, arrêt à la 2e coupure).

Portefeuille : 29 trades, solde final 96 529 (-3,5 %), rupture non, coupure globale non.
Marchés gelés par leur budget : EURUSD, USOIL, XAGUSD, XAUUSD, US100, US30.

| Marché | Trades | Solde final | Gelé par budget |
|---|---|---|---|
| EURUSD (yf) | 1 | 98 974 | oui |
| US100 | 5 | 100 627 | oui |
| US30 | 8 | 100 137 | oui |
| US500 | 8 | 100 480 | non |
| USOIL (yf) | 1 | 99 175 | oui |
| XAGUSD (yf) | 2 | 99 157 | oui |
| XAUUSD | 4 | 99 492 | oui |

Lecture : aucune rupture. La configuration protège le compte mais ne crée pas de rendement. Le résultat yfinance précédent (-0,96 %) n'est pas reproduit avec les bougies Dukascopy : le résultat dépend de la source, ce qui indique l'absence d'avantage démontré.
