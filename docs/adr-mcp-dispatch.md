# ADR — Despachantes MCP para RELAY / REVIEW / HALL (ACT fica individual)

ADR id: ADR-20260913133108-1fac9c
Status: proposed
Supersede: ADR-20260913122812-c91fc8 (aquela tinha ACT no escopo por engano)

## 0. Por que uma nova ADR e nao um update

O `conscio_decide` no modo update (conscio/gates.py:93-104) preserva o context
e alternatives ORIGINAIS; so status e deciders mudam. O contexto da ADR antiga
dizia "4 conjuntos lifecycle (ACT/REVIEW/RELAY/HALL)" como decisao. Nao havia
como corrigir o escopo dentro da mesma ADR — esta ADR carrega o escopo final e
marca a anterior como superseded.

## 1. Escopo final (decisao do owner + peer review)

Despachante (gate com enum de op) SOMENTE em:

    RELAY   (5)  send, inbox, read, broadcast, peers
    REVIEW  (4)  reviews, approve, reject, poll
    HALL    (7)  create, list, join, leave, members, send, manage

ACT FICA INDIVIDUAL (5 tools separadas).

## 2. Por que ACT saiu (objecao que derrubou)

O host autoriza por NOME de ferramenta; `op` e argumento, nao identidade.
Fundir act/approve/reject/pending em `conscio_act(op=...)` torna a autorizacao
tudo-ou-nada exatamente no grupo sensivel:

    hoje:  permitir conscio_pending (leitura) e negar conscio_approve (escrita)
    fundido:  conscio_act(op=approve) nao pode ser negado sem negar o op=pending

ACT tambem e a menor economia dos quatro (1190B -> 527B). O custo de perder a
granularidade de autorizacao nao justifica ~663B salvos.

Note: meu trade-off original (tipagem forte) nomeava o custo de VALIDACAO.
O de AUTORIZACAO e o que decide, e eu nao o tinha calculado.

## 3. Numeros verificados (json.dumps, derivado do codigo)

SENSIBILIDADE A SEPARADOR: o valor absoluto em bytes depende dos separadores
do json.dumps — default (', ', ': ') vs compacto (',', ':'). Medicao do peer
(claude-opus-5, separador compacto): lite 3075B, balanced 6178B (vs 3229B /
6502B default aqui). Os DELTAS e a ordem das desigualdades (a monotonicidade)
sao invariantes ao separador — as duas medicoes concordam em tudo que decide:
inversao lite+relay+hall > balanced puro e verdadeira nos dois separadores
(6343 > 6178 compacto; 6697 > 6502 default), e despachante restaura abaixo
(4814 compacto; 4968 default). Nesta ADR: separadores default.

Surface maxima = 58 tools (37 base+mode + 21 flags: act 5, review 4, relay 5,
hall 7).

Modos puros (sem flags):

    lite      3229B
    balanced  6502B
    high      7561B
    ultra    13379B

### 3.1 Monotonicidade de bytes (o argumento que faltava)

INVERSAO hoje na escada lite (sem despachante):

    lite + relay + hall individual = 6697B  >  balanced puro 6502B

Com os 3 despachantes (1739B total):

    lite puro + 3 despachantes      = 4968B  <  6502B
    lite + 3 despachantes + ACT     = 6158B  <  6502B  (folga +344B)

O despachante restaura a monotonicidade na escada lite, que e onde o mode
system mais trabalha (modelo pequeno afoga em tools demais).

### 3.2 LIMITE do argumento (nao forcar para alem do real)

Em balanced->high a monotonicidade de bytes NAO se sustenta nem com despachante:

    balanced puro + 3 despachantes  = 8241B  >  high puro 7561B  (folga -680B)
    hoje ja estava invertido        = folga -3337B

A invariante real de modes.py e NESTING DE CONJUNTOS, nao bytes:

    lite ⊂ balanced ⊂ high ⊂ ultra
    subir o modo nunca remove uma tool que o host ja usava

