"""Identidades de destinatário: telefone ou impressão de cadastro de grupo."""
import hashlib
import re


def recipient_key(value):
    if not isinstance(value,str):raise ValueError('recipient_invalid')
    if value.startswith('group:'):
        if not re.fullmatch(r'group:[a-f0-9]{64}',value):raise ValueError('group_identity_invalid')
        return value
    if not re.fullmatch(r'\+?[\d ()-]{8,25}',value):raise ValueError('contact_number_invalid')
    result=re.sub(r'\D','',value)
    if not 8<=len(result)<=15:raise ValueError('contact_number_invalid')
    return result


def composer_name(view):
    match=re.search(r'(?m)^@e\d+ textbox "(?:Digite uma mensagem para|Type a message to) ([^"]+)"',view)
    if not match:return None,False
    value=match[1]
    for prefix in ('o grupo ','the group ','group '):
        if value.startswith(prefix):return value[len(prefix):],True
    return value,False


def group_identity(client,expected_name):
    from operacoes.contatos import normalized
    view=client.snapshot();name,is_group=composer_name(view)
    if not is_group or normalized(name)!=normalized(expected_name):raise ValueError('group_not_open')
    opened='group-info-drawer-body' not in client.call('html')
    if opened:client.call('click','[aria-label="Dados do perfil"]')
    try:
        text=client.call('text','[data-testid="chat-info-drawer"]')
        subject=client.call('text','[data-testid^="group-info-drawer-subject-input-read-only"]')
        if normalized(subject)!=normalized(expected_name) and normalized(expected_name) not in normalized(subject):
            raise ValueError(f'group_subject_changed: subject={subject!r} expected={expected_name!r}')
        records=[line.strip() for line in text.splitlines() if re.search(r'criado|created',line,re.I)
                 and re.search(r'\d{1,2}/\d{1,2}/\d{2,4}',line)]
        if len(records)!=1:raise ValueError('group_identity_unavailable: cadastro de criação não confirmado')
        # Sem ID interno exposto pelo WhatsApp: congelar nome + registro de criação.
        # Mudanças nesse cadastro exigem nova conferência, nunca redirecionamento.
        return 'group:'+hashlib.sha256((normalized(subject)+'\0'+normalized(records[0])).encode()).hexdigest()
    finally:
        if opened:client.call('click','[data-testid="drawer-right"] button[aria-label="Fechar"]')
