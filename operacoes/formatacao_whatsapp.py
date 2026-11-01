#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
operacoes/formatacao_whatsapp.py - Padronizador e Organizador Visual de Mensagens do WhatsApp
Garante que todas as mensagens geradas e enviadas pelo sistema (FLOW Reuniões, Stark, Operações)
sigam uma estrutura visual arejada, sem blocos densos ("textão"), com quebras de linha duplas,
destaque em negrito e tópicos organizados.
"""

import re

# Padrões comuns de saudação
SAUDACOES_REGEX = re.compile(
    r'^(ol[aá](?:,\s*pessoal)?(?:!\s*tudo\s*bem\??|\?)?|boa\s*(?:tarde|noite|dia)(?:,\s*pessoal)?(?:!|\?)?|fala,\s*[\w\s]+(?:!|\?)?|oi,\s*[\w\s]+(?:!|\?)?)',
    re.IGNORECASE
)

# Frases comuns de fechamento / Call to Action
FECHAMENTO_REGEX = re.compile(
    r'(\b(?:caso precisem|fico no aguardo|qualquer d[uú]vida|podemos marcar|como est[aá] a agenda|me avisem|combinamos assim|vamos nos falando|abra[cç]os?|atenciosamente)\b[^\n]*)',
    re.IGNORECASE
)

def organizar_mensagem_whatsapp(texto: str) -> str:
    """
    Formata e organiza o texto para exibição limpa e executiva no WhatsApp:
    1. Separa saudação inicial com quebra dupla.
    2. Coloca itens numerados (1., 2. ou 1), 2)) em linhas próprias.
    3. Separa perguntas consecutivas ou pontos de ação.
    4. Separa chamadas para ação / fechamento ao final.
    5. Normaliza quebras de linha excessivas.
    """
    if not texto or not isinstance(texto, str):
        return ""

    t = texto.strip().replace('\r\n', '\n').replace('\r', '\n')

    # 1. Separar saudação no início
    # Ex: "Olá, pessoal! Tudo bem? Passando para..." -> "Olá, pessoal! Tudo bem?\n\nPassando para..."
    m_saud = re.match(r'^((?:ol[aá]|boa\s*(?:tarde|noite|dia)|fala|oi)[^\n.!?]*[.!?](?:\s*tudo\s*bem[?!]?)?)(\s+)(.*)$', t, re.IGNORECASE | re.DOTALL)
    if m_saud:
        saudacao = m_saud.group(1).strip()
        resto = m_saud.group(3).strip()
        t = f"{saudacao}\n\n{resto}"

    # 2. Separar itens numerados que estejam na mesma linha
    # Ex: "1) Pergunta 1? 2) Pergunta 2?" -> "\n\n1) Pergunta 1?\n2) Pergunta 2?"
    t = re.sub(r'(\S)\s+([1-9][\.\)])\s+', r'\1\n\n\2 ', t)
    t = re.sub(r'([1-9][\.\)][^\n]+?)\s+([2-9][\.\)])\s+', r'\1\n\2 ', t)

    # 3. Separar itens com marcadores/bullets (• ou -) colados
    t = re.sub(r'(\S)\s+([•\-]\s+)', r'\1\n\2', t)

    # 4. Separar avisos/atenção (ex: "Além disso...", "Importante:...", "Lembrando que...")
    t = re.sub(r'(\.|\?|\!)\s+(al[eé]m disso|lembrando que|importante:|nota:|aten[cç][aã]o:)\b', r'\1\n\n\2', t, flags=re.IGNORECASE)

    # 5. Separar fechamento / Call to Action no final
    # Ex: "...no sistema. Caso precisem de qualquer apoio..." -> "...no sistema.\n\nCaso precisem de qualquer apoio..."
    t = re.sub(r'(\.|\?|\!)\s+(caso precisem|fico no aguardo|qualquer d[uú]vida|podemos marcar|me avisem|como est[aá] a (?:agenda|disponibilidade))\b', r'\1\n\n\2', t, flags=re.IGNORECASE)

    # 6. Limpar espaços antes de pontuação e quebras
    t = re.sub(r'[ \t]+\n', '\n', t)
    # Evitar mais de 2 quebras consecutivas
    t = re.sub(r'\n{3,}', '\n\n', t)

    return t.strip()

# Prompt padrão a ser injetado em LLMs para geração de rascunhos WhatsApp
DIRETRIZ_ESTRUTURA_WHATSAPP = """
DIRETRIZ DE FORMATAÇÃO E ORGANIZAÇÃO VISUAL NO WHATSAPP:
- NUNCA envie parágrafo único contínuo ou "textão" colado.
- Saudação inicial em linha isolada (ex: "Olá, pessoal! Tudo bem?" ou "Boa tarde, pessoal!").
- Pule SEMPRE uma linha em branco (\\n\\n) após a saudação.
- Contexto curto e objetivo (máximo 1 a 2 linhas).
- Se houver perguntas, tópicos ou requisitos, separe-os SEMPRE com quebra de linha e use marcadores limpos (• ou 1., 2.).
- Destaque termos-chave, nomes de tabelas, sistemas e prazos com *negrito do WhatsApp* (ex: *tabela de produtos*, *API oficial*, *BM*).
- Fechamento / Call-to-action sempre em parágrafo próprio após uma linha em branco (\\n\\n) (ex: "Fico no aguardo para darmos o próximo passo.").
"""

if __name__ == "__main__":
    t1 = "Olá, pessoal! Tudo bem? Passando para acompanhar o envio e a conexão da tabela de produtos e valores no sistema. Vocês conseguiram avançar com esse arquivo? Caso precisem de qualquer apoio para estruturar ou subir os dados, me avisem que marcamos uma reunião rápida para resolvermos juntos."
    print("--- TESTE 1 (Lídia) ---")
    print(organizar_mensagem_whatsapp(t1))

    t2 = "Boa tarde, pessoal! 1) Qual é o objetivo e formato da campanha (conteúdo da mensagem)? 2) Qual a estimativa de contatos que pretendem disparar? Além disso, lembrem-se de que será necessário conectar um cartão de crédito diretamente na BM de vocês para o consumo do Meta. Fico no aguardo dessas definições para criarmos os templates."
    print("\n--- TESTE 2 (Paycard) ---")
    print(organizar_mensagem_whatsapp(t2))
