[![English](https://img.shields.io/badge/Language-English-red)](README.md) [![Français](https://img.shields.io/badge/Langue-Fran%C3%A7ais-blue)](#)

# Hydrao Custom pour Home Assistant 🚿
[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/Adrien40/ha-hydrao-custom)](https://github.com/Adrien40/ha-hydrao-custom/releases)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![Tests](https://img.shields.io/github/actions/workflow/status/Adrien40/ha-hydrao-custom/tests.yaml?branch=main&label=tests)](https://github.com/Adrien40/ha-hydrao-custom/actions/workflows/tests.yaml)
[![HACS](https://img.shields.io/github/actions/workflow/status/Adrien40/ha-hydrao-custom/hacs.yaml?branch=main&label=hacs)](https://github.com/Adrien40/ha-hydrao-custom/actions/workflows/hacs.yaml)
[![Hassfest](https://img.shields.io/github/actions/workflow/status/Adrien40/ha-hydrao-custom/hassfest.yaml?branch=main&label=hassfest)](https://github.com/Adrien40/ha-hydrao-custom/actions/workflows/hassfest.yaml)
[![Linting](https://img.shields.io/github/actions/workflow/status/Adrien40/ha-hydrao-custom/ruff.yaml?branch=main&label=lint)](https://github.com/Adrien40/ha-hydrao-custom/actions/workflows/ruff.yaml)
[![Typing](https://img.shields.io/github/actions/workflow/status/Adrien40/ha-hydrao-custom/mypy.yaml?branch=main&label=mypy%20--strict)](https://github.com/Adrien40/ha-hydrao-custom/actions/workflows/mypy.yaml)
[![Quality Scale](https://img.shields.io/badge/HA%20Quality%20Scale-Platinum-e5e4e2)](custom_components/hydrao_custom/quality_scale.yaml)

Une **intégration 100 % locale pour Home Assistant** qui dialogue directement en Bluetooth Low Energy (BLE) avec votre appareil de douche Hydrao, pour suivre votre consommation d'eau douche après douche, sans aucune dépendance au Cloud. 🛡️

> ℹ️ **À savoir** : Cette intégration interroge directement l'appareil Hydrao en Bluetooth pendant que l'eau coule — c'est nécessaire pour lire les données et envoyer les réglages en direct à l'appareil.

Si ce projet vous est utile, vous pouvez soutenir son développement 🙏

<a href="https://www.buymeacoffee.com/adrien40"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" width="160"></a>

---

## ⚡ En résumé
- 🔌 Fonctionnement 100 % local via Bluetooth (BLE)
- 🏠 Compatible Home Assistant (Sans cloud)
- 🚿 Suivi détaillé de chaque douche : Volume, durée, volume perdu en eau froide
- 🔄⭐ Synchro Mode Confort : Remise à zéro automatique du compteur de l'appareil dès que l'eau atteint la Température de confort minimum définie
- 🎨 Réglage des 4 seuils en litres et choix de leur couleur via un sélecteur direct intégré
- 🌡️ Température de confort minimum réglable dans Home Assistant (pas d'envoi à l'appareil Hydrao)
- 🔘 Bouton "Douche Terminée" pour clôturer une douche manuellement ou via une automatisation (Ex. : Hey Google, Douche Terminée "Prénom") — <sub>un `input_boolean` peut être nécessaire pour le relier à Google Assistant</sub>
- ⚙️ Installation via HACS en 2 minutes

---

## 📸 Exemples dans Home Assistant

### 🔍 Détails techniques

<p align="center">
  <img src="docs/screenshots/entities_overview.png" width="600">
</p>

<p align="center">
  <em>🔍 Entités exposées par l'intégration & ⚙️ Options de Configuration avancées</em>
</p>

---

### 💡 Pourquoi cette intégration ?
Cette intégration exploite directement le protocole BLE de votre Hydrao pour un suivi domotique complet, sans compromis :

* **🔒 100 % local :** Aucune connexion internet nécessaire, les données transitent uniquement de la douche à Home Assistant.
* **🚿 Suivi précis par douche :** Volume, durée, et surtout le volume d'eau froide perdu avant que l'eau ne soit à bonne température.
* **🔄 Synchro Mode Confort :** Redémarre le compteur sur l'appareil dès que la Température de confort minimum est atteinte, pour que les seuils de litres et couleurs ne reflètent que l'eau réellement confortable et donc utilisée.
* **🛡️ Pérennité :** Aucune dépendance à un serveur ou une application tierce.

---

### ✅ Compatibilité / Prérequis
* 🏠 **Home Assistant** : version **2026.5.0 ou plus récente** (l'intégration s'appuie sur une fonction Bluetooth livrée pour la première fois dans cette version).
* 🏷️ **Modèles supportés** : Appareils Hydrao diffusant en Bluetooth (BLE) sous un nom détecté automatiquement (`HYDRAO*`).
* 🏅 **Testé sur** : Validé sur le **Hydrao Aloé (HYDRA_SHOWER)**, version Hardware 9.
* 🛠️ **Matériel requis** : Adaptateur Bluetooth interne, clé USB Bluetooth, ou **Bluetooth Proxy ESPHome** (Recommandé pour la portée, [installation facile ici](https://esphome.github.io/bluetooth-proxies/)).
* 💧 **Réveil de l'appareil** : L'Hydrao ne communique en Bluetooth que lorsque l'eau coule, pensez à faire couler l'eau pour que l'intégration puisse lire ou écrire les réglages (seuils, couleurs, temps de savonnage).
* 📶 **Signal Bluetooth** : Un capteur RSSI dédié permet de surveiller la qualité du signal en direct.

---

### ✨ Points forts
* 🔄⭐ **Synchro Mode Confort** (Interrupteur) : Remise à zéro automatique du compteur de l'appareil dès que l'eau atteint la Température de confort minimum définie — la fonctionnalité phare de cette intégration.
* 🏠 **100 % Local (BLE)** : Aucune dépendance au Cloud.
* 💧 **Volumes détaillés** : Volume total cumulé, volume de la douche en cours, volume confort, volume perdu (eau froide) — en cumul de session et en cumul total.
* ⏱️ **Durées détaillées** : Durée de la douche en cours, durée en zone de confort, durée en eau froide et temps avant d'atteindre l'eau chaude.
* 🎨 **Seuils & Couleurs** : Réglage des 4 seuils en litres et choix de leur couleur via un sélecteur direct intégré, lus et modifiés en direct sur l'appareil.
* 🌡️ **Température de confort minimum** : Réglable dans Home Assistant via une entité Number, avec plage validée (0 - 50 °C) — ce réglage reste dans Home Assistant, il n'est jamais envoyé à l'appareil Hydrao.
* 🧴 **Durée maximale de savonnage** : Durée avant remise à zéro des compteurs, réglable (10 à 600 secondes).
* 🔘 **Bouton "Douche Terminée"** : Termine manuellement le comptage en cours sans attendre la coupure d'eau. <sub>Astuce : un `input_boolean` peut être nécessaire pour relier ce bouton à Google Assistant.</sub>
* 🔵 **État Bluetooth détaillé** : Eau Coupée, Connexion, Connecté, Erreur, Envoi de Configuration, Configuration Appliquée, Échec, ou Redémarrage de l'appareil.
* 📋 **Configuration en Attente** : Indique en un coup d'œil si des réglages (seuils, couleurs, temps de savonnage) n'ont pas encore pu être envoyés à l'appareil.
* 📶 **Signal Bluetooth en direct** via écoute passive, sans solliciter l'appareil ni sa batterie.
* 🔧 **Diagnostic** : Firmware, Hardware et Identifiant Unique de l'appareil exposés au niveau de la fiche appareil.
* ⚙️ **Configuration 100 % UI** : Découverte automatique Bluetooth ou ajout manuel par adresse MAC, tout se règle depuis l'interface Home Assistant.
* 🔄 **Réinitialisation aux valeurs d'usine** disponible directement depuis les options.

---

### 🚀 Installation

#### Via HACS (Recommandé)
Ce dépôt n'étant pas (encore) dans la liste officielle par défaut, vous devez l'ajouter en tant que dépôt personnalisé.

1. Ouvrez **HACS** dans votre Home Assistant.
2. Cliquez sur les 3 petits points en haut à droite et sélectionnez **Dépôts personnalisés**.
3. Dans **Dépôt**, collez l'URL : `https://github.com/Adrien40/ha-hydrao-custom`
4. Dans **Type**, choisissez **Intégration** puis cliquez sur **Ajouter**.
5. Une fois ajouté, une fenêtre apparaît : Cliquez sur **Télécharger** (Sélectionnez la dernière version).
6. **Redémarrez complètement Home Assistant**.
7. Allez dans **Paramètres** > **Appareils et Services** > **Ajouter une intégration** et cherchez "Hydrao Custom".

#### Manuelle
Copiez le dossier `custom_components/hydrao_custom` dans le dossier `custom_components` de votre configuration Home Assistant, puis redémarrez.

---

### 📊 Capteurs et Contrôles disponibles
| Entité | Unité / Type | Description |
| :--- | :--- | :--- |
| 🔘 **Douche Terminée** | Bouton | Termine manuellement le comptage de la douche en cours. |
| ⏱️ **Durée Douche** | s (affichée en min) | Durée brute de la douche en cours. |
| ⏱️ **Durée Douche Confort** | s (affichée en min) | Durée passée en zone de confort. |
| ❄️ **Durée Douche Eau Froide** | s (affichée en min) | Durée passée sous la température de confort, pour la douche en cours. Capteur de diagnostic, désactivé par défaut. |
| ⏳ **Durée avant Temp. Confort** | s (affichée en min) | Temps écoulé avant d'atteindre la température de confort pour la première fois. Valeur figée une fois atteinte, même si l'eau refroidit ensuite. Indisponible tant qu'elle n'est pas atteinte, ou si l'eau était déjà chaude à la connexion. Capteur de diagnostic, désactivé par défaut. |
| 🌡️ **Température** | °C | Température de l'eau mesurée en direct. |
| 🚿 **Volume Douche** | L | Volume brut de la douche en cours. |
| 💧 **Volume Douche Confort** | L | Volume utilisé une fois la température de confort atteinte, pour la douche en cours. |
| 💧 **Volume Douche Confort Cumulé** | L | Cumul historique du volume utilisé une fois la température de confort atteinte. |
| 💧 **Volume Douche Cumulé** | L | Volume total cumulé depuis l'installation. |
| ❄️ **Volume Perdu (Eau Froide)** | L | Volume perdu avant d'atteindre la température de confort, pour la douche en cours. |
| ❄️ **Volume Perdu Cumulé** | L | Cumul historique du volume perdu en eau froide. |
| 🔄 **Synchro Mode Confort** | Interrupteur | Active la remise à zéro automatique dès le confort atteint. |
| 🌡️ **Température de confort minimum** | Number (°C) | Seuil de confort réglable (0 - 50 °C). |
| 📋 **Configuration en attente** | Statut | Réglage(s) en attente d'envoi à l'appareil (Aucune, Savonnage, Seuils, Couleurs, ou combinaisons). |
| 💨 **Débit** | L/min | Débit d'eau instantané. |
| 🧴 **Durée maximale de savonnage** | s | Durée maximale de savonnage actuellement configurée, lue sur l'appareil. |
| 🔵 **État Bluetooth** | Statut | Eau Coupée / Connexion / Connecté / Erreur / Envoi Configuration / Configuration Appliquée / Échec / Redémarrage de l'appareil. |
| 🟢🔵🩷🔴 **Seuil 1 à 4** | L | Les 4 paliers de litres configurés sur l'appareil, avec leur couleur en attribut. |
| 📶 **Signal Bluetooth** | dBm | Force du signal Bluetooth reçu en temps réel. Capteur de diagnostic, désactivé par défaut : activez-le dans les paramètres de l'entité pour vérifier la portée Bluetooth. |

ℹ️ *Le Firmware, l'Hardware et l'Identifiant Unique de l'appareil sont exposés par Home Assistant au niveau de la fiche appareil*

ℹ️ *Les capteurs par douche (**Volume Douche**, **Volume Douche Confort**, **Volume Perdu (Eau Froide)**) repartent de zéro à chaque douche. Pour les statistiques à long terme et le tableau de bord Eau, utilisez plutôt les capteurs **Cumulé**.*

---

## 🚀 Configuration
1. Allez dans **Paramètres** > **Appareils et services**.
2. **Si l'eau coule et que l'appareil Hydrao est à portée**, Home Assistant le détecte automatiquement : ouvrez la notification de découverte et suivez l'assistant. **Sinon**, cliquez sur **Ajouter une intégration**, recherchez **Hydrao Custom**, puis renseignez l'adresse MAC de l'appareil manuellement.
3. Dans les deux cas, vous pouvez régler la Température de confort minimum dès cette étape.

### ⚙️ Options
Une fois l'appareil ajouté, cliquez sur **Configurer** ⚙️ pour :
* Ajuster la Température de confort minimum, la Durée maximale de savonnage et la Synchro Mode Confort.
* Modifier les 4 seuils de litres et leurs couleurs (Uniquement une fois une première connexion établie — faites couler l'eau pour réveiller l'appareil).
* Réinitialiser aux valeurs d'usine en un clic.

> ⚠️ **L'eau doit couler** au moment de la validation du formulaire pour que les nouveaux seuils soient envoyés à l'appareil. Si ce n'est pas le cas, l'état affichera **"🚰 Eau Coupée"** et le réglage restera visible dans le capteur **Configuration en Attente** jusqu'à la prochaine douche — et si l'envoi échoue malgré tout une fois l'eau relancée, l'intégration réessaiera automatiquement à la douche suivante.

---

### 🔄 Mise à jour des données
L'intégration ne **fait pas de polling** : elle écoute passivement les annonces Bluetooth du Hydrao, qui n'apparaissent **que lorsque l'eau coule** :

* **L'eau démarre :** Home Assistant voit l'appareil, l'intégration se connecte et lit volume, durée, température et débit en continu tant que la connexion dure. Les entités sont mises à jour à chaque lecture, et **État Bluetooth** affiche *✅ Connecté (Douche en cours)*.
* **L'eau s'arrête :** la connexion se termine. Le **Débit** retombe à 0, l'**État Bluetooth** repasse à *🚰 Eau coupée*, et les autres capteurs conservent les valeurs de la dernière douche.
* **Nouvelle douche :** les compteurs repartent quand l'appareil est resté silencieux plus longtemps que la **Durée maximale de savonnage**, quand vous appuyez sur **Douche Terminée**, ou quand la **Synchro Mode Confort** les réinitialise.
* **Les réglages modifiés** (seuils, couleurs, temps de savonnage) sont mis en file d'attente et écrits à la prochaine connexion : ils apparaissent dans **Configuration en attente** jusqu'à la douche suivante.
* Le capteur **Signal Bluetooth** se met à jour à chaque annonce reçue.

---

### 🎯 Cas d'usage
* **Réduire l'eau gaspillée :** voir, douche après douche, combien de litres d'eau froide coulent avant d'atteindre la bonne température, et suivre la tendance avec les capteurs cumulés.
* **Des seuils en litres qui ont du sens :** avec le **Synchro Mode Confort**, les paliers de couleur de l'appareil ne comptent que l'eau réellement confortable.
* **Présence sous la douche :** utiliser l'**État Bluetooth** (*Connecté*) comme signal « douche en cours » pour la ventilation, l'éclairage ou le chauffage.
* **Pilotage vocal ou automatisé :** terminer une douche ou changer la température de confort depuis un script, un tableau de bord ou un assistant vocal.

---

### 🤖 Exemples d'automatisations
Remplacez les identifiants d'entités ci-dessous par les vôtres (ils commencent par le nom de votre appareil, par exemple `sensor.hydrao_eeff_...`).

**Être prévenu quand une douche a gaspillé trop d'eau froide**
```yaml
automation:
  - alias: "Douche : bilan de l'eau gaspillée"
    triggers:
      - trigger: state
        entity_id: sensor.hydrao_eeff_etat_bluetooth
        from: "success"
        to: "waiting"
    conditions:
      - condition: numeric_state
        entity_id: sensor.hydrao_eeff_volume_perdu_eau_froide
        above: 5
    actions:
      - action: notify.notify
        data:
          message: >-
            {{ states('sensor.hydrao_eeff_volume_perdu_eau_froide') }} L d'eau
            froide gaspillés pendant cette douche.
```

**Terminer la douche depuis une automatisation**
```yaml
actions:
  - action: button.press
    target:
      entity_id: button.hydrao_eeff_douche_terminee
```

---

### ⚠️ Limitations connues
* **L'eau doit couler** pour que l'appareil soit joignable : rien ne peut être lu ni écrit autrement. Les réglages modifiés eau coupée sont transmis à la douche suivante.
* **Les seuils et les couleurs** ne sont modifiables qu'après une première connexion réussie.
* La **Température de confort minimum** n'existe que dans Home Assistant et n'est jamais envoyée au Hydrao.
* La **Durée avant Temp. Confort** reste indisponible si l'eau était déjà chaude à la connexion.
* Seul le **Hydrao Aloé (HYDRA_SHOWER)**, version matérielle 9, a été testé. Les autres modèles Hydrao devraient fonctionner mais ne sont pas validés.
* La fiabilité dépend de la portée Bluetooth : un signal faible peut provoquer une *Erreur de connexion* ; un [proxy Bluetooth ESPHome](https://esphome.github.io/bluetooth-proxies/) près de la douche aide.

---

### 🗑️ Suppression de l'intégration
1. Allez dans **Paramètres** > **Appareils et services** et ouvrez **Hydrao Custom**.
2. Cliquez sur le menu ⋮ à côté de votre appareil et choisissez **Supprimer**. Home Assistant supprime l'appareil et ses entités.
3. Pour supprimer aussi le code : si vous avez installé via HACS, ouvrez **HACS**, sélectionnez **Hydrao Custom** et choisissez **Supprimer** ; si l'installation est manuelle, supprimez le dossier `custom_components/hydrao_custom`. Redémarrez ensuite Home Assistant.

> ℹ️ La suppression de l'intégration ne modifie rien sur le Hydrao lui-même : ses seuils, ses couleurs et sa durée de savonnage restent tels quels. Pour les remettre d'abord aux valeurs d'usine, utilisez **Réinitialiser aux valeurs d'usine** dans les options (voir plus haut).

> ℹ️ Les deux capteurs cumulés (**Volume Perdu Cumulé** et **Volume Douche Confort Cumulé**) conservent leurs totaux dans un fichier à eux, dans le dossier `.storage` de Home Assistant, inclus dans les sauvegardes de Home Assistant. Supprimer l'appareil supprime aussi ce fichier : si vous ajoutez à nouveau l'appareil, ces totaux repartent de 0.

---

### 🐛 Dépannage

<details>
<summary>⚠️ Voir les problèmes fréquents</summary>

* **"Eau Coupée" en permanence** : Normal, l'Hydrao ne communique en Bluetooth que lorsque l'eau coule.
* **Température affichée *Inconnu* (et un avertissement dans le journal)** : l'appareil a envoyé une température impossible (hors de 0–100 °C), donc l'intégration l'ignore au lieu de la compter comme de l'eau chaude. Certaines révisions de l'Hydrao encodent peut-être leurs valeurs autrement. [Ouvrez une issue](https://github.com/Adrien40/ha-hydrao-custom/issues) en joignant le fichier de diagnostics (**Paramètres** > **Appareils et services** > **Hydrao Custom** > ⋮ > **Télécharger les diagnostics**) : il contient les trames Bluetooth brutes, le firmware et la révision matérielle, ce qu'il faut pour corriger le décodage.
* **"Erreur de Connexion"** : Contrairement à "Eau Coupée", ce statut signifie que l'appareil a bien été détecté à portée, mais que la connexion ou la lecture a tout de même échoué (signal trop faible ou instable, coupure en plein milieu d'une douche). Rapprochez votre antenne ou [installez un Proxy Bluetooth ESPHome](https://esphome.github.io/bluetooth-proxies/) près de la douche.
* **"Échec de la Configuration"** : L'écriture des nouveaux réglages sur l'appareil a échoué après plusieurs tentatives. Aucune inquiétude : Le changement n'est pas perdu (visible dans **Configuration en Attente**), il sera automatiquement retenté à la prochaine douche.
* **Seuils/Couleurs grisés dans les options** : Ils ne sont lisibles/modifiables qu'après une première connexion réussie — faites couler l'eau une fois avant de les régler.

</details>

---

### 🤝 Contributions & Support
Pour tout bug ou demande d'amélioration, merci d'ouvrir une [Issue](https://github.com/Adrien40/ha-hydrao-custom/issues) sur ce dépôt.

### ⚖️ Licence & Avertissement
Projet sous licence **GPLv3**. Il s'agit d'un projet indépendant, sans aucun lien avec la société Hydrao. L'utilisation de ce logiciel se fait sous votre propre responsabilité.

---

**Développé avec ❤️ par @Adrien40**

<a href="https://www.buymeacoffee.com/adrien40"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" width="180"></a>

<!-- Keywords: Home Assistant custom integration, BLE sensor, water saving, shower monitoring, local control -->
