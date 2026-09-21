# Pluggy → Firefly III: sincronização automática + relatório diário

Automatiza duas coisas que hoje dependem de você lembrar de fazer manualmente:

1. **Sincronizar transações reais dos seus bancos/cartões para o Firefly III**, via Open Finance (Meu Pluggy, gratuito para uso pessoal).
2. **Enviar todo dia, por Telegram, quanto você pode gastar e quanto deve guardar hoje**, com base no saldo real e nas contas que ainda vão vencer no mês.

Feito para rodar no seu Raspberry Pi como um container Docker (via Portainer, o app do Umbrel App Store indicado pelo próprio umbrelOS pra esse tipo de uso) — não como script solto no terminal de debug do Umbrel, que não persiste entre atualizações do sistema.

## O que é seu, o que é meu

**Sua parte (só você pode fazer, ninguém mais deve logar nessas contas):**

1. Criar uma conta gratuita em https://meu.pluggy.ai e conectar seus bancos/cartões reais (Itaú, Bradesco, Mercado Pago).
   - Isso é Open Finance de verdade: dados só saem dos bancos com sua autorização, e você pode revogar quando quiser.
2. Criar uma aplicação em https://dashboard.pluggy.ai e pegar o `CLIENT_ID` e `CLIENT_SECRET`.
3. No Firefly III, gerar um Personal Access Token (Opções → Perfil → OAuth → Personal Access Tokens) para o script poder criar transações.
4. Preencher o arquivo `.env` (veja `.env.example`) com essas chaves, e o `account_mapping.json` ligando cada conta da Pluggy à conta correspondente no Firefly.

**Minha parte (já entregue neste pacote):**

- `pluggy_client.py` e `firefly_client.py`: as integrações com as duas APIs.
- `sync.py`: busca transações novas na Pluggy e cria no Firefly, sem duplicar (usa o id da transação da Pluggy como `external_id`).
- `daily_report.py`: calcula quanto sobra pra gastar hoje e quanto guardar hoje, e manda a mensagem pro seu Telegram.
- `category_rules.example.json`: ponto de partida pra mapear descrição da transação → categoria, já com suas assinaturas atuais.

## Por que "Meu Pluggy" e não a API comercial da Pluggy

A API comercial da Pluggy custa a partir de R$2.500/mês (plano pensado pra empresas). Pra uso pessoal, a própria Pluggy oferece o **Meu Pluggy** (meu.pluggy.ai): você conecta suas contas lá, gera credenciais no dashboard, e usa a API normalmente, sem custo. É o mesmo motor de Open Finance, só que licenciado para uso individual.

## Como testar antes de containerizar (num computador, não no terminal de debug do Umbrel)

```bash
pip install -r requirements.txt
cp .env.example .env          # preencha com suas chaves
cp account_mapping.example.json account_mapping.json   # ajuste os ids
cp category_rules.example.json category_rules.json     # ajuste as regras

python sync.py --list-accounts   # lista as contas da Pluggy, pra montar o account_mapping.json
python sync.py                   # roda uma sincronização
python daily_report.py           # gera e envia o relatório do dia
```

Vale rodar assim primeiro (no seu computador, ou até no próprio terminal de debug do Umbrel só pra esse teste pontual, já que nada aqui precisa persistir) pra conferir que as transações estão entrando certas no Firefly antes de deixar rodando sozinho.

## Como colocar pra rodar de verdade: Docker via Portainer

Testei a tela real de criação de stack no seu Portainer (CE 2.45.1): ela só aceita colar/subir o `docker-compose.yml` em si — **não** dá pra subir o `Dockerfile` e os `.py` junto por ali. Pra buildar a imagem com todos os arquivos, o jeito certo é o método **Repository**: o Portainer clona um repositório Git e usa tudo que está nele como contexto do build. Como você já tem conta no GitHub, o caminho é:

### 1. Suba o código pro GitHub (sem o `.env`)

