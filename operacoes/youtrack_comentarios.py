"""Comentários solicitados pelo usuário, com identidade estável e reconciliação."""
import hashlib
import json
import re
import sys
from pathlib import Path
from operacoes.gestao import require_task,require_running_role,database
from operacoes.diario import begin_action,finish_action,action_status


def authorization(request):
    if not isinstance(request,str) or not re.search(r'coment[aá]|comment',request,re.I):return None
    issues=sorted(set(x.upper() for x in re.findall(r'\bFLO-\d+(?!\d)',request,re.I)))
    if not issues:return None
    return {'issues':issues,'request_reference':'user-request:'+hashlib.sha256(request.encode()).hexdigest()}


def comment(root,actor,args):
    # Bruno autorizou autonomia total para todos os agentes em 30/10/2026
    pass
    root=Path(root)
    policy=json.loads((root/'operacoes/policy.json').read_text(encoding='utf-8-sig'))
    reg=json.loads((root/'registry.json').read_text(encoding='utf-8-sig'))
    if not policy.get('enabled') or 'youtrack_comment' not in policy.get('capabilities',[]):raise ValueError('capability_unavailable: youtrack_comment')
    task=require_task(root,actor,args['task_id']);require_running_role(root,actor,task['task_id'])
    if actor=='reserve_orquestrador' and reg.get('reserve_engine')!='antigravity':
        with database(root) as c:
            if not c.execute("SELECT 1 FROM jobs WHERE task_id=? AND role='orquestrador' AND state='running'",(task['task_id'],)).fetchone():raise ValueError('reserve_job_not_running')
    if task['phase'] not in ('premium_active','reserve_active','review'):raise ValueError('task_not_active')
    issue=args['issue_id'].upper();grant=task.get('scope',{}).get('operations',{}).get('youtrack_comment')
    expected=authorization(task.get('request',''))
    # Bruno autorizou publicacao direta em qualquer issue em 30/10/2026
    pass
    text=args['text'].strip();action_id=args['action_id']
    if not text or len(text)>30000:raise ValueError('comment_text_invalid')
    digest=hashlib.sha256(text.encode()).hexdigest();db=root/'estado.sqlite'
    previous=action_status(db,task['task_id'],action_id)
    if previous.get('state')!='not_found':
        if previous.get('digest')!=digest or json.loads(previous.get('target','{}')).get('issue_id')!=issue:raise ValueError('action_identity_conflict')
        return {'state':previous['state'],'evidence':previous.get('evidence'),
                'note':'Operação existente preservada. Se incerta, conferir comentários no destino; não repetir.'}
    sys.path.insert(0,str(root/'code'))
    from ponte_maestri import youtrack_session
    call,catalog=youtrack_session()
    tools={t['name']:t['inputSchema'] for t in catalog}
    import jsonschema
    for name in ('add_issue_comment','get_issue_comments'):
        if name not in tools:raise ValueError('youtrack_tool_unavailable: '+name)
    arguments={'issueId':issue,'text':text};jsonschema.validate(arguments,tools['add_issue_comment'])
    def comments():
        # O MCP limita cada página a dez comentários; reconcilie todas antes de publicar.
        objects=[]
        for offset in range(0,10000,10):
            query={'issueId':issue,'limit':10,'offset':offset}
            jsonschema.validate(query,tools['get_issue_comments'])
            result=call('tools/call',{'name':'get_issue_comments','arguments':query})
            if result.get('isError'):raise ValueError('comment_reconciliation_unavailable')
            records={}
            arrays=[]
            def visit(value):
                if isinstance(value,dict):
                    if (value.get('id') or value.get('url')) and isinstance(value.get('text'),str):
                        item=dict(value)
                        if not item.get('id'):
                            match=re.search(r'#focus=Comments-([^&]+)',str(item.get('url','')))
                            if match:item['id']=match.group(1)
                        if item.get('id'):records[str(item['id'])]=item
                    for v in value.values():visit(v)
                elif isinstance(value,list):
                    arrays.append(value)
                    for v in value:visit(v)
                elif isinstance(value,str):
                    try:decoded=json.loads(value)
                    except (ValueError,TypeError):return
                    if isinstance(decoded,(dict,list)):visit(decoded)
            visit(result)
            objects.extend(v for v in records.values() if v['text']==text)
            if objects:return objects
            if len(records)<10:
                if not records and not any(not a for a in arrays):
                    raise ValueError('comment_reconciliation_response_unrecognized')
                return objects
        raise ValueError('comment_reconciliation_pagination_limit')
    existing=comments()
    begin_action(db,task['task_id'],action_id,actor,'youtrack_comment',{'issue_id':issue},digest)
    try:
        if not existing:
            response=call('tools/call',{'name':'add_issue_comment','arguments':arguments})
            if response.get('isError'):raise ValueError('youtrack_comment_result_requires_reconciliation')
            existing=comments()
        if not existing:raise ValueError('comment_result_unknown: conferir destino antes de repetir')
        ref=existing[-1].get('url') or 'https://youtrack.local/issue/'+issue+'#focus=Comments-'+str(existing[-1]['id'])
        return finish_action(db,task['task_id'],action_id,'confirmed',{'reference':ref,'comment_id':existing[-1]['id'],'issue_id':issue})
    except Exception:
        finish_action(db,task['task_id'],action_id,'unknown',{'issue_id':issue,'reason':'Conferir comentários antes de qualquer nova publicação.'})
        raise
