# IAgis Agent

Agente Python para **homologação exclusivamente documental e de governança de softwares** em
chamados do GLPI 11. Ele reage a `@IAgis`, pesquisa evidências com a ferramenta web oficial da
Gemini (padrão) ou OpenAI, produz saída Pydantic e submete o parecer a regras determinísticas. Nesta fase, o sistema
**não baixa, instala nem executa instaladores** e jamais encerra chamados.

## Arquitetura

```text
GLPI API V1 (leitura) ─> monitor/detector ─> SQLite (idempotência)
                                 │
                                 v
               IAgis Governança + WebSearchTool
                                 │ relatório Pydantic (sem acesso GLPI)
                                 v
                       motor determinístico
                                 │
                      prévia/aprovação humana
                                 v
               publicador Python ─> acompanhamento GLPI
```

- `glpi_client.py`: sessão por App/User Token, TLS, timeout, retries, HTTP 206 e paginação.
- `worker.py` e `mention_detector.py`: monitor, entidades autorizadas, prevenção de loop e retomada
  pelo histórico completo.
- `governance_agent.py`: OpenAI Agents SDK, Web Search e fronteira contra prompt injection.
- `governance_models.py` / `governance_rules.py`: contrato Pydantic e barreira determinística.
- `repository.py`: estado, auditoria, hash único e idempotência em SQLite.
- `report_formatter.py` / `cli.py`: prévia e única via permitida de publicação.

O agente de IA não recebe cliente, token nem função de escrita no GLPI. Somente a aplicação chama
`create_followup`; não existem operações de exclusão ou encerramento no cliente.

## Configuração

Requer Python 3.11+. Copie apenas os nomes de `.env.example` para o gerenciador de segredos ou
ambiente do serviço. **Não crie `.env` com credenciais**.

| Variável | Obrigatória | Descrição |
|---|---:|---|
| `GLPI_URL` | sim | URL HTTPS do GLPI, sem `/apirest.php` |
| `GLPI_APP_TOKEN` | sim | App-Token da integração |
| `GLPI_USER_TOKEN` | sim | User-Token técnico, com privilégio mínimo |
| `AI_PROVIDER` | não | `gemini` (padrão), `openai`; nomes `anthropic` e `ollama` reservados para adaptadores futuros |
| `AI_MODEL` | não | Modelo do provedor; padrão `gemini-2.5-flash` |
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

O instalador pergunta os segredos com entrada oculta, grava-os em `/etc/iagis/iagis.env` com modo
`0600`, constrói o container, testa somente a conexão, lista entidades, pede a entidade e inicia o
worker em dry-run. Baixe e revise antes de executar:

```bash
curl -fsSL https://raw.githubusercontent.com/agisgithub/IAgis/main/scripts/install-debian.sh \
  -o /tmp/install-iagis.sh
less /tmp/install-iagis.sh
bash /tmp/install-iagis.sh
```

O script nunca cria `.env` dentro do repositório. Ele exige usuário com `sudo`, Debian 13+, Docker
e Compose v2, detecta a arquitetura automaticamente e mantém publicação automática desabilitada.

## CLI

```bash
python -m iagis.cli check [--entity-id 0]
python -m iagis.cli entities [--entity-id 0]
python -m iagis.cli ticket --ticket-id ID --entity-id ID
python -m iagis.cli analyze --ticket-id ID --entity-id ID
python -m iagis.cli preview --analysis-id ID
python -m iagis.cli publish --analysis-id ID [--confirm]
python -m iagis.cli worker --entity-id ID
```

`publish` sem `--confirm` imprime somente a prévia. Com dry-run ativo, até `--confirm` é bloqueado.
Resultados `INCONCLUSIVO`, `ALTO` ou `CRITICO` nunca são publicados automaticamente: exigem a
invocação humana explícita com `--confirm`. Demais resultados precisam também estar na allowlist.

## Fluxo de análise e aprovação

1. O monitor lê somente uma entidade autorizada e detecta menção na descrição ou follow-up.
2. Menções do usuário técnico, hashes já vistos e conteúdo duplicado são ignorados.
3. O histórico e **somente metadados** dos anexos entram como dados não confiáveis; arquivos não são
   baixados. Uma resposta posterior com nova menção/hash cria análise usando o histórico atualizado.
4. A IA pesquisa fontes prioritariamente oficiais e retorna o contrato obrigatório. Ausências viram
   `não confirmado`, perguntas objetivas em `pendencias` e, quando essenciais, `INCONCLUSIVO`.
5. Regras podem impor `NAO_HOMOLOGADO`, `INCONCLUSIVO` ou `HOMOLOGADO_COM_RESTRICOES`; cada regra
   fica em `regras_aplicadas`.
6. A análise e o parecer ficam no SQLite. Em dry-run a prévia é a única saída.
7. Uma pessoa revisa `preview` e solicita publicação explicitamente. A aplicação publica um único
   acompanhamento e registra ID/data, impedindo repetição.

## Segurança

- TLS obrigatório, validação de certificado ativa, timeouts e retentativas limitadas.
- Privilégio mínimo: conta restrita às entidades necessárias; nenhuma API destrutiva é implementada.
- Chamados, comentários, metadados e resultados web são não confiáveis. A instrução privilegiada
  proíbe obedecer pedidos neles, revelar tokens, executar comandos, mudar regras/escopo ou autorizar
  publicação. A aprovação jamais é ferramenta da IA.
- Logs JSON guardam IDs/estados/tipos de erro, não corpos integrais, documentos, credenciais,
  cabeçalhos de autenticação ou tokens. Erros persistidos são truncados e não incluem a mensagem de
  provedores externos.
- SQLite deve ficar em volume protegido, com backup, retenção e permissões do usuário do serviço.
- O container é não-root, read-only, sem capabilities, com `no-new-privileges` e volumes separados.
- Respostas ainda devem ser revisadas: busca e modelos podem errar, omitir ou encontrar fontes
  desatualizadas. O parecer não é validação binária, técnica, EDR ou autorização de implantação.

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

O serviço reinicia `unless-stopped`, persiste `/data` e `/reports` e possui healthcheck de conexão.
Nenhuma credencial entra no build; o Compose exige injeção externa. Em produção prefira secrets do
orquestrador em vez do ambiente quando disponível.

## Limitações do piloto

- A compatibilidade exata de campos/perfis depende da configuração GLPI 11; valide com conta de
  homologação e entidade escolhida.
- Anexos não são obtidos nem inspecionados nesta fase; apenas metadados informam o parecer.
- O polling lista chamados visíveis da entidade. Em bases grandes, recomenda-se adaptar filtros de
  busca oficiais da instalação sem relaxar idempotência.
- Não existe aprovação final automática, remediação, download, execução ou teste de software.
