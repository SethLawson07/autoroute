# Autoroute

🇫🇷 **Français** · 🇬🇧 [English](README.en.md)

**Apporte tes clés API. Autoroute choisit le modèle.**

Tu donnes à Autoroute ton prompt, c'est-à-dire la tâche à accomplir. Il choisit le modèle suffisant (le plus léger capable de la réussir sans perte de qualité) parmi ceux auxquels **tes clés API donnent accès** et te renvoie son nom. Tu n'as pas à savoir quel modèle utiliser pour quoi : Autoroute le fait à ta place et te dit pourquoi.

```python
from langchain.chat_models import init_chat_model
from autoroute import route

prompt = "Écris une fonction Python qui parse un CSV"
r = route(prompt)                 # le choix d'Autoroute : r.model, r.reason, r.fallbacks…
model = init_chat_model(r.model)  # ex. "anthropic:claude-sonnet-…"
```

- **OpenAI, Anthropic, Gemini, Mistral** : les modèles sont découverts avec tes clés, sans liste à maintenir.
- **Tes clés restent chez toi** : aucun serveur Autoroute, appels directs aux fournisseurs.
- **Mode souverain** : avec `AUTOROUTE_MODE=sovereign`, seuls les modèles Mistral (France 🇫🇷 🇪🇺) sont utilisés. Voir [Modes](#modes).
- **Le bon prix pour chaque tâche** : un modèle léger pour les tâches simples, un modèle puissant seulement quand il le faut. La mesure du gain n'est pas encore disponible.
- **Chaque choix est expliqué** : la décision et sa raison sont écrites dans un log JSONL.
- **Le routage ne plante pas ton application** : orchestrateurs de secours, puis repli sur un modèle découvert.

Autoroute n'appelle **pas** le modèle : il te renvoie un objet dont `r.model` est le nom du modèle, au format de LangChain (par défaut) ou au format natif des SDK officiels, de LlamaIndex et de Haystack (`route(prompt, to="native")`).

## Comment ça marche

![Comment fonctionne Autoroute](assets/how-it-works.fr.svg)

1. **Découverte** : Autoroute interroge l'API de liste des modèles de chaque fournisseur dont la clé est renseignée, et garde les modèles de chat. Il n'y a aucune liste de modèles à maintenir. La liste est gardée en mémoire 5 minutes : les appels suivants à `route()` ne la redemandent pas, et la découverte n'est refaite qu'au premier `route()` après ce délai. Chaque commande `autoroute` démarre un nouveau processus et refait donc la découverte. Pour choisir toi-même quand découvrir, voir [`discover_config()`](#choisir-quand-découvrir-les-modèles).
2. **Routage** : un **orchestrateur**, modèle léger choisi automatiquement parmi tes modèles pour que le routage reste rapide et peu coûteux (ou celui que tu choisis dans `.autoroute/config.yaml`), reçoit ton prompt et cette liste. Il renvoie le modèle suffisant pour la tâche, jusqu'à 2 replis, et la raison de son choix. Sa réponse est limitée aux modèles découverts.
3. **Log** : chaque décision est écrite dans un log JSONL.

## Installation

```bash
pip install autoroute-ai
```

- Avec uv : `uv add autoroute-ai`
- Version en cours de développement : `pip install git+https://github.com/SethLawson07/autoroute`

Python 3.10 ou plus. LangChain et les intégrations OpenAI, Anthropic, Google GenAI et Mistral sont installés avec Autoroute.

Déjà installés dans ton projet ? Les versions récentes sont conservées : Autoroute ne demande que des versions minimales (LangChain 1.0 ou plus). Si ton projet est encore en LangChain 0.x, pip le mettra à jour, ce qui peut changer le comportement de ton code existant. Pour éviter tout conflit, installe Autoroute dans un environnement virtuel.

## Démarrage rapide

```powershell
autoroute init                   # crée .env.example (et .autoroute/config.yaml, facultatif)
copy .env.example .env           # macOS / Linux : cp .env.example .env
# renseigne au moins une clé API (OpenAI, Anthropic, Gemini ou Mistral) dans .env ou dans une variable d'environnement
autoroute models                 # affiche la source de chaque clé et les modèles découverts
autoroute route "Conçois l'architecture d'une API de paiement sécurisée, avec gestion des remboursements"
autoroute ui                     # le log des décisions dans ton navigateur
```

Exemple de sortie :

```
Model:     anthropic:claude-opus-5-5
Fallbacks: openai:gpt-5, google_genai:gemini-3.1-pro-preview
Reason:    Conception d'architecture complexe avec enjeux de sécurité : un modèle puissant est nécessaire.
Routed:    yes
Id:        ae3a775c9f1e4b0d8c2a6e1f3b5d7a90
```

Dans ton code :

```python
from autoroute import route

r = route("Écris une fonction Python qui parse un CSV")
print(r.model, r.reason)
```

Pour l'utiliser avec LangChain (modèle de chat, agent, replis), voir [Utilisation avec LangChain](#utilisation-avec-langchain).

## Configuration

### `.env` : tes clés API

Une clé par fournisseur. Laisse une clé vide si tu n'utilises pas ce fournisseur : il est ignoré.

```
OPENAI_API_KEY=       # https://platform.openai.com/api-keys
ANTHROPIC_API_KEY=    # https://platform.claude.com (Claude Console)
GOOGLE_API_KEY=       # https://aistudio.google.com (Gemini)
MISTRAL_API_KEY=      # https://console.mistral.ai (Mistral AI Studio)
```

Les variables d'environnement système sont prioritaires sur `.env`. `autoroute models` affiche la source de chaque clé (`system`, `.env`, `system (.env ignored)` ou `missing`), jamais sa valeur.

**Tes clés restent chez toi.** Autoroute n'a pas de serveur : nous n'avons jamais accès à tes clés ni à tes prompts. Tes clés servent uniquement à appeler directement l'API de chaque fournisseur (liste des modèles, orchestrateur). Elles ne sont jamais écrites dans le log. Ton prompt n'est envoyé qu'aux fournisseurs des orchestrateurs : le principal, puis un orchestrateur de secours s'il ne répond pas. Ils appartiennent toujours au mode actif (en mode `sovereign` : Mistral uniquement).

### Modes

`AUTOROUTE_MODE` choisit vers quels fournisseurs Autoroute peut router. Pour changer de mode, ajoute par exemple `AUTOROUTE_MODE=sovereign` dans `.env` (ou dans une variable d'environnement).

| Mode | Fournisseurs | Clé nécessaire |
|---|---|---|
| `all` (défaut) | OpenAI, Anthropic, Gemini, Mistral | au moins une |
| `openai` | OpenAI | `OPENAI_API_KEY` |
| `anthropic` | Anthropic | `ANTHROPIC_API_KEY` |
| `gemini` | Gemini | `GOOGLE_API_KEY` |
| `sovereign` | Mistral uniquement (fournisseur français 🇫🇷 🇪🇺), avec tous les modèles accessibles par ta clé | `MISTRAL_API_KEY` |

Le mode s'applique aussi à l'orchestrateur : il appartient toujours à un fournisseur du mode actif.

### L'orchestrateur : choisi automatiquement

L'orchestrateur est le modèle qui fait le routage. Tu n'as pas à le choisir, et aucun nom de modèle n'est figé dans le code : Autoroute le prend dans la liste des modèles découverts.

- Il cherche un modèle léger, pour que le routage reste rapide et peu coûteux, reconnu à sa famille (`haiku`, `mini`, `flash`, `small`), et prend le plus récent. Quand un fournisseur sort une nouvelle version, elle est prise sans mise à jour d'Autoroute.
- Il garde jusqu'à 2 orchestrateurs de secours, si possible chez d'autres fournisseurs, essayés si le premier ne répond pas.
- Seuls les fournisseurs du mode sont utilisés : en mode `sovereign`, ton prompt n'est jamais envoyé à un modèle autre que Mistral.

`autoroute models` affiche l'orchestrateur retenu et ceux de secours.

**Le routage ne fait pas planter ton application.** Si aucun orchestrateur ne répond (panne, quota, plus de 20 s), Autoroute renvoie quand même un modèle découvert (autre que les orchestrateurs en échec, quand c'est possible), avec `r.routed == False`, et note l'erreur dans le log. Il ne lève une exception que s'il n'a aucun modèle à proposer (aucune clé valide). Un orchestrateur qui cite un modèle inexistant compte comme un échec : l'orchestrateur suivant prend le relais. Chaque échec d'orchestrateur, même rattrapé par un orchestrateur de secours, est signalé par un message `WARNING` du logger standard `autoroute` (module `logging`) : affiché par défaut sur la sortie d'erreur, il rejoint la supervision de ton application si tu configures `logging`.

### `.autoroute/config.yaml` (facultatif) : choisir ton orchestrateur

Sans ce fichier, tout fonctionne. Il sert seulement à remplacer le choix automatique, mode par mode :

```yaml
orchestrators:
  all: anthropic:claude-haiku-5-5
```

L'orchestrateur choisi passe en premier, les orchestrateurs automatiques restent en secours. Il doit appartenir à un fournisseur du mode. S'il est inaccessible (autre fournisseur, modèle retiré, clé absente) ou si le fichier est illisible, Autoroute affiche un avertissement et utilise le choix automatique. `autoroute init -o fournisseur:nom` l'écrit pour le mode actif.

Le choix de l'orchestrateur compte : il ne voit que les **noms** des modèles, donc son choix dépend de ce qu'il sait d'eux. Un orchestrateur récent connaît mieux les modèles récents.

### Le dossier `.autoroute/`

Autoroute range ses fichiers dans `.autoroute/`, à la racine de ton projet (le dossier depuis lequel tu lances ton application) :

```
mon-projet/
├── .env                 # tes clés API
└── .autoroute/
    ├── config.yaml      # facultatif : peut être versionné
    ├── log.jsonl        # journal des décisions
    └── .gitignore       # créé automatiquement : exclut *.jsonl
```

Le log contient le texte de tes tâches : le `.gitignore` créé par Autoroute l'empêche de partir dans git.

### Variables d'environnement

Elles peuvent aussi être écrites dans `.env`.

| Variable | Rôle | Défaut |
|---|---|---|
| `AUTOROUTE_MODE` | Mode de routage | `all` |
| `AUTOROUTE_CONFIG` | Chemin du fichier de config | `.autoroute/config.yaml` |
| `AUTOROUTE_LOG` | Chemin du fichier de log, ou `stdout` / `off` (voir [Observabilité](#observabilité)) | `.autoroute/log.jsonl` |

### Choisir quand découvrir les modèles

Par défaut, `route(prompt)` découvre les modèles au premier appel et garde la liste 5 minutes (voir [Comment ça marche](#comment-ça-marche)). Pour décider toi-même quand découvrir, appelle `discover_config()` et passe le résultat à `route()` :

```python
from autoroute import discover_config, route

config = discover_config()        # interroge les fournisseurs maintenant (mode : AUTOROUTE_MODE)
r = route(prompt, config=config)  # aucune découverte : la liste de config est utilisée telle quelle
```

- `discover_config()` interroge les fournisseurs à chaque appel, sans cache. `discover_config(mode="sovereign")` impose un mode. Il lève une `ValueError` s'il ne trouve aucun modèle (aucune clé valide).
- Avec `config=`, la liste ne se met jamais à jour : un nouveau modèle, un modèle retiré ou une nouvelle clé ne sont pris en compte qu'au prochain `discover_config()`. Si ton application tourne longtemps, rappelle-le régulièrement.
- Les orchestrateurs de secours, le dernier recours et le log fonctionnent comme sans `config`.

Utile pour découvrir les modèles une fois au démarrage de ton application (le premier `route()` ne paie pas la découverte), ou pour garder la même liste pendant un traitement par lots.

## Utilisation avec LangChain

```python
from autoroute import route

r = route("Écris une fonction Python qui parse un CSV")
r.model      # "anthropic:claude-sonnet-5-5"
r.fallbacks  # ["openai:gpt-5.5", "google_genai:gemini-3.8-pro"]  (0 à 2, sans r.model)
r.provider   # "anthropic" : fournisseur de r.model (openai, anthropic, google, mistral)
r.providers  # ["anthropic", "openai", "google"] : fournisseurs de [r.model, *r.fallbacks], dans l'ordre
r.reason     # pourquoi ce modèle, dans la langue de la tâche (en anglais si r.routed == False)
r.routed     # True : choisi pour ta tâche. False : modèle par défaut (aucun orchestrateur n'a répondu)
r.id         # identifiant de la décision dans le log
```

### Modèle de chat, avec replis

Pour une question et une réponse : le modèle reçoit ton prompt et répond en une fois.
Si le modèle choisi échoue (rate limit, panne, pas d'accès…), LangChain passe au repli suivant.

```python
from langchain.chat_models import init_chat_model
from autoroute import route

prompt = "Écris une fonction Python qui parse un CSV"
r = route(prompt)

model = init_chat_model(r.model).with_fallbacks([init_chat_model(m) for m in r.fallbacks])
print(model.invoke(prompt).content)
```

### Agent, avec replis

Pour une tâche en plusieurs étapes : l'agent utilise des outils (fichiers, web, API…) jusqu'à pouvoir répondre. Si le modèle échoue en cours de route, l'agent passe au repli suivant.

```python
from langchain.agents import create_agent
from langchain.agents.middleware import ModelFallbackMiddleware
from autoroute import route

prompt = "Analyse ce fichier de logs et résume les erreurs"
r = route(prompt)

agent = create_agent(
    model=r.model,
    tools=[...],
    middleware=[ModelFallbackMiddleware(*r.fallbacks)] if r.fallbacks else [],
)
agent.invoke({"messages": [{"role": "user", "content": prompt}]})
```

Tout outil de l'écosystème LangChain qui accepte un modèle de chat (LangGraph, Deep Agents…) fonctionne de la même façon : passe-lui `init_chat_model(r.model)`.

### Exemples

Voir [`examples/`](examples) :

| Fichier | Montre |
|---|---|
| `01_chat_model.py` | Modèle de chat avec replis |
| `02_agent.py` | Agent avec `ModelFallbackMiddleware` |
| `03_task_levels.py` | Routage de tâches simples et complexes |
| `04_fallback.py` | Un vrai repli, avec un modèle principal forcé en échec |

### Autres frameworks : `to="native"`

Le paramètre `to` choisit le format de `r.model` et `r.fallbacks` :

| `to=` | `r.model` | Pour |
|---|---|---|
| `"langchain"` (défaut) | `"google_genai:gemini-3.8-flash"` | LangChain, LangGraph, Deep Agents |
| `"native"` | `"gemini-3.8-flash"` | SDK officiels (OpenAI, Anthropic, Google, Mistral), LlamaIndex, Haystack |

Avec `"native"`, `r.model` est le nom officiel du modèle, et `r.provider` dit quel SDK ou quelle classe utiliser (`r.providers` fait de même pour chaque repli) :

```python
r = route(prompt, to="native")   # ex. r.model = "claude-haiku-5-5", r.provider = "anthropic"

# SDK officiel
import anthropic
anthropic.Anthropic().messages.create(model=r.model, max_tokens=1024, messages=[{"role": "user", "content": prompt}])

# LlamaIndex
from llama_index.llms.anthropic import Anthropic
llm = Anthropic(model=r.model)

# Haystack
from haystack_integrations.components.generators.anthropic import AnthropicChatGenerator
generator = AnthropicChatGenerator(model=r.model)
```

Pour gérer tous les fournisseurs, associe chacun à sa classe, par exemple `{"anthropic": Anthropic, "openai": OpenAI, ...}[r.provider](model=r.model)`. Le SDK, la classe et les replis restent à ta charge : Autoroute n'appelle jamais le modèle. Une valeur de `to` inconnue lève une `ValueError`.

D'autres formats (LiteLLM, Pydantic AI) sont prévus.

## Observabilité

Le log te permet de comprendre chaque choix. Il reste sur ta machine.

Tout est ajouté à `.autoroute/log.jsonl`, un objet JSON par ligne. Le champ `schema_version` change si le format du log évolue : utile si tu le lis avec un script.

Si le log ne peut pas être écrit (dossier en lecture seule, par exemple dans un conteneur), `route()` fonctionne quand même : la décision n'est pas enregistrée et un `WARNING` du logger `autoroute` le signale une fois. Choisis alors un dossier accessible en écriture avec `AUTOROUTE_LOG`.

En production, `AUTOROUTE_LOG` accepte aussi deux valeurs :

| `AUTOROUTE_LOG=` | Effet |
|---|---|
| `stdout` | Une ligne JSON par décision sur la sortie standard : ta plateforme (Docker, Kubernetes, Cloud Run…) la récupère avec tes autres logs. Ces lignes contiennent tes prompts. |
| `off` | Aucune décision enregistrée. |

Dans ces deux cas, `autoroute ui` a besoin d'un fichier : `autoroute ui --log fichier.jsonl`.

Sous Windows PowerShell 5.1, lis-le avec `Get-Content .autoroute/log.jsonl -Encoding UTF8`, sinon les accents s'affichent mal.

### `autoroute ui` : le log dans ton navigateur

```powershell
autoroute ui                     # ouvre http://127.0.0.1:8765
autoroute ui --log prod.jsonl    # un autre fichier, par exemple un log copié depuis ta production
autoroute ui --port 9000         # un autre port
```

La page affiche un résumé (taux de routages non routés et de choix invalides, latence moyenne, modèles et orchestrateurs les plus choisis), le tableau des 1 000 dernières décisions avec recherche et filtres, et le détail de chaque décision (tâche, raison, candidats, erreurs). Le bouton « Refresh » relit le log sans relancer la commande. `Ctrl+C` arrête le serveur.

Le serveur n'écoute que sur ta machine (`127.0.0.1`), en lecture seule, et la page ne charge aucune ressource externe. Ne l'expose jamais sur un réseau : le log contient tes prompts. Pour un serveur distant, récupère le fichier et lance `autoroute ui --log` en local, ou passe par un tunnel SSH (`ssh -L 8765:127.0.0.1:8765 serveur`).

### `decision` (choix) : écrite à chaque routage

```json
{"schema_version": "3", "timestamp": "2026-11-07T12:48:25+00:00", "event": "decision", "decision_id": "ae3a775c…", "task": "Écris une fonction Python qui parse un CSV", "orchestrator": "anthropic:claude-haiku-5-5", "model": "openai:gpt-5-mini", "candidates": ["openai:gpt-5-mini", "google_genai:gemini-3.8-flash", "anthropic:claude-haiku-5-5"], "reason": "...", "invalid_choice": false, "routing_failed": false, "latency_ms": 2965, "mode": "all", "error": null}
```

| Champ | Signification |
|---|---|
| `decision_id` | Identifiant de la décision (`r.id`) |
| `task` | Ton prompt |
| `orchestrator` | Le modèle qui a fait le choix |
| `model` | Modèle choisi |
| `candidates` | Le modèle choisi, puis jusqu'à 2 replis, dans l'ordre |
| `reason` | Pourquoi l'orchestrateur l'a choisi |
| `invalid_choice` | `true` si un orchestrateur a cité un modèle inexistant : l'orchestrateur suivant a pris le relais |
| `routing_failed` | `true` si aucun orchestrateur n'a répondu (`r.routed == False`) : le modèle renvoyé n'a pas été choisi pour ta tâche |
| `latency_ms` | Durée du routage |
| `mode` | Mode utilisé |
| `error` | Erreurs des orchestrateurs qui n'ont pas répondu ou ont cité un modèle inexistant (y compris si un orchestrateur de secours a pris le relais) |

> **Le log contient tes prompts.** Il est ignoré par git. Ne le partage pas si tes prompts sont sensibles.

## Limites connues

- L'orchestrateur est reconnu à son nom de famille (`haiku`, `mini`, `flash`, `small`) : si un fournisseur change ses noms, Autoroute prend un autre modèle découvert, peut-être moins adapté au routage.
- L'orchestrateur ne voit que les **noms** des modèles, pas leurs capacités ni leurs prix : il peut mal juger un modèle qu'il ne connaît pas.
- Le routage n'est pas déterministe : le même prompt peut recevoir des choix différents, mais raisonnables.
- Le routage ajoute un appel payant et quelques secondes (environ 1 à 7 s selon l'orchestrateur, dans nos essais).
- Chaque orchestrateur a 20 s pour répondre, avec une relance au plus : au-delà, l'orchestrateur de secours prend le relais. Dans le pire cas (les 3 orchestrateurs trop lents), le routage peut donc prendre environ 2 minutes avant le repli sur un modèle découvert.
- Les modèles découverts sont gardés en mémoire 5 minutes : un nouveau modèle d'un fournisseur peut mettre jusqu'à 5 minutes à apparaître. Un changement de clé API, de `AUTOROUTE_MODE` ou de `.autoroute/config.yaml` est pris en compte tout de suite (avec `config=discover_config()`, seulement au prochain `discover_config()`). Un fournisseur qui ne répond pas pendant la découverte est écarté jusqu'à la suivante, soit 5 minutes au plus.
- Un fournisseur peut lister un modèle que ta clé ne peut pas appeler : s'il est choisi, les replis prennent le relais.
- Un seul choix par prompt : pas de re-routage à chaque étape d'un agent.
- Aucune vérification des capacités du modèle choisi (appel d'outils, vision).
- Pas encore de calcul de coût ni de mesure du gain.
- La CLI, les avertissements et la page de `autoroute ui` sont en anglais. La raison de l'orchestrateur suit la langue de ton prompt, sauf quand aucun orchestrateur n'a répondu (`r.routed == False`) : aucun modèle ne peut alors la rédiger, et elle est fixe, en anglais (« No orchestrator answered: falling back to a discovered model. »).
- LangChain est l'intégration de référence : les exemples et les replis automatiques sont écrits pour lui. Avec `to="native"`, le choix du SDK et les replis sont à ta charge. LangChain reste installé avec Autoroute, qui l'utilise pour interroger l'orchestrateur.

## Sécurité

Pour signaler une faille de sécurité, n'ouvre pas d'issue publique : utilise le [signalement privé de GitHub](https://github.com/SethLawson07/autoroute/security/advisories/new). Voir [SECURITY.md](SECURITY.md).

## Licence

[MIT](LICENSE) © 2026 Laté Seth Lawson-Hetchely · [sethlawson.fr](https://sethlawson.fr/)
