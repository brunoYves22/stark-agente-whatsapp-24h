"""Histórico completo carregado no DOM e navegação de documentos, sem envio."""
from html.parser import HTMLParser
import json
import re


def conversation(html,limit=120):
    class Parser(HTMLParser):
        def __init__(self):super().__init__();self.depth=0;self.active=None;self.body=None;self.items=[]
        def handle_starttag(self,tag,attrs):
            a=dict(attrs);identifier=a.get('data-id','')
            if self.active is None and identifier:
                self.active={'level':self.depth,'reference':identifier,'direction':'outgoing' if identifier.startswith('true_') else 'incoming','source_timestamp':'','text':[],'attachments':[],'audio':False,'media_label_parts':[]}
            if self.active:
                if re.search(r'mensagem de voz|voice message',a.get('aria-label',''),re.I):self.active['audio']=True
                if a.get('data-pre-plain-text'):self.active['source_timestamp']=a['data-pre-plain-text']
                if self.body is None and ('selectable-text' in a.get('class','').split() or a.get('data-testid')=='selectable-text'):self.body=self.depth
                if self.body is not None and tag=='br':self.active['text'].append('\n')
                if self.body is not None and tag=='img' and a.get('alt'):self.active['text'].append(a['alt'])
                for key in ('title','aria-label','download'):
                    value=a.get(key,'')
                    if re.search(r'\.(?:pdf|docx?|xlsx?|png|jpe?g|mp[34]|ogg|opus)(?:\b|$)',value,re.I) and value not in self.active['attachments']:
                        self.active['attachments'].append(value[:500])
            if tag not in ('br','img','input','hr','meta','link','source','wbr'):self.depth+=1
        def handle_data(self,text):
            if self.active:
                self.active['media_label_parts'].append(text)
                if self.body is not None:self.active['text'].append(text)
        def handle_endtag(self,tag):
            if tag in ('br','img','input','hr','meta','link','source','wbr'):return
            self.depth=max(0,self.depth-1)
            if self.body is not None and self.depth==self.body:self.body=None
            if self.active and self.depth==self.active['level']:
                self.active['media_label']=' '.join(self.active.pop('media_label_parts'))[:1000]
                self.active.pop('level');self.active['text']=''.join(self.active['text'])
                self.items.append(self.active);self.active=None
    parser=Parser();parser.feed(html)
    return {'messages':parser.items[-limit:],'loaded_messages':len(parser.items),
            'scope':'Somente histórico já carregado. Role a conversa e leia novamente para mensagens anteriores. Não presumir completude.',
            'timestamp_note':'source_timestamp é o metadado original do WhatsApp; confirmar data e remetente antes de filtrar por horário.'}


def allow_click(context,portal,selector):
    """Autoriza somente abrir/baixar mídia/documento ou fechar o painel de leitura."""
    from operacoes.portal import native
    if not isinstance(selector,str) or len(selector)>500:raise ValueError('document_selector_invalid')
    script=r'''(()=>{const s=SELECTOR;
      const e=s.startsWith('@')?document.querySelector('[data-mref="'+s.slice(1)+'"]'):document.querySelector(s);
      if(!e)return {allowed:false,reason:'element_missing'};
      if(e.closest('footer,[contenteditable="true"],input,textarea'))return {allowed:false,reason:'composer_denied'};
      const label=(e.getAttribute('aria-label')||e.getAttribute('title')||e.innerText||e.textContent||'').trim().replace(/\s+/g,' ');
      if(/^(enviar|send|apagar|excluir|encaminhar|delete|forward|bloquear)/i.test(label))return {allowed:false,reason:'not_read_navigation'};
      const icon=e.matches('[data-icon="download"],[data-icon="document"]')||e.querySelector('[data-icon="download"],[data-icon="document"]');
      const link=e.getAttribute('href')||'';
      const documentName=/.(pdf|docx?|xlsx?|png|jpe?g|mp[34]|ogg|opus)( |$)/i.test(label);
      const download=/^(baixar|download)( |$)/i.test(label)||e.hasAttribute('download');
      const readNavigation=/^(Dados do perfil|Pesquisar|Pesquisar mensagens|Mídia, links e docs|Documentos|Mídia|Links)(?:\s+\d+)?$/i.test(label);
      const close=/^(fechar|close)$/i.test(label)&&e.closest('[data-testid="drawer-right"],[role="dialog"],[data-testid*="media-viewer"]');
      const openMedia=/abrir|imagem|foto|vídeo|video|deslizar|fim da página|down/i.test(label)||Boolean(e.querySelector('img, video, [data-icon="down"]'))||e.matches('img, video, [data-icon="down"]');
      return {allowed:!!(icon||documentName||download||close||readNavigation||link.startsWith('blob:')||openMedia),reason:'document_navigation_only'};
    })()'''.replace('SELECTOR',json.dumps(selector))
    result=json.loads(native(context,['portal','evaluate',portal,script]))
    if result.get('allowed') is not True:raise ValueError('whatsapp_document_navigation_only: '+result.get('reason',''))


def allow_search_fill(context,portal,selector):
    from operacoes.portal import native
    script=r'''(()=>{const s=SELECTOR;
      const e=s.startsWith('@')?document.querySelector('[data-mref="'+s.slice(1)+'"]'):document.querySelector(s);
      if(!e||e.closest('footer')||e.matches('[data-testid="conversation-compose-box-input"]'))return {allowed:false};
      const label=[e.getAttribute('aria-label'),e.getAttribute('placeholder'),e.getAttribute('data-testid')].filter(Boolean).join(' ');
      return {allowed:!!((e.matches('input[type="text"],[role="textbox"]'))&&(e.closest('#side')||/pesquis|search|busc/i.test(label)))};
    })()'''.replace('SELECTOR',json.dumps(selector))
    if json.loads(native(context,['portal','evaluate',portal,script])).get('allowed') is not True:
        raise ValueError('whatsapp_search_field_only')
