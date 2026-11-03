# Comparaison des protections (H1 existant, ~2 ans, mode réel)

```
variante                                             trades  solde final  rupture  coupures marchés gelés
1. Actuel : verrou définitif, pas de reprise             30        94838    False         1 -
2. B : reprise à taille réduite (2 coupures max)         30        94838    False         1 -
3. C : budgets égaux 1,2 %                               16        95118    False         1 USOIL,XAGUSD,XAUUSD,EURUSD
4. C pondéré : indices 1,5 %, autres 0,9 %               35        99044    False         0 EURUSD,USOIL,XAGUSD,XAUUSD,US30,US100,US500
5. C pondéré + B (recommandé)                            35        99044    False         0 EURUSD,USOIL,XAGUSD,XAUUSD,US30,US100,US500
6. C égal + B                                            16        95118    False         1 USOIL,XAGUSD,XAUUSD,EURUSD
```