O nesting e intocado pelo despachante. O teste de CI deve afirmar nesting de
conjuntos, NAO monotonicidade de bytes (que ja e negativa em bal->high por
design quando os grupos estao ligados).

### 3.3 Totais de economia

    flagged subset    21 tools  5586B ->  2929B  (-47,6%)
    surface anunciada          18965B -> 16308B  (-14,0%)

Mediana das tools BASE: 339,5B (36 tools, dict puro, separadores default;
valores do meio 329/350). O peer mediu 341,5B / min 138 / max 728: +2B
sistematico em TODO valor, inclusive min e max — artefato do colchete de
lista (json.dumps([d]) = +2B/tool), nao erro de julgamento. Deltas e ordem
identicos; a conclusao fica igual (e mais forte): uma tool nova no bloco
flagged quebra a folga.

## 3.2b Redistribuicao v4.6 (fato que faltava — muda o veredito de bal->high)

O v4.6 move 6 tools do ultra para o high (aceptance_criteria 387B,
delivery_check 227B, investigate 301B, evaluate 490B, eval_harness 544B,
rules_distill 453B = +2402B medidos). NAO estava na ADR original — agora esta:

    high HOJE            = 7561B
    high REDISTRIBUIDO   = 9963B  (+2402B)

Folga bal->high nos DOIS baselines (default separators):

                                 vs high HOJE   vs high REDISTRIBUIDO
    bal+3desp (ACT fora)   8241      -680             +1722
    bal+TUDO (ACT int.)    9441     -1880              +522

Com a redistribuicao, a monotonicidade de bytes se restaura NO DEGRAU
bal->high tambem (folga positiva mesmo com ACT inteiro). Sem ela, permanece
invertida. LOGO: o argumento de bytes e CONDICIONADO a redistribuicao ter
entrado — ver teste condicional abaixo.

## 3.2c Custo MARGINAL de adicao (correcao do peer #444 — derruba conclusao anterior)

O peer tinha afirmado (2x, inclusive aqui) que "uma unica tool nova no bloco
flagged quebra a invariante". Errado — e a conclusao correta e MAIS alarmante:

O custo de acrescentar uma tool a uma lista que ja existe e o dict MAIS UMA
VIRGULA (os colchetes ja estao la), nao o dict entre colchetes. Medido contra
a folga mais apertada (lite+tudo com ACT inteiro -> balanced puro, +344B
default):

    custo marginal mediano       339,5B (dict + virgula, soma direta)
    uma tool mediana             passa com 4,5B de sobra (ruido, nao margem)
    duas tools medianas           -335B  -> quebra
    tools que estouram sozinhas  18/36 (50%)

Nota de cross-check (peer, separador compacto + colchete na medicao): folga
362B, custo mediano 351B, sobra 11B, 16/36 = 44%. Os dois concordam no que
decide: a mediana passa por ruido (4,5B ou 11B), a segunda adicao quebra, e
metade das tools estoura sozinha.

CONCLUSAO REGISTRADA: a folga nao aguenta DUAS adicoes medianas, e a adicao
de UMA ja e aposta contra ruido. Reforca o §6b: o teste de bytes de CI e
componente ativo do design, nao rede de seguranca.

## 4. Desenho (schema + alias fallback) — inalterado, so 3 grupos agora

### 4.1 Schema do despachante (ex.: conscio_relay)

    {
      "name": "conscio_relay",
      "description": "Relay dispatch. op=send|inbox|read|broadcast|peers. "
                    "send(to,type,payload), inbox(limit), read(ids), "
                    "broadcast(type,payload), peers()",
      "inputSchema": {
        "type": "object",
        "properties": {
          "op": {"type": "string",
                 "enum": ["send", "inbox", "read", "broadcast", "peers"]},
          "to":      {"type": "string"},
          "type":    {"type": "string"},
          "payload": {"type": "object"},
          "ids":     {"type": "array", "items": {"type": "integer"}},
          "limit":   {"type": "integer"}
        },
        "required": ["op"]
      }
    }

