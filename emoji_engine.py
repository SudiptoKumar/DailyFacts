from __future__ import annotations

CATEGORY_EMOJI = {
    "science": "🔬", "space": "🌌", "human body": "🫀", "psychology": "🧠",
    "animals": "🐾", "geography": "🌍", "history": "🏛", "technology": "💻",
    "mystery & unexplained": "🕵️", "mystery": "🕵️", "nature": "🌿", "food": "🍽",
    "inventions": "💡", "country facts": "🗺️", "mythology & legends": "🏺",
    "ocean & marine": "🌊", "ocean": "🌊", "language": "🔤", "money & economics": "💰",
    "money": "💰", "everyday life": "🏠", "ancient civilizations": "🏛",
    "world records & extremes": "🏆",
}

SUBJECT_RULES = [
    ("dna", "🧬"), ("gene", "🧬"), ("brain", "🧠"), ("heart", "❤️"), ("blood", "🩸"),
    ("moon", "🌙"), ("sun", "☀️"), ("planet", "🪐"), ("mars", "🔴"), ("star", "⭐"),
    ("black hole", "🕳️"), ("galaxy", "🌌"), ("volcano", "🌋"), ("earthquake", "🌎"),
    ("ocean", "🌊"), ("sea", "🌊"), ("fish", "🐟"), ("shark", "🦈"), ("whale", "🐋"),
    ("octopus", "🐙"), ("bird", "🐦"), ("eagle", "🦅"), ("cat", "🐈"), ("dog", "🐕"),
    ("snake", "🐍"), ("spider", "🕷️"), ("bee", "🐝"), ("tree", "🌳"), ("flower", "🌸"),
    ("leaf", "🍃"), ("rain", "🌧️"), ("ice", "🧊"), ("fire", "🔥"), ("coffee", "☕"),
    ("chocolate", "🍫"), ("bread", "🍞"), ("money", "💵"), ("coin", "🪙"), ("bank", "🏦"),
    ("computer", "💻"), ("internet", "🌐"), ("phone", "📱"), ("robot", "🤖"),
    ("language", "🔤"), ("book", "📚"), ("king", "👑"), ("queen", "👑"), ("war", "⚔️"),
]


def category_emoji(category: str) -> str:
    return CATEGORY_EMOJI.get(category.strip().casefold(), "📚")


def subject_emoji(title: str, fact: str, category: str) -> str:
    hay = f"{title} {fact}".casefold()
    for token, emoji in SUBJECT_RULES:
        if token in hay:
            return emoji
    return category_emoji(category)
