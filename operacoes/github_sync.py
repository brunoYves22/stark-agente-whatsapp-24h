import hashlib
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

AUTHOR_NAME = "Bruno Yves Monteiro de Paula"
AUTHOR_EMAIL = "260249131+brunoYves22@users.noreply.github.com"

FLOW_ROOT = Path(r"./runtime")
REPOS_BASE = Path(r"~\.gemini\antigravity\scratch\github_repos")
REPO_REUNIOES = REPOS_BASE / "flow-reunioes"
REPO_STARK = REPOS_BASE / "stark-agente-whatsapp-24h"

REUNIOES_FILES = [
    "__init__.py", "armazenamento.py", "core.py", "sentinela.py", "captura_saude.py",
    "meet_capture.py", "meet_video.py", "brave-extension/manifest.json",
    "brave-extension/background.js", "brave-extension/content.js", "media.py",
    "visual.py", "visual_ocr.ps1", "qualidade.py", "precisao.py", "validacao.py",
    "revisao.py", "inspecao.py", "recuperacao.py", "evidencias.py", "processar.py",
    "propostas.py", "servico.py", "painel.html", "app.js"
]

STARK_FILES = [
    "operacoes/__init__.py", "operacoes/provisionar.py", "operacoes/arquivos.py",
    "operacoes/saude.py", "operacoes/diagnostico.py", "operacoes/diario.py",
    "operacoes/companhia.py", "operacoes/receitas.py", "operacoes/continuidade.py",
    "operacoes/recuperacao.py", "operacoes/entrega_terminal.py", "operacoes/portal.py",
    "operacoes/painel.py", "operacoes/central_http.py", "operacoes/gestao.py",
    "operacoes/entrada.py", "operacoes/autoridade.py", "operacoes/conhecimento.py",
    "operacoes/editor.py", "operacoes/mcp.py", "operacoes/gestao_mcp.py",
    "operacoes/agy_mcp_router.py", "operacoes/fluxos.py", "operacoes/youtrack_comentarios.py",
    "operacoes/destinatarios.py", "operacoes/contatos.py", "operacoes/formatacao_whatsapp.py",
    "operacoes/whatsapp.py", "operacoes/fila_whatsapp.py", "operacoes/pesquisa_whatsapp.py",
    "operacoes/audio_whatsapp.py", "operacoes/sentinela_whatsapp_stark.py",
    "operacoes/executar_proposta.py", "operacoes/github_sync.py",
    "operacoes/painel_operacional.html", "operacoes/central.js",
    "instructions/orquestrador.md", "instructions/analista-youtrack.md",
    "instructions/testador-flow.md", "instructions/validador.md"
]

def sanitize_text(text: str) -> str:
    text = text.replace(
        r"./runtime",
        r"./runtime"
    )
    text = text.replace(
        r"./runtime",
        r"./runtime"
    )
    text = text.replace(r"~", r"~")
    text = text.replace(r"~", r"~")
    text = text.replace("https://youtrack.local/issue/", "https://youtrack.local/issue/")
    text = text.replace("youtrack.local", "youtrack.local")
    return text

def sync_file(src: Path, dst: Path) -> bool:
    if not src.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.suffix.lower() in (".py", ".js", ".html", ".md", ".json", ".ps1", ".css", ".txt"):
        clean = sanitize_text(src.read_text(encoding="utf-8", errors="replace"))
        if dst.exists():
            old = dst.read_text(encoding="utf-8", errors="replace")
            if old == clean:
                return False
        dst.write_text(clean, encoding="utf-8")
        return True
    else:
        b_new = src.read_bytes()
        if dst.exists() and dst.read_bytes() == b_new:
            return False
        shutil.copy2(src, dst)
        return True

def commit_and_push(repo_dir: Path, changed_rels: list[str], message: str) -> bool:
    if not changed_rels or not (repo_dir / ".git").exists():
        return False
    subprocess.run(["git", "add"] + changed_rels, cwd=str(repo_dir), check=True)
    status = subprocess.check_output(["git", "status", "--porcelain"], cwd=str(repo_dir), text=True).strip()
    if not status:
        return False
    env = os.environ.copy()
    env["GIT_AUTHOR_NAME"] = AUTHOR_NAME
    env["GIT_AUTHOR_EMAIL"] = AUTHOR_EMAIL
    env["GIT_COMMITTER_NAME"] = AUTHOR_NAME
    env["GIT_COMMITTER_EMAIL"] = AUTHOR_EMAIL
    msg = message or f"chore(sync): atualização automática ({', '.join(changed_rels[:3])})"
    subprocess.run(["git", "commit", "-m", msg], cwd=str(repo_dir), env=env, check=True)
    subprocess.run(["git", "push", "origin", "main"], cwd=str(repo_dir), check=True)
    print(f"[GITHUB SYNC] Commit & Push realizado em {repo_dir.name}: {msg}")
    return True

def sync_all(commit_msg: str = "") -> int:
    pushed = 0
    # 1. Sync FLOW Reuniões
    changed_r = []
    for rel in REUNIOES_FILES:
        if sync_file(FLOW_ROOT / "reunioes" / rel, REPO_REUNIOES / rel):
            changed_r.append(rel)
    if changed_r:
        m = commit_msg or f"feat(reunioes): atualiza {', '.join(changed_r[:3])}"
        if commit_and_push(REPO_REUNIOES, changed_r, m):
            pushed += 1

    # 2. Sync Stark WhatsApp 24h
    changed_s = []
    for rel in STARK_FILES:
        if sync_file(FLOW_ROOT / rel, REPO_STARK / rel):
            changed_s.append(rel)
    if changed_s:
        m = commit_msg or f"feat(operacoes): atualiza {', '.join(Path(x).name for x in changed_s[:3])}"
        if commit_and_push(REPO_STARK, changed_s, m):
            pushed += 1

    return pushed

if __name__ == "__main__":
    # Self-check of sanitization rules
    assert "youtrack.local" in sanitize_text("https://youtrack.local/issue/FLO-1")
    assert "./runtime" in sanitize_text(str(FLOW_ROOT))

    if "--watch" in sys.argv:
        print("[GITHUB SYNC] Monitoramento automático iniciado (intervalo: 15s)...")
        while True:
            try:
                sync_all()
            except Exception as e:
                print(f"[GITHUB SYNC] Erro no ciclo: {e}", file=sys.stderr)
            time.sleep(15)
    else:
        msg = " ".join(a for a in sys.argv[1:] if not a.startswith("--")).strip()
        n = sync_all(msg)
        print(f"[GITHUB SYNC] Concluído ({n} repositório(s) atualizado(s)).")
