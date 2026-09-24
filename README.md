# IAgis Agent

Agente Python para um piloto de atendimento assistido no GLPI 11. No modo padrão `suggestion`, ele
reage a `@iagis` e usa Ollama, Gemini ou OpenAI para gerar uma **sugestão em português**. Em
dry-run apenas salva a prévia; com ativação explícita, publica a resposta no mesmo chamado. Pedidos
OpenVPN explícitos passam por classificação estruturada, autorização determinística no GLPI e um
broker local isolado antes de criar, consultar ou revogar um perfil.

## Arquitetura

```text
GLPI Search API ─> chamados alterados ─> eventos idempotentes/SQLite ─> provedor de IA
                                              │                              │
                                              ├──── sugestão estruturada ────┘
                                              │
                                              └─ autorização GLPI ─> broker VPN ─> PKI
                                                                         │
                                                          anexo .ovpn + resposta no chamado
```

- `glpi_client.py`: sessão por App/User Token, TLS, timeout, retries, HTTP 206 e paginação.
- `worker.py` e `mention_detector.py`: busca incremental por `date_mod`, entidades autorizadas,
  cursor persistente com sobreposição, prevenção de loop e retomada idempotente.
- `ollama_agent.py`: sugestão estruturada, timeout e uma única correção de JSON, sem pesquisa web.
- `ai_provider.py`: seleção de Ollama, Gemini ou OpenAI para o mesmo contrato de sugestão.
- `vpn_action_planner.py`: interpretação estruturada; nunca concede autorização.
- `vpn-broker/`: serviço mínimo no loopback; é o único componente com acesso à CA OpenVPN.
- `governance_models.py` / `governance_rules.py`: contrato Pydantic e barreira determinística.
- `repository.py`: migração, auditoria, claim atômico, retries e identidade de evento em SQLite.
- `report_formatter.py` / `cli.py`: prévia e publicação controlada de resposta operacional.

O modelo não recebe credenciais nem acesso à PKI. Somente o worker escreve acompanhamentos/anexos;
somente o broker cria ou revoga certificados. Aprovação, mudança de status e encerramento continuam
fora do cliente GLPI.

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
| `IAGIS_GLPI_INITIAL_LOOKBACK_HOURS` | não | Janela inicial após primeiro start; padrão 24 h |
| `IAGIS_GLPI_POLL_OVERLAP_SECONDS` | não | Sobreposição do cursor; padrão 120 s |
| `IAGIS_DATABASE_PATH` | não | SQLite; padrão `data/iagis.db` |
| `IAGIS_ALLOWED_ENTITY_IDS` | worker | IDs separados por vírgula |
| `IAGIS_AUTO_PUBLISH_VERDICTS` | não | Vereditos aptos à automação |
| `IAGIS_GLPI_USER_ID` | recomendado | ID técnico para ignorar os próprios comentários |
| `IAGIS_VPN_ENABLED` | não | Habilita planejamento e execução OpenVPN; padrão `false` |
| `IAGIS_VPN_BROKER_TOKEN` | com VPN | Segredo aleatório compartilhado, mínimo 32 caracteres |
| `IAGIS_VPN_BROKER_URL` | não | HTTPS ou HTTP somente em loopback; padrão `127.0.0.1:8091` |
| `IAGIS_VPN_ACTION_MIN_CONFIDENCE` | não | Abaixo deste valor o agente pergunta; padrão `0.80` |
| `IAGIS_VPN_REMOTE_HOST` | com VPN | Endereço público gravado no perfil `.ovpn` |
| `IAGIS_OPENVPN_ROOT` | com VPN | Diretório host da PKI/configuração existente |

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
outro modelo. O instalador mantém `IAGIS_DRY_RUN=true`; produção é sempre uma alteração manual.

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

`preview` mostra a sugestão salva; `analyses` lista estado/tentativas/erro sem conteúdo do chamado;
`retry` libera uma falha dentro do limite. `publish` sem `--confirm` mostra somente a prévia; com
`--confirm` publica apenas quando o dry-run estiver desativado.

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

1. O monitor consulta o Search API por chamados alterados desde um cursor persistente, com pequena
   sobreposição. No primeiro start cobre a janela configurada, em vez de reler toda a base.
2. A descrição vem primeiro; acompanhamentos são ordenados explicitamente por data e ID.
3. A identidade contém entidade, chamado, origem/ID e hash da versão. Texto igual em eventos
   diferentes é processado; o mesmo evento não. Editar descrição ou acompanhamento cria nova versão.
4. Comentários do próprio IAgis são ignorados. Histórico e somente metadados dos anexos entram como
   dados não confiáveis; arquivos não são baixados ou executados.
5. O provedor selecionado devolve JSON validado. Validade estrutural não é autorização nem prova
   factual.
6. Em dry-run a sugestão fica no SQLite. Em produção, o worker publica um ITILFollowup no mesmo
   chamado e registra seu ID; a identidade do evento impede uma segunda publicação.
7. Falhas transitórias aguardam `IAGIS_RETRY_DELAY` e respeitam `IAGIS_MAX_ATTEMPTS`; configuração
   inválida não entra em retry automático. `retry` permite tentativa administrativa dentro do limite.

