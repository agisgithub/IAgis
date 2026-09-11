# IAgis Agent

Agente Python para um piloto de atendimento assistido no GLPI 11. No modo padrão `suggestion`, ele
reage a `@iagis` e usa o Ollama local para gerar uma **sugestão em português para revisão humana**.
Não pesquisa a web, publica, aprova, homologa, encerra nem altera chamados. Os adaptadores anteriores
de Gemini/OpenAI e o modo de governança permanecem preservados, mas fora deste piloto.

## Arquitetura

```text
GLPI API V1 (somente leitura) ─> eventos ordenados/SQLite ─> Ollama no loopback do host
                                           │                         │
                                           └──── sugestão JSON ──────┘
                                                        │
                                             CLI de revisão humana
```

- `glpi_client.py`: sessão por App/User Token, TLS, timeout, retries, HTTP 206 e paginação.
- `worker.py` e `mention_detector.py`: monitor, entidades autorizadas, prevenção de loop e retomada
  pelo histórico completo.
- `ollama_agent.py`: sugestão estruturada, timeout e uma única correção de JSON, sem pesquisa web.
- `ai_provider.py`: interface que preserva os adaptadores Gemini e OpenAI.
- `governance_models.py` / `governance_rules.py`: contrato Pydantic e barreira determinística.
- `repository.py`: migração, auditoria, claim atômico, retries e identidade de evento em SQLite.
- `report_formatter.py` / `cli.py`: lista e prévia local; publicação bloqueada no piloto.

O agente de IA não recebe cliente, token nem função de escrita no GLPI. O modo do piloto não chama
`create_followup`; não existem operações de exclusão ou encerramento no cliente.

## Configuração

Requer Python 3.11+. Copie apenas os nomes de `.env.example` para o gerenciador de segredos ou
ambiente do serviço. **Não crie `.env` com credenciais**.

| Variável | Obrigatória | Descrição |
|---|---:|---|
| `GLPI_URL` | sim | URL HTTPS do GLPI, sem `/apirest.php` |
| `GLPI_APP_TOKEN` | sim | App-Token da integração |
| `GLPI_USER_TOKEN` | sim | User-Token técnico, com privilégio mínimo |
| `AI_PROVIDER` | não | `ollama` (padrão), `gemini` ou `openai` |
| `AI_MODEL` | não | Padrão `qwen3:4b-instruct-2507-q4_K_M` |
| `IAGIS_MODE` | não | `suggestion` no piloto; `governance` preserva o fluxo anterior |
| `OLLAMA_URL` | não | Padrão `http://127.0.0.1:11434` |
| `OLLAMA_TIMEOUT` | não | Timeout do modelo em segundos; padrão 120 |
| `IAGIS_MAX_ATTEMPTS` | não | Máximo de tentativas por evento; padrão 3 |
| `IAGIS_RETRY_DELAY` | não | Espera entre tentativas automáticas; padrão 300 segundos |
| `IAGIS_ADMIN_USER` | admin | Usuário HTTP Basic; padrão `iagis` |
| `IAGIS_ADMIN_PASSWORD` | admin | Senha forte, obrigatória para iniciar a página |
| `IAGIS_ADMIN_HOST` | não | Padrão seguro `127.0.0.1` |
| `IAGIS_ADMIN_PORT` | não | Padrão `8090` |
| `GEMINI_API_KEY` | com Gemini | Chave do Gemini AI Studio |
| `OPENAI_API_KEY` | com OpenAI | Chave da API OpenAI |
| `IAGIS_MENTION` | não | Menção; padrão `@IAgis` |
| `IAGIS_DRY_RUN` | não | Padrão seguro `true` |
| `IAGIS_POLL_INTERVAL` | não | Segundos entre ciclos (mínimo 5) |
| `IAGIS_DATABASE_PATH` | não | SQLite; padrão `data/iagis.db` |
| `IAGIS_ALLOWED_ENTITY_IDS` | worker | IDs separados por vírgula |
| `IAGIS_AUTO_PUBLISH_VERDICTS` | não | Vereditos aptos à automação |
| `IAGIS_GLPI_USER_ID` | recomendado | ID técnico para ignorar os próprios comentários |

