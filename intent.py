#!/usr/bin/env python3
"""
intent.py — classificador de intenção de mensagem inbound

Sem ML, sem dependência externa. Regex + heurística pondera:
  opt_out          (prioridade máxima — auto-blacklist)
  agendamento      ("posso reunir", "marca", "que horas", "calendário")
  interessado      ("quero", "manda", "tenho interesse", "como faço")
  objecao_preco    ("caro", "valor", "desconto", "muito dinheiro")
  pergunta         (termina em ? OU tem palavra interrogativa)
  sem_interesse    ("não tenho", "agora não", "depois")
  saudacao         ("oi", "bom dia", "olá")
  ruido            (figurinha, áudio só, emoji só, "ok", "k", "👍")
  desconhecido     (não bateu em nada)

Cada intent retorna score 0-1. Quando 2+ batem, vence o de maior peso.
"""

import re
import unicodedata


def _norm(s):
    if not s:
        return ""
    n = unicodedata.normalize("NFKD", s)
    return n.encode("ascii", "ignore").decode("ascii").lower().strip()


# Patterns por intent (regex em texto NORMALIZADO sem acento)
PATTERNS = {
    "opt_out": [
        (r"\bsair\b",                        1.0),
        (r"\bpara(r)?\b(?!.*pra que)",       0.95),
        (r"\bstop\b",                        1.0),
        (r"\bme tira\b",                     1.0),
        (r"\bnao quero (mais|receber)\b",    1.0),
        (r"\bdescadastr",                    1.0),
        (r"\bremov(er|a) (me|do grupo|da lista)\b", 1.0),
        (r"\bcancela(r)?\b.*\b(disparo|envio|mensagens)\b", 0.95),
        (r"\bopt[- ]?out\b",                 1.0),
        (r"\bunsubscribe\b",                 1.0),
    ],
    "agendamento": [
        (r"\b(marca|agenda|agendar|marcar) (uma )?(reuniao|conversa|call|ligacao)\b", 1.0),
        (r"\b(posso|podemos) (conversar|falar|reunir)\b", 0.9),
        (r"\bqu(e|ais) horas?\b",            0.7),
        (r"\bque dia\b",                     0.7),
        (r"\b(seg|ter|qua|qui|sex)[a-z]*[- ]?(feira)?\b.*\b\d{1,2}h", 0.9),
        (r"\b\d{1,2}[h:]\d{0,2}\b",          0.6),
        (r"\bcalendario\b",                  0.8),
        (r"\bdisponibilidade\b",             0.85),
    ],
    "interessado": [
        (r"\bquero\b",                       0.85),
        (r"\btenho interesse\b",             1.0),
        (r"\bme interessa\b",                0.9),
        (r"\bme manda\b",                    0.7),
        (r"\bcomo (faco|fazer)\b",           0.75),
        (r"\bcomo eu (faco|entro|compro)\b", 0.85),
        (r"\b(eu )?topo\b",                  0.85),
        (r"\bbora\b",                        0.6),
        (r"\bvamo(s)?\b",                    0.55),
        (r"\bmanda (o link|mais info|detalhes|os )", 0.85),
        (r"\bcomprar?\b",                    0.85),
        (r"\bfechar\b",                      0.7),
        (r"\bseparar?\b",                    0.55),
        (r"\bgaranta(do|r)\b",               0.7),
    ],
    "objecao_preco": [
        (r"\b(muito )?caro\b",               0.9),
        (r"\bvalor\b.*\?",                   0.7),
        (r"\bquanto (custa|fica|sai)\b",     0.95),
        (r"\bdesconto\b",                    0.9),
        (r"\bfora do (meu )?orcamento\b",    0.95),
        (r"\bsem (grana|dinheiro|condicoes)\b", 0.9),
        (r"\bparcela(r)?\b",                 0.8),
    ],
    "sem_interesse": [
        (r"\bnao tenho interesse\b",         1.0),
        (r"\bagora nao\b",                   0.85),
        (r"\bdepois (eu )?vejo\b",           0.7),
        (r"\bnao e pra mim\b",               0.95),
        (r"\bobrigad[oa].*nao\b",            0.85),
        (r"\bvou pensar\b",                  0.65),
        (r"\bnao da\b",                      0.6),
    ],
    "saudacao": [
        (r"^bom dia\b",                      0.85),
        (r"^boa tarde\b",                    0.85),
        (r"^boa noite\b",                    0.85),
        (r"^ola\b",                          0.7),
        (r"^oi\b",                           0.6),
        (r"^e ai\b",                         0.6),
        (r"^salve\b",                        0.6),
    ],
    "ruido": [
        (r"^[\W_]*$",                        0.95),  # só símbolo/emoji
        (r"^(ok|kk+|hm+|k|aham|aha)\.?$",    0.9),
        (r"^(rs+|kkkk+)\.?$",                0.9),
        (r"^\w{1,2}$",                       0.7),
    ],
}


