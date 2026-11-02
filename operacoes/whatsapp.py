"""WhatsApp por UI nativa; ação identificada e resultado conciliável."""
import hashlib,json,re,time
from datetime import datetime,timezone,timedelta
from pathlib import Path
from .diario import begin_action,finish_action,action_status

PORTAL='WhatsApp — Atendimento FLOW'

def digits(value):return re.sub(r'\D','',value)

from .portal import native,portal_lock,resolve_portal_by_url

class Client:
    def __init__(self,context):
        self.context=context
        self.portal=context.get('whatsapp_portal') or resolve_portal_by_url(context,'whatsapp.com',PORTAL)
    def call(self,op,*args):
        return native(self.context,['portal',op,getattr(self,'portal',PORTAL),*args])
    def snapshot(self):return self.call('snapshot')
    def ref(self,pattern):
        match=re.search(r'(?m)^(@e\d+) '+pattern,self.snapshot())
        if not match:raise ValueError('whatsapp_element_missing: '+pattern[:80])
        return match[1]
    def open_contact(self,contact):
        from operacoes.destinatarios import recipient_key
        if contact.startswith('group:'):
            expected=recipient_key(contact)
            try:
                if self.contact_id()==expected:return
            except ValueError:pass
            import sqlite3
            from contextlib import closing
            with closing(sqlite3.connect(Path(self.context['runtime'])/'estado.sqlite',timeout=15)) as db:
                names=[r[0] for r in db.execute('SELECT DISTINCT name FROM whatsapp_name_bindings WHERE task_id=? AND contact_id=?',
                      (self.context['task']['task_id'],expected))]
            if len(names)!=1:raise ValueError('group_binding_missing_or_ambiguous')
            from operacoes.contatos import resolve
            result=resolve(self.context,names[0],client=self)
            if not result.get('resolved') or result['contact_id']!=expected:raise ValueError('group_identity_changed: conferir cadastro antes de enviar')
            return
        expected=digits(contact)
        if not 8<=len(expected)<=15:raise ValueError('contact_number_invalid')
        # Não navegar novamente se a conversa correta já está aberta.
        try:
            if self.contact_id()==expected:return
        except ValueError:pass
        self.call('navigate','https://web.whatsapp.com/send?phone='+expected)
        deadline=time.monotonic()+45
        while time.monotonic()<deadline:
            view=self.snapshot()
            if not view.startswith('loading:') and re.search(r'textbox "(?:Digite uma mensagem|Type a message)',view):
                try:
                    if self.contact_id()==expected:return
                except ValueError:pass
            time.sleep(.4)
        raise ValueError('contact_open_not_confirmed')
    def contact_id(self):
        view=self.snapshot()
        from operacoes.destinatarios import composer_name,group_identity
        title,is_group=composer_name(view)
        if is_group:return group_identity(self,title)
        header_digits=digits(title)
        if 8<=len(header_digits)<=15:return header_digits
        panel=re.search(r'(?m)^(@e\d+) div "Dados do contato',view)
        opened=False
        if not panel:
            profile=re.search(r'(?m)^(@e\d+) button "Dados do perfil"',view)
            if not profile:raise ValueError('contact_identity_not_visible')
            self.call('click','[aria-label="Dados do perfil"]');opened=True
            for _ in range(10):
                view=self.snapshot();panel=re.search(r'(?m)^(@e\d+) div "Dados do contato',view)
                if panel:break
                time.sleep(.2)
        try:
            if not panel:raise ValueError('contact_identity_unavailable: dados do contato indisponíveis')
            # O snapshot trunca nomes longos. Ler somente o painel, não o histórico da conversa.
            text=self.call('text','[data-testid="chat-info-drawer"]')
            phones=re.findall(r'(?m)^\s*(\+\d[\d ()\u00a0-]{8,})\s*$',text)
            numbers={digits(value) for value in phones if 8<=len(digits(value))<=15}
            if len(numbers)!=1:raise ValueError('contact_identity_unavailable: não foi possível identificar um único contato pelo cadastro')
            return numbers.pop()
        finally:
            if opened:
                current=self.snapshot();close=re.search(r'(?m)^(@e\d+) button "Fechar"',current)
                if close:self.call('click','[data-testid="drawer-right"] button[aria-label="Fechar"]')
    def read_draft(self):
        from .editor import read
        return read(self)
    def set_draft(self,text):
        from .editor import set_text
        return set_text(self,text)
    def commit(self):self.call('click',self.ref(r'button "Enviar"'))
    def messages(self):
        return parse_messages(self.call('html'))
    def capture_baseline(self):self.baseline={item['ref'] for item in self.messages()}
    def confirm(self,text):
        for _ in range(20):
            for item in reversed(self.messages()):
                if ''.join(item['text']).strip()==text.strip() and item['ref'] not in getattr(self,'baseline',set()):
                    return {'reference':item['ref'],'text_sha256':hashlib.sha256(text.encode()).hexdigest()}
            time.sleep(.3)
        raise ValueError('send_result_unknown: mensagem não confirmada no DOM')

