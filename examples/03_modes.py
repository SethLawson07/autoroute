from autoroute.router import route_with_reason

TASKS = [
    "Dis bonjour en trois langues",
    "Démontre que racine de 2 est irrationnelle, étape par étape",
    "Refactorise ce module Python de 800 lignes en architecture hexagonale",
]

for task in TASKS:
    r = route_with_reason(task)
    print(f"{r.model}  (invalid_choice={r.invalid_choice})\n  {r.reason}\n")