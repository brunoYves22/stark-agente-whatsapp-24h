"""Observações tipadas. Sem navegar, clicar, ler chats ou inferir login de um PID."""
import json
import re
import time
from pathlib import Path
from operacoes.gestao import observe,code_module

def refresh_quotas(root,free=False):
    root=Path(root)
    try:
        health=json.loads((root/'health.json').read_text(encoding='utf-8'))
        value=health.get('remaining_percent');age=time.time()-health.get('observed_at',0)
        observe(root,'execution_monitor',{'state':'available' if 0<=age<=120 and health.get('state')=='running' else 'unknown',
                 'source':'monitor de execução existente','reason':health.get('state','not_observed')},max(1,120-age) if 0<=age<=120 else 30)
        good=isinstance(value,(int,float)) and not isinstance(value,bool) and 0<=value<=100 and 0<=age<=120 and health.get('state')=='running'
        observe(root,'quota_codex',{'state':'available' if good else 'unknown','remaining_percent':value if good else None,
                'source':'monitor Codex','source_observed_at':health.get('observed_at'),'windows':health.get('quota_windows',[]),'reason':'fresh' if good else 'missing_or_stale'},max(1,120-age) if good else 60)
    except (OSError,ValueError):
        good=False
        observe(root,'quota_codex',{'state':'unknown','source':'monitor Codex','remaining_percent':None},60)
    if not good:
        try:
            quota_module=code_module(root,'quota')
            registry=json.loads((root/'registry.json').read_text(encoding='utf-8-sig'))
            limits=quota_module.read_codex_limits(Path(registry['codex']))
            if limits.get('account_fingerprint')!=registry.get('account_fingerprint'):raise ValueError('account_mismatch')
            remaining=quota_module.remaining_percent(limits)
            windows=[]
            buckets=limits.get('rateLimitsByLimitId') or {}
            bucket=buckets.get('codex',{}) if buckets else limits.get('rateLimits',{})
            for key in ('primary','secondary'):
                window=bucket.get(key) or {}
                windows.append({'window':key,**{k:window[k] for k in ('usedPercent','resetsAt','windowDurationMins') if k in window}})
            observe(root,'quota_codex',{'state':'available' if remaining is not None else 'unknown',
                  'remaining_percent':remaining,'windows':windows,'source':'Codex account/rateLimits/read'},120)
        except Exception as error:
            observe(root,'quota_codex',{'state':'unknown','remaining_percent':None,'source':'Codex account/rateLimits/read','error_type':type(error).__name__},60)
    if free:
        try:
            quota=code_module(root,'catalogo').free_quota();daily=quota.get('daily') or {};remaining=daily.get('remaining')
            good=isinstance(remaining,(int,float)) and not isinstance(remaining,bool) and remaining>=0
            safe={k:daily.get(k) for k in ('limit','used','remaining','reset_at','resets_at') if isinstance(daily.get(k),(int,float,str))}
            observe(root,'quota_free',{'state':('available' if remaining>=2 else 'exhausted') if good else 'unknown',
                    'daily':safe,'source':'OpenRouter /api/v1/key','shared_between_devices':True},300)
        except Exception as error:
            observe(root,'quota_free',{'state':'unknown','source':'OpenRouter /api/v1/key','error_type':type(error).__name__},60)

def refresh_integrations(root):
    root=Path(root)
    try:
        from operacoes.portal import native
        registry=json.loads((root/'registry.json').read_text(encoding='utf-8-sig'))
        text=native({'runtime':str(root),'registry':registry},['list'])
        # Terminais com FLOW no nome não provam a existência de portal FLOW.
        low='\n'.join(line for line in text.splitlines() if re.search(r'portal|browser|https?://|🌐',line,re.I)).casefold()
        observe(root,'maestri',{'state':'available','source':'CLI list'},30)
        for key,needle in (('whatsapp','whatsapp'),('youtrack','youtrack'),('flow','flow'),('sopha','sopha')):
            # Título presente é apenas inventário; nunca prova autenticação.
            observe(root,'integration_'+key,{'state':'unknown','portal_listed':needle in low,
                    'reason':'login_not_verified' if needle in low else 'portal_not_identified','source':'inventário Maestri'},30)
    except Exception as error:
        observe(root,'maestri',{'state':'unknown','reason':'closed_or_unavailable','error_type':type(error).__name__},30)

def record_integration(root,key,state,evidence_reference):
    if key not in ('whatsapp','youtrack','flow','sopha') or state not in ('available','disconnected','unavailable','busy','unknown'):
        raise ValueError('integration_observation_invalid')
    if not evidence_reference or len(evidence_reference)>200:raise ValueError('evidence_reference_required')
    # Esta é observação relatada, não verificação independente do backend.
    observe(root,'reported_'+key,{'state':state,'source':'agente; consultar evidência','evidence_reference':evidence_reference},120)
    return {'recorded':True,'verified_by_backend':False}
