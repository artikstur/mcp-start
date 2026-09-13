from __future__ import annotations

import re
from collections import deque
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ..config import settings
from ..http import get_text


ALLOWED_HOSTS = {"kpfu.ru", "www.kpfu.ru"}
ALLOWED_PREFIX = "/biology-medicine"


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _tokenize(text: str) -> list[str]:
    return [
        word.lower()
        for word in re.findall(r"[A-Za-zА-Яа-яЁё0-9]+", text)
        if len(word) > 2
    ]


def _same_site(url: str) -> bool:
    parsed = urlparse(url)
    return (
            parsed.netloc in ALLOWED_HOSTS
            and parsed.path.startswith(ALLOWED_PREFIX)
    )


# ---------------------------------------------------------
# Levenshtein
# ---------------------------------------------------------

def _levenshtein(a: str, b: str) -> int:
    """
    Расстояние Левенштейна между двумя словами.
    Чем меньше число, тем слова ближе.
    """

    if a == b:
        return 0

    if not a:
        return len(b)

    if not b:
        return len(a)

    previous = list(range(len(b) + 1))

    for i, char_a in enumerate(a, start=1):
        current = [i]

        for j, char_b in enumerate(b, start=1):
            insert_cost = current[j - 1] + 1
            delete_cost = previous[j] + 1
            replace_cost = previous[j - 1] + (char_a != char_b)

            current.append(
                min(insert_cost, delete_cost, replace_cost)
            )

        previous = current

    return previous[-1]


def _word_similarity(query_word: str, text_word: str) -> float:
    """
    1.0 = полное совпадение.
    Чем меньше значение, тем хуже совпадение.
    """

    if query_word == text_word:
        return 1.0

    distance = _levenshtein(query_word, text_word)
    max_len = max(len(query_word), len(text_word))

    if max_len == 0:
        return 0.0

    similarity = 1 - distance / max_len

    # Слишком далёкие слова не считаем совпадением.
    return similarity if similarity >= 0.65 else 0.0


# ---------------------------------------------------------
# Поиск
# ---------------------------------------------------------

def _find_relevant_fragment(
        query: str,
        title: str,
        text: str,
        max_length: int = 1200,
) -> str:
    """
    Ищет в тексте место, наиболее похожее на запрос,
    и возвращает фрагмент вокруг него.
    """

    query_words = _tokenize(query)

    if not query_words:
        return text[:max_length]

    text_words = list(
        re.finditer(
            r"[A-Za-zА-Яа-яЁё0-9]+",
            text,
        )
    )

    if not text_words:
        return text[:max_length]

    best_position = 0
    best_score = 0.0

    # Проверяем небольшие окна текста.
    window_size = 12

    for i in range(len(text_words)):
        window = text_words[i : i + window_size]

        window_words = [
            match.group(0).lower()
            for match in window
        ]

        score = 0.0

        for query_word in query_words:
            best_similarity = max(
                (
                    _word_similarity(query_word, word)
                    for word in window_words
                ),
                default=0.0,
            )

            score += best_similarity

        if score > best_score:
            best_score = score
            best_position = i

    if best_score == 0:
        return text[:max_length]

    start_match = text_words[best_position]

    # Берём текст немного до найденного места.
    start = max(0, start_match.start() - 300)
    end = min(len(text), start + max_length)

    fragment = text[start:end].strip()

    if start > 0:
        fragment = "..." + fragment

    if end < len(text):
        fragment += "..."

    return fragment


def _score(query: str, title: str, text: str) -> float:
    """
    Оценивает релевантность страницы запросу.

    Точные совпадения получают больший вес.
    Опечатки учитываются через Левенштейна.
    """

    query_words = _tokenize(query)

    if not query_words:
        return 0.0

    title_words = _tokenize(title)
    text_words = _tokenize(text)

    score = 0.0

    for query_word in query_words:

        # Ищем лучшее совпадение в заголовке.
        title_similarity = max(
            (
                _word_similarity(query_word, word)
                for word in title_words
            ),
            default=0.0,
        )

        if title_similarity:
            score += title_similarity * 5

        # Ищем лучшее совпадение в тексте.
        text_similarities = [
            _word_similarity(query_word, word)
            for word in text_words
        ]

        if text_similarities:
            best = max(text_similarities)

            if best:
                score += best

                # Дополнительный бонус за точное слово.
                if query_word in text_words:
                    score += min(text_words.count(query_word), 5)

    return round(score, 3)


# ---------------------------------------------------------
# HTML
# ---------------------------------------------------------

async def _extract_page(url: str) -> dict:
    html = await get_text(url)

    soup = BeautifulSoup(html, "html.parser")

    title = _normalize(
        soup.title.get_text(" ", strip=True)
        if soup.title
        else ""
    )

    # Убираем технические элементы.
    for element in soup(
            ["script", "style", "noscript", "svg"]
    ):
        element.decompose()

    main = soup.find("main") or soup

    text = _normalize(
        main.get_text(" ", strip=True)
    )

    links = []

    for a in soup.find_all("a", href=True):
        absolute = urljoin(
            url,
            a["href"],
        ).split("#", 1)[0]

        if _same_site(absolute):
            links.append(absolute)

    return {
        "url": url,
        "title": title,
        "text": text,
        "links": list(dict.fromkeys(links)),
    }


# ---------------------------------------------------------
# Crawl
# ---------------------------------------------------------

async def crawl_pages() -> list[dict]:
    queue = deque(
        [settings.institute_base_url]
    )

    seen: set[str] = set()
    pages: list[dict] = []

    while (
            queue
            and len(pages) < settings.max_crawl_pages
    ):
        url = queue.popleft()

        if url in seen:
            continue

        if not _same_site(url):
            continue

        seen.add(url)

        try:
            page = await _extract_page(url)
        except Exception as e:
            print(f"ERROR: {url}")
            print(type(e).__name__, e)
            continue

        pages.append(
            {
                "url": page["url"],
                "title": page["title"],
                "text": page["text"],
            }
        )

        for link in page["links"]:
            if link not in seen:
                queue.append(link)

    return pages


# ---------------------------------------------------------
# Public search function
# ---------------------------------------------------------

async def search_institute_info(
        query: str,
        limit: int = 5,
) -> list[dict]:

    query = _normalize(query)

    if not query:
        return []

    pages = await crawl_pages()

    ranked = sorted(
        pages,
        key=lambda page: _score(
            query,
            page["title"],
            page["text"],
        ),
        reverse=True,
    )

    results = []

    for page in ranked:

        score = _score(
            query,
            page["title"],
            page["text"],
        )

        if score <= 0:
            continue

        snippet = _find_relevant_fragment(
            query=query,
            title=page["title"],
            text=page["text"],
        )

        results.append(
            {
                "title": page["title"],
                "url": page["url"],
                "snippet": snippet,
                "score": score,
            }
        )

        if len(results) >= limit:
            break

    return results
