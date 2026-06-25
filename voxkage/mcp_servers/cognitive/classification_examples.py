import re

# Stopwords to filter out when generating message fingerprint
_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "if", "then", "of", "to", "in", "on", 
    "at", "by", "for", "with", "about", "against", "between", "into", "through", 
    "during", "before", "after", "above", "below", "from", "up", "down", "out", 
    "off", "over", "under", "again", "further", "once", "here", "there", "when", 
    "where", "why", "how", "all", "any", "both", "each", "few", "more", "most", 
    "other", "some", "such", "no", "nor", "not", "only", "own", "same", "so", 
    "than", "too", "very", "s", "t", "can", "will", "just", "don", "should", 
    "now", "i", "me", "my", "myself", "we", "our", "ours", "ourselves", "you", 
    "your", "yours", "yourself", "yourselves", "he", "him", "his", "himself", 
    "she", "her", "hers", "herself", "it", "its", "itself", "they", "them", 
    "their", "theirs", "themselves", "what", "which", "who", "whom", "this", 
    "that", "these", "those", "am", "is", "are", "was", "were", "be", "been", 
    "being", "have", "has", "had", "having", "do", "does", "did", "doing", 
    "would", "should", "could", "ought", "i'm", "you're", "he's", "she's", 
    "it's", "we're", "they're", "i've", "you've", "we've", "they've", "i'd", 
    "you'd", "he'd", "she'd", "we'd", "they'd", "i'll", "you'll", "he'll", 
    "she'll", "we'll", "they'll", "isn't", "aren't", "wasn't", "weren't", 
    "hasn't", "haven't", "hadn't", "doesn't", "don't", "didn't", "won't", 
    "wouldn't", "shan't", "shouldn't", "can't", "cannot", "couldn't", "mustn't", 
    "let's", "that's", "who's", "what's", "here's", "there's", "when's", 
    "where's", "why's", "how's", "d", "ll", "m", "o", "re", "ve", "y", "wanna",
    "gonna", "gotta", "please", "basically", "actually", "just", "soo"
}

def _get_fingerprint(message: str) -> str:
    """Extracts unique, ordered content words (up to 7) to fingerprint the message."""
    if not message:
        return ""
    # Lowercase and clean punctuation
    cleaned = re.sub(r"[^\w\s]", " ", message.lower())
    words = cleaned.split()
    
    content_words = []
    seen = set()
    for w in words:
        if w not in _STOPWORDS and w not in seen and not w.isdigit():
            seen.add(w)
            content_words.append(w)
            if len(content_words) >= 7:
                break
    return " ".join(content_words)

def _jaccard_similarity(fp1: str, fp2: str) -> float:
    """Computes Jaccard similarity score between two fingerprints."""
    if not fp1 or not fp2:
        return 0.0
    set1 = set(fp1.split())
    set2 = set(fp2.split())
    if not set1 or not set2:
        return 0.0
    return len(set1 & set2) / len(set1 | set2)

def _find_similar_examples(fingerprint: str, top_n: int = 3) -> list:
    """Loads classification examples and finds top_n similar past examples."""
    from .storage import _load_classification_examples
    
    data = _load_classification_examples()
    examples = data.get("examples", [])
    if not examples or not fingerprint:
        return []
        
    scored = []
    for ex in examples:
        sim = _jaccard_similarity(fingerprint, ex.get("message_fingerprint", ""))
        if sim > 0.0:
            scored.append((sim, ex))
            
    # Sort descending by similarity
    scored.sort(key=lambda x: x[0], reverse=True)
    
    results = []
    for sim, ex in scored[:top_n]:
        results.append({
            "message_fingerprint": ex.get("message_fingerprint"),
            "message_sample": ex.get("message_sample"),
            "correct_domain": ex.get("correct_domain"),
            "correct_tier": ex.get("correct_tier"),
            "classifier_said": ex.get("classifier_said"),
            "was_corrected": ex.get("was_corrected", False),
            "similarity": sim
        })
    return results