def send_message(context,contact_id,text,action_id,client=None):
    from operacoes.destinatarios import recipient_key
    contact_id=recipient_key(contact_id)
    if not isinstance(text,str) or not text.strip() or len(text)>8000:raise ValueError('message_text_invalid')
    client=client or Client(context);db=Path(context['runtime'])/'estado.sqlite';task_id=context['task']['task_id']
    client.open_contact(contact_id)
    if client.contact_id()!=contact_id:raise ValueError('recipient_changed')
    draft=client.read_draft();begun=False;clicked=False
    draft_file=Path(context['runtime'])/'operacoes/drafts'/(hashlib.sha256((task_id+action_id).encode()).hexdigest()+'.json')
    draft_file.parent.mkdir(parents=True,exist_ok=True)
    # Não substituir o rascunho original numa tentativa repetida.
    if not draft_file.exists():draft_file.write_text(json.dumps({'draft':draft,'contact_id':contact_id},ensure_ascii=False),encoding='utf-8')
    try:
        name=re.search(r'(?i)\bsou\s+(?:(?:o|a)\s+)?(Cais|Prisma|Trilho|Crivo|Sol|Luna|Astra|Orquestrador)\b',text)
        purpose=None
        if name and 'bom dia' in text.casefold():purpose=hashlib.sha256((datetime.now(timezone(timedelta(hours=-3))).date().isoformat()+contact_id+name[1].casefold()).encode()).hexdigest()
        begin_action(db,task_id,action_id,context['actor'],'whatsapp_send',{'contact_id':contact_id},hashlib.sha256(text.encode()).hexdigest(),purpose);begun=True
        client.set_draft(text)
        if client.contact_id()!=contact_id:raise ValueError('recipient_changed')
        if hasattr(client,'capture_baseline'):client.capture_baseline()
        clicked=True;client.commit();evidence=client.confirm(text)
        if client.contact_id()!=contact_id:raise ValueError('recipient_changed_after_click')
        return finish_action(db,task_id,action_id,'confirmed',evidence)
    except Exception as error:
        if begun:finish_action(db,task_id,action_id,'unknown',{'reason':type(error).__name__,'clicked':clicked})
        raise ValueError('send_uncertain' if clicked else 'send_not_performed') from error
    finally:
        if begun:
            try:
                current=client.read_draft()
                if client.contact_id()==contact_id and current in ('',text):client.set_draft(draft)
            except Exception:pass # Cópia privada anterior permanece no diário de rascunhos pelo transporte.

def find_contact(context,query):
    from operacoes.contatos import resolve
    return resolve(context,query)


def parse_messages(html):
    """Accept stable message IDs and explicit outgoing/delivery markers, not CSS hashes."""
    from html.parser import HTMLParser
    class Parser(HTMLParser):
        def __init__(self):super().__init__();self.depth=0;self.active=None;self.body=None;self.items=[]
        def handle_starttag(self,tag,attrs):
            a=dict(attrs);identifier=a.get('data-id','')
            if self.active is None and identifier:
                self.active={'depth':self.depth,'text':[],'ref':identifier,'outgoing':identifier.startswith('true_'),'delivery':False}
            if self.active:
                if a.get('data-testid')=='tail-out' or a.get('data-icon')=='tail-out' or 'message-out' in a.get('class','').split():self.active['outgoing']=True
                label=a.get('aria-label','').strip().casefold()
                if label in ('você:','you:'):self.active['outgoing']=True
                if label in ('entregue','enviada','enviado','lida','lido','sent','delivered','read'):self.active['delivery']=True;self.active['outgoing']=True
                if self.body is None and ('selectable-text' in a.get('class','').split() or a.get('data-testid')=='selectable-text'):self.body=self.depth
                if self.body is not None and tag=='br':self.active['text'].append('\n')
                if self.body is not None and tag=='img' and a.get('alt'):self.active['text'].append(a['alt'])
            if tag not in ('br','img','input','hr','meta','link','source','wbr'):self.depth+=1
        def handle_data(self,data):
            if self.active and self.body is not None:self.active['text'].append(data)
        def handle_endtag(self,tag):
            if tag in ('br','img','input','hr','meta','link','source','wbr'):return
            self.depth=max(0,self.depth-1)
            if self.body is not None and self.depth==self.body:self.body=None
            if self.active and self.depth==self.active['depth']:
                if self.active['outgoing'] and self.active['text']:self.items.append(self.active)
                self.active=None
    parser=Parser();parser.feed(html);return parser.items

def execute_queued_message(context,operation,before_commit,client=None):
    """Whole UI transaction owns the shared resource; authorization rechecked at commit."""
    with portal_lock(context,PORTAL):
        c=client or Client(context);contact=operation['contact_id'];text=operation['body']
        c.open_contact(contact)
        if c.contact_id()!=contact:raise ValueError('recipient_changed')
        draft=c.read_draft();draft_file=Path(context['runtime'])/'operacoes/drafts'/(operation['operation_id']+'.json')
        draft_file.parent.mkdir(parents=True,exist_ok=True)
        if not draft_file.exists():draft_file.write_text(json.dumps({'draft':draft,'contact_id':contact},ensure_ascii=False),encoding='utf-8')
        else:
            saved=json.loads(draft_file.read_text(encoding='utf-8'))
            if saved['contact_id']!=contact:raise ValueError('recipient_changed_during_recovery')
            draft=saved['draft']
        try:
            c.set_draft(text)
            if c.contact_id()!=contact:raise ValueError('recipient_changed')
            c.capture_baseline()
            before_commit() # Persist commit intent BEFORE the external click.
            c.commit();evidence=c.confirm(text)
            if c.contact_id()!=contact:raise ValueError('recipient_changed_after_click')
            return evidence
        finally:
            try:
                if c.contact_id()==contact and c.read_draft() in ('',text):c.set_draft(draft)
            except Exception:pass # Private original retained for manual recovery.
