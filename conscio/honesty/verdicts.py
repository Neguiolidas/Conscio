"""UNICA definicao dos valores de desfecho. Zero imports por invariante.

Reexportado por ``conscio.agency.outcome``; nunca redefinido la. Duas listas
iguais divergem no primeiro valor que alguem acrescenta so de um lado.
"""

OUT_OF_SCOPE = ""
PENDING = "PENDING"
VERIFIED = "VERIFIED"
CONTRADICTED = "CONTRADICTED"
UNSUPPORTED = "UNSUPPORTED"

TERMINAL = frozenset({VERIFIED, CONTRADICTED, UNSUPPORTED})

RETENTION_DAYS = 30  # espelha obsstore.prune(max_age_days=30)
