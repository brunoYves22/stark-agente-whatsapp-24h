#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
operacoes/monitor_youtrack_entregas.py
Monitora cards do projeto FLO no YouTrack que transitam para o estado 'Concluída'.
Quando uma alteração é visível ao cliente:
1. Identifica se o card menciona o cliente/grupo solicitante (tags, título ou descrição).
2. Gera um rascunho de mensagem amigável (sem jargão técnico) para o grupo do cliente.
3. Registra em operacoes/propostas_pendentes.json e avisa Bruno no grupo MAESTRI:
   - Se souber o grupo: pede confirmação (@Stark aprovar <ID>).
   - Se não souber o grupo: pergunta se algum cliente pediu e para qual grupo enviar (@Stark enviar <ID> para <Grupo>).
"""

import argparse
import json
from pathlib import Path
import re
import sys
import time

_RUNTIME_ROOT = Path(__file__).resolve().parent.parent
if str(_RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(_RUNTIME_ROOT))
if str(_RUNTIME_ROOT / "code") not in sys.path:
    sys.path.insert(0, str(_RUNTIME_ROOT / "code"))

DEFAULT_CLIENT_GROUP_MAP = {
    "flow - oca": "Flow - Oca",
    "oca 2": "Flow - Oca",
    "oca": "Flow - Oca",
    "uly": "Flow - Oca",
    "uly diniz": "Flow - Oca",
    "efinance": "Efinance",
    "paycard": "ONBOARDING <> PAYCARD",
    "lidia": "Lidia Fábrica de Moveis <> SYNAPSE",
    "lídia": "Lidia Fábrica de Moveis <> SYNAPSE",
    "casa pronta": "Casa Pronta",
    "humani": "FLOW / Implantação Humani",
    "bari": "Bari Planejados",
    "mariage": "Mariage",
    "dávila": "Mariage",
    "davila": "Mariage",
}

INTERNAL_ONLY_PATTERNS = [
    re.compile(r"\[opera[cç][oõ]es\s*/\s*pop\]", re.IGNORECASE),
    re.compile(r"\bpop\s+para\b", re.IGNORECASE),
    re.compile(r"\bdesenvolver\s+mcp\b", re.IGNORECASE),
    re.compile(r"\bdesmembrado\b", re.IGNORECASE),
    re.compile(r"\brefatora[cç][aã]o\s+interna\b", re.IGNORECASE),
    re.compile(r"\binfraestrutura\s+interna\b", re.IGNORECASE),
    re.compile(r"\bteste\s+interno\b", re.IGNORECASE),
]


def load_client_group_map(root: Path) -> dict:
    cfg_path = root / "operacoes" / "youtrack_clientes_grupos.json"
    if not cfg_path.exists():
        try:
            cfg_path.write_text(json.dumps(DEFAULT_CLIENT_GROUP_MAP, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        return dict(DEFAULT_CLIENT_GROUP_MAP)
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        merged = dict(DEFAULT_CLIENT_GROUP_MAP)
        for k, v in data.items():
            if k and v:
                merged[str(k).strip().lower()] = str(v).strip()
        return merged
    except Exception:
        return dict(DEFAULT_CLIENT_GROUP_MAP)


def is_client_visible_issue(issue: dict) -> bool:
    """Retorna True se o card representa um bug corrigido ou funcionalidade visível/sentida pelo cliente."""
    summary = (issue.get("summary") or "").strip()
    desc = (issue.get("description") or "").strip()
    combined = f"{summary}\n{desc}"
    for pat in INTERNAL_ONLY_PATTERNS:
        if pat.search(combined):
            return False
    return True


def resolve_group_alias(raw_name: str, root: Path) -> str:
    """Converte um apelido curto (ex: 'Oca', 'Lidia', 'Paycard') no nome exato do grupo do WhatsApp."""
    if not raw_name:
        return ""
    clean = str(raw_name).strip()
    low = clean.lower()
    mapping = load_client_group_map(root)
    if low in mapping:
        return mapping[low]
    for k, grp in sorted(mapping.items(), key=lambda x: len(x[0]), reverse=True):
        if low == k or f" {k}" in f" {low} ":
            return grp
    return clean


def detect_client_group(issue: dict, root: Path):
    """
    Procura menção explícita a cliente ou grupo no título, descrição, tags ou comentários.
    Retorna (detected_group, mention_snippet).
    """
    mapping = load_client_group_map(root)
    summary = (issue.get("summary") or "").strip()
    desc = (issue.get("description") or "").strip()
    comments_text = "\n".join(str(c.get("text") or "") for c in (issue.get("comments") or []))
    tags = [str(t).strip() for t in (issue.get("tags") or []) if str(t).lower() not in ("estrela", "star")]

    # 1. Procurar linha explícita de Origem / Cliente / Relato / Grupo na descrição ou comentários
    for source_label, text_block in (("descrição", desc), ("título", summary), ("comentários", comments_text)):
        for line in text_block.splitlines():
            clean_line = re.sub(r"[*_#>`~]+", "", line).strip()
            if not clean_line:
                continue
            m_grp = re.search(r"grupo\s+([A-Za-zÀ-ÿ0-9_<> -]{3,35}?)(?:[,.;\n)]|$)", clean_line, re.IGNORECASE)
            if m_grp:
                grp = resolve_group_alias(m_grp.group(1).strip(), root)
                return grp, clean_line[:110]

            low_line = clean_line.lower()
            for key in sorted(mapping.keys(), key=len, reverse=True):
                if re.search(r"(?<![a-z0-9])" + re.escape(key) + r"(?![a-z0-9])", low_line):
                    return mapping[key], f'{clean_line[:110]} ({source_label})'

    # 2. Procurar nas tags (exceto Estrela/Star)
    for tag in tags:
        t_low = tag.lower()
        for key in sorted(mapping.keys(), key=len, reverse=True):
            if key in t_low or t_low in key:
                return mapping[key], f'Tag "{tag}"'

    return None, None


def clean_summary_for_client(summary: str) -> str:
    """Limpa prefixos internos do título do card para ficar natural na mensagem ao cliente."""
    s = re.sub(r"\s*\([^)]*\)\s*$", "", (summary or "").strip())
    s = re.sub(r"\s*[—–-]\s*relato\s+d[aeo]\s+\w+.*$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^(?:investigar|corrigir|ajustar|verificar)\s+", "", s, flags=re.IGNORECASE)
    return (s[:1].upper() + s[1:]).strip() if s else "Atualização no sistema"


def extract_client_benefit_bullets(issue: dict) -> list:
    """Extrai de 1 a 3 pontos objetivos em linguagem simples (sem blocos de código) sobre o que foi resolvido."""
    desc = issue.get("description") or ""
    # Remover blocos de código e imagens markdown
    desc_clean = re.sub(r"```[\s\S]*?```", "", desc)
    desc_clean = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", desc_clean)

    bullets = []
    # Tentar extrair da seção 'Solução esperada' ou 'Requisitos'
    sec_match = re.search(r"(?:Solu[cç][aã]o\s+esperada|Requisitos[^:\n]*|Comportamento[^:\n]*)[:\s]*([\s\S]+)", desc_clean, re.IGNORECASE)
    target_text = sec_match.group(1) if sec_match else desc_clean

    for raw_line in target_text.splitlines():
        line = re.sub(r"\*\*([^*]+)\*\*", r"*\1*", raw_line.strip())
        line = re.sub(r"^(?:[\d.)+-]+|[•-]|(?:\*(?=\s)))\s*", "", line).strip()
        line = re.sub(r"`[^`]+`", "", line).strip()
        if len(line) < 25 or line.startswith("#") or line.lower().startswith("origem:") or line.endswith(":") or line.endswith(":*"):
            continue
        if any(tech in line.lower() for tech in ("front-end", "backend", "array", "função", "endpoint", "json", "sql", "canseeinternaldata")):
            continue
        bullets.append(line)
        if len(bullets) >= 2:
            break

    return bullets


def build_client_delivery_message(issue: dict) -> str:
    """Gera a mensagem no Padrão Ouro WhatsApp para avisar o grupo do cliente."""
    issue_type = (issue.get("customFields") or {}).get("Type") or "Melhoria"
    clean_title = clean_summary_for_client(issue.get("summary") or "Atualização no sistema")
    bullets = extract_client_benefit_bullets(issue)

    if str(issue_type).lower() == "bug":
        intro = f"Passando para avisar que concluímos a correção sobre *{clean_title}* no sistema *FLOW*. ✅"
    else:
        intro = f"Passando para compartilhar uma novidade que acabamos de liberar no sistema *FLOW*: *{clean_title}*. 🚀"

    lines = [
        "Olá, pessoal! Tudo bem?",
        "",
        intro,
    ]
    if bullets:
        lines.append("")
        lines.append("*O que mudou na prática:*")
        for b in bullets:
            lines.append(f"• {b}")

    lines.extend([
        "",
        "Já está disponível em produção para uso da equipe. Qualquer dúvida ou ajuste, estamos totalmente à disposição por aqui!",
    ])
    return "\n".join(lines)


def build_maestri_prompt_message(issue: dict, prop_id: int, detected_group, mention_snippet, draft_msg: str) -> str:
    """Formata o aviso híbrido (A + B) que o Sentinela posta no grupo MAESTRI para Bruno decidir."""
    yid = issue.get("id")
    summary = issue.get("summary") or ""

    if detected_group:
        mention_txt = f'"{mention_snippet}"' if mention_snippet else f'"{detected_group}"'
        return (
            f"*FLOW Entregas (YouTrack):* O ticket *{yid}* (*{summary}*) foi concluído! ✅\n\n"
            f"• *Menção identificada no ticket:* {mention_txt}\n"
            f"• *Vai para o grupo:* *{detected_group}*\n\n"
            f"*Mensagem pronta para envio (Proposta #{prop_id}):*\n"
            f"\"{draft_msg}\"\n\n"
            f"👉 *Posso enviar para o grupo {detected_group}?*\n"
            f"• Para aprovar: *@Stark aprovar {prop_id}*\n"
            f"• Para outro grupo: *@Stark enviar {prop_id} para <Nome do Grupo>*\n"
            f"• Para descartar: *@Stark ignorar {prop_id}*"
        )
    else:
        return (
            f"*FLOW Entregas (YouTrack):* A tarefa *{yid}* (*{summary}*) foi concluída, *mas no ticket não informa quem pediu e não sei para onde enviar*. 🔔\n\n"
            f"*Mensagem pronta para envio (Proposta #{prop_id}):*\n"
            f"\"{draft_msg}\"\n\n"
            f"👉 *Para qual grupo devo enviar?*\n"
            f"• Me informe o destino: *@Stark enviar {prop_id} para <Nome do Grupo>* (ex: *@Stark enviar {prop_id} para Oca*)\n"
            f"• Ou descarte se não precisar avisar: *@Stark ignorar {prop_id}*"
        )


def register_youtrack_proposal(root: Path, issue: dict, detected_group, draft_msg: str) -> int:
    p_file = root / "operacoes" / "propostas_pendentes.json"
    propostas = []
    if p_file.exists():
        try:
            propostas = json.loads(p_file.read_text(encoding="utf-8"))
        except Exception:
            propostas = []

    yid = issue.get("id")
    for existing in propostas:
        if existing.get("youtrack_id") == yid and existing.get("status") == "aguardando_aprovacao":
            return int(existing["id"])

    next_id = max([int(p.get("id", 0)) for p in propostas if str(p.get("id", "")).isdigit()], default=0) + 1
    propostas.append({
        "id": next_id,
        "youtrack_id": yid,
        "area": f"YouTrack {yid}",
        "action": f"Notificar cliente sobre conclusão de {yid}: {issue.get('summary', '')}",
        "destination": detected_group or "A_DEFINIR",
        "channel": "whatsapp_grupo",
        "draft_message": draft_msg,
        "timestamp": time.strftime("%H:%M"),
        "meeting_id": f"youtrack-{yid.lower()}",
        "meeting_title": f"Entrega YouTrack {yid}",
        "status": "aguardando_aprovacao",
        "created_at": time.time(),
    })
    p_file.write_text(json.dumps(propostas, ensure_ascii=False, indent=2), encoding="utf-8")
    return next_id


def fetch_resolved_flo_issues(limit: int = 20) -> list:
    from ponte_maestri import youtrack_session
    call, _ = youtrack_session()
    res = call("tools/call", {
        "name": "search_issues",
        "arguments": {
            "query": "project: FLO State: Concluída sort by: updated desc",
            "limit": limit,
        }
    })
    content = (res or {}).get("content") or []
    for block in content:
        if block.get("type") == "text":
            data = json.loads(block["text"])
            return data.get("issuesPage") or []
    return []


def fetch_full_issue(issue_id: str) -> dict:
    from ponte_maestri import youtrack_session
    call, _ = youtrack_session()
    res = call("tools/call", {
        "name": "get_issue",
        "arguments": {"issueId": issue_id},
    })
    issue_data = {}
    content = (res or {}).get("content") or []
    for block in content:
        if block.get("type") == "text":
            issue_data = json.loads(block["text"])
            break
    if issue_data and issue_data.get("commentsCount", 0) > 0:
        try:
            c_res = call("tools/call", {
                "name": "get_issue_comments",
                "arguments": {"issueId": issue_id, "limit": 10},
            })
            for block in (c_res or {}).get("content") or []:
                if block.get("type") == "text":
                    c_data = json.loads(block["text"])
                    issue_data["comments"] = c_data.get("commentsPage") or c_data if isinstance(c_data, list) else []
        except Exception:
            pass
    return issue_data


def poll_youtrack_resolved(root: Path, send_maestri_fn=None, simulate_issue_id: str = None) -> list:
    """
    Verifica cards concluídos no YouTrack.
    - Na primeira execução, registra todos os cards já concluídos como vistos (evita disparar histórico antigo).
    - Para cada novo card concluído visível ao cliente, cria proposta e envia mensagem no grupo MAESTRI via send_maestri_fn.
    """
    root = Path(root)
    load_client_group_map(root)
    state_file = root / "operacoes" / "youtrack_entregas_state.json"
    state = {"initialized": False, "seen_resolved_ids": [], "last_poll": 0}
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    seen = set(state.get("seen_resolved_ids") or [])
    notifications = []

    if simulate_issue_id:
        full_issue = fetch_full_issue(simulate_issue_id)
        if not full_issue:
            return []
        candidates = [full_issue]
    else:
        resolved_list = fetch_resolved_flo_issues(limit=20)
        if not state.get("initialized"):
            for item in resolved_list:
                if item.get("id"):
                    seen.add(item["id"])
            state["initialized"] = True
            state["seen_resolved_ids"] = sorted(seen)
            state["last_poll"] = time.time()
            state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            return []

        new_ids = [item["id"] for item in resolved_list if item.get("id") and item["id"] not in seen]
        candidates = []
        for yid in reversed(new_ids):
            seen.add(yid)
            full_issue = fetch_full_issue(yid)
            if full_issue:
                candidates.append(full_issue)

    for issue in candidates:
        yid = issue.get("id")
        if not is_client_visible_issue(issue):
            continue
        detected_group, mention_snippet = detect_client_group(issue, root)
        draft_msg = build_client_delivery_message(issue)
        prop_id = register_youtrack_proposal(root, issue, detected_group, draft_msg)
        maestri_msg = build_maestri_prompt_message(issue, prop_id, detected_group, mention_snippet, draft_msg)
        if send_maestri_fn:
            send_maestri_fn(maestri_msg)
        notifications.append({
            "issue_id": yid,
            "proposal_id": prop_id,
            "detected_group": detected_group,
            "mention_snippet": mention_snippet,
            "maestri_message": maestri_msg,
        })

    if not simulate_issue_id:
        state["seen_resolved_ids"] = sorted(seen)
        state["last_poll"] = time.time()
        state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    return notifications


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Monitor de Entregas Concluídas no YouTrack (FLO)")
    parser.add_argument("--runtime", default=str(_RUNTIME_ROOT))
    parser.add_argument("--simulate", help="Simular detecção de um card específico (ex: FLO-32 ou FLO-52)")
    parser.add_argument("--send-whatsapp", action="store_true", help="Enviar notificação real no grupo MAESTRI")
    args = parser.parse_args()

    rt = Path(args.runtime).resolve()

    def _send_wa(msg_text):
        if not args.send_whatsapp:
            print("\n--- [PRÉVIA MENSAGEM GRUPO MAESTRI] ---\n" + msg_text + "\n---------------------------------------")
            return
        import operacoes.whatsapp as op_wa
        from operacoes.whatsapp import Client
        from operacoes.audio_whatsapp import open_for_read
        from operacoes.portal import portal_lock
        from operacoes.sentinela_whatsapp_stark import find_active_portal
        reg = json.loads((rt / "registry.json").read_text(encoding="utf-8-sig"))
        stark_tid = reg["roles"]["orquestrador"]["terminalId"]
        portal_name = find_active_portal(reg, stark_tid)
        op_wa.PORTAL = portal_name
        client = Client({"runtime": str(rt), "registry": reg, "whatsapp_portal": portal_name, "primary_terminal": stark_tid})
        with portal_lock({"runtime": str(rt)}, portal_name):
            target = open_for_read(client.context, "MAESTRI")
            time.sleep(0.4)
            target.set_draft(msg_text)
            time.sleep(0.4)
            target.commit()
            time.sleep(0.6)

    out = poll_youtrack_resolved(rt, send_maestri_fn=_send_wa, simulate_issue_id=args.simulate)
    print(f"[*] Processados {len(out)} card(s) visíveis ao cliente.")