### Fluxo OpenVPN

1. Somente mensagens com contexto de VPN são enviadas ao classificador estruturado.
2. Criação/revogação exige intenção clara, confiança mínima, nome inequívoco e autor identificado.
3. O worker confirma pela API que o autor é técnico atribuído ao chamado ou membro de grupo técnico
   atribuído. O modelo não participa dessa decisão.
4. O broker escuta apenas no loopback, exige bearer token e valida nomes. Ele reutiliza a PKI
   existente, mantém perfis em `0600` e atualiza o CRL ao revogar.
5. Criação envia o perfil ao endpoint oficial `Document`, vincula-o via `Document_Item` e publica a
   confirmação. Operações ficam auditadas e são idempotentes por evento do GLPI.
6. Pedido ambíguo, baixa confiança ou autor não autorizado gera uma resposta explicativa sem tocar
   na PKI.

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
- O worker não monta Docker socket, CA ou chave de cliente. Apenas o broker opcional monta a PKI,
  roda como o UID/GID proprietário e permanece sem capabilities e com filesystem raiz read-only.
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
# Com gestão VPN habilitada:
docker compose --profile vpn up -d --build
```

O serviço reinicia `unless-stopped`, persiste `/data` e `/reports` e possui healthcheck apenas do
runtime. Sucesso de GLPI e IA aparece separadamente nos logs. No Linux, `network_mode: host` permite
ao container alcançar `127.0.0.1:11434`; o Ollama continua ligado somente ao loopback e não precisa
ser exposto em `0.0.0.0`. `host.docker.internal` sozinho não resolveria um listener em loopback.
Nenhuma credencial entra no build; o Compose exige injeção externa. Em produção prefira secrets do
orquestrador em vez do ambiente quando disponível.

Para anexar perfis, o GLPI deve possuir um tipo de documento uploadable para a extensão `ovpn`. O
diretório `${IAGIS_OPENVPN_ROOT}/profiles` deve existir e pertencer ao UID/GID configurado. O
processo OpenVPN precisa conseguir atravessar o diretório que contém `crl.pem`; valide isso como o
usuário `nobody` dentro do container e confira os logs após uma revogação.

### Ativação explícita de produção

Antes de usar escrita, confirme no arquivo protegido (sem imprimir seu conteúdo):

- `IAGIS_DRY_RUN=false`;
- `IAGIS_MODE=suggestion`;
- `IAGIS_GLPI_USER_ID` preenchido com o usuário técnico que escreve os acompanhamentos;
- `IAGIS_ALLOWED_ENTITY_IDS` e `IAGIS_ENTITY_ID` coerentes;
- User-Token com permissão mínima para criar `ITILFollowup` nessa entidade.

O default do código continua sendo dry-run. O corpo publicado não contém `@IAgis`, evitando um novo
gatilho; a aplicação também ignora acompanhamentos cujo autor seja `IAGIS_GLPI_USER_ID`. Esse token
passa a possuir permissão de escrita de follow-up e deve ter escopo mínimo, rotação e auditoria.

### Recuperar o admin em restart

Defina uma senha forte sem mostrá-la no terminal e recrie somente o serviço admin:

```bash
read -rsp 'Nova senha administrativa: ' ADMIN_PASS; echo
sudo sed -i '/^IAGIS_ADMIN_PASSWORD=/d;/^IAGIS_ADMIN_USER=/d;/^IAGIS_ADMIN_HOST=/d;/^IAGIS_ADMIN_PORT=/d' /etc/iagis/iagis.env
printf 'IAGIS_ADMIN_USER=iagis\nIAGIS_ADMIN_PASSWORD=%s\nIAGIS_ADMIN_HOST=127.0.0.1\nIAGIS_ADMIN_PORT=8090\n' "$ADMIN_PASS" \
  | sudo tee -a /etc/iagis/iagis.env >/dev/null
unset ADMIN_PASS
sudo chmod 600 /etc/iagis/iagis.env
sudo docker compose --env-file /etc/iagis/iagis.env -f /opt/iagis-codex/compose.yaml \
  --profile admin up -d --force-recreate admin
sudo docker compose --env-file /etc/iagis/iagis.env -f /opt/iagis-codex/compose.yaml \
  --profile admin ps
sudo ss -lntp | grep '127.0.0.1:8090'
```

Acesse exclusivamente pelo túnel `ssh -N -L 8090:127.0.0.1:8090 agis@10.11.46.109`. Não altere
`IAGIS_ADMIN_HOST` para `0.0.0.0`.

## Limitações do piloto

- A compatibilidade exata de campos/perfis depende da configuração GLPI 11; valide com conta de
  homologação e entidade escolhida.
- Anexos recebidos continuam sem download/execução; o único upload é o perfil criado pelo broker.
- Os IDs dos campos Search API usados foram validados no GLPI 11 deste piloto; outra instalação deve
  validar `Ticket.id=2` e `Ticket.date_mod=19` com `listSearchOptions/Ticket`.
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
