import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import parse_qs, quote, urlencode, urlparse

import requests
from bs4 import BeautifulSoup


SEARCH_URL = 'https://html.duckduckgo.com/html/'
PRICE_PATTERN = re.compile(r'R\$\s*([\d.]+(?:,\d{1,2})?)', re.IGNORECASE)


def search_product_prices(product_name):
    response = requests.get(
        SEARCH_URL,
        params={'q': f'"{product_name}" preço comprar'},
        headers={'User-Agent': 'Mozilla/5.0 (compatible; RUN_LAB price lookup)'},
        timeout=8,
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    results = []

    for result in soup.select('.result'):
        title_link = result.select_one('.result__title a')
        snippet = result.select_one('.result__snippet')
        if title_link is None or snippet is None:
            continue

        title = ' '.join(title_link.get_text(' ', strip=True).split())
        description = ' '.join(snippet.get_text(' ', strip=True).split())
        match = PRICE_PATTERN.search(f'{title} {description}')
        if not match:
            continue

        raw_price = match.group(1)
        price_text = raw_price.replace('.', '').replace(',', '.') if ',' in raw_price else raw_price
        try:
            price = Decimal(price_text)
        except InvalidOperation:
            continue
        result_url = result.select_one('.result__url')
        store_url = result_url.get_text(' ', strip=True) if result_url else title_link.get('href', '')
        href = title_link.get('href', '')
        parsed_href = urlparse(href if '://' in href else f'https:{href}' if href.startswith('//') else href)
        destination = parse_qs(parsed_href.query).get('uddg', [href])[0]
        if not destination.startswith(('https://', 'http://')):
            continue
        store = urlparse(destination).netloc.removeprefix('www.')
        results.append({
            'produto': title,
            'loja': store or 'Loja online',
            'preco': str(price),
            'preco_formatado': f"R$ {price:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.'),
            'url': destination,
            'fonte': 'internet',
        })

    return sorted(results, key=lambda item: Decimal(item['preco']))[:3]


def mock_product_prices(product_name, reference_price):
    base_price = Decimal(reference_price)
    if base_price <= 0:
        base_price = Decimal('10.00')

    stores = (
        ('Amazon Brasil', 'https://www.amazon.com.br/s'),
        ('Mercado Livre', 'https://lista.mercadolivre.com.br/'),
        ('Magalu', 'https://www.magazineluiza.com.br/busca/'),
    )
    multipliers = (Decimal('0.97'), Decimal('1.00'), Decimal('1.04'))
    options = []
    for (store, search_url), multiplier in zip(stores, multipliers):
        price = (base_price * multiplier).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        if '?' in search_url:
            url = f'{search_url}?{urlencode({"k": product_name})}'
        else:
            url = f'{search_url}{quote(product_name, safe="")}'
        options.append({
            'produto': product_name,
            'loja': store,
            'preco': str(price),
            'preco_formatado': f"R$ {price:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.'),
            'url': url,
            'fonte': 'estimativa',
        })
    return options
