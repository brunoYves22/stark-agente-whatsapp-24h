
import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

def registrar_entrega_obsidian(item):
    try:
        from datetime import datetime
        obs_dir = Path(r"~\OneDrive\Documentos\atlss\atlass\Trabalho\Flow\Maestri\entregas")
        if not obs_dir.exists():
            return
        today_str = datetime.now().strftime("%Y-%m-%d")
        log_file = obs_dir / f"envios-{today_str}.md"
        
        pid = item.get("id")
        dest = item.get("destination")
        area = item.get("area")
        msg = item.get("draft_message", "")
        m_title = item.get("meeting_title", "Reunião FLOW")
        time_str = datetime.now().strftime("%H:%M:%S")
        
        entry = (
            f"\n### [{time_str}] Proposta #{pid} — {area} ({dest})\n"
            f"- **Reunião de Origem:** {m_title}\n"
            f"- **Canal / Destino:** `{item.get('channel')}` -> {dest}\n"
            f"- **Status:** `{item.get('status')}`\n"
            f"- **Conteúdo / Mensagem:**\n"
            f"```text\n{msg}\n```\n"
        )
        if not log_file.exists():
            log_file.write_text(f"# Registro de Envios e Execuções — {today_str}\n\nDiário sincronizado automaticamente com as aprovações de Bruno no WhatsApp e YouTrack.\n", encoding="utf-8")
        with log_file.open("a", encoding="utf-8") as f:
            f.write(entry)
    except Exception as err:
        print(f"[!] Erro ao registrar entrega no Obsidian: {err}")

def criar_card_youtrack_interno(root, item):
    try:
        sys.path.insert(0, str(root / "code"))
        from ponte_maestri import youtrack_session
        call, _ = youtrack_session()
        summary = f"[{item.get('area', 'FLOW')}] {item.get('action')}"
        desc = (
            f"Tarefa identificada automaticamente pelo FLOW Reuniões para Bruno Yves.\n\n"
            f"**Origem:** {item.get('meeting_title')}\n"
            f"**Timestamp do Áudio:** {item.get('timestamp')}\n\n"
            f"**Procedimento / Detalhes:**\n{item.get('draft_message')}"
        )
        res = call("tools/call", {
            "name": "create_issue",
            "arguments": {
                "project": "FLO",
                "summary": summary,
                "description": desc
            }
        })
        return res
    except Exception as err:
        print(f"[!] Erro ao criar card YouTrack: {err}")
        return None

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
operacoes/executar_proposta.py - Executor de Propostas de Ação Aprovadas por Bruno
Executa o envio no WhatsApp (grupo ou privado) de propostas originadas no FLOW Reuniões,
atualiza o status em propostas_pendentes.json e gera o relatório de confirmação.
"""

import argparse
import json
import os
from pathlib import Path
import sys
import time

def executar_proposta(root, prop_id=None, execute_all=False):
    root = Path(root)
    sys.path.insert(0, str(root))

    p_file = root / "operacoes" / "propostas_pendentes.json"
    if not p_file.exists():
        return {"ok": False, "error": "Nenhuma proposta pendente encontrada em propostas_pendentes.json."}

    propostas = json.loads(p_file.read_text(encoding="utf-8"))
    if not propostas:
        return {"ok": False, "error": "Lista de propostas pendentes está vazia."}

    reg = json.loads((root / "registry.json").read_text(encoding="utf-8-sig"))
    stark_tid = reg["roles"]["orquestrador"]["terminalId"]

    to_execute = []
    if execute_all:
        to_execute = [p for p in propostas if p.get("status") == "aguardando_aprovacao"]
    else:
        pid = int(prop_id)
        to_execute = [p for p in propostas if p.get("id") == pid]

    if not to_execute:
        return {"ok": False, "error": f"Proposta {prop_id} não encontrada ou já executada."}

    # Carregar cliente WhatsApp
    import operacoes.whatsapp as op_wa
    from operacoes.whatsapp import Client
    from operacoes.audio_whatsapp import open_for_read

    portal_name = "WhatsApp — Atendimento FLOW #2"
    op_wa.PORTAL = portal_name
    client = Client({"runtime": str(root), "registry": reg, "whatsapp_portal": portal_name, "primary_terminal": stark_tid})

    results = []
    for item in to_execute:
        pid = item.get("id")
        area = item.get("area")
        dest = item.get("destination")
        channel = item.get("channel")
        msg = item.get("draft_message")
        from operacoes.formatacao_whatsapp import organizar_mensagem_whatsapp
        msg = organizar_mensagem_whatsapp(msg)

        print(f"[*] Processando Proposta [{pid}] para '{dest}'...")

        if channel in ("whatsapp_grupo", "whatsapp_privado"):
            try:
                # Navegar até o grupo ou contato de destino
                target_client = open_for_read(client.context, dest)
                time.sleep(0.5)

                # Digitar rascunho e enviar
                target_client.set_draft(msg)
                time.sleep(0.5)
                target_client.commit()
                time.sleep(0.8)

                item["status"] = "executada"
                item["executed_at"] = time.time()
                results.append(f"✅ Proposta [{pid}] executada: Mensagem enviada para '{dest}' com sucesso.")
            except Exception as err:
                item["status"] = "erro_envio"
                item["error_detail"] = str(err)
                results.append(f"❌ Erro na Proposta [{pid}] ('{dest}'): {err}")
        else:
            # Tarefa interna / POP -> Criar card no YouTrack
            yt_res = criar_card_youtrack_interno(root, item)
            item["status"] = "card_criado_youtrack" if yt_res else "registrada_internamente"
            item["executed_at"] = time.time()
            if yt_res:
                results.append(f"🎫 Proposta [{pid}] (Ação Interna): Card criado no YouTrack com sucesso!")
            else:
                results.append(f"📋 Proposta [{pid}] (Ação Interna): Registrada para acompanhamento.")

        # Registrar no Obsidian
        registrar_entrega_obsidian(item)

    # Salvar estado atualizado
    p_file.write_text(json.dumps(propostas, ensure_ascii=False, indent=2), encoding="utf-8")

    # Retornar foco ao grupo MAESTRI para o sentinela continuar ouvindo
    try:
        open_for_read(client.context, "MAESTRI")
    except Exception:
        pass

    # Retornar relatório consolidado
    return {"ok": True, "results": results}

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Executar Proposta Aprovada")
    parser.add_argument("--runtime", default=r"./runtime")
    parser.add_argument("--id", help="ID da proposta (ex: 1)")
    parser.add_argument("--all", action="store_true", help="Executar todas as propostas pendentes")
    args = parser.parse_args()

    if not args.id and not args.all:
        print("Informe --id <numero> ou --all")
        sys.exit(1)

    res = executar_proposta(args.runtime, prop_id=args.id, execute_all=args.all)
    if res.get("ok"):
        for line in res.get("results", []):
            print(line)
    else:
        print(f"[!] Falha: {res.get('error')}")
