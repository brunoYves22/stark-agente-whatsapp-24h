# Stark // Agente Autônomo 24h no WhatsApp & Orquestrador Multi-Agente FLOW

![Status](https://img.shields.io/badge/Sentinela-Ativo_24%2F7_(Porta_58377)-10b981?style=for-the-badge)
![Voz](https://img.shields.io/badge/Voz_Local-Faster--Whisper_(CPU%2Fint8)-38bdf8?style=for-the-badge)
![Agentes](https://img.shields.io/badge/Esquadrão-Stark_%7C_Vision_%7C_Parker_%7C_Strange-a855f7?style=for-the-badge)
![Integrações](https://img.shields.io/badge/Ponte-WhatsApp_%2B_YouTrack_%2B_Obsidian-f59e0b?style=for-the-badge)

**Stark** é um agente autônomo de operação 24/7 conectado em tempo real ao **WhatsApp**, permitindo comandar toda a rotina de engenharia de software, triagem de atendimento, abertura de cards no **YouTrack** e documentação no **Obsidian** diretamente pelo celular — por **áudio ou texto** — com execução 100% local no notebook.

---

## Visão Geral & Arquitetura em Produção

### 1. Comando por Voz ou Texto no Celular -> Execução Local no Notebook
![Arquitetura Stark 24h](docs/assets/01-arquitetura-stark-24h.png)

### 2. Motor Sentinela 24/7 + Transcrição de Voz Offline (`Faster-Whisper int8`)
![Sentinela & Whisper Local](docs/assets/02-sentinela-whisper-local.png)

### 3. Injeção Direta no Terminal via IPC & Coordenação do Esquadrão de Agentes
![Injeção IPC & Esquadrão](docs/assets/03-injecao-ipc-esquadrao.png)

### 4. Ponte com o FLOW Reuniões: Aprovação Multicanal em 1 Comando (`@Stark aprova tudo`)
![Aprovação em 1 Comando](docs/assets/04-aprovacao-1-comando.png)

### 5. Resultados e Benefícios Reais na Operação
![Benefícios Reais](docs/assets/05-beneficios-operacionais.png)

---

## Fluxo de Funcionamento

```mermaid
sequenceDiagram
    participant Celular as Bruno (WhatsApp Mobile)
    participant Sentinela as Sentinela 24h (Porta 58377)
    participant Whisper as Faster-Whisper (CPU/int8)
    participant Stark as Terminal Stark (Orquestrador)
    participant Squad as Esquadrão (Vision / Parker / Strange)
    participant Canais as Grupos Clientes + YouTrack + Obsidian

    Celular->>Sentinela: Envia Áudio ou "@Stark ..." no grupo MAESTRI
    Sentinela->>Sentinela: Filtro Anti-Eco + Debounce Hash (seen_ids)
    Sentinela->>Whisper: Baixa e transcreve áudio localmente (Custo R$ 0)
    Whisper-->>Sentinela: Texto transcrito em < 1.2s
    Sentinela->>Stark: Injeta comando via Ponte Maestri IPC
    Stark->>Squad: Delega análise (YouTrack), testes (Portais) e validação
    Squad-->>Stark: Evidências validadas + Rascunhos prontos
    Celular->>Sentinela: "@Stark aprova tudo"
    Sentinela->>Canais: Dispara grupos WhatsApp + Cria Card YouTrack + Salva Obsidian
```

---

## Principais Componentes

| Módulo | Descrição |
| :--- | :--- |
| `operacoes/sentinela_whatsapp_stark.py` | Daemon 24/7 que monitora o grupo operacional na porta `58377`, com *keep-alive* automático, proteção estrita anti-loop e *debounce* persistente por hash. |
| `operacoes/audio_whatsapp.py` | Captura áudios recebidos no WhatsApp e executa transcrição 100% local via `faster_whisper` (`int8` na CPU) sem chamadas pagas de API. |
| `operacoes/executar_proposta.py` | Conecta as tarefas extraídas pelo `FLOW Reuniões` ao WhatsApp: ao receber `@Stark aprova tudo`, envia mensagens nos grupos de clientes, cria issues no YouTrack e registra o log diário no Obsidian. |
| `operacoes/entrega_terminal.py` | Ponte IPC que injeta mensagens do WhatsApp diretamente no terminal ativo do agente Orquestrador. |
| `operacoes/painel_operacional.html` | Central da Equipe (`http://127.0.0.1:58375`) para acompanhamento visual dos 8 agentes (Time Principal + Time Reserva 24h), filas e integrações. |
| `instructions/` | Contratos operacionais dos 4 papéis do esquadrão: **Stark** (Orquestrador), **Vision** (Analista YouTrack), **Parker** (Testador FLOW) e **Strange** (Validador). |

---

## Benefícios Reais na Prática

1. **Mobilidade Total (100% Mobile):** Comande o notebook, aprove demandas e acione clientes enviando um áudio rápido no trânsito.
2. **Fim dos Áudios Longos sem Triagem (`-85% Tempo`):** Áudios extensos de clientes são transcritos e transformados em checklists objetivos em segundos.
3. **Zero Retrabalho Manual:** Fim do copia-e-cola entre ata de reunião, grupos de WhatsApp, cards do YouTrack e notas no Obsidian.
4. **Custo Zero de API de Voz & Privacidade Local:** Transcrição rodando inteiramente na CPU local com isolamento total de dados sensíveis.
