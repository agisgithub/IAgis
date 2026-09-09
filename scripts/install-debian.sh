#!/usr/bin/env bash
set -Eeuo pipefail

REPOSITORY="https://github.com/agisgithub/IAgis.git"
BRANCH="main"
INSTALL_DIR="/opt/iagis-codex"
ENV_DIR="/etc/iagis"
ENV_FILE="$ENV_DIR/iagis.env"
GLPI_DEFAULT="https://servicedesk.grupoagis.com.br"

info() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
die() { printf '\nERRO: %s\n' "$*" >&2; exit 1; }
ask() { local prompt="$1" default="${2:-}" value; read -r -p "$prompt${default:+ [$default]}: " value; printf '%s' "${value:-$default}"; }
secret() { local prompt="$1" value; read -r -s -p "$prompt: " value; printf '\n' >&2; [[ -n "$value" ]] || die "$prompt não pode ficar vazio"; printf '%s' "$value"; }
validate_line() { [[ "$1" != *$'\n'* && "$1" != *$'\r'* ]] || die "valor contém quebra de linha"; }

[[ -r /etc/os-release ]] || die "sistema sem /etc/os-release"
. /etc/os-release
[[ "${ID:-}" == "debian" ]] || die "este instalador é destinado ao Debian"
[[ "${VERSION_ID%%.*}" -ge 13 ]] || die "Debian 13 ou superior é necessário"
command -v sudo >/dev/null || die "sudo não está instalado"
sudo -v

info "Instalando utilitários básicos"
sudo apt-get update
sudo apt-get install -y git ca-certificates curl
command -v docker >/dev/null || die "Docker não encontrado. Instale o Docker antes de continuar."
sudo docker compose version >/dev/null || die "Docker Compose v2 não encontrado."
printf 'Arquitetura detectada: %s\n' "$(uname -m)"

info "Coletando configuração (os tokens não serão exibidos)"
GLPI_URL="$(ask 'URL HTTPS do GLPI' "$GLPI_DEFAULT")"
[[ "$GLPI_URL" == https://* ]] || die "a URL do GLPI deve começar com https://"
GLPI_URL="${GLPI_URL%/}"
APP_TOKEN="$(secret 'App-Token do GLPI')"
USER_TOKEN="$(secret 'User-Token do usuário IAgis')"
GEMINI_KEY="$(secret 'Chave da API do Gemini AI Studio')"
IAGIS_USER_ID="$(ask 'ID numérico do usuário IAgis no GLPI')"
[[ "$IAGIS_USER_ID" =~ ^[0-9]+$ ]] || die "o ID do IAgis deve ser numérico"
MODEL="$(ask 'Modelo Gemini' 'gemini-2.5-flash')"
for value in "$GLPI_URL" "$APP_TOKEN" "$USER_TOKEN" "$GEMINI_KEY" "$MODEL"; do validate_line "$value"; done

info "Baixando o IAgis"
if [[ -d "$INSTALL_DIR/.git" ]]; then
  sudo git -C "$INSTALL_DIR" fetch --depth 1 origin "$BRANCH"
  sudo git -C "$INSTALL_DIR" checkout -f "$BRANCH"
  sudo git -C "$INSTALL_DIR" reset --hard "origin/$BRANCH"
else
  sudo rm -rf "$INSTALL_DIR"
  sudo git clone --depth 1 --branch "$BRANCH" "$REPOSITORY" "$INSTALL_DIR"
fi

info "Gravando segredos fora do repositório"
sudo install -d -m 700 "$ENV_DIR"
TMP="$(mktemp)"; trap 'rm -f "$TMP"' EXIT
cat >"$TMP" <<CONFIG
GLPI_URL=$GLPI_URL
GLPI_APP_TOKEN=$APP_TOKEN
GLPI_USER_TOKEN=$USER_TOKEN
AI_PROVIDER=gemini
AI_MODEL=$MODEL
GEMINI_API_KEY=$GEMINI_KEY
OPENAI_API_KEY=
IAGIS_MENTION=@IAgis
IAGIS_DRY_RUN=true
IAGIS_POLL_INTERVAL=60
IAGIS_DATABASE_PATH=/data/iagis.db
IAGIS_ALLOWED_ENTITY_IDS=
IAGIS_AUTO_PUBLISH_VERDICTS=
IAGIS_GLPI_USER_ID=$IAGIS_USER_ID
IAGIS_ENTITY_ID=0
CONFIG
sudo install -m 600 -o root -g root "$TMP" "$ENV_FILE"
unset APP_TOKEN USER_TOKEN GEMINI_KEY

compose() { sudo docker compose --env-file "$ENV_FILE" -f "$INSTALL_DIR/compose.yaml" "$@"; }
info "Construindo o container"
compose build

info "Testando a conexão com o GLPI (nenhum chamado será consultado)"
compose run --rm iagis check --entity-id 0

info "Entidades disponíveis"
ENTITIES="$(compose run --rm iagis entities --entity-id 0)"
printf '%s\n' "$ENTITIES"
ENTITY_ID="$(ask 'Digite o ID da entidade que será monitorada')"
[[ "$ENTITY_ID" =~ ^[0-9]+$ ]] || die "o ID da entidade deve ser numérico"
if ! printf '%s\n' "$ENTITIES" | grep -Eq "^${ENTITY_ID}[[:space:]]"; then
  ANSWER="$(ask 'O ID não foi localizado na saída. Continuar mesmo assim? (digite SIM)' 'não')"
  [[ "$ANSWER" == "SIM" ]] || die "instalação cancelada antes de iniciar o worker"
fi
sudo sed -i \
  -e "s/^IAGIS_ALLOWED_ENTITY_IDS=.*/IAGIS_ALLOWED_ENTITY_IDS=$ENTITY_ID/" \
  -e "s/^IAGIS_ENTITY_ID=.*/IAGIS_ENTITY_ID=$ENTITY_ID/" "$ENV_FILE"

info "Iniciando worker em DRY-RUN"
compose up -d
compose ps
cat <<FINAL

Instalação concluída.
- Diretório: $INSTALL_DIR
- Configuração protegida: $ENV_FILE
- Entidade: $ENTITY_ID
- DRY-RUN: ativo (nenhum acompanhamento será publicado)

Comandos úteis:
  sudo docker compose --env-file $ENV_FILE -f $INSTALL_DIR/compose.yaml ps
  sudo docker compose --env-file $ENV_FILE -f $INSTALL_DIR/compose.yaml logs -f --tail=100 iagis
  sudo docker compose --env-file $ENV_FILE -f $INSTALL_DIR/compose.yaml down

Nunca copie o conteúdo de $ENV_FILE para o GitHub ou para chamados.
FINAL
