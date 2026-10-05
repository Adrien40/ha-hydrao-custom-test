# Hydrao Custom - Journal des modifications

## 1.1.0

💧💧💧💧💧💧💧💧💧💧

Cette version rend l'intégration plus fiable et plus précise : durées calculées à partir du compteur de l'appareil, totaux cumulés conservés dans un fichier dédié, deux nouveaux capteurs de diagnostic, et une suite de plus de 500 tests.

### 🚨 Changements majeurs
- **Home Assistant 2026.5.0 ou plus récent** (`hacs.json`), l'intégration utilisant une fonction Bluetooth apparue dans cette version (Python 3.14).

### ✨ Nouveautés
- Capteur de diagnostic **Durée Douche Eau Froide** : temps passé sous la température de confort pendant la douche en cours.
- Capteur de diagnostic **Durée avant Temp. Confort** : temps écoulé avant d'atteindre pour la première fois la température de confort (indisponible si l'eau était déjà chaude à la connexion).
- **Diagnostics** téléchargeables (adresse, nom et numéro de série masqués) : trames Bluetooth brutes, firmware et état du coordinateur, pour faciliter le support.
- Textes d'aide (`data_description`) dans les formulaires de configuration et d'options, dans les 19 langues.

### 🔄 Changements
- Les durées sont maintenant calculées en secondes à partir du compteur de l'appareil (1/50 s) et affichées en minutes ; les valeurs restaurées d'avant la mise à jour sont converties automatiquement.
- Le temps de confort est calculé par interpolation au moment où la température franchit le seuil, au lieu de compter l'intervalle entier d'un seul côté.
- Le capteur **Signal Bluetooth** est désactivé par défaut (à activer pour vérifier la portée) et limité à une mise à jour par seconde.
- Les capteurs de volume par douche passent en `total_increasing` et le débit reçoit sa classe d'appareil. Pour les statistiques long terme, utiliser les capteurs **Cumul Total**.

### 🐛 Corrections
- Les **totaux cumulés** (volume perdu et volume confort) sont désormais enregistrés dans leur propre fichier `.storage` : ils ne dépendent plus de l'état des entités. Migration automatique depuis la 1.0.0 ; le fichier est supprimé avec l'appareil.
- Une **température impossible** (hors 0–100 °C) est ignorée (*Inconnu* + avertissement unique dans le journal) au lieu d'être comptée comme eau chaude.
- Le **dépassement du compteur de durée** (uint16) est géré correctement ; toute autre baisse est traitée comme une réinitialisation de l'appareil, sans durée aberrante.
- Une trame d'annonce Bluetooth en cache n'est plus prise pour un signal récent (c'est son horodatage propre qui est utilisé).
- Le temps de savonnage est borné à 10–600 s avant l'écriture BLE, et les seuils de volume doivent tenir sur un octet.
- La connexion Bluetooth est fermée proprement après chaque session.

### 🧰 Maintenance
- Nouvelle classe de base `HydraoEntity`, icônes déplacées dans `icons.json`, `PARALLEL_UPDATES` déclaré, constantes nommées, typage complet (`mypy --strict`).
- Les dépendances `bleak` / `bleak-retry-connector` ne sont plus déclarées dans le manifest (fournies par l'intégration Bluetooth de Home Assistant).
- Le manifest déclare l'échelle de qualité `platinum` (`quality_scale.yaml`), avec des tests qui la gardent cohérente.
- Suite de **plus de 500 tests** : config/options flow, coordinateur, entités, diagnostics, traductions, stockage des totaux, documentation.
- CI : workflows *Tests*, *Typing* (mypy) et *Release*, configuration ruff (`ruff.toml`) et `.coveragerc`.

### 📚 Documentation
- `README.md` / `README.fr.md` : badges, version minimale de Home Assistant, sections *Mise à jour des données*, *Cas d'usage*, *Exemples d'automatisations*, *Limites connues* et *Désinstallation*, nouveaux capteurs et dépannage de la température.

💧💧💧💧💧💧💧💧💧💧

## 1.0.0

💧💧💧💧💧💧💧💧💧💧

### ✨ Nouveautés
- Première version stable.

💧💧💧💧💧💧💧💧💧💧
