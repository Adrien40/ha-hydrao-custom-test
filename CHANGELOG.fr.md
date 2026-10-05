# Hydrao Custom - Journal des modifications

## 1.1.0

🐬🐬🐬🐬🐬🐬🐬🐬🐬🐬

Cette version est consacrée à la précision et à la fiabilité : les durées de douche et la répartition eau froide / confort sont désormais correctes, deux nouveaux capteurs de diagnostic montrent combien de temps l'eau est restée froide, et l'intégration est entièrement testée et typée strictement.

### ✨ Nouveautés
- Capteur **Durée Douche Eau Froide** : temps passé sous la température de confort pendant la douche en cours. Diagnostic, désactivé par défaut.
- Capteur **Durée avant Temp. Confort** : temps mis par l'eau pour atteindre la température de confort. Il reste *inconnu* quand l'eau était déjà chaude à la connexion, car la phase froide ne peut alors pas être mesurée. Diagnostic, désactivé par défaut.
- **Téléchargement des diagnostics** (*Télécharger les diagnostics* sur la page de l'appareil), avec l'adresse Bluetooth, le nom de l'appareil, le titre et l'ID de l'appareil masqués : le fichier peut être joint sans risque à une issue publique. Il contient les dernières trames Bluetooth brutes, ce qui permet de comprendre comment une révision de l'appareil encode ses valeurs.
- Le capteur **Débit** a maintenant la classe d'appareil *débit volumique*, ce qui permet à Home Assistant de l'afficher dans d'autres unités.
- Le capteur **Signal Bluetooth** (RSSI) est maintenant désactivé par défaut : c'est un outil d'assistance. Activez-le dans les paramètres de l'entité pour vérifier la portée Bluetooth.

### 🐛 Corrections
- **La version minimale de Home Assistant est maintenant déclarée correctement : 2026.5.0** (`hacs.json`, auparavant 2025.1.0). L'intégration a toujours eu besoin d'une fonction Bluetooth (`async_clear_advertisement_history`) livrée pour la première fois dans Home Assistant 2026.5.0 : sur une version antérieure, elle ne pouvait pas se charger du tout.
- **Les durées de douche sont maintenant correctes au-delà d'environ 21 minutes.** L'appareil compte le temps par pas de 1/50 s sur 16 bits, donc son compteur repart de zéro toutes les 21,8 minutes ; ce dépassement est maintenant géré, et les durées ne repartent plus de zéro.
- **La répartition eau froide / confort est plus précise.** Quand la température franchit le seuil de confort entre deux relevés, le volume et le temps sont maintenant partagés au point de passage, au lieu d'être comptés entièrement du côté du dernier relevé.
- **Durée maximale de savonnage hors limites** (hors de 10–600 s) : il est ramené dans la plage avec un avertissement dans le journal, au lieu d'être envoyé tel quel à l'appareil.
- Les durées enregistrées avant la mise à jour (en minutes) sont converties à la restauration, donc un redémarrage juste après la mise à jour n'affiche plus des valeurs 60 fois trop petites.
- Les capteurs de volume perdu, de volume de douche confort et de volume de douche brut utilisent maintenant la classe d'état `total_increasing` au lieu de `measurement`, que Home Assistant refuse pour la classe d'appareil `water` (un avertissement était journalisé à chaque démarrage). Voir les notes de mise à jour.
- **Le capteur Température affiche *inconnu* au lieu de 0 °C** pendant qu'un *Douche Terminée* attend sa confirmation, ce qui évite de fausser l'historique et les moyennes.
- **La première mesure d'une douche arrive plus tôt** : les réglages de l'appareil sont lus après elle, et non avant. Cela aide *Durée avant Temp. Confort*.

### 🛡️ Renforcement
- **Une température de l'eau hors de 0–100 °C n'est plus utilisée.** Certaines révisions de l'Hydrao encodent peut-être la température autrement, ce qui pouvait afficher des centaines de degrés et compter toute l'eau comme confortable. Le capteur *Température* affiche maintenant *inconnu*, les chiffres confort / froid et la synchro du mode confort ignorent ce relevé, le volume, la durée et le débit continuent de fonctionner, et un seul avertissement dans le journal donne le firmware, le matériel et la trame brute à signaler.
- Une baisse du compteur de durée de l'appareil qui n'est pas un dépassement du compteur est traitée comme une réinitialisation de l'appareil et ne compte pour rien, au lieu de produire une durée énorme et fausse.
- Les seuils hors de 0–255 sont refusés ; une valeur matérielle vide ou des réglages enregistrés incomplets ne provoquent plus d'erreur ; une annonce en cache périmée n'est plus prise pour récente au démarrage.
- **Les totaux cumulés survivent à un plantage** : enregistrés dans leur propre fichier quelques secondes après chaque changement, et non toutes les 15 minutes. Les totaux de la 1.0.0 sont repris automatiquement.
- **La connexion Bluetooth est fermée explicitement** après chaque cycle ; un échec de fermeture est seulement journalisé.

### 🧰 Maintenance
- Toutes les entités partagent maintenant une classe de base commune (`HydraoEntity`) : noms traduits, ID unique construit à partir de l'adresse Bluetooth, appareil. **Les ID d'entités et les ID uniques sont inchangés.**
- Les icônes sont définies dans `icons.json` (mêmes icônes qu'avant, le statut Bluetooth garde une icône par état).
- Les durées sont calculées en secondes à partir de différences entières du compteur, et affichées en minutes par Home Assistant.
- `PARALLEL_UPDATES` est défini sur chaque plateforme.
- Descriptions de champs (`data_description`) ajoutées aux formulaires d'installation et d'options, traduites dans les 19 langues.
- Toute l'intégration passe `mypy --strict`, vérifié par un workflow *Typing* dédié.
- Suite de tests passée de 15 à plus de 500 tests, avec 100 % de couverture (lignes et branches) : appareil Bluetooth simulé, coordinateur, config / options flow, entités, cycle de vie de l'entrée, traductions.
- CI : workflow pytest + couverture (95 % minimum), workflow *Typing*, et un workflow de publication qui reprend les notes de ce journal (`scripts/release_notes.py`). `.coveragerc` mesure uniquement l'intégration, avec les branches.
- Le manifest déclare l'échelle de qualité `platinum` (auto-évaluée dans `quality_scale.yaml`, hassfest ne la valide pas pour les intégrations personnalisées), avec des tests qui la gardent cohérente avec le code.
- Le capteur **Signal Bluetooth** ne met à jour son état qu'une fois par seconde au plus, et seulement si la valeur change.
- `manifest.json` ne déclare plus `bleak` ni `bleak-retry-connector` (fournis par l'intégration Bluetooth de Home Assistant) ; règles `ruff` plus strictes.
- Refactorisation interne, sans changement de comportement : les capteurs portent leur fonction de valeur, les champs des formulaires partagent un helper, les constantes sont nommées.

### 📚 Documentation
- `README.md` / `README.fr.md` : version minimale de Home Assistant, les nouveaux capteurs, et les nouvelles sections *Mise à jour des données*, *Cas d'usage*, *Exemples d'automatisations*, *Limitations connues* et *Suppression de l'intégration*.
- Une note sur les capteurs de volume par douche et cumulés, et des badges d'état (CI, licence, échelle de qualité).

### 📋 Notes de mise à jour
- **Les capteurs de durée gardent leur unité** : Home Assistant les convertit automatiquement, donc une *Durée Douche* existante continue de s'afficher en minutes.
- **Statistiques de trois capteurs** : *Volume Perdu (Eau Froide)*, *Volume Douche Confort* et *Volume Douche* changent de classe d'état. Home Assistant peut proposer de corriger leurs statistiques à long terme dans **Outils de développement** > **Statistiques** ; acceptez. Pour les statistiques à long terme et le tableau de bord Eau, utilisez les capteurs cumulés (*Volume Douche Cumulé*, *Volume Perdu Cumulé*, *Volume Douche Confort Cumulé*) plutôt que ceux par douche.
- Le capteur **Signal Bluetooth** n'est désactivé que sur les nouvelles installations : un capteur existant reste activé.
- **Les totaux cumulés sont repris** au premier démarrage après la mise à jour. Gardez les deux capteurs cumulés **activés** pour ce démarrage, sinon leur total repart de 0.
- **Le capteur Température peut être *inconnu*** au lieu de 0 °C pendant une confirmation *Douche Terminée* : à prévoir dans les templates.

🐬🐬🐬🐬🐬🐬🐬🐬🐬🐬

## 1.0.0

🐬🐬🐬🐬🐬🐬🐬🐬🐬🐬

### ✨ Nouveautés
- Première version stable.

🐬🐬🐬🐬🐬🐬🐬🐬🐬🐬
