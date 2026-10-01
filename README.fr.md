# Sidekick

**Un agent IA local qui tourne sur votre appareil Android.** Un seul fichier Python, aucune dépendance tierce, le navigateur comme interface.

Il ne se contente pas de discuter — il **agit réellement sur votre appareil** : lire et écrire des fichiers, exécuter des commandes, modifier du code, naviguer sur le web, piloter votre téléphone, créer des tableurs, retoucher des images.

> `Sidekick` n'est que le nom d'usine par défaut. Renommez-le comme vous voulez dans la page « Identité ».

**Langues :** [English](README.en.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-TW.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Español](README.es.md) | [Deutsch](README.de.md) | **Français** | [Русский](README.ru.md) | [العربية](README.ar.md)

---

## Ce qui le distingue des autres IA

### 1. Installation à toute épreuve — deux commandes, pas de troisième étape

Pas besoin de connaître Python. Pas de configuration d'environnement. Pas de base de données. Installez Termux, collez deux lignes, ouvrez le navigateur, c'est prêt.

```bash
pkg install -y python curl
bash install.sh
```

Ni Docker, ni Node, ni conflits de dépendances. Il n'utilise **que la bibliothèque standard de Python**, donc les mises à jour en amont ne peuvent pas le casser — s'il fonctionne aujourd'hui, il fonctionnera encore dans trois ans.

### 2. Il évolue tout seul chaque jour — sans entretien, sans mise à jour distante

C'est le point le plus inhabituel : **il s'améliore un peu chaque jour, et personne n'a besoin de vous pousser une mise à jour.**

Chaque jour, il évalue sa propre performance, trouve un point à améliorer, applique le changement et l'inscrit dans un journal. Voici du travail réel qu'il a fait de lui-même :

| Date | Ce qu'il a modifié tout seul |
|---|---|
| 09-28 | Ajout de suggestions « archiver par type » à `list_dir` — en listant les fichiers, il vous dit désormais que les images vont dans `Pictures/` et les documents dans `Documents/` |
| 09-28 | Quand un argument d'outil est mal orthographié, il écarte silencieusement les arguments non pris en charge et suggère le nom correct le plus proche (ex. `cwd` → `command`) |
| 09-29 | `read_file` ne crache plus d'octets illisibles sur les fichiers binaires ; il indique le type et la taille du fichier |
| 10-01 | Hauteur fixe pour la carte de la console — car les tests ont révélé que le bouton Enregistrer se décalait de 447 px au changement d'onglet, provoquant des appuis manqués |

> Ce ne sont pas des slogans marketing. Ce sont des extraits de son propre journal d'évolution. Une fois lancé sur votre machine, il développera des améliorations **qui vous concernent**.

**Ce que cela signifie :** votre agent et celui d'une autre personne divergeront après un mois d'usage. Si vous lui faites souvent trier des albums photo, il développera un meilleur traitement d'image. Si vous l'utilisez pour des rapports, il développera un flux de travail documentaire plus fluide. **Chaque appareil élève un agent unique.**

### 3. Il répare ses propres bugs — et vous pouvez le voir diagnostiquer

Quand le programme plante, il ne laisse pas qu'une trace d'erreur. Il intègre un mécanisme d'**auto-réparation** : détecter le plantage → lire ses propres journaux pour le localiser → corriger le code → vérifier → consigner le résultat.

Un enregistrement réel (issu de `selfheal.json`) :

```
10-01 14:22  Plantage détecté   journal du watchdog · empreinte f99407371b41
10-01 14:23  Réparation finie   Confirmé — la ligne 2809 est du code de lecture
                                de journaux sans rapport, et tool_wx_auto est
                                défini. Aucune modification supplémentaire.
```

Il va même jusqu'à **renverser sa propre conclusion erronée** : lors d'une autre auto-réparation, il a conclu que « ce n'est pas un bug à corriger, le fichier s'est déjà réparé lui-même », en exposant les preuves dans un tableau. Il ne modifie rien à l'aveugle.

Avant de se modifier lui-même, il **sauvegarde d'abord**, puis **effectue une vérification de syntaxe**, et **revient automatiquement en arrière** s'il ne démarre pas. « Il se répare » ne veut donc pas dire « il se détruit ».

