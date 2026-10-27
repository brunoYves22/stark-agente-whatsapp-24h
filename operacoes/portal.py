"""Transporte Maestri tipado, sem comandos raw nem JavaScript."""
from contextlib import contextmanager
import hashlib,json,os,re,subprocess,time,urllib.parse
from pathlib import Path

@contextmanager
def portal_lock(context,name):
    import msvcrt
    p=Path(context['runtime'])/'operacoes'/('portal-'+hashlib.sha256(name.encode()).hexdigest()[:16]+'.lock')
    p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('a+b') as f:
        f.seek(0);f.write(b'0');f.flush();f.seek(0)
        try:msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:raise ValueError('portal_busy: aguarde outra operação terminar') from None
        try:yield
        finally:f.seek(0);msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)

def native(context,argv):
    registry=context['registry'];env=dict(os.environ);env.pop('FLOW_YOUTRACK_TOKEN',None)
    pipes=[p for p in os.listdir('\\\\.\\pipe\\') if p.startswith('maestri-')]
    if len(pipes)!=1:raise ValueError('maestri_unavailable_or_ambiguous')
    env['MAESTRI_PIPE']='\\\\.\\pipe\\'+pipes[0]
    # Recursos conectados ao coordenador são compartilhados pelo backend, não por novas sessões/cookies.
    env['MAESTRI_TERMINAL_ID']=registry['primary_terminal']
    r=subprocess.run([registry['maestri_path'],*argv],capture_output=True,text=True,encoding='utf-8',errors='replace',
                     timeout=25,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    out=r.stdout.strip()
    if r.returncode or re.match(r'(?i)(?:maestri:|error:|\w+ failed)',out):raise ValueError('portal_failed: '+(out or r.stderr)[:250])
    return out

def resolve_portal_by_url(context, url_keyword='web.whatsapp.com', fallback='WhatsApp — Atendimento FLOW #2'):
    try:
        out = native(context, ['list'])
        for line in out.splitlines():
            if url_keyword.lower() in line.lower() and 'name: "' in line:
                m = re.search(r'name:\s*"([^"]+)"', line)
                if m:
                    return m.group(1)
    except Exception:
        pass
    return fallback

def safe_url(url):
    p=urllib.parse.urlsplit(url)
    if p.scheme not in ('http','https') or not p.hostname or p.username or p.password:raise ValueError('browser_url_invalid')
    return url

def portal_call(context,portal,operation,arguments):
    if operation=='list':return {'text':native(context,['list'])}
    if operation=='create':
        url=safe_url(arguments['url'])
        if 'whatsapp.com' in urllib.parse.urlsplit(url).netloc.casefold():raise ValueError('use_dedicated_whatsapp_tools')
        with portal_lock(context,portal):return {'text':native(context,['portal','create',url,portal])}
    allowed={'info','snapshot','navigate','click','fill','type','key','select','scroll','text','screenshot'}
    if operation not in allowed:raise ValueError('browser_operation_not_allowed')
    if operation=='navigate' and 'web.whatsapp.com' in urllib.parse.urlsplit(arguments['url']).netloc.casefold():
        raise ValueError('use_dedicated_whatsapp_tools')
    args=[]
    if operation=='navigate':args=[safe_url(arguments['url'])]
    if operation in ('click','text'):args=[arguments['selector']]
    if operation=='fill':args=[arguments['selector'],arguments['text']]
    if operation=='type':
        raise ValueError('use_fill_or_explicit_form_tools: type não garante substituição')
    if operation=='key':
        if arguments['key'] not in ('Enter','Tab','Escape','Backspace','ArrowUp','ArrowDown','ArrowLeft','ArrowRight','Space','ctrl+a','ctrl+c','ctrl+v'):
            raise ValueError('key_not_allowed')
        args=[arguments['key']]
    if operation=='select':args=[arguments['selector'],arguments['option']]
    if operation=='scroll':
        if arguments['direction'] not in ('up','down','left','right'):raise ValueError('scroll_direction_invalid')
        args=[arguments['direction'],str(min(max(int(arguments.get('pixels',300)),1),2000))]
        if arguments.get('selector'):args.append(arguments['selector'])
    with portal_lock(context,portal):
        current_info=native(context,['portal','info',portal])
        if portal=='WhatsApp — Atendimento FLOW' or 'web.whatsapp.com' in current_info:
            from operacoes.autoridade import authorize
            authorize(context['policy'],context['task'],context['actor'],'whatsapp_read',{})
            if operation=='click':
                from operacoes.pesquisa_whatsapp import allow_click
                allow_click(context,portal,arguments['selector'])
            elif operation=='fill':
                if len(arguments['text'])>200:raise ValueError('search_query_too_long')
                from operacoes.pesquisa_whatsapp import allow_search_fill
                allow_search_fill(context,portal,arguments['selector'])
            elif operation=='key' and arguments['key']=='Escape':pass
            elif operation not in ('info','snapshot','scroll','text','screenshot'):
                raise ValueError('whatsapp_read_navigation_only: envios usam flow_whatsapp_send')
        out=native(context,['portal',operation,portal,*args])
    # Password inputs e tokens nunca são evidência textual necessária.
    out=re.sub(r'(?im)^.*type=password.*$','[campo de senha omitido]',out)
    return {'text':out[:60000]}
