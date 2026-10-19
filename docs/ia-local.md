# IA local (KaviPC, RTX 3060 Ti)

## A ideia em uma frase

O Raspberry Pi faz o trabalho chato e confiável (buscar, gravar, somar). A GPU do KaviPC
entra só onde inteligência ajuda: **entender uma descrição de banco que nenhuma regra
conhece** e **transformar números em conselhos legíveis**. Se o PC estiver desligado,
o sistema continua funcionando sem ela.

## Onde a IA entra (e onde não entra)

| Tarefa | Quem faz | Por quê |
|---|---|---|
| Somar, comparar, projetar 30 dias | Python | conta tem que ser exata; LLM erra aritmética |
| Categoria por regra/histórico | Python | determinístico, auditável, de graça |
| Categoria por padrão de texto | ML (Naive Bayes no Pi) | aprende com o seu histórico, roda em milissegundos |
| Categoria do que sobrou | **LLM local** | entende "CINEMARK SHOP ARUJA" sem regra prévia |
| Texto da análise semanal | **LLM local** | redige 3 a 5 dicas a partir de fatos já calculados |
| Detectar gasto fora do padrão | Python (regra de Tukey) | estatística simples, explicável |

Conceitos que aparecem aqui:
- **Structured output**: o Ollama recebe um JSON Schema em `format` e é obrigado a responder
  nesse formato. A resposta vira dado validado (categoria precisa estar na lista do Firefly,
  confiança entre 0 e 1), não texto livre.
- **Prompt injection**: a descrição da transação vem do banco e poderia conter texto
  malicioso. Ela vai entre `<transacao>` e `</transacao>`, o modelo é instruído a nunca
  obedecer o que estiver dentro, e qualquer categoria fora da lista é descartada.
- **Limiar de confiança**: abaixo de 0,80 a IA só **sugere**; a transação fica com a tag
  `revisar` e a sugestão vai na nota.

## Ligar (o Ollama já existe)

O worker GPU do pokemon-radar já roda o Ollama em Docker no WSL do KaviPC, com o
`qwen2.5:7b-instruct` (Q4_K_M, 4,7 GB, contexto de 32k, suporte a ferramentas), ouvindo só
em 127.0.0.1 e no IP do Tailscale, com o firewall do Windows liberando só `100.64.0.0/10`.
Não precisa instalar nada novo:

1. No Portainer, stack `personal-finance`: `LLM_BASE_URL` = o mesmo valor do `LLM_LOCAL_URL`
   do pokemon-radar (ex.: `http://100.x.y.z:11434`) e `LLM_MODEL=qwen2.5:7b-instruct`.
2. Redeploy. No log aparece `IA local configurada`.
3. Teste no Console: `python -c "from app.clients.llm import *; from app.config import load_settings as L; s=L(); print(LocalLLM(s.llm_base_url, s.llm_model).online())"`

## Convivência com o pokemon-radar (sem um travar o outro)

A GPU de 8 GB é uma só. O radar tem uma agenda (`config/gpu_agenda.yaml`) com perfis
cheio, metade, mínimo e pausa, e o worker pausa sozinho com jogo aberto ou GPU quente.
O modelo de 4,7 GB só cabe junto com a visão no perfil **cheio**. Por isso:

| Situação | O que o finanças faz |
|---|---|
| Dentro da janela (todo dia 00:00 a 07:00; ter, qui e sex 07:00 a 18:30) | usa a IA normalmente (revisão das 03:00, gestor das 05:00) |
| Fora da janela, job em lote | segue sem IA (regras e ML); nada fica esperando |
| Fora da janela, pergunta no bot | usa a IA **só se o modelo já estiver carregado** (`/api/ps`), sem ocupar VRAM nova |
| Outra chamada do finanças em andamento | espera até 20 s (bot) ou 3 min (lote) e desiste |
| PC desligado ou Ollama fora | tudo segue sem IA |

`keep_alive` curto (5 min dentro da janela, 1 min fora) para devolver a VRAM rápido.
Se a agenda do radar mudar, atualize `config/llm_janelas.json`.

## "Ensinar" a IA: por que RAG e ferramentas, não fine-tuning

Treinar (fine-tune) o qwen com QLoRA na 3060 Ti é possível, mas ele decoraria números que
mudam (Selic, preços, seu saldo) e continuaria podendo inventar. Em vez disso, a cada
mensagem o gestor recebe:

1. **Conhecimento** curado em `app/knowledge/` (15 aulas: juros compostos, taxa equivalente,
   valor presente, ciclo do cartão, Pix no crédito, reserva, Selic/CDI/IPCA e ciclos, juro
   real, 50/30/20, assinaturas, metas, impulso, negócio x pessoal, parcelas, câmbio).
2. **Dados atualizados** do Banco Central com histórico (Selic de hoje x 6 e 12 meses, CDI,
   IPCA mensal e 12 meses, juro real).
3. **Seus fatos** calculados em Python (mês, parcelas futuras, custo do Pix no crédito,
   Pokémon x orçamento, metas) e o **histórico do próprio gestor** (o que sugeriu antes).
4. **Ferramentas** no bot: o modelo escolhe qual função rodar (gastos por categoria,
   simulação de parcelamento, meses até a meta...) e só explica o resultado.

Para "ensinar" algo novo, escreva ou corrija um `.md` em `app/knowledge/`.

## Como medir se está ajudando

Cada execução do sync imprime quantas transações cada etapa resolveu
(`etapas: {'regra': 12, 'historico': 5, 'ml': 3, 'llm': 2, 'revisar': 1}`) e cada
transação leva no campo de notas do Firefly quem a classificou e com que confiança.
A meta é ver `revisar` e `llm` caindo com o tempo: cada correção sua vira histórico,
e o histórico vira regra e ML, que são de graça.

## Próximos passos possíveis (fase 6)

- Revisão pelo Telegram com botões ("Isso é Lazer? ✅ / trocar").
- Perguntas livres no bot ("quanto gastei com iFood em setembro?"): o LLM só escolhe qual
  consulta rodar; a consulta e a soma continuam em Python.
- Comparar modelos (qwen vs llama) com as suas próprias correções como conjunto de teste.
