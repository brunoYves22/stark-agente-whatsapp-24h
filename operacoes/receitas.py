"""Atalhos editáveis de pedidos. Selecionar receita nunca concede autorização."""

RECIPES = {
    'whatsapp_youtrack': {
        'name': 'WhatsApp → YouTrack',
        'request': 'Analise as mensagens de [contato ou grupo] no WhatsApp, no período [período], incluindo os áudios. Separe bugs, melhorias e dúvidas; registre referências e procure duplicidades no YouTrack. Prepare os relatos para minha revisão.',
        'acceptance': 'Relatos com origem, evidências, resultado esperado e duplicidades; trechos incertos identificados. Publicação somente se eu solicitar.',
    },
    'sopha_flow': {
        'name': 'Sopha → FLOW',
        'request': 'Transfira do Sopha para o FLOW o cliente [nome], para a loja [destino]. Confira os dados de origem, correspondências e duplicidades. Execute somente este cliente e informe o registro no destino.',
        'acceptance': 'Cliente identificado, correspondência dos dados conferida e resultado confirmado no FLOW; nenhum cliente além do solicitado.',
    },
    'testes_flow': {
        'name': 'Testar uma função do FLOW',
        'request': 'Verifique a função [função] no FLOW, no ambiente [ambiente], usando [dados autorizados]. Prepare e execute os cenários do escopo, registre esperado/obtido e separe defeitos confirmados de hipóteses.',
        'acceptance': 'Cenários e evidências por resultado, ambiente identificado, defeitos reproduzidos e limitações explícitas.',
    },
    'reuniao_tarefas': {
        'name': 'Reunião → tarefas',
        'request': 'Organize a reunião [ID ou arquivo local] em resumo, decisões, pontos de atenção e tarefas. Reutilize o processamento local existente e indique trechos incertos. Prepare propostas para o YouTrack, sem publicar ainda.',
        'acceptance': 'Memória local com referência da reunião e propostas revisáveis. Nenhuma informação inventada a partir de áudio incerto.',
    },
    'whatsapp_send': {
        'name': 'Enviar mensagem no WhatsApp',
        'request': 'Envie para [nome do contato] no WhatsApp a mensagem: [texto]. Confira o contato encontrado; se houver mais de um possível destinatário, pergunte qual é. Informe a confirmação do envio.',
        'acceptance': 'Uma única mensagem ao contato solicitado, com confirmação da ferramenta; resultado incerto deve ser conferido antes de repetir.',
    },
}


def catalog():
    return [{'id': key, **value} for key, value in RECIPES.items()]


def validate(key, request):
    if key and key not in RECIPES:
        raise ValueError('Procedimento desconhecido.')
    if key and __import__('re').search(r'\[[^\]\n]{1,100}\]', request):
        raise ValueError('Preencha os campos entre colchetes do procedimento antes de enviar.')
    return key
