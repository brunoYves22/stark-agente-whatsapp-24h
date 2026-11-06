"""Roteador MCP por papel para terminais Antigravity (agy) no Maestri FLOW.
Identifica automaticamente o papel do terminal via FLOW_AGY_ROLE, MAESTRI_TERMINAL_ID ou CWD,
e aplica exatamente as mesmas ferramentas e permissoes do time principal, sem expor tokens em arquivos.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

RUNTIME = Path(r"./runtime")
PROJECT = Path(r"~\Desktop\FLOW_TESTES_SYNC")
PYTHON = Path(r"~\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe")

TERMINAL_MAP = {
    "d27068a2-e9e4-4d31-a1fa-d1c836c95bae": ("reserve_orquestrador", "orquestrador"),
    "b58657fc-c39e-4bd1-be73-f30663a06062": ("reserve_analista", "analista_youtrack"),
    "2c65739d-0419-4db8-a74f-14f5337b736e": ("reserve_testador", "testador_flow"),
    "eb9cc44e-61f3-45e1-b6f5-aa9bb4058a6f": ("reserve_validador", "validador"),
    "ea973a0c-d9e9-4f99-8563-1f077a931a57": ("orquestrador", "orquestrador"),
    "bddba3fa-984c-4a4f-8dd6-68f8d7fa3010": ("analista_youtrack", "analista_youtrack"),
    "b5416494-d3fa-44de-a3cd-63cc0ceaa6f6": ("testador_flow", "testador_flow"),
    "9ccead5c-b2a8-4079-b471-3c57702ddd19": ("validador", "validador"),
}

YOUTRACK_READ_TOOLS = [
    "find_projects", "get_project", "search_issues", "get_issue",
    "get_issue_comments", "get_issue_fields_schema", "get_saved_issue_searches",
    "get_current_user", "find_user", "find_user_groups", "get_user_group_members"
]


def detect_role():
    explicit = os.environ.get("FLOW_AGY_ROLE", "").strip().lower()
    if explicit in ("orquestrador", "reserve_orquestrador"):
        return "reserve_orquestrador", "orquestrador"
    if explicit in ("analista", "analista_youtrack", "reserve_analista"):
        return "reserve_analista", "analista_youtrack"
    if explicit in ("testador", "testador_flow", "reserve_testador"):
        return "reserve_testador", "testador_flow"
    if explicit in ("validador", "reserve_validador"):
        return "reserve_validador", "validador"

    tid = os.environ.get("MAESTRI_TERMINAL_ID", "").strip().lower()
    if tid in TERMINAL_MAP:
        return TERMINAL_MAP[tid]

    try:
        reg = json.loads((RUNTIME / "registry.json").read_text(encoding="utf-8-sig"))
        for rname, rinfo in reg.get("roles", {}).items():
            if str(rinfo.get("terminalId", "")).lower() == tid and tid:
                primary_equiv = {
                    "orquestrador": "orquestrador",
                    "analista": "analista_youtrack",
                    "testador": "testador_flow",
                    "validador": "validador",
                }[rname]
                return f"reserve_{rname}", primary_equiv
    except Exception:
        pass

    cwd = os.getcwd().replace("\\", "/").lower()
    if "/analista" in cwd:
        return "reserve_analista", "analista_youtrack"
    if "/testador" in cwd:
        return "reserve_testador", "testador_flow"
    if "/validador" in cwd:
        return "reserve_validador", "validador"
    return "reserve_orquestrador", "orquestrador"


def run_youtrack_proxy(actor):
    sys.path.insert(0, str(RUNTIME / "code"))
    from ponte_maestri import youtrack_session

    allowed = list(YOUTRACK_READ_TOOLS)
    if actor in ("orquestrador", "reserve_orquestrador"):
        allowed.append("create_issue")

    session_cache = {"call": None, "tools": None}

    def ensure_session():
        if session_cache["call"] is None:
            call, catalog = youtrack_session()
            session_cache["call"] = call
            session_cache["tools"] = [t for t in catalog if t.get("name") in allowed]
        return session_cache["call"], session_cache["tools"]

    for line in sys.stdin:
        request = {}
        try:
            request = json.loads(line)
            if "id" not in request:
                continue
            method = request.get("method")
            params = request.get("params", {})
            if method == "initialize":
                result = {
                    "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "youtrack-flow-proxy", "version": "1.0.0"},
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                _, tools = ensure_session()
                result = {"tools": tools}
            elif method == "tools/call":
                tname = params.get("name")
                if tname not in allowed:
                    raise ValueError(f"Ferramenta YouTrack nao permitida para {actor}: {tname}")
                call, _ = ensure_session()
                result = call("tools/call", {"name": tname, "arguments": params.get("arguments", {})})
            else:
                raise ValueError("Metodo MCP nao suportado")
            reply = {"jsonrpc": "2.0", "id": request["id"], "result": result}
        except Exception as error:
            detail = str(error) if isinstance(error, ValueError) else type(error).__name__
            reply = {
                "jsonrpc": "2.0",
                "id": request.get("id") if isinstance(request, dict) else None,
                "error": {"code": -32603, "message": detail},
            }
        print(json.dumps(reply, ensure_ascii=False), flush=True)



def run_synapse_admin_proxy():
    import urllib.request
    token = os.environ.get("SYNAPSE_ADMIN_MCP_TOKEN")
    if not token:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                token = winreg.QueryValueEx(key, "SYNAPSE_ADMIN_MCP_TOKEN")[0]
        except OSError:
            pass
    if not token:
        raise ValueError("Token SYNAPSE_ADMIN_MCP_TOKEN ausente neste PC; configure localmente")

    url = "https://admin.synapse-agents.com.br/mcp"
    headers = {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "User-Agent": "AGY-MCP-Bridge/1.0",
    }

    def remote_call(method, params=None, req_id=1):
        payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
        with urllib.request.urlopen(req, timeout=40) as resp:
            if resp.headers.get("Mcp-Session-Id"):
                headers["Mcp-Session-Id"] = resp.headers["Mcp-Session-Id"]
            raw = resp.read().decode("utf-8")
            ctype = resp.headers.get("Content-Type", "")
            if "text/event-stream" in ctype:
                val = json.loads("\n".join(x[5:].strip() for x in raw.splitlines() if x.startswith("data:")))
            else:
                val = json.loads(raw)
            if val.get("error"):
                raise ValueError(val["error"].get("message", "Erro MCP synapse-admin"))
            return val.get("result")

    for line in sys.stdin:
        request = {}
        try:
            request = json.loads(line)
            if "id" not in request:
                continue
            method = request.get("method")
            params = request.get("params", {})
            if method == "initialize":
                result = {
                    "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "synapse-admin", "version": "1.0.0"},
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = remote_call("tools/list", {}, request["id"])
            elif method == "tools/call":
                result = remote_call("tools/call", {"name": params.get("name"), "arguments": params.get("arguments", {})}, request["id"])
            else:
                raise ValueError("Metodo MCP nao suportado")
            reply = {"jsonrpc": "2.0", "id": request["id"], "result": result}
        except Exception as error:
            detail = str(error) if isinstance(error, ValueError) else type(error).__name__
            reply = {
                "jsonrpc": "2.0",
                "id": request.get("id") if isinstance(request, dict) else None,
                "error": {"code": -32603, "message": detail},
            }
        print(json.dumps(reply, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", required=True, choices=[
        "flow_operacoes", "flow_reserve", "flow_maestri", "flow_whatsapp_local", "youtrack", "synapse-admin"
    ])
    parser.add_argument("--role", default="auto")
    opts = parser.parse_args()

    if opts.role != "auto":
        os.environ["FLOW_AGY_ROLE"] = opts.role
    reserve_actor, primary_equiv = detect_role()

    if opts.server == "youtrack":
        run_youtrack_proxy(reserve_actor)
        return
    if opts.server == "synapse-admin":
        run_synapse_admin_proxy()
        return

    if opts.server == "flow_operacoes":
        cmd = [str(PYTHON), "-X", "utf8", str(RUNTIME / "operacoes/mcp.py"), "--runtime", str(RUNTIME), "--role", reserve_actor]
    elif opts.server == "flow_reserve":
        cmd = [str(PYTHON), "-X", "utf8", str(RUNTIME / "code/mcp.py"), "--runtime", str(RUNTIME), "--role", reserve_actor]
    elif opts.server == "flow_maestri":
        cmd = [str(PYTHON), "-X", "utf8", str(PROJECT / ".maestri/maestri_mcp.py"), "--role", reserve_actor]
    elif opts.server == "flow_whatsapp_local":
        cmd = [str(PYTHON), "-X", "utf8", str(PROJECT / ".maestri/whatsapp_mcp_local.py"), "--role", primary_equiv]
    else:
        raise ValueError("Servidor desconhecido")

    proc = subprocess.Popen(cmd, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr)
    sys.exit(proc.wait())


if __name__ == "__main__":
    main()
