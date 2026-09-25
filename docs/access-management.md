# Gestão de acesso a Claude e ChatGPT

## Decisão de arquitetura

O IAgis administra a fonte de identidade no Microsoft Entra ID. Em `group`, modo recomendado, o
broker adiciona ou remove o usuário de grupos de acesso fixos; Claude ou ChatGPT recebem a mudança
por SCIM. Isso evita armazenar cookies, senha ou MFA de contas administrativas e preserva uma trilha
de auditoria no Entra, no provedor e no SQLite.

O convite à organização da API OpenAI (`POST /organization/invites`) não equivale a um assento no
workspace do ChatGPT e não é usado. Também não há automação de navegador embutida.

Referências oficiais:

- [Claude: JIT e SCIM](https://support.claude.com/en/articles/13133195-set-up-jit-or-scim-provisioning)
- [Claude: assentos Enterprise](https://support.claude.com/en/articles/13393991-purchase-and-manage-seats-on-enterprise-plans)
- [ChatGPT: ciclo de vida de usuários](https://learn.chatgpt.com/docs/enterprise/user-lifecycle)
- [Microsoft Graph: adicionar membro a grupo](https://learn.microsoft.com/en-us/graph/api/group-post-members)
- [Microsoft Graph: atribuir aplicação](https://learn.microsoft.com/en-us/graph/api/serviceprincipal-post-approleassignedto)

## Pré-requisitos

### Claude

- Claude Enterprise para SCIM. JIT existe em Team/Enterprise, mas a remoção no IdP não remove o
  membro/assento no Claude; portanto JIT isolado não satisfaz a revogação completa.
- SSO e SCIM configurados na organização Claude e na Enterprise Application do Entra.
- Grupo(s) Entra mapeados ao papel e, quando aplicável, ao tipo de assento correto.
- Assentos disponíveis. Em Enterprise de assento único, o usuário provisionado recebe um assento
  automaticamente se houver disponibilidade. O Entra pode levar aproximadamente 40 minutos para
  enviar uma mudança SCIM.

### ChatGPT

- ChatGPT Enterprise, Edu ou Healthcare para SCIM. Business não possui esse fluxo de sincronização.
- Directory Sync habilitado no workspace correto e grupo Entra associado a ele.
- Tipo de assento padrão revisado antes da ativação. Uma conexão compartilhada não concede acesso a
  todos os workspaces nem à organização da API Platform.

## App Registration do broker

Crie uma App Registration exclusiva, use credencial de curta duração e conceda consentimento de
administrador somente às permissões de aplicação necessárias:

- modo `group`: `User.Read.All` e `GroupMember.ReadWrite.All`;
- modo `app_role`: `User.Read.All`, `Application.Read.All` e
  `AppRoleAssignment.ReadWrite.All`.

Não use grupos com função administrativa atribuível. O broker valida domínio, produto, ação e UUID,
escuta apenas no loopback e não aceita URL, grupo ou aplicação vindos do chamado.

## Cofre e configuração

Gere um token sem imprimi-lo no histórico e crie o arquivo separado:

```bash
sudo install -d -m 0700 -o root -g root /etc/iagis
sudo install -m 0600 -o root -g root access-broker.env.example /etc/iagis/access-broker.env
sudoedit /etc/iagis/access-broker.env
```

Em `/etc/iagis/access-broker.env`, preencha tenant, App Registration, segredo, domínios e os Object
IDs dos grupos. Configure **todos** os grupos que concedem acesso a cada produto; a revogação percorre
todos eles.

No arquivo principal `/etc/iagis/iagis.env`, mantenha apenas:

```dotenv
IAGIS_ACCESS_ENABLED=true
IAGIS_ACCESS_BROKER_URL=http://127.0.0.1:8092
IAGIS_ACCESS_BROKER_TOKEN=<mesmo token aleatório de 32+ caracteres>
IAGIS_ACCESS_ENV_FILE=/etc/iagis/access-broker.env
```

O `AZURE_CLIENT_SECRET` não deve ser copiado para o arquivo principal, commit, imagem ou chamado.

## Subida e validação

```bash
cd /opt/iagis-codex
sudo docker compose --env-file /etc/iagis/iagis.env --profile access build
sudo docker compose --env-file /etc/iagis/iagis.env --profile access up -d
sudo docker compose --env-file /etc/iagis/iagis.env ps
sudo docker compose --env-file /etc/iagis/iagis.env logs --tail=100 access-broker iagis
```

Teste primeiro em `IAGIS_DRY_RUN=true`. Depois, com uma conta e grupos de homologação, use pedidos
novos em chamados não sensíveis:

```text
@iagis libere Claude para usuario-teste@empresa.com.br
@iagis consulte o acesso ao ChatGPT de usuario-teste@empresa.com.br
@iagis revogue o Claude de usuario-teste@empresa.com.br
```

Valide quatro evidências antes de liberar produção: acompanhamento no GLPI, linha em
`access_operations`, auditoria do Entra e membro/assento no serviço depois do SCIM. Um retorno
`PENDING_SYNC` confirma apenas a alteração no Entra, não o provisionamento final no SaaS.