As configurações falham cedo se faltarem valores obrigatórios ou se `GLPI_URL` não usar HTTPS.
Tokens são `SecretStr`, nunca são impressos pelos comandos e os logs estruturados filtram campos
sensíveis.

## Execução local

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
pytest
python -m iagis.cli check
python -m iagis.cli entities
```

Use variáveis exportadas pelo shell/secret manager. `check` inicia a sessão, valida a conexão e
sempre executa `killSession`; `entities` é somente leitura. O contexto raiz explícito é ID `0`.

## Instalação assistida no Debian 13

O instalador pergunta os segredos GLPI com entrada oculta, grava-os em `/etc/iagis/iagis.env` com modo
`0600`, constrói o container, testa somente a conexão, lista entidades, pede a entidade e inicia o
worker em dry-run. Baixe e revise antes de executar:

```bash
curl -fsSL https://raw.githubusercontent.com/agisgithub/IAgis/main/scripts/install-debian.sh \
  -o /tmp/install-iagis.sh
less /tmp/install-iagis.sh
bash /tmp/install-iagis.sh
```

O script nunca cria `.env` dentro do repositório. Ele exige usuário com `sudo`, Debian 13+, Docker
e Compose v2, detecta a arquitetura automaticamente, confirma que o modelo já existe e nunca baixa
outro modelo. A publicação permanece desabilitada.

## CLI

```bash
python -m iagis.cli check [--entity-id 0]
python -m iagis.cli entities [--entity-id 0]
python -m iagis.cli ticket --ticket-id ID --entity-id ID
python -m iagis.cli analyze --ticket-id ID --entity-id ID
python -m iagis.cli preview --analysis-id ID
python -m iagis.cli analyses --limit 20
python -m iagis.cli retry --analysis-id ID
python -m iagis.cli publish --analysis-id ID [--confirm]
python -m iagis.cli worker --entity-id ID
```

No modo `suggestion`, `publish` é sempre bloqueado, inclusive com `--confirm` e independentemente de
configuração. `preview` mostra a sugestão salva; `analyses` lista estado/tentativas/erro sem conteúdo
do chamado; `retry` libera uma falha dentro do limite e tenta o mesmo evento novamente.

## Página administrativa

Inicie o perfil opcional e crie um túnel SSH; a página não é exposta na rede:

```bash
sudo docker compose --env-file /etc/iagis/iagis.env -f /opt/iagis-codex/compose.yaml \
  --profile admin up -d
# Na sua estação, não no servidor:
ssh -L 8090:127.0.0.1:8090 USUARIO@SERVIDOR
```

Abra `http://127.0.0.1:8090` e autentique com `IAGIS_ADMIN_USER` e a senha configurada. A página
permite cadastrar/ativar modelos, agentes, regras simples por texto em título/descrição/categoria,
importar skills e vinculá-las aos agentes. A primeira regra habilitada por prioridade é usada. No
piloto, apenas modelos Ollama são executados pelo roteamento; Gemini/OpenAI permanecem preservados
no fluxo anterior.

### Contrato de uma skill

Toda skill é cadastrada inicialmente **desabilitada**, recebe um objeto JSON e deve retornar outro.
Defina schemas JSON de entrada/saída e exatamente uma função, sem imports:

```python
def run(input_data):
    titulo = input_data.get("titulo", "")
    return {"titulo_normalizado": titulo.strip().lower()}
```

Exemplo de schema de entrada:

```json
{"type":"object","properties":{"titulo":{"type":"string"}},"required":["titulo"],"additionalProperties":false}
```

Exemplo de saída:

```json
{"type":"object","properties":{"titulo_normalizado":{"type":"string"}},"required":["titulo_normalizado"],"additionalProperties":false}
```