### 4. Il reprend là où il s'est arrêté — les tâches longues survivent aux interruptions

Une tâche d'une demi-heure et Android tue Termux ? Après redémarrage, il **reprend automatiquement**, sans que vous ayez à tout réexpliquer.

```bash
$ tail evolve.log
[auto-réparation] Marqueur de reprise explicite trouvé ; poursuite automatique
[auto-réparation] Reprise de 20260928-034008-1058 (boot), tentative 2
```

Redémarrage du téléphone, changement de réseau, Termux tué par le système — il se souvient où il en était.

---

:::tip En une phrase

**Facile à installer, grandit à l'usage, se répare quand il casse, reprend après une coupure.**

:::

### Vérifiez par vous-même — ne me croyez pas sur parole

Après l'installation, consultez ses propres journaux d'évolution :

```bash
cat ~/.termux-agent/evolve.json     # ce qu'il a amélioré pour lui-même, et pourquoi
cat ~/.termux-agent/selfheal.json   # quels plantages il a trouvés et corrigés
tail ~/.termux-agent/evolve.log     # journal en direct de l'évolution et de l'auto-réparation
```

Ces fichiers sont **écrits par l'agent à l'exécution** ; ce n'est pas du matériel marketing préemballé. Laissez-le tourner un moment et ils se rempliront d'enregistrements qui vous appartiennent.

---

## Sommaire

