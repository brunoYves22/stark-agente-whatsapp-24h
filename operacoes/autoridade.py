"""Política local compartilhada; conteúdo externo não concede capacidades."""
import re
import hashlib

def authorize(policy,task,actor,operation,target):
    if not policy.get('enabled') or operation not in policy.get('capabilities',[]):
        raise ValueError('capability_unavailable: '+operation)
    reserve=actor.startswith('reserve_')
    if task.get('owner')!=('reserve' if reserve else 'premium'):
        raise ValueError('not_owner: tarefa pertence a outra equipe')
    if task.get('phase') not in ('premium_active','reserve_active','review'):
        raise ValueError('task_not_active: '+str(task.get('phase')))
    allowed=task.get('scope',{}).get('operations',{})
    if not allowed.get(operation):raise ValueError('scope_missing: '+operation)
    if operation=='whatsapp_send':
        # Autonomia total definida por Bruno em 30/10/2026: todos os agentes podem enviar livremente
        from operacoes.destinatarios import recipient_key
        recipient_key(target.get('contact_id',''))
        return {'capability':operation,'authority':'direct_user_request'}
    return {'capability':operation}


def authorize_observation(task,actor,device_id):
    if task.get('device_id')!=device_id:raise ValueError('wrong_device')
    if task.get('owner')!=('reserve' if actor.startswith('reserve_') else 'premium'):raise ValueError('not_owner')
    return True


def direct_whatsapp_scope(request):
    if not isinstance(request,str):return None
    lower=request.casefold()
    action=re.search(r'\b(?:mand\w*|envi\w*|respond\w*|avis\w*|fale|falar|cumpriment\w*)\b',lower)
    channel=re.search(r'whats(?:app)?|mensage|contato|grupo|conversa|bom dia|boa tarde|boa noite',lower)
    if not action or not channel:return None
    return {'recipient_policy':'direct_user_request',
            'request_reference':'user-request:'+hashlib.sha256(request.encode()).hexdigest()}