# Pesos por intent — quem vence quando dois batem
PRIORITY = {
    "opt_out":        100,
    "agendamento":     80,
    "interessado":     70,
    "objecao_preco":   65,
    "sem_interesse":   60,
    "pergunta":        50,
    "saudacao":        20,
    "ruido":           10,
    "desconhecido":     0,
}


def classify(text):
    """Retorna (intent, score 0-1, debug_dict).

    score: confidence média dos patterns que bateram.
    """
    if not text or not text.strip():
        return "ruido", 0.95, {"reason": "vazio"}

    norm = _norm(text)

    # Cumprimento puro ("oi, tudo bem?") é saudação, não pergunta
    if re.fullmatch(
        r"(oi+|ola|opa|e ai|salve|bom dia|boa tarde|boa noite)[\s,!.]*"
        r"(tudo (bem|bom|certo|joia)|td bem|como vai|beleza|blz)?[\s?!.]*",
        norm,
    ):
        return "saudacao", 0.9, {"reason": "cumprimento"}

    scores = {}

    for intent, patterns in PATTERNS.items():
        matches = []
        for regex, weight in patterns:
            if re.search(regex, norm):
                matches.append(weight)
        if matches:
            scores[intent] = (sum(matches) / len(matches), len(matches))

    # Pergunta — heurística simples
    if "?" in text or re.search(r"\b(quem|o que|como|quando|onde|por que|porque|qual|quais)\b", norm):
        scores.setdefault("pergunta", (0.7, 1))

    if not scores:
        return "desconhecido", 0.0, {"reason": "sem match"}

    # Resolve por prioridade × score
    best = max(scores.items(), key=lambda kv: PRIORITY.get(kv[0], 0) * kv[1][0])
    intent_name = best[0]
    score = round(best[1][0], 2)
    return intent_name, score, {
        "all_matches": {k: v[0] for k, v in scores.items()},
        "winner_priority": PRIORITY.get(intent_name, 0),
    }


def is_opt_out(text):
    intent, score, _ = classify(text)
    return intent == "opt_out" and score >= 0.85


# Self-test rápido
if __name__ == "__main__":
    samples = [
        ("SAIR", "opt_out"),
        ("para de me mandar mensagem por favor", "opt_out"),
        ("quero saber mais sobre o produto", "interessado"),
        ("manda o link", "interessado"),
        ("tá muito caro", "objecao_preco"),
        ("quanto custa?", "objecao_preco"),
        ("agora não, depois eu vejo", "sem_interesse"),
        ("podemos marcar uma reunião amanhã 14h?", "agendamento"),
        ("oi, tudo bem?", "saudacao"),
        ("ok", "ruido"),
        ("👍", "ruido"),
        ("", "ruido"),
        ("isso é spam, me tira da lista", "opt_out"),
        ("como funciona?", "pergunta"),
    ]
    print(f"{'INPUT':<55} {'EXPECTED':<15} {'GOT':<15} {'SCORE':<6}")
    ok = 0
    for text, expected in samples:
        intent, score, _ = classify(text)
        mark = "✓" if intent == expected else "✗"
        print(f"{mark} {text[:50]:<53} {expected:<15} {intent:<15} {score}")
        if intent == expected:
            ok += 1
    print(f"\n{ok}/{len(samples)} OK")
