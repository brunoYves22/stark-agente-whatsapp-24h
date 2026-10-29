"""Adaptador restrito ao compositor WhatsApp; nunca clica em Enviar."""
import json
import time

SELECTOR='[data-testid="conversation-compose-box-input"][contenteditable="true"]'


def read(client):
    # JSON preserva espaços/quebras que stdout.strip do transporte retiraria.
    script=r'''(()=>{const e=document.querySelector(SELECTOR);
      if(!e)return {ok:false,reason:'editor_missing'};
      const textOf=n=>{
        if(n.nodeType===3)return n.nodeValue;
        if(n.nodeName==='BR')return '\n';
        if(n.nodeName==='IMG')return n.getAttribute('alt')||'';
        return Array.from(n.childNodes).map(textOf).join('');
      };
      const blocks=Array.from(e.children);
      const value=blocks.length&&blocks.every(n=>n.nodeName==='P')
        ? blocks.map(n=>n.childNodes.length===1&&n.firstChild.nodeName==='BR'?'':textOf(n)).join('\n')
        : textOf(e);
      return {ok:true,text:value};})()'''.replace('SELECTOR',json.dumps(SELECTOR))
    result=json.loads(client.call('evaluate',script))
    if result.get('ok') is not True or not isinstance(result.get('text'),str):
        raise ValueError('composer_missing: nenhuma mensagem enviada')
    return result['text'].replace('\r\n','\n')


def set_text(client,text):
    if not isinstance(text,str):raise ValueError('composer_text_invalid')
    if '\r' in text:raise ValueError('composer_line_endings_invalid: usar quebras LF')
    if read(client)==text:return
    client.call('focus',SELECTOR)
    # selectAll explícito atualiza a seleção do editor; ctrl+a sintético é ignorado em alguns estados.
    client.call('selectall',SELECTOR)
    client.call('key','Backspace')
    for _ in range(10):
        if read(client)=='':break
        time.sleep(.15)
    else:raise ValueError('composer_clear_not_confirmed: nenhuma mensagem enviada')
    if text:
        # Um único evento nativo do editor. Não disparar beforeinput sintético antes:
        # Lexical pode inserir por esse evento e novamente pelo comando.
        script=r'''(()=>{const e=document.querySelector(SELECTOR),text=TEXT;
          if(!e||e.textContent!=='')return {ok:false,reason:'editor_not_empty'};
          e.focus();
          const editor = e.__lexicalEditor;
          let handled = false;
          if (editor && editor._commands) {
            for (const k of editor._commands.keys()) {
              if ((k.type || k.name || String(k)) === 'PASTE_COMMAND') {
                const dt = new DataTransfer();
                dt.setData('text/plain', text);
                const evt = new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true });
                editor.dispatchCommand(k, evt);
                handled = true;
                break;
              }
            }
          }
          if (!handled) {
            document.execCommand('insertText', false, text);
          }
          return {ok:true};})()'''.replace('SELECTOR',json.dumps(SELECTOR)).replace('TEXT',json.dumps(text,ensure_ascii=False))
        try:client.call('evaluate',script)
        except ValueError:
            # Timeout/erro não permite repetir a inserção. Ler o resultado antes.
            pass
    curr = ''
    for _ in range(12):
        curr = read(client)
        if curr == text or ''.join(curr.split()).replace('\u200b','').replace('\ufeff','').replace('\u00a0','') == ''.join(text.split()).replace('\u200b','').replace('\ufeff','').replace('\u00a0',''):
            return
        time.sleep(.15)
    from pathlib import Path
    try:
        Path('C:/Users/bruno/Desktop/FLOW_TESTES_SYNC/outputs/downloads/composer_diff.json').write_text(
            json.dumps({'read': curr, 'expected': text}, ensure_ascii=False), encoding='utf-8')
    except Exception: pass
    raise ValueError('composer_text_mismatch: nenhuma mensagem enviada')
