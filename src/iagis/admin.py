"""Página administrativa deliberadamente simples, protegida por HTTP Basic."""
from __future__ import annotations

import hmac
import html
import json
from urllib.parse import parse_qs
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from .config import Settings, get_settings
from .repository import Repository
from .skill_runtime import validate_skill_source

app = FastAPI(title="IAgis Admin", docs_url=None, redoc_url=None)
security = HTTPBasic()


def authorized(credentials: HTTPBasicCredentials = Depends(security),
               settings: Settings = Depends(get_settings)) -> None:
    password = settings.admin_password.get_secret_value()
    valid = bool(password) and hmac.compare_digest(credentials.username.encode(), settings.admin_user.encode()) \
        and hmac.compare_digest(credentials.password.encode(), password.encode())
    if not valid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Não autorizado",
                            headers={"WWW-Authenticate": "Basic"})


def repo(settings: Settings = Depends(get_settings)) -> Repository:
    return Repository(settings.database_path)


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def table(title: str, rows: list, fields: tuple[str, ...]) -> str:
    head = "".join(f"<th>{esc(field)}</th>" for field in fields)
    body = "".join("<tr>" + "".join(f"<td>{esc(row[field])}</td>" for field in fields) + "</tr>" for row in rows)
    return f"<h2>{esc(title)}</h2><table><tr>{head}</tr>{body}</table>"


@app.get("/", response_class=HTMLResponse, dependencies=[Depends(authorized)])
def index(database: Repository = Depends(repo)) -> str:
    models = database.admin_rows("ai_models")
    agents = database.admin_rows("agents_config")
    skills = database.admin_rows("skills")
    rules = database.admin_rows("routing_rules")
    return f"""<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'><title>IAgis Admin</title>
<style>body{{font:14px sans-serif;max-width:1200px;margin:20px auto}}table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #bbb;padding:5px;text-align:left}}form{{padding:10px;background:#eee;margin:8px 0}}
input,textarea,select{{width:100%;box-sizing:border-box;margin:3px 0}}button{{padding:7px}}.warn{{background:#ffd;padding:10px}}</style></head><body>
<h1>IAgis — administração</h1><p class='warn'>Scripts Python são código confiável de administrador, não uma sandbox.
Nunca importe scripts de chamados ou usuários. Alterações afetam os próximos atendimentos.</p>
{table('Modelos', models, ('id','name','provider','model_name','base_url','timeout','enabled'))}
<form method='post' action='/models'><b>Novo modelo</b><input name='name' placeholder='Nome'><select name='provider'><option>ollama</option><option>gemini</option><option>openai</option></select><input name='model_name' placeholder='Modelo'><input name='base_url' placeholder='URL'><input name='timeout' value='120'><button>Adicionar</button></form>
{table('Agentes', agents, ('id','name','description','model_id','enabled'))}
<form method='post' action='/agents'><b>Novo agente</b><input name='name' placeholder='Nome'><input name='description' placeholder='Quando usar'><input name='model_id' type='number' placeholder='ID do modelo'><textarea name='prompt' placeholder='Prompt privilegiado'></textarea><button>Adicionar</button></form>
{table('Skills', skills, ('id','name','description','input_schema','output_schema','enabled'))}
<form method='post' action='/skills'><b>Importar skill Python</b><input name='name' placeholder='Nome'><input name='description' placeholder='Descrição'><textarea name='input_schema' placeholder='Schema JSON de entrada'></textarea><textarea name='output_schema' placeholder='Schema JSON de saída'></textarea><textarea name='source' rows='10' placeholder='def run(input_data):&#10;    return {{"resultado": "..."}}'></textarea><button>Importar desabilitada</button></form>
<form method='post' action='/bindings'><b>Vincular skill</b><input name='agent_id' type='number' placeholder='ID agente'><input name='skill_id' type='number' placeholder='ID skill'><button>Vincular</button></form>
{table('Regras de roteamento', rules, ('id','name','priority','field','pattern','agent_id','enabled'))}
<form method='post' action='/rules'><b>Nova regra</b><input name='name'><input name='priority' type='number' value='100'><select name='field'><option value='titulo'>Título</option><option value='descricao'>Descrição</option><option value='categoria'>Categoria</option></select><input name='pattern' placeholder='Texto que deve estar presente'><input name='agent_id' type='number' placeholder='ID agente'><button>Adicionar</button></form>
<form method='post' action='/toggle'><b>Ativar/desativar</b><select name='table'><option>ai_models</option><option>agents_config</option><option>skills</option><option>routing_rules</option></select><input name='id' type='number' placeholder='ID'><select name='enabled'><option value='1'>Ativar</option><option value='0'>Desativar</option></select><button>Aplicar</button></form>
</body></html>"""


async def form(request: Request) -> dict[str, str]:
    origin = request.headers.get("origin")
    if origin and urlparse(origin).netloc != request.headers.get("host"):
        raise HTTPException(403, "Origem inválida")
    parsed = parse_qs((await request.body()).decode(), keep_blank_values=True)
    return {key: values[-1] for key, values in parsed.items()}


def done() -> RedirectResponse:
    return RedirectResponse("/", status_code=303)


@app.post("/models", dependencies=[Depends(authorized)])
async def models(request: Request, database: Repository = Depends(repo)):
    data = await form(request); database.add_model(data["name"], data["provider"], data["model_name"], data["base_url"], float(data["timeout"])); return done()

@app.post("/agents", dependencies=[Depends(authorized)])
async def agents(request: Request, database: Repository = Depends(repo)):
    data = await form(request); database.add_agent(data["name"], data["description"], data["prompt"], int(data["model_id"])); return done()

@app.post("/skills", dependencies=[Depends(authorized)])
async def skills(request: Request, database: Repository = Depends(repo)):
    data = await form(request); json.loads(data["input_schema"]); json.loads(data["output_schema"]); validate_skill_source(data["source"]); database.add_skill(data["name"], data["description"], data["input_schema"], data["output_schema"], data["source"]); return done()

@app.post("/bindings", dependencies=[Depends(authorized)])
async def bindings(request: Request, database: Repository = Depends(repo)):
    data = await form(request); database.bind_skill(int(data["agent_id"]), int(data["skill_id"])); return done()

@app.post("/rules", dependencies=[Depends(authorized)])
async def rules(request: Request, database: Repository = Depends(repo)):
    data = await form(request); database.add_rule(data["name"], int(data["priority"]), data["field"], data["pattern"], int(data["agent_id"])); return done()

@app.post("/toggle", dependencies=[Depends(authorized)])
async def toggle(request: Request, database: Repository = Depends(repo)):
    data = await form(request); database.set_enabled(data["table"], int(data["id"]), data["enabled"] == "1"); return done()