1. Crie um repositório novo no GitHub (pode ser público ou privado — não tem problema nenhum ser público, porque **nenhum segredo vai pro repositório**: as chaves ficam só dentro do Portainer, no passo 3). Se preferir privado, funciona igual, só que no passo 2 o Portainer vai pedir um token de acesso (Personal Access Token) do GitHub pra poder clonar.
2. Na página do repositório, **Add file → Upload files**, e arraste todos os arquivos desta pasta **exceto o `.env`** (esse não deve ir pro GitHub de jeito nenhum — o `.gitignore` que já vai no pacote lembra disso, mas como o upload é manual, é você quem precisa deixar ele de fora).
3. Commit.

### 2. Crie a stack no Portainer apontando pro repositório

1. Portainer → **primary** (clica no card do ambiente) → **Stacks** → **Add stack**.
2. Nome: `pluggy-firefly-sync`.
3. Build method: **Repository**.
4. Repository URL: a URL do repositório que você criou (ex: `https://github.com/seu-usuario/pluggy-firefly-sync`).
5. Se o repositório for privado, marca "Authentication" e cola um Personal Access Token do GitHub (Settings → Developer settings → Personal access tokens, com permissão só de leitura no repositório).
6. Compose path: `docker-compose.yml` (já vem certo por padrão).

### 3. Cadastre as chaves na própria tela do Portainer (não no GitHub)

Na seção **Environment variables** da mesma tela de criação da stack, clica em "Advanced mode" e cola o conteúdo do arquivo `.env` que te mandei separado (`env-completo.txt`) — linha por linha, no formato `CHAVE=valor`. O `docker-compose.yml` deste pacote já está preparado pra ler essas variáveis daí (não depende de nenhum arquivo `.env` dentro do repositório).

### 4. Deploy

Clica em **Deploy the stack**. O Portainer clona o repositório, builda a imagem (Python + dependências + cron dentro do container) e deixa rodando.

Pra atualizar depois (se eu mandar uma versão nova do código), é só subir os arquivos novos pro GitHub e, na tela da stack no Portainer, clicar em **Pull and redeploy**.

### 5. Logs

Containers → `pluggy-firefly-sync` → Logs, direto na interface do Portainer — é lá que aparecem os prints do `sync.py` e do `daily_report.py`.

Um ponto que não tenho como validar sem ver o seu ambiente: o `docker-compose.yml` está configurado com `network_mode: host`, pra `http://umbrel.local:30009` resolver igual resolveria rodando direto no Pi. Se o container não conseguir falar com o Firefly, provavelmente é questão de rede Docker — me manda o erro que a gente ajusta (geralmente é trocar pra `bridge` e apontar `FIREFLY_BASE_URL` pro nome do container do Firefly dentro da rede do Docker, em vez do `umbrel.local`).

## Limitações desta primeira versão (deixei sinalizado no código com `# TODO`)

- **Categorização automática é baseada em palavra-chave** (`category_rules.json`). Vai errar em transações novas até você ir ajustando as regras. É mais rápido que digitar tudo à mão, mas não é perfeito de cara.
- **"Quanto posso gastar hoje" ainda é uma aproximação**, porque orçamentos por categoria ainda não estão configurados no Firefly (item pendente do levantamento). Por enquanto o cálculo é: saldo menos contas fixas que ainda vão vencer no mês, dividido pelos dias restantes. Quando os orçamentos existirem, dá pra refinar por categoria.
- **Não cobre o Bradesco Black dividido com a Katarina.** A conta Bradesco Black é uma fatura conjunta, o Open Finance traz o valor total da fatura, não a divisão 50/50. O script importa o valor cheio; a divisão continua sendo um ajuste manual mensal (como já vem sendo feito).
- **Sinais de preço/notícia (inflação, combustível etc.) não estão neste script** de propósito: são mais fáceis de manter atualizados via pesquisa web do que via API paga, então ficam num agendador separado do Claude (não depende do seu Pi estar ligado numa hora específica). Combine os dois: o Pi cuida do que é factual e financeiro, o agendador cuida do contexto do mundo.
