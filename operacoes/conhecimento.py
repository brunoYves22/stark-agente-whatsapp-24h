"""Projeção Markdown local, com IDs estáveis e sem conversas/dados de clientes."""
import json
import os
import re
from pathlib import Path
from operacoes.gestao import overview
from operacoes.fluxos import CATALOG

def atomic(path,text):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists() and path.read_text(encoding='utf-8')==text:return
    tmp=path.with_name(path.name+'.'+str(os.getpid())+'.tmp')
    tmp.write_text(text,encoding='utf-8');os.replace(tmp,path)

def export(root):
    root=Path(root);view=overview(root);folder=root/'conhecimento'
    connected=(folder/'.obsidian/app.json').is_file()
    registry=json.loads((root/'registry.json').read_text(encoding='utf-8-sig')) if (root/'registry.json').exists() else {}
    teams='Principal Codex e equipe Antigravity (Stark, Vision, Parker e Strange).' if registry.get('reserve_engine')=='antigravity' else 'Dois times: principal e reserva (Cais, Prisma, Trilho e Crivo).'
    atomic(folder/'INDICE.md','''---
id: flow-conhecimento-local
schema_version: 1
authority: projection
---
# Conhecimento da equipe FLOW

Estado de execução: SQLite privado. Estas notas não concedem autorização nem disparam ações.

- [Contexto permanente](contexto/PROJETO.md)
- [Contexto compartilhado editável](compartilhado/CONTEXTO.md)
- [Como usar esta memória](compartilhado/COMO-USAR.md)
- [Entregas dos agentes](entregas/INDICE.md)
- [Reuniões](reunioes/INDICE.md)
- [Andamento atual](ANDAMENTO.md)
- [Histórico de entregas](HISTORICO.md)
- [Decisões locais](decisoes/20261018-operacao-local.md)
- [Procedimentos](procedimentos/INDICE.md)
- Tarefas: notas em `tarefas/`, pelo mesmo ID do banco.

Este diretório é a memória comum das equipes. O Obsidian abre estes mesmos arquivos, sem cópia intermediária.
Notas geradas têm nomes fixos: escreva notas manuais em arquivos diferentes para preservá-las.
''')
    atomic(folder/'contexto/PROJETO.md','''---
id: flow-contexto-projeto
schema_version: 1
authority: context
---
# Projeto FLOW

Equipe de agentes para apoiar atendimento, análise, testes, validação e tarefas operacionais autorizadas no FLOW.
'''+teams+'''
Instruções permanentes ficam nos perfis; permissões efetivas no backend e no escopo da tarefa.
Relatos individuais, como os de Uly, pertencem à respectiva tarefa, não a este contexto permanente.
Esta evolução está instalada somente no ZENBOOK. BATCAVERNA aguarda avaliação de Bruno.
''')
    atomic(folder/'decisoes/20261018-operacao-local.md','''# Decisão — operação local

- Implementação somente no notebook, por escolha de Bruno em 18/10/2026.
- Nenhuma alteração desta evolução é publicada na sincronização.
- Fontes antigas sincronizadas são históricas; instruções operacionais locais descrevem a evolução instalada.
- Grants e escopo continuam obrigatórios; nenhuma nota pode expandi-los.
- Recuperação não repete efeito confirmado ou incerto.
- Memória comum acessível pelo Obsidian local, sem sincronização entre máquinas nesta etapa.
''')
    procedures=['# Procedimentos\n']
    for key,flow in CATALOG.items():
        atomic(folder/'procedimentos'/(key+'.md'),'---\nid: flow-procedimento-'+key+'\nschema_version: 1\n---\n# '+flow['name']+'\n\n'+'\n'.join(str(i+1)+'. '+s for i,s in enumerate(flow['steps']))+'\n\nEvidências: '+', '.join(flow['evidence'])+'.\n')
        procedures.append('- ['+flow['name']+']('+key+'.md)')
    atomic(folder/'procedimentos/INDICE.md','\n'.join(procedures)+'\n')
    current=['# Andamento FLOW — ZENBOOK\n','Gerado a partir do banco local. Sem conteúdo de cliente.\n']
    history=['# Histórico de tarefas concluídas\n','Projeção do banco; conclusão declarada deve ser acompanhada das evidências.\n']
    for task in view['tasks']:
        tid=task['id']
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',tid):continue
        text='---\nid: '+tid+'\nschema_version: 1\nauthority: projection\n---\n# '+tid+'\n\n'
        text+='- Equipe: '+str(task['team'])+'\n- Dispositivo: '+str(task['device'])+'\n- Estado: '+task['state']+'\n'
        text+='- Evidências registradas: '+str(task['evidence_count'])+'\n- Ações confirmadas: '+str(task['confirmed'])+'\n- Ações a reconciliar: '+str(task['uncertain'])+'\n'
        text+='- Próxima etapa: '+task['next_step']+'\n\nDetalhes privados devem ser consultados pela ferramenta autorizada de tarefa.\n'
        atomic(folder/'tarefas'/(tid+'.md'),text)
        if task['phase']!='completed':current.append('- ['+tid+'](tarefas/'+tid+'.md): '+task['state'])
        else:history.append('- ['+tid+'](tarefas/'+tid+'.md): '+str(task['confirmed'])+' ações confirmadas')
    atomic(folder/'ANDAMENTO.md','\n'.join(current)+'\n')
    atomic(folder/'HISTORICO.md','\n'.join(history)+'\n')
    for sub,title in (('entregas','Entregas dos agentes'),('reunioes','Reuniões')):
        entries=['# '+title+'\n','Índice gerado. Edite os registros individuais, não este índice.\n']
        for p in sorted((folder/sub).glob('*.md')):
            if p.name!='INDICE.md' and p.resolve().is_relative_to(folder.resolve()):
                entries.append('- [['+sub+'/'+p.stem+']]')
        atomic(folder/sub/'INDICE.md','\n'.join(entries)+'\n')
    return {'directory':str(folder),'task_notes':len(view['tasks']),'contains_customer_content':False,'obsidian_connected':connected}

def read(root,relative_path):
    folder=(Path(root)/'conhecimento').resolve()
    path=(folder/relative_path).resolve()
    if not path.is_relative_to(folder) or path.suffix!='.md' or path.is_symlink():raise ValueError('knowledge_path_invalid')
    allowed={'INDICE.md','ANDAMENTO.md','HISTORICO.md','contexto/PROJETO.md','decisoes/20261018-operacao-local.md','procedimentos/INDICE.md'}
    allowed.update('procedimentos/'+key+'.md' for key in CATALOG)
    allowed.update('tarefas/'+t['id']+'.md' for t in overview(root)['tasks'] if re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',t['id']))
    relative=path.relative_to(folder)
    shared=len(relative.parts)>1 and relative.parts[0] in ('compartilhado','entregas','reunioes')
    if relative.as_posix() not in allowed and not shared:raise ValueError('knowledge_note_not_allowed')
    return {'path':relative_path,'content':path.read_text(encoding='utf-8')[:24000],'authority':'projection_not_permission'}