- [Ce qui le distingue des autres IA](#ce-qui-le-distingue-des-autres-ia)
  - [Installation à toute épreuve](#1-installation-à-toute-épreuve--deux-commandes-pas-de-troisième-étape)
  - [Évolution quotidienne](#2-il-évolue-tout-seul-chaque-jour--sans-entretien-sans-mise-à-jour-distante)
  - [Auto-réparation](#3-il-répare-ses-propres-bugs--et-vous-pouvez-le-voir-diagnostiquer)
  - [Reprise après interruption](#4-il-reprend-là-où-il-sest-arrêté--les-tâches-longues-survivent-aux-interruptions)
- [Ce que c'est](#ce-que-cest)
- [Installation](#installation)
- [Configuration de la clé API](#configuration-de-la-clé-api)
- [Usage quotidien](#usage-quotidien)
- [Aperçu des capacités](#aperçu-des-capacités)
- [Référence des outils (20)](#référence-des-outils-20)
- [Bibliothèque de skills (36)](#bibliothèque-de-skills-36)
- [Arborescence](#arborescence)
- [Notes de sécurité](#notes-de-sécurité)
- [FAQ](#faq)

---

## Ce que c'est

Un assistant IA qui vit dans votre téléphone ou votre tablette, utilisé via une interface navigateur.

**Choix de conception**

| Principe | Explication |
|---|---|
| **Aucune dépendance tierce** | N'utilise que la bibliothèque standard de Python ; `pkg install python` suffit. Aucun module natif à compiler, donc les mises à jour en amont ne pourront jamais le casser |
| **Stockage local** | Conversations, mémoire et clés restent dans `~/.termux-agent/` sur votre appareil. Rien n'est envoyé dans le cloud |
| **Peut se modifier lui-même** | Il peut éditer son propre code source pour ajouter des fonctions, et revient automatiquement en arrière si la modification le casse |
| **Watchdog intégré** | Si le service meurt, il redémarre automatiquement, sans systemd (qu'Android n'a pas) |
| **Aucune mise à jour distante** | Pas de serveur, pas de push de mise à jour. Son évolution est autonome ; les nouvelles capacités ne dépendent pas d'une publication de l'auteur |

---

## Installation

Prérequis : [Termux](https://f-droid.org/packages/com.termux/) installé sur l'appareil Android (**la version F-Droid est recommandée** — les modules complémentaires doivent partager la signature de l'application principale).

```bash
# 1. Installer Python
pkg update && pkg install -y python curl

# 2. Depuis ce répertoire, lancer l'installateur
bash install.sh
```

L'installateur va, dans l'ordre : vérifier l'environnement → sauvegarder toute installation existante → copier le programme et les skills → générer une configuration par défaut (**sans écraser une clé API existante**) → démarrer le service et s'auto-tester.

---

## Configuration de la clé API

Une clé API est nécessaire au premier lancement. Deux méthodes :

**Option 1 (recommandée)** : ouvrez `http://127.0.0.1:8765/` et renseignez-la dans « Réglages », en haut à droite.

**Option 2** : en ligne de commande
```bash
python3 ~/.termux-agent/agent.py config
```

Par défaut, il utilise DeepSeek (`https://api.deepseek.com`), mais vous pouvez le pointer vers n'importe quel point de terminaison compatible OpenAI — y compris un modèle local sur votre réseau.

---

## Usage quotidien

```bash
bash ~/.termux-agent/start.sh              # restaurer le service après un redémarrage de Termux
python3 ~/.termux-agent/agent.py doctor    # vérification de l'environnement + test de connectivité
python3 ~/.termux-agent/agent.py restart   # redémarrer le service
python3 ~/.termux-agent/agent.py selfcheck # auto-inspection (source/watchdog/service/sauvegardes)
```

Puis ouvrez **http://127.0.0.1:8765/** dans le navigateur.

### Usage direct en ligne de commande (sans interface)

```bash
python3 ~/.termux-agent/agent.py "vérifie l'occupation disque"   # exécuter une fois et quitter
python3 ~/.termux-agent/agent.py -c                             # reprendre la dernière conversation
python3 ~/.termux-agent/agent.py chat                           # discuter dans le terminal (basique)
```

### Démarrage automatique au boot (facultatif)

Installez [Termux:Boot](https://f-droid.org/packages/com.termux.boot/), ouvrez cette application une fois manuellement, puis :

```bash
python3 ~/.termux-agent/agent.py autostart
```

---

## Aperçu des capacités

| Catégorie | Capacités |
|---|---|
| **Opérations système** | Exécuter du shell, lire les paramètres système avec les privilèges shell, installer des APK, gérer les processus |
| **Traitement de fichiers** | Lire/écrire/modifier des fichiers, correctifs par lots, parcourir les répertoires, recherche récursive par expression régulière, lecture paginée des gros fichiers |
| **Réseau** | Récupérer des pages web en texte, recherche web sans clé, téléchargement en flux des gros fichiers |
| **Hébergement de code** | Flux GitHub complet (créer un dépôt / clone / pull / commit / push / rechercher du code) |
| **Pilotage d'appareils** | Piloter à distance un téléphone Huawei (protocole HDC), agir sur cet appareil en tant qu'adb shell |
| **Traitement d'images** | Effacer du texte ou un filigrane, restaurer de vieilles photos, améliorer la qualité, retoucher des portraits, détourer des arrière-plans |
| **Production de documents** | Générer Word / Excel / PPT, convertir HTML en DOCX, modèles de contrats, mise en forme soignée |
| **Gestion des tâches** | Listes de tâches, raisonnement parallèle par sous-agent, sollicitation de vos décisions |
| **Auto-évolution** | Modifier son propre code pour ajouter des fonctions, avec sauvegarde automatique + vérification de syntaxe + retour arrière en cas d'échec |
| **Skills métier** | 36 documents de skills métier (rédaction, design, juridique, finance, débogage, …) |

---

## Référence des outils (20)

### Système et fichiers

| Outil | Rôle | Points forts |
|---|---|---|
| `bash` | Exécuter des commandes shell | L'outil principal : installer des paquets, gérer des fichiers, lancer des programmes |
| `read_file` | Lire des fichiers texte (avec numéros de ligne) | Lecture paginée des gros fichiers ; un `offset` négatif lit les journaux depuis la fin |
| `write_file` | Écrire un fichier entier | Crée les répertoires parents automatiquement |
| `edit_file` | Remplacement exact de chaîne | Exige une correspondance unique ; en cas de doublons, indique leurs numéros de ligne |
| `apply_patch` | Correctif multi-sections / multi-fichiers | **Atomique** : si une section ne s'applique pas, rien n'est écrit — pas de modification à moitié |
| `list_dir` | Lister un répertoire | Inclut le type, la taille, la date de modification |
| `grep` | Recherche récursive par expression régulière | Renvoie `fichier:ligne: contenu` |

### Réseau

| Outil | Rôle | Remarques |
|---|---|---|
| `fetch_url` | Récupérer une page/API en texte brut | Lecture de texte uniquement |
| `web_search` | Recherche web | **Aucune clé API requise** |
| `download` | Téléchargement en flux des gros fichiers | Sans troncature, avec délai d'attente et nouvelle tentative automatique. Idéal pour les APK et les installateurs |

### Appareils et systèmes externes

| Outil | Rôle | Remarques |
|---|---|---|
| `sysshell` | Exécuter en tant que shell (adb) | Privilèges supérieurs à une application normale : lire les paramètres système, `dumpsys`, `getprop`, `pm/am`. **Pas root** |
| `hdcmate` | Piloter à distance un téléphone Huawei | Utilise le protocole HDC (pas adb). `exec` exécute une commande / `target` mémorise l'adresse / `test` teste la connexion. Automatisation complète de l'interface possible |
| `github` | Piloter des dépôts GitHub | Neuf actions : `list/repo/read/tree/clone/pull/push/create/search`. Nécessite un jeton |
| `wps` | Générer Word/Excel/PPT | Implémenté via un service MCP local, **sans compte requis**. Les fichiers arrivent dans `~/storage/shared/WPS_AI/` |
| `imgedit` | Traitement d'image par IA | Cinq opérations : `erase` texte/filigrane / `restore` vieilles photos / `enhance` qualité / `beauty` portrait / `matting` détourage |

### Collaboration et auto-évolution

| Outil | Rôle | Remarques |
|---|---|---|
| `todo_write` | Tenir une liste de tâches | Découpe les tâches de 3 étapes ou plus en 2 à 6 éléments, affichés en direct au-dessus de votre zone de saisie ; après une reconnexion, il reprend à partir de là |
| `subagent` | Réflexion indépendante d'un sous-agent | Confie un sous-problème isolé à un agent distinct (sans accès aux fichiers locaux). Utile pour paralléliser les problèmes difficiles |
| `ask_user` | Vous demander de décider | Ouvre un panneau de choix. Utilisé uniquement quand votre décision est indispensable : dépenser de l'argent, supprimer des données, choisir entre des approches |
| `selfupdate` | **Se modifier lui-même** | Sauvegarde automatique + vérification de syntaxe, retour arrière immédiat en cas d'échec ; le service redémarre automatiquement en cas de succès |

### Archivé

| Outil | Remarques |
|---|---|
| `wx_auto` | Compagnon de réponse automatique WeChat (lecture d'écran + réponse automatique ; nécessite une autorisation et la connaissance de l'autre partie). Trois garde-fous : les lectures incertaines sont seulement journalisées, abandon si le bouton d'envoi est introuvable, et plafond de 20 messages par heure |

---

## Bibliothèque de skills (36)

Les skills sont des **documents de méthodologie métier** remis à l'IA. Avant une tâche concernée, elle lit la skill correspondante et suit les conventions et les leçons apprises qu'elle contient.

### Rédaction (11)

| Skill | Usage |
|---|---|
| `general-writer` | **Solution de repli pour la rédaction générale (L1).** Documents officiels, rapports hebdomadaires, propositions, e-mails, copywriting, essais, nouveaux médias ; notation qualité en 7 dimensions + matrice d'adaptation à 10 genres |
| `academic-paper-expert` | Articles académiques : conception de structure, revue de littérature, résumés, normes de citation APA/GB-T7714, polissage académique |
| `tech-blog-expert` | Blogs techniques : tutoriels, analyses d'architecture, analyse de code source, documentation open source, README |
| `business-copy-expert` | Copywriting commercial : textes de marque, e-mails marketing, descriptions produit, slogans, conformité publicitaire (modèle AIDA) |
| `work-report-expert` | Rapports professionnels : bilans annuels, évaluations de performance, discours de promotion, rapports hebdomadaires/mensuels (principe de la pyramide + STAR) |
| `science-writing-expert` | Vulgarisation scientifique : explications, tests technologiques, reportages longs (technique de Feynman) |
| `poetry-prose-expert` | Poésie et prose : poésie moderne, vers classiques chinois, essais, critique littéraire |
| `stock-research-report-expert` | **Recherche en valeurs mobilières (L2).** Analyses sectorielles approfondies, recherche sur une action, commentaire d'événement, business plans ; quatre niveaux de longueur |
| `legal-contract-expert` | **Contrats juridiques (L2).** Rédaction et révision : exhaustivité des clauses obligatoires, symétrie des droits et obligations, prévention des risques élevés |
| `humanizer-zh` | Supprimer le « style IA » (chinois) : détecte et corrige d'après le guide Wikipédia sur les signes d'écriture par IA |
| `humanizer` | Supprimer le « style IA » (anglais) |

### Production de documents (5)

| Skill | Usage |
|---|---|
| `doc-typeset` | **Mise en page et finition.** Consomme des design tokens + du contenu et produit un HTML soigné. Inclut 7 modèles métier (contrats / articles académiques / documents officiels / rapports d'entreprise / comptes rendus de réunion / rapports de recherche / rapports annuels) |
| `html-to-docx` | Conversion HTML → Word haute fidélité. Prétraitement des variables CSS, mappage précis de plus de 10 types d'éléments et de 14 propriétés CSS |
| `format-extract` | Convertir un .docx en HTML sémantique + extraire les images intégrées (en préservant la hiérarchie des titres, les styles de tableau, les retraits, les couleurs) |
| `generate-fillable-contract-html` | Générer des HTML remplissables de contrats, devis et procurations en chinois |
| `underline-toolkit` | Documents à trous : `create` génère un modèle vide / `fill` injecte des données dans un modèle existant (contrats, formulaires, pages de garde) |
| `html-review` | **Contrôle qualité HTML.** Applique des vérifications en 5 dimensions à la sortie mise en page (conformité des tokens, intégrité structurelle, pertinence de la mise en page, adéquation au genre, modération des décors) et la renvoie pour corrections ciblées en cas d'échec |

### Design (8)

| Skill | Usage |
|---|---|
| `design-router` | Répartiteur de tâches de design : détermine quelle famille de design s'applique, puis distribue |
| `design-token` | Émet des design tokens normalisés par type de document, pilotant toutes les décisions de style de doc-typeset |
| `design-variables` | Lier/délier des variables de design (design tokens) à des propriétés de nœud |
| `ardot-design-to-code` | Transformer des maquettes en code front-end, ou extraire un design system / guide de style d'un site web |
| `ardot-ui-design` | Design d'interface : pages web, tableaux de bord, landing pages, interfaces mobiles |
| `ardot-poster` | Affiches visuelles : affiches, flyers, panneaux, bannières, visuels clés de campagne |
| `ardot-slides` | Design de présentations (pas des fichiers .pptx, mais des maquettes) |
| `component-instance` | Gestion des instances de composants : créer/mettre à jour des instances, définir les propriétés, changer de variante |
| `shared-styles` | Lier/délier des styles partagés (styles de texte, remplissages, contours, effets) |

### Débogage et exploitation (5)

| Skill | Usage |
|---|---|
| `termux-traps` | **Pièges de l'environnement Android Termux** : les chausse-trapes sur ce type d'appareil et la bonne façon de les gérer |
| `log-debug` | Guide de diagnostic par les journaux : quel fichier consulter en premier en cas de problème |
| `selfupdate` | Le bon flux d'automodification : le canal correct et les règles d'or pour éditer `agent.py` |
| `ui-debug` | Débogage de l'interface web : modifier l'UI et vérifier sur-le-champ |
| `pc-debug` | Exécuter des commandes et lire/écrire des fichiers sur ce PC Windows via le « pont de débogage PC » |

### Connectivité des appareils (2)

| Skill | Usage |
|---|---|
| `hdcmate` | Débogage de téléphone par HDC : notes sur le protocole, commandes HarmonyOS courantes, automatisation de l'interface, écueils rencontrés |
| `chrome-cdp` | Débogage et extraction Chrome : lire des données web via le protocole DevTools sans toucher à l'écran |

### Gestion des skills (4)

| Skill | Usage |
|---|---|
| `find-skills` | Vous aide à découvrir et installer des skills |
| `skill-creator` | Un guide pour créer de nouvelles skills |
| `marketplace-skill-installer` | Rechercher et installer depuis une place de marché de skills |
| `underline-toolkit` | Voir « Production de documents » ci-dessus |

---

## Arborescence

```
~/.termux-agent/
├── agent.py          Programme principal (fichier unique, bibliothèque standard seule)
├── cdp.py            Client CDP (débogage/extraction navigateur ; utilisé par ui-debug / chrome-cdp)
├── chrome_read.py    Lecteur de données web Chrome (utilisé par chrome-cdp)
├── hdc.py            Implémentation du protocole HDC (utilisé par hdcmate ; 447 lignes)
├── wps.py            Génération de documents WPS
├── wps_mcp_server.py Service MCP WPS local
├── mcp_call.py       Assistant d'appel MCP
├── config.json       Configuration (contient la clé API, mode 600)
├── skills/           Documents de skills (36)
├── sessions/         Enregistrements de conversations
├── memory.md         Mémoire à long terme
├── evolve.json       Journal d'évolution (ce qu'il change chaque jour, et pourquoi)
├── evolve.log        Journal d'exécution de l'évolution
├── selfheal.json     Historique d'auto-réparation (plantage détecté → réparation finie)
├── selfheal.py       Module d'auto-réparation et de reprise
├── uploads/          Images téléversées
├── outputs/          Artefacts générés
├── versions/         Sauvegardes de versions du code (pour l'auto-mise à jour)
├── logs/             Journaux
├── tmp/              Fichiers temporaires
├── supervisor.sh     Watchdog (généré automatiquement)
└── start.sh          Script de démarrage
```

---

## Notes de sécurité

- L'interface **n'écoute que sur `127.0.0.1`** et n'est pas exposée au réseau local (l'interface dispose de privilèges d'exécution de commandes)
- `config.json` et les fichiers de jeton sont en mode `600`
- Les opérations irréversibles — formater, écrire directement sur des périphériques de blocs, supprimer la racine — sont interceptées automatiquement
- Aucun privilège root, et il ne peut pas lire les données privées des autres applications
- Auto-vérification de l'environnement : `python3 ~/.termux-agent/agent.py doctor`

---

## FAQ

**Le service ne démarre pas ?**
```bash
python3 ~/.termux-agent/agent.py selfcheck   # voir ce qu'il dit de lui-même
tail -30 ~/.termux-agent/supervisor.log      # consulter le journal du watchdog
```

**Évolue-t-il vraiment tout seul ? Qu'a-t-il changé ?**
Regardez `~/.termux-agent/evolve.json` : chaque entrée indique ce qui a changé et pourquoi. Consultation rapide en ligne de commande :
```bash
python3 -c "import json;print('\n'.join(f'{e[\"time\"]}  {e[\"summary\"].splitlines()[0]}' for e in json.load(open('$HOME/.termux-agent/evolve.json'))['log']))"
```

**En quoi l'auto-évolution diffère-t-elle d'une mise à jour distante ?**
Il ne télécharge aucun paquet de mise à jour et aucun serveur ne lui pousse de version. Il **lit son propre code et ses journaux et modifie sur place**. C'est pourquoi les capacités diffèrent d'un appareil à l'autre — cela dépend de votre usage.

**Et s'il se casse en modifiant son propre code ?**
Le canal `selfupdate` **sauvegarde d'abord → vérifie la syntaxe après l'édition → restaure immédiatement en cas d'échec**. Même si le nouveau code démarre mais se comporte mal, le watchdog revient à la version précédente. Le processus d'auto-réparation laisse lui aussi une trace (`selfheal.json`).

**Une tâche longue a été tuée en cours par le système ?**
Après redémarrage, elle reprend automatiquement ; inutile de tout réexpliquer. Vous verrez « reprise » dans le journal.

**J'ai modifié le code mais rien ne change ?**
Le nouveau code ne prend effet qu'une fois chargé par le navigateur — actualisez simplement la page.

**Où voir les pièges qu'il a rencontrés ?**
`skills/termux-traps.md` documente les pièges de l'environnement sur ce type d'appareil.

**Comment ajouter de nouvelles capacités ?**
Utilisez la skill `skill-creator` pour créer un nouveau `.md` dans `skills/`, ou laissez l'IA s'ajouter un outil via `selfupdate`.

---

## Licence

Un projet personnel. Prenez-le et utilisez-le comme vous voulez.
