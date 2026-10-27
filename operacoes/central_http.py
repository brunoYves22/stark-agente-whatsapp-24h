"""Rotas privadas da Central, limitadas à origem local e token da sessão."""
import hmac
import json
from urllib.parse import urlsplit,parse_qs
from operacoes import entrada


def reply(handler,status,data):
    body=json.dumps(data,ensure_ascii=False).encode()
    handler.send_response(status)
    handler.send_header('Content-Type','application/json; charset=utf-8')
    handler.send_header('Content-Length',str(len(body)))
    handler.send_header('Cache-Control','no-store')
    handler.send_header('X-Content-Type-Options','nosniff')
    handler.send_header('X-Frame-Options','SAMEORIGIN')
    handler.end_headers();handler.wfile.write(body)


def origin_ok(handler,write=False):
    host=handler.headers.get('Host','')
    allowed={'127.0.0.1:'+str(handler.server.server_port),'localhost:'+str(handler.server.server_port)}
    if host not in allowed:return False
    origin=handler.headers.get('Origin')
    if write and origin!='http://'+host:return False
    if origin and origin!='http://'+host:return False
    return handler.headers.get('Sec-Fetch-Site','same-origin') not in ('cross-site',)


def get(handler,root,token):
    url=urlsplit(handler.path)
    if url.path not in ('/api/central','/api/task'):return False
    if not origin_ok(handler):reply(handler,403,{'error':'Origem não autorizada.'});return True
    try:
        if url.path=='/api/central':
            from operacoes.receitas import catalog
            reply(handler,200,{'token':token,'entries':entrada.list_entries(root),'recipes':catalog()})
        else:
            if not hmac.compare_digest(handler.headers.get('X-Central-Token',''),token):
                reply(handler,403,{'error':'Atualize a página antes de abrir detalhes.'});return True
            reply(handler,200,entrada.detail(root,parse_qs(url.query).get('id',[''])[0]))
    except ValueError as error:reply(handler,400,{'error':str(error)})
    except Exception:reply(handler,503,{'error':'Não foi possível consultar os registros locais.'})
    return True


def post(handler,root,token):
    if not origin_ok(handler,True) or not hmac.compare_digest(handler.headers.get('X-Central-Token',''),token):
        reply(handler,403,{'error':'Sessão local mudou. Atualize a página.'});return
    if handler.headers.get('Content-Type','').split(';')[0]!='application/json':
        reply(handler,415,{'error':'Formato inválido.'});return
    try:
        size=int(handler.headers.get('Content-Length','0'))
        if not 0<size<=80000:raise ValueError('Pedido acima do limite.')
        data=json.loads(handler.rfile.read(size))
        if not isinstance(data,dict):raise ValueError('Pedido inválido.')
        if handler.path=='/api/requests':result=entrada.submit(root,data)
        elif handler.path=='/api/commands':result=entrada.command(root,data)
        else:reply(handler,404,{'error':'Rota não encontrada.'});return
        reply(handler,200,result)
    except (ValueError,TypeError) as error:reply(handler,400,{'error':str(error)[:200]})
    except Exception:reply(handler,503,{'error':'Registro não confirmado. Tente novamente com o mesmo pedido; o identificador será preservado.'})