O runtime limita tamanho, duração e ambiente, bloqueia imports e valida os dois schemas. Isso
**reduz risco, mas não constitui sandbox de segurança**: somente administradores podem importar
scripts revisados e confiáveis. Nunca transforme código vindo de chamado em skill. Resultados de
skills são tratados como dados não confiáveis pelo modelo.

## Fluxo de análise e aprovação

1. O monitor lista **todos os chamados visíveis retornados pela API na entidade**, sem filtro de
   status ou data neste piloto, e procura a menção na descrição e nos acompanhamentos.
2. A descrição vem primeiro; acompanhamentos são ordenados explicitamente por data e ID.
3. A identidade contém entidade, chamado, origem/ID e hash da versão. Texto igual em eventos
   diferentes é processado; o mesmo evento não. Editar descrição ou acompanhamento cria nova versão.
4. Comentários do próprio IAgis são ignorados. Histórico e somente metadados dos anexos entram como
   dados não confiáveis; arquivos não são baixados ou executados.
5. Ollama devolve JSON validado. Se a estrutura for inválida, há no máximo uma solicitação de
   correção; validade estrutural não é tratada como evidência factual.
6. A sugestão fica no SQLite para `preview`. Nada é publicado no GLPI.
7. Falhas transitórias aguardam `IAGIS_RETRY_DELAY` e respeitam `IAGIS_MAX_ATTEMPTS`; configuração
   inválida não entra em retry automático. `retry` permite tentativa administrativa dentro do limite.

## Segurança

- TLS obrigatório, validação de certificado ativa, timeouts e retentativas limitadas.
- Privilégio mínimo: conta restrita às entidades necessárias; nenhuma API destrutiva é implementada.
- Chamados, comentários e metadados são não confiáveis. A instrução privilegiada
  proíbe obedecer pedidos neles, revelar tokens, executar comandos, mudar regras/escopo ou autorizar
  publicação. A aprovação jamais é ferramenta da IA.
- Logs JSON guardam IDs/estados/tipos de erro, não corpos integrais, documentos, credenciais,
  cabeçalhos de autenticação ou tokens. Erros persistidos são truncados e não incluem a mensagem de
  provedores externos.
- SQLite deve ficar em volume protegido, com backup, retenção e permissões do usuário do serviço.
- O container é não-root, read-only, sem capabilities, com `no-new-privileges` e volumes separados.
- Respostas devem ser revisadas: o Qwen é candidato ao piloto e pode errar ou omitir fatos. A
  sugestão não é pesquisa, homologação, aprovação nem autorização de implantação.

### Revogação de tokens

Em suspeita de exposição: (1) pare o serviço; (2) revogue App-Token/User-Token no GLPI e a chave no
painel OpenAI; (3) emita credenciais novas, mantendo escopo mínimo; (4) atualize o secret manager,
nunca o repositório/imagem; (5) revise logs de auditoria de ambos os provedores e o SQLite; (6)
reinicie e rode `check`; (7) registre o incidente conforme a política institucional. Rotacione
periodicamente e remova imediatamente credenciais de operadores desligados.

## Docker

```bash
docker compose build
# Exporte as variáveis, inclusive IAGIS_ENTITY_ID e IAGIS_ALLOWED_ENTITY_IDS
docker compose up -d
```

O serviço reinicia `unless-stopped`, persiste `/data` e `/reports` e possui healthcheck apenas do
runtime. Sucesso de GLPI e IA aparece separadamente nos logs. No Linux, `network_mode: host` permite
ao container alcançar `127.0.0.1:11434`; o Ollama continua ligado somente ao loopback e não precisa
ser exposto em `0.0.0.0`. `host.docker.internal` sozinho não resolveria um listener em loopback.
Nenhuma credencial entra no build; o Compose exige injeção externa. Em produção prefira secrets do
orquestrador em vez do ambiente quando disponível.

## Limitações do piloto

- A compatibilidade exata de campos/perfis depende da configuração GLPI 11; valide com conta de
  homologação e entidade escolhida.
- Anexos não são obtidos nem inspecionados nesta fase; apenas metadados informam o parecer.
- O polling lista chamados visíveis da entidade. Em bases grandes, recomenda-se adaptar filtros de
  busca oficiais da instalação sem relaxar idempotência.