### 4.2 Dispatch (server.py, _tools)

    def _relay_dispatch(self, args):
        op = self._require(args, "op")
        handler = {
            "send":      lambda: self._relay_send(args),
            "inbox":     lambda: self._relay_inbox(args),
            "read":      lambda: self._relay_read(args),
            "broadcast": lambda: self._relay_broadcast(args),
            "peers":     lambda: self._relay_peers_tool(args),
        }.get(op)
        if handler is None:
            raise j.InvalidParams(
                f"unknown op {op!r}; expected one of "
                f"send|inbox|read|broadcast|peers")
        return handler()

A validacao de obrigatorio por-op reusa _require (server.py:239-243), ja usado
pelos lambdas individuais hoje — custo zero.

### 4.3 Alias fallback (retrocompat)

    tools = {
        "conscio_relay": self._relay_dispatch,
        # aliases — convivem no dispatch; NAO aparecem no tools/list
        "conscio_relay_send":      lambda a: self._relay_send(a),
        "conscio_relay_inbox":     lambda a: self._relay_inbox(a),
        "conscio_relay_read":      lambda a: self._relay_read(a),
        "conscio_relay_broadcast": lambda a: self._relay_broadcast(a),
        "conscio_relay_peers":     lambda a: self._relay_peers_tool(a),
    }

Padrao ja praticado no projeto: _legacy_name (server.py:53-64) e exatamente
isso — o docstring prova que a v4.1 sobreviveu ao rename ponto->underscore
com alias-que-despacha-sem-ser-anunciado.

## 5. Camadas tocadas

    schemas.py   RELAY/LIAISON/HALL_TOOL_DEFS (5+4+7 dicts) -> 1 dispatch def cada
                 ACT_TOOL_DEFS intocado
    server.py    .tool_defs(): flagged += [dispatch def] (3x)
                 ._tools():    registra despachante + aliases
    modes.py     intocado (relay/act/hall nao fazem parte de mode set)

## 6. Armadilha de implementacao (para quem mexer)

folga(rung) = proximo_modo_puro - modo_atual_tudo.

    normalizar uma tool BASE presente nos DOIS rungs -> folga inalterada
      (conjuntos aninhados)
    normalizar tool exclusiva do modo SUPERIOR -> ENCOLHE a folga do rung inferior
    normalizar grupo flagged -> CRESCE a folga (flagged so aparece no lado esquerdo)

Logo: qualquer normalizacao de schema para baratear o balanced tem que ser
aplicada TAMBEM aos grupos flagged, senao o ganho de custo sai pago com a
honestidade do rotulo de folga.

## 6b. Testes de CI (dois, com escopo declarado — proposta do peer #439)

    1. NESTING (sempre ativo): lite ⊂ balanced ⊂ high ⊂ ultra.
       Verdadeiro POR CONSTRUCAO (BALANCED = LITE | {...}), entao so pega
       alguem reescrevendo os sets como literais. Barato; escopo: regressao
       do nesting, nao da superficie.

    2. BYTES (CONDICIONAL a redistribuicao v4.6 ter entrado): afirma folga
       positiva (modo + flags < proximo modo puro) usando o baseline do high
       REDISTRIBUIDO (9963B). Se a redistribuicao (ou o E3) for cortada do
       v4.6, este teste NAO pode ser ativado — afirmaria coisa falsa contra
       o high de HOJE (7561B), o oposto do que se esta construindo.
       Gate: pular o teste com skip condicional checando se as 6 tools
       movidas estao no set do high (proxy barato e direto).

## 7. Decisao

Despachante em RELAY, REVIEW e HALL. ACT individual por autorizacao por nome.
BASE no mode system (sem despachante, semantica heterogenea).
Alias via _legacy_name para nao quebrar host scriptado.