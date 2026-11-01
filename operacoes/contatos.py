"""Resolve nomes pelo cadastro do próprio WhatsApp, sem pedir telefone ao usuário."""
import hashlib
import json
import re
import sqlite3
import time
import unicodedata
from contextlib import closing
from html.parser import HTMLParser
from pathlib import Path


def normalized(value):
    return ' '.join(''.join(c for c in unicodedata.normalize('NFKD',value) if not unicodedata.combining(c)).casefold().split())


class Names(HTMLParser):
    """Somente títulos de conversas na barra lateral; ignora mensagens/grupos em comum."""
    def __init__(self):
        super().__init__();self.stack=[];self.names=[]
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if (tag=='span' and a.get('title') and
                any(x.get('id')=='pane-side' for _,x in self.stack) and
                any(x.get('data-testid')=='cell-frame-title' for _,x in self.stack)):
            self.names.append(a['title'])
        if tag not in ('area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'):
            self.stack.append((tag,a))
    def handle_endtag(self,tag):
        for i in range(len(self.stack)-1,-1,-1):
            if self.stack[i][0]==tag:del self.stack[i:];break


def candidates(html,query):
    parser=Names();parser.feed(html);needle=normalized(query)
    exact=[n for n in parser.names if normalized(n)==needle]
    res = exact if exact else [n for n in parser.names if needle in normalized(n)]
    return list(dict.fromkeys(res))


def selector(name):
    if not isinstance(name,str) or not name.strip() or len(name)>160 or re.search(r'[\x00-\x1f\x7f]',name):
        raise ValueError('contact_name_invalid')
    escaped=name.replace('\\','\\\\').replace('"','\\"')
    return '#pane-side [data-testid="cell-frame-title"] span[title="'+escaped+'"]'


def search(client,query):
    selector(query)  # validar antes de interagir
    # Fechar dados do contato abertos por outra consulta antes da busca.
    view=client.snapshot()
    if re.search(r'(?m)^@e\d+ div "Dados do (?:contato|grupo)',view):
        close=re.search(r'(?m)^(@e\d+) button "Fechar"',view)
        if close:client.call('click',close[1])
    ref=client.ref(r'textbox "(?!Digite uma mensagem|Type a message)[^"\n]*" type=text')
    client.call('fill',ref,query)
    # A busca tem debounce: duas observações consecutivas estáveis, com prazo limitado.
    last=None;stable=0;names=[]
    for _ in range(12):
        time.sleep(.3)
        names=candidates(client.call('html'),query)
        if names==last:stable+=1
        else:last=names;stable=0
        if names and stable>=1:break
    return names



def conversation_name(client):
    """O composer pode anunciar telefone; o cabeçalho mantém o nome salvo.

    Isto confirma apenas qual conversa abriu. O envio continua exigindo a
    identidade obtida no cadastro e congelada por action_id.
    """
    from operacoes.destinatarios import composer_name
    title,is_group=composer_name(client.snapshot())
    if title and not is_group and re.fullmatch(r'\+?[\d ()-]{8,25}',title):
        try:
            title=client.call('text','#main [data-testid="conversation-info-header-chat-title"]').strip()
        except ValueError:
            return None,False
        if not title:return None,False
    return title,is_group


def resolve(context,name,client=None):
    from operacoes.whatsapp import Client
    c=client or Client(context);names=search(c,name)
    if len(names)!=1:
        return {'resolved':False,'query':name,'results':[{'name':n} for n in names[:20]],
                'reason':'ambiguous' if names else 'not_found',
                'note':'Peça para distinguir os contatos pelo nome.' if names else 'Confira o nome salvo; não peça o telefone ao usuário.'}
    selected=names[0]
    # Conferir novamente o título antes do clique; ref da busca anterior não é reutilizada.
    if candidates(c.call('html'),name)!=names:raise ValueError('contact_search_changed: repetir busca, sem enviar')
    c.call('click',selector(selected))
    # O composer confirma que a conversa selecionada abriu; não digitar nem clicar Enviar.
    for _ in range(12):
        view=c.snapshot()
        title,is_group=conversation_name(c)
        if title and normalized(title)==normalized(selected):break
        time.sleep(.2)
    else:raise ValueError('contact_open_not_confirmed: nenhuma mensagem enviada')
    from operacoes.destinatarios import recipient_key
    number=recipient_key(c.contact_id())
    return {'resolved':True,'query':name,'name':selected,'contact_id':number,
            'recipient_type':'group' if number.startswith('group:') else 'contact',
            'identity_source':'whatsapp_group_creation_record' if number.startswith('group:') else 'whatsapp_contact_details',
            'note':'Identidade obtida automaticamente; não peça o telefone ao usuário.'}


def recipient_for_send(context,value,action_id,text,purpose=None,resolver=resolve):
    """Congela nome→número por operação. Retry nunca redireciona a outra pessoa."""
    if not isinstance(value,str):raise ValueError('contact_name_invalid')
    if value.startswith('group:'):
        from operacoes.destinatarios import recipient_key,composer_name
        from operacoes.whatsapp import Client
        from operacoes.portal import portal_lock
        expected=recipient_key(value)
        with portal_lock(context,'WhatsApp — Atendimento FLOW'):
            current=Client(context)
            if current.contact_id()!=expected:raise ValueError('group_identity_not_current: informe o nome do grupo, não telefone')
            value,is_group=composer_name(current.snapshot())
            if not is_group:raise ValueError('group_not_open')
    if re.fullmatch(r'\+?[\d ()-]{8,25}',value):
        number=re.sub(r'\D','',value)
        if not 8<=len(number)<=15:raise ValueError('contact_number_invalid')
        return number
    selector(value)
    db=Path(context['runtime'])/'estado.sqlite';tid=context['task']['task_id'];actor=context['actor']
    digest=hashlib.sha256(text.encode()).hexdigest()
    with closing(sqlite3.connect(db,timeout=15)) as c:
        c.execute('''CREATE TABLE IF NOT EXISTS whatsapp_name_bindings(
          task_id TEXT,action_id TEXT,actor TEXT,name TEXT,contact_id TEXT,digest TEXT,purpose TEXT,
          created REAL,PRIMARY KEY(task_id,action_id))''');c.commit()
        old=c.execute('SELECT actor,name,contact_id,digest,purpose FROM whatsapp_name_bindings WHERE task_id=? AND action_id=?',(tid,action_id)).fetchone()
        if old:
            if old[0]!=actor or normalized(old[1])!=normalized(value) or old[3]!=digest or old[4]!=(purpose or ''):
                raise ValueError('action_identity_conflict: mesmo ID exige o mesmo destinatário e conteúdo')
            return old[2]
    from operacoes.portal import portal_lock
    with portal_lock(context,'WhatsApp — Atendimento FLOW'):
        match=resolver(context,value)
    if not match.get('resolved'):
        raise ValueError('recipient_'+match['reason']+': '+json.dumps(match,ensure_ascii=False))
    number=match['contact_id']
    with closing(sqlite3.connect(db,timeout=15)) as c:
        c.execute('BEGIN IMMEDIATE')
        c.execute('INSERT OR IGNORE INTO whatsapp_name_bindings VALUES(?,?,?,?,?,?,?,?)',
                  (tid,action_id,actor,value,number,digest,purpose or '',time.time()))
        saved=c.execute('SELECT actor,name,contact_id,digest,purpose FROM whatsapp_name_bindings WHERE task_id=? AND action_id=?',(tid,action_id)).fetchone()
        if saved!=(actor,value,number,digest,purpose or ''):
            c.rollback();raise ValueError('action_identity_conflict: resolução concorrente divergiu')
        c.commit()
    return number
