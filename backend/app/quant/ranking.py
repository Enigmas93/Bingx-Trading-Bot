"""Ranking de oportunidades e decisão (com motivo registrado para cada uma).

Ordem: maior final_score primeiro (desempate: confluência, depois R esperado). Cada oportunidade recebe uma lista de
`reasons` — tanto as aprovações quanto o motivo exato de recusa — para a interface explicar a decisão.
"""

from collections import Counter

from .confluence import LEVEL_RANK


def rank_and_decide(opps: list[dict], ctx: dict) -> list[dict]:
    """
    opps: [{symbol, direction, final_score, confluence_level, primary_strategy, reward_r, ...}]
    ctx:  min_final_score, min_confluence_level, single_signal_min_score, conflict_margin, max_positions,
          max_same_direction, max_open_per_strategy, max_correlation, open_symbols (set), open_count,
          open_dirs (Counter), open_per_strategy (Counter), risk_paused (str|None), correlation(sym_a, sym_b)->float|None,
          open_list [(symbol, direction)], engine_ready (bool), engine_reason (str)
    Retorna a mesma lista, ordenada, com rank, status (SELECTED|SKIPPED|CONFLICT) e reasons.
    """
    opps = sorted(opps, key=lambda o: (o["final_score"], LEVEL_RANK[o["confluence_level"]], o.get("reward_r", 0)),
                  reverse=True)
    for i, o in enumerate(opps, 1):
        o["rank"] = i
        o["reasons"] = list(o.get("pre_reasons") or [])   # bloqueios já conhecidos (custo, spread, funding)
        o["status"] = "SKIPPED" if o.get("pre_reasons") else None

    # conflito LONG x SHORT no mesmo par
    by_sym: dict[str, list[dict]] = {}
    for o in opps:
        by_sym.setdefault(o["symbol"], []).append(o)
    for sym, lst in by_sym.items():
        if len(lst) < 2:
            continue
        a, b = sorted(lst, key=lambda o: o["final_score"], reverse=True)[:2]
        gap = a["final_score"] - b["final_score"]
        if gap < ctx["conflict_margin"]:
            for o in (a, b):
                o["status"] = "CONFLICT"
                o["reasons"].append(f"LONG e SHORT simultâneos em {sym} com diferença de {gap:.1f} pts "
                                    f"(< {ctx['conflict_margin']:.0f}): sinais contraditórios, nenhum é executado")
        else:
            b["status"] = "CONFLICT"
            b["reasons"].append(f"direção oposta ({a['direction']}) tem score {gap:.1f} pts maior")

    open_count = ctx["open_count"]
    open_dirs = Counter(ctx["open_dirs"])
    per_strat = Counter(ctx["open_per_strategy"])
    chosen: list[tuple[str, str]] = list(ctx.get("open_list", []))
    min_lvl = LEVEL_RANK[ctx["min_confluence_level"]]

    for o in opps:
        if o["status"]:
            continue
        r = o["reasons"]
        lvl = LEVEL_RANK[o["confluence_level"]]
        if not ctx.get("engine_ready", True):
            o["status"] = "SKIPPED"
            r.append(f"motor travado: {ctx.get('engine_reason')}")
            continue
        if o["final_score"] < ctx["min_final_score"]:
            o["status"] = "SKIPPED"
            r.append(f"score final {o['final_score']:.1f} < mínimo {ctx['min_final_score']:.0f}")
            continue
        if lvl < min_lvl:
            if o["confluence_level"] == "SINGLE_SIGNAL" and o["final_score"] >= ctx["single_signal_min_score"]:
                r.append(f"sinal único aceito por score excepcional ({o['final_score']:.1f} ≥ {ctx['single_signal_min_score']:.0f})")
            else:
                o["status"] = "SKIPPED"
                r.append(f"confluência {o['confluence_level']} abaixo do mínimo {ctx['min_confluence_level']}"
                         f" (sinal único precisa de score ≥ {ctx['single_signal_min_score']:.0f})")
                continue
        if ctx.get("risk_paused"):
            o["status"] = "SKIPPED"
            r.append(ctx["risk_paused"] if ctx["risk_paused"].startswith("horário") else f"risco: {ctx['risk_paused']}")
            continue
        if o["symbol"] in ctx["open_symbols"]:
            o["status"] = "SKIPPED"
            r.append("já existe posição aberta neste par")
            continue
        if open_count >= ctx["max_positions"]:
            o["status"] = "SKIPPED"
            r.append(f"limite de posições simultâneas atingido ({ctx['max_positions']})")
            continue
        if open_dirs[o["direction"]] >= ctx["max_same_direction"]:
            o["status"] = "SKIPPED"
            r.append(f"limite de {ctx['max_same_direction']} posições {o['direction']} simultâneas")
            continue
        if per_strat[o["primary_strategy"]] >= ctx["max_open_per_strategy"]:
            o["status"] = "SKIPPED"
            r.append(f"{o['primary_strategy']} já tem {ctx['max_open_per_strategy']} posições abertas")
            continue
        corr_hit = None
        for sym, d in chosen:
            if d != o["direction"]:
                continue
            rho = ctx["correlation"](o["symbol"], sym)
            if rho is not None and rho > ctx["max_correlation"]:
                corr_hit = (sym, rho)
                break
        if corr_hit:
            o["status"] = "SKIPPED"
            r.append(f"correlação {corr_hit[1]:.2f} com {corr_hit[0]} na mesma direção (> {ctx['max_correlation']:.2f})")
            continue
        o["status"] = "SELECTED"
        r.append(f"selecionada: #{o['rank']} no ranking, score {o['final_score']:.1f}, {o['confluence_level']}")
        open_count += 1
        open_dirs[o["direction"]] += 1
        per_strat[o["primary_strategy"]] += 1
        chosen.append((o["symbol"], o["direction"]))
    return opps
