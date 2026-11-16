"""Stable pseudonyms for peers in project review ("Thirsty Herschel")."""

import hashlib
from functools import lru_cache
from pathlib import Path

WORDS_DIR = Path(__file__).with_name('peer_pseudonym_words')


def _word_list(name):
    words = []
    for raw_line in (WORDS_DIR / name).read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if line and not line.startswith('#'):
            words.append(line)
    return tuple(words)


@lru_cache(maxsize=1)
def _words():
    return _word_list('adjectives.txt'), _word_list('famous_people.txt')


def peer_pseudonym(user_id, course_id):
    """Deterministic ``"<Adjective> <FamousPerson>"`` for one learner in one course."""
    adjectives, people = _words()
    digest = hashlib.sha256(f'aisl-peer:{course_id}:{user_id}'.encode()).digest()
    adjective = adjectives[int.from_bytes(digest[:8], 'big') % len(adjectives)]
    person = people[int.from_bytes(digest[8:16], 'big') % len(people)]
    return f'{adjective} {person}'
