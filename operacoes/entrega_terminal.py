"""Confirma somente um pedido da Central comprovadamente parado no input.

O retorno do transporte não comprova aceite. Timeout e estado ambíguo continuam
exigindo conferência; nunca se repete o texto do pedido para tentar recuperá-lo.
"""
import re

PREFIX='PEDIDO DIRETO DO USUÁRIO PELA CENTRAL.'
ANSI=re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)')

def idle_input(output):
    if not isinstance(output,str):return False
    clean=ANSI.sub('',output).replace('\r','')
    if re.search(r'Working\s*\([^\n]*esc to interrupt',clean[-4000:],re.I):return False
    prompts=list(re.finditer(r'^[ \t]*›[ \t]*(.*)',clean,re.MULTILINE))
    return bool(prompts and prompts[-1].group(1).strip()=='Ask Codex to do anything')


def pending_task_input(output,task_id):
    if not isinstance(output,str) or not re.fullmatch(r'central-[a-f0-9]{24}',task_id):return False
    clean=ANSI.sub('',output).replace('\r','')
    prompts=list(re.finditer(r'^\s*›\s*(.*)',clean,re.MULTILINE))
    if not prompts:return False
    last=prompts[-1];block=clean[last.start():]
    if not last.group(1).startswith(PREFIX):return False
    if not re.search(r'Tarefa já registrada:\s*'+re.escape(task_id)+r'\.',block):return False
    if re.search(r'Working\s*\(|esc to interrupt|Calling\s|Thinking\s',block,re.IGNORECASE):return False
    return True

def send_registered_request(cli,registry,task_id,instruction,identity,before_submit=None):
    output=cli(registry,['ask',registry['primary_name'],instruction],identity=identity,timeout=60)
    if not pending_task_input(output,task_id):return {'output':output,'submitted_pending':False}
    current=cli(registry,['check',registry['primary_name']],identity=identity,timeout=15)
    if not pending_task_input(current,task_id):return {'output':current,'submitted_pending':False}
    # Record intent before sending. If saving fails, do not touch the terminal.
    if before_submit is not None:before_submit()
    output=cli(registry,['ask',registry['primary_name'],'--raw',r'\n'],identity=identity,timeout=60)
    return {'output':output,'submitted_pending':True}