- Não existe aprovação final automática, remediação, download, execução ou teste de software.

## Atualização do servidor, backup e reversão

Execute no servidor após o merge. O bloco não lê nem imprime o arquivo de credenciais:

```bash
set -e
cd /opt/iagis-codex
sudo docker compose --env-file /etc/iagis/iagis.env -f compose.yaml down
sudo cp -a /etc/iagis/iagis.env "/etc/iagis/iagis.env.backup.$(date +%Y%m%d-%H%M%S)"
sudo docker run --rm -v iagis-codex_iagis_data:/data -v /var/backups:/backup \
  alpine sh -c 'tar czf /backup/iagis-data-before-ollama.tgz -C /data .'
PREVIOUS_COMMIT="$(git rev-parse HEAD)"
printf '%s\n' "$PREVIOUS_COMMIT" | sudo tee /var/backups/iagis-previous-commit >/dev/null
sudo git fetch origin main
sudo git reset --hard origin/main
sudo sed -i -e 's/^AI_PROVIDER=.*/AI_PROVIDER=ollama/' \
  -e 's/^AI_MODEL=.*/AI_MODEL=qwen3:4b-instruct-2507-q4_K_M/' \
  -e 's/^IAGIS_DRY_RUN=.*/IAGIS_DRY_RUN=true/' /etc/iagis/iagis.env
sudo sh -c 'grep -q "^IAGIS_MODE=" /etc/iagis/iagis.env || echo IAGIS_MODE=suggestion >>/etc/iagis/iagis.env'
sudo sh -c 'grep -q "^OLLAMA_URL=" /etc/iagis/iagis.env || echo OLLAMA_URL=http://127.0.0.1:11434 >>/etc/iagis/iagis.env'
sudo sh -c 'grep -q "^OLLAMA_TIMEOUT=" /etc/iagis/iagis.env || echo OLLAMA_TIMEOUT=120 >>/etc/iagis/iagis.env'
sudo sh -c 'grep -q "^IAGIS_MAX_ATTEMPTS=" /etc/iagis/iagis.env || echo IAGIS_MAX_ATTEMPTS=3 >>/etc/iagis/iagis.env'
sudo sh -c 'grep -q "^IAGIS_RETRY_DELAY=" /etc/iagis/iagis.env || echo IAGIS_RETRY_DELAY=300 >>/etc/iagis/iagis.env'
sudo docker compose --env-file /etc/iagis/iagis.env -f compose.yaml build
sudo docker compose --env-file /etc/iagis/iagis.env -f compose.yaml up -d
sudo docker compose --env-file /etc/iagis/iagis.env -f compose.yaml logs --tail=100 iagis
```

Reversão do código (o backup SQLite permanece disponível em `/var/backups`):

```bash
cd /opt/iagis-codex
OLD="$(sudo cat /var/backups/iagis-previous-commit)"
sudo docker compose --env-file /etc/iagis/iagis.env -f compose.yaml down
sudo git reset --hard "$OLD"
sudo docker compose --env-file /etc/iagis/iagis.env -f compose.yaml build
sudo docker compose --env-file /etc/iagis/iagis.env -f compose.yaml up -d
```

## Teste curto com chamados reais

1. Confirme `IAGIS_DRY_RUN=true`, entidade `1`, usuário IAgis `1838` e os logs do monitor.
2. Em um chamado não sensível da entidade 1, escreva `@iagis sugira uma resposta para este pedido`.
3. Aguarde um ciclo e rode `sudo docker compose --env-file /etc/iagis/iagis.env -f
   /opt/iagis-codex/compose.yaml run --rm iagis analyses --limit 10`.
4. Rode o mesmo comando substituindo o final por `preview --analysis-id ID`; revise resumo,
   perguntas, limitações e a sugestão.
5. Repita com outro chamado contendo o mesmo texto e confirme que ambos aparecem.
6. Não use `publish`: ele é bloqueado neste piloto.
