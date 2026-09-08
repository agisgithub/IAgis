from iagis.logging_config import redact_secrets

def test_secret_fields_redacted():
    result=redact_secrets(None,"event",{"event":"ok","Authorization":"Bearer abc","api_key":"x"})
    assert result["Authorization"] == result["api_key"] == "[REDACTED]"
