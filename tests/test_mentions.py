from iagis.mention_detector import detect_mention

def test_detect_html_case_insensitive():
    assert detect_mention("<p>favor, @iAgIs!</p>", "@IAgis")

def test_no_partial_or_own_loop():
    assert detect_mention("@IAgisBot", "@IAgis") is None
    assert detect_mention("@IAgis", "@IAgis", author_id=7, own_user_id=7) is None

def test_duplicate_hash_stable():
    assert detect_mention("<b>@IAgis</b> agora", "@IAgis").content_hash == detect_mention(" @iagis agora ", "@IAgis").content_hash
