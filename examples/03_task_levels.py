from autoroute import route

TASKS = [
    "Dis bonjour en trois langues",
    "Démontre que racine de 2 est irrationnelle, étape par étape",
    "Refactorise ce module Python de 800 lignes en architecture hexagonale",
]

for task in TASKS:
    r = route(task)
    print(f"{r.model}  (routed={r.routed})\n  {r.reason}\n")
