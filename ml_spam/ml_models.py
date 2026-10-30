import re

class SpamMLModel:
    def __init__(self):
        self.spam_keywords = [
            'test', 'testing', 'sample', 'example', 'asdf', 'qwerty',
            'bitcoin', 'crypto', 'casino', 'lottery', 'prize', 'winner',
            'viagra', 'pharmacy', 'loan', 'cash', 'money',
            'panalo', 'premyo', 'libre', 'pabonus', 'pautang'
        ]
    
    def predict(self, text):
        text_lower = text.lower()
        score = 0
        
        for kw in self.spam_keywords:
            if kw in text_lower:
                score += 1
        
        if len(text) < 20:
            score += 1
        
        if text.isupper() and len(text) > 10:
            score += 1
        
        if text.count('!') > 2:
            score += 1
        
        if 'http' in text_lower:
            score += 1
        
        is_spam = score >= 2
        confidence = min(score / 5, 0.95)
        
        return is_spam, confidence


spam_ml = SpamMLModel()