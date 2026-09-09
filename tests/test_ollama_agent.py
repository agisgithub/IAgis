import json
import pytest
import requests
from iagis.ollama_agent import OllamaError, OllamaSuggestionAgent
from iagis.suggestion_models import ResponseSuggestion

DATA={"resumo_pedido":"Pedido de acesso","sugestao_resposta":"Informe o sistema.",
      "informacoes_faltantes":["Sistema"],"limitacoes":["Não houve aprovação"],"confianca":0.7}
class Reply:
    status_code=200; headers={}
    def __init__(self, content): self.content=content
    def raise_for_status(self): pass
    def json(self): return {"message":{"content":self.content}}
class HTTP:
    def __init__(self, contents): self.contents=iter(contents); self.calls=[]
    def post(self,*args,**kwargs): self.calls.append(kwargs); return Reply(next(self.contents))

@pytest.mark.asyncio
async def test_structured_response_and_schema():
    agent=OllamaSuggestionAgent("http://localhost:11434","qwen",10); agent.http=HTTP([json.dumps(DATA)])
    result,_=await agent.analyze({"descricao":"@iagis"})
    assert isinstance(result,ResponseSuggestion)
    assert agent.http.calls[0]["json"]["format"]["type"] == "object"

@pytest.mark.asyncio
async def test_only_one_structural_correction():
    agent=OllamaSuggestionAgent("http://localhost:11434","qwen"); agent.http=HTTP(["invalid","still invalid"])
    with pytest.raises(OllamaError,match="após uma correção"): await agent.analyze({})
    assert len(agent.http.calls)==2
