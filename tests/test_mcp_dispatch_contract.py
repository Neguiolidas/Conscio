"""O despachante e os defs individuais nao podem divergir.

O E3 trocou N tools anunciadas por 1 despachante com ``op`` como argumento. A
lista de ops passou a viver escrita a mao no enum do despachante, enquanto os
defs individuais continuam sendo a documentacao de cada operacao -- e nada
ligava os dois. Acrescentar uma operacao a um lado e esquecer o outro nao
quebrava nada: o usuario veria uma op anunciada sem descricao, ou uma descricao
de op que o enum recusa.

Este teste e o elo. Ele tambem e o motivo de HALL_TOOL_DEFS/RELAY_TOOL_DEFS
continuarem existindo: sem ele seriam codigo morto, e a resposta certa seria
apaga-los em vez de whitelista-los.
"""
import pytest

from conscio.mcp import schemas


def _ops(dispatch_def) -> set[str]:
    return set(dispatch_def["inputSchema"]["properties"]["op"]["enum"])


def _suffixes(defs, prefix) -> set[str]:
    return {d["name"][len(prefix):] for d in defs if d["name"].startswith(prefix)}


@pytest.mark.parametrize("dispatch_name,defs_name,prefix", [
    ("_HALL_DISPATCH_DEF", "HALL_TOOL_DEFS", "conscio_hall_"),
    ("_RELAY_DISPATCH_DEF", "RELAY_TOOL_DEFS", "conscio_relay_"),
])
def test_every_dispatch_op_has_an_individual_def(dispatch_name, defs_name,
                                                 prefix):
    dispatch = getattr(schemas, dispatch_name)
    defs = getattr(schemas, defs_name)
    ops, names = _ops(dispatch), _suffixes(defs, prefix)
    assert ops == names, (
        f"{dispatch_name} e {defs_name} divergiram: "
        f"so no enum={sorted(ops - names)}, so nos defs={sorted(names - ops)}")


@pytest.mark.parametrize("dispatch_name", ["_HALL_DISPATCH_DEF",
                                           "_RELAY_DISPATCH_DEF"])
def test_every_op_is_described_in_the_dispatch_description(dispatch_name):
    """O host so ve a descricao do despachante: uma op ausente dali e uma op
    que o modelo nao sabe que existe."""
    dispatch = getattr(schemas, dispatch_name)
    description = dispatch["description"]
    missing = [op for op in _ops(dispatch) if op not in description]
    assert not missing, f"{dispatch_name} nao descreve as ops: {missing}"
