# Contributing / Contribuer

🇬🇧 [English](#english) · 🇫🇷 [Français](#français)

## English

Contributions are welcome. For a significant change, open an issue first to discuss it.

### Setup

```bash
git clone https://github.com/SethLawson07/autoroute
cd autoroute
python -m venv .venv
.venv\Scripts\Activate.ps1      # macOS / Linux: source .venv/bin/activate
pip install -e ".[dev]"
pytest                          # fast tests, no API calls
```

### Rules

- One PR per topic, from a short-lived branch (`feature/…`, `fix/…`, `docs/…`) to `main`.
- Every new behavior is covered by a `pytest` test, with no API calls (mocked calls).
- `README.md` (French) and `README.en.md` (English) are updated together.
- Never write an API key in the code, tests or examples.

Tests run automatically on every PR. Tests against the real APIs (`pytest -m live`) are optional for you: the maintainer runs them before merging.

### Adding a provider

1. A model-listing function in `src/autoroute/catalog.py`.
2. Its key in `src/autoroute/templates/env.example`.
3. Its LangChain integration in the `dependencies` of `pyproject.toml` (minimum version `>=`, no upper bound).
4. Tests, including a live test in `tests/live/`.

### Security

Do not report a vulnerability in a public issue: see [SECURITY.md](SECURITY.md).

## Français

Les contributions sont bienvenues. Pour une modification importante, ouvre d'abord une issue pour en discuter.

### Mise en place

```bash
git clone https://github.com/SethLawson07/autoroute
cd autoroute
python -m venv .venv
.venv\Scripts\Activate.ps1      # macOS / Linux : source .venv/bin/activate
pip install -e ".[dev]"
pytest                          # tests rapides, sans appel aux API
```

### Règles

- Une PR par sujet, depuis une branche courte (`feature/…`, `fix/…`, `docs/…`) vers `main`.
- Tout nouveau comportement est couvert par un test `pytest`, sans appel aux API (appels simulés).
- `README.md` (français) et `README.en.md` (anglais) sont mis à jour ensemble.
- Ne jamais écrire de clé API dans le code, les tests ou les exemples.

Les tests sont lancés automatiquement sur chaque PR. Les tests sur les vraies API (`pytest -m live`) sont facultatifs pour toi : le mainteneur les lance avant la fusion.

### Ajouter un fournisseur

1. Une fonction de listing des modèles dans `src/autoroute/catalog.py`.
2. Sa clé dans `src/autoroute/templates/env.example`.
3. Son intégration LangChain dans les `dependencies` de `pyproject.toml` (version minimale `>=`, sans borne haute).
4. Des tests, dont un test live dans `tests/live/`.

### Sécurité

Ne signale pas une faille dans une issue publique : voir [SECURITY.md](SECURITY.md).
