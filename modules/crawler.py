import requests
from bs4 import BeautifulSoup
from datetime import datetime
import re
import time
import math


class StockNewsCrawler:
    def __init__(self):
        self.base_url = "https://news.naver.com/main/list.naver"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        self.news_companies = {
            "매일경제": "009",
            "한국경제": "015",
            "서울경제": "011",
            "헤럴드경제": "016",
            "아시아경제": "277",
            "파이낸셜뉴스": "014"
        }
        self.general_newspapers = {
            "조선일보": "023",
            "중앙일보": "025",
            "동아일보": "020"
        }
        self._theme_cache = {}
        self._theme_cache_time = None
        self.session = requests.Session()
        self._cache_duration = 3600

    def get_news_list(self, date, company_name, company_code, progress_callback=None):
        articles = []
        page_articles = {}
        params = {
            "mode": "LPOD", "mid": "sec", "oid": company_code,
            "listType": "paper", "date": date
        }
        try:
            response = requests.get(self.base_url, params=params, headers=self.headers, timeout=15)
            soup = BeautifulSoup(response.text, 'html.parser')
            sections = soup.find_all('h4', class_='paper_h4')
            for section in sections:
                current_page = section.text.strip()
                page_number = self._extract_page_number(current_page)
                if page_number not in page_articles:
                    page_articles[page_number] = []
                article_list = section.find_next('ul', class_='type13')
                if not article_list:
                    continue
                article_list = article_list.find_all('li')
                for article in article_list:
                    if article.select_one("dt.photo"):
                        title_tag = article.select("dt a")
                        title_tag = title_tag[1] if len(title_tag) > 1 else title_tag[0] if title_tag else None
                    else:
                        title_tag = article.select_one("dt a")
                    if title_tag:
                        title_text = title_tag.text.strip()
                        link = title_tag.get('href')
                        newspaper_info = article.select_one("span.newspaper_info")
                        is_top = "TOP" in newspaper_info.text if newspaper_info else False
                        article_data = {
                            'title': title_text, 'link': link, 'date': date,
                            'page': current_page, 'is_top': is_top,
                            'company': company_name, 'body': ''
                        }
                        page_articles[page_number].append(article_data)
            for page_num in sorted(page_articles.keys()):
                articles.extend(page_articles[page_num])
        except Exception as e:
            if progress_callback:
                progress_callback(f"❌ {company_name} 크롤링 오류: {str(e)}")
        return articles

    def get_article_body(self, url):
        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            soup = BeautifulSoup(response.text, 'html.parser')
            article_body = soup.select_one('#dic_area')
            if article_body:
                return article_body.get_text().strip()[:2000]
            return ""
        except:
            return ""

    def _extract_page_number(self, page_text):
        match = re.search(r'[A]?(\d+)', page_text)
        return int(match.group(1)) if match else 999

    def get_sector_stocks_api(self):
        current_time = datetime.now()
        if (self._theme_cache and self._theme_cache_time and
            (current_time - self._theme_cache_time).seconds < self._cache_duration):
            return self._theme_cache
        try:
            api_url = "https://api.infostock.co.kr:9081/web/theme/all"
            headers = {
                "Accept": "*/*", "Content-Type": "application/json",
                "Origin": "https://www.infostock.co.kr",
                "Referer": "https://www.infostock.co.kr/",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
            data = {"type": "all", "page": 1, "size": 1000, "sort": "name,asc"}
            for attempt in range(3):
                try:
                    response = self.session.post(api_url, headers=headers, json=data, timeout=(5, 30))
                    if response.status_code == 200:
                        result = response.json()
                        self._theme_cache = result
                        self._theme_cache_time = current_time
                        return result
                except:
                    time.sleep(1)
            return {}
        except:
            return {}

    def get_theme_detail(self, theme_code):
        url = "https://api.infostock.co.kr:9081/web/theme/detail"
        try:
            code = str(int(theme_code))
            data = {"code": code, "idx": "0"}
            headers = {
                'Content-Type': 'application/json', 'Accept': 'application/json',
                'Origin': 'https://new.infostock.co.kr',
                'Referer': 'https://new.infostock.co.kr/',
                'User-Agent': 'Mozilla/5.0'
            }
            response = requests.post(url, json=data, headers=headers, timeout=15)
            if response.status_code == 200:
                result = response.json()
                if result.get('success'):
                    return result.get('data', {})
            return None
        except:
            return None

    def analyze_sectors(self, articles):
        theme_data = self.get_sector_stocks_api()
        if not theme_data or 'data' not in theme_data:
            return {}
        theme_mentions = {}
        items = theme_data.get('data', {}).get('items', [])
        for article in articles:
            title_lower = article['title'].lower()
            body_lower = article.get('body', '').lower()
            combined = title_lower + ' ' + body_lower
            for theme in items:
                theme_name = theme['name']
                theme_code = str(int(str(theme['code']).lstrip('0')))
                keywords = [theme_name.lower()]
                if '(' in theme_name:
                    base = theme_name.split('(')[0].strip().lower()
                    keywords.append(base)
                    parens = re.findall(r'\((.*?)\)', theme_name)
                    for p in parens:
                        keywords.extend([k.strip().lower() for k in p.split('/')])
                for kw in keywords:
                    if len(kw) >= 2 and kw in combined:
                        if theme_name not in theme_mentions:
                            theme_mentions[theme_name] = {
                                'count': 0, 'code': theme_code,
                                'articles': [], 'top_count': 0, 'companies': set()
                            }
                        theme_mentions[theme_name]['count'] += 1
                        if article['is_top']:
                            theme_mentions[theme_name]['top_count'] += 1
                        theme_mentions[theme_name]['companies'].add(article['company'])
                        theme_mentions[theme_name]['articles'].append({
                            'title': article['title'], 'company': article['company'],
                            'page': article['page'], 'is_top': article['is_top']
                        })
                        break
        for k in theme_mentions:
            theme_mentions[k]['companies'] = list(theme_mentions[k]['companies'])
        return theme_mentions

    def get_stock_price_data(self, stock_code):
        try:
            url = f"https://fchart.stock.naver.com/sise.nhn?symbol={stock_code}&timeframe=day&count=120&requestType=0"
            response = requests.get(url, headers=self.headers, timeout=10)
            soup = BeautifulSoup(response.text, 'html.parser')
            items = soup.find_all('item')
            prices = []
            for item in items:
                data = item['data'].split('|')
                if len(data) >= 5:
                    prices.append({
                        'date': data[0], 'open': int(data[1]), 'high': int(data[2]),
                        'low': int(data[3]), 'close': int(data[4]),
                        'volume': int(data[5]) if len(data) > 5 else 0
                    })
            return prices
        except:
            return []

    def get_current_price(self, stock_code):
        try:
            url = f"https://finance.naver.com/item/main.naver?code={stock_code}"
            response = requests.get(url, headers=self.headers, timeout=10)
            soup = BeautifulSoup(response.text, 'html.parser')
            price_tag = soup.select_one('.no_today .blind')
            if price_tag:
                return int(price_tag.text.replace(',', ''))
            return None
        except:
            return None

    def get_market_cap(self, stock_code):
        try:
            url = f"https://finance.naver.com/item/main.naver?code={stock_code}"
            response = requests.get(url, headers=self.headers, timeout=10)
            soup = BeautifulSoup(response.text, 'html.parser')
            cap_tag = soup.select_one('#_market_sum')
            if cap_tag:
                text = cap_tag.get_text().strip().replace(',', '').replace('\n', '').replace('\t', '')
                nums = re.findall(r'[\d]+', text)
                if nums:
                    return int(nums[0])
            return 0
        except:
            return 0

    def calculate_technical_indicators(self, prices):
        if len(prices) < 20:
            return None
        closes = [p['close'] for p in prices]
        highs = [p['high'] for p in prices]
        lows = [p['low'] for p in prices]
        volumes = [p['volume'] for p in prices]

        ma5 = sum(closes[-5:]) / 5
        ma20 = sum(closes[-20:]) / 20
        ma60 = sum(closes[-60:]) / 60 if len(closes) >= 60 else None
        ma120 = sum(closes[-120:]) / 120 if len(closes) >= 120 else None

        gains, losses = [], []
        for i in range(-14, 0):
            diff = closes[i] - closes[i-1]
            gains.append(max(0, diff))
            losses.append(max(0, -diff))
        avg_gain = sum(gains) / 14
        avg_loss = sum(losses) / 14
        rs = avg_gain / avg_loss if avg_loss > 0 else 100
        rsi = 100 - (100 / (1 + rs))

        ema12 = self._ema(closes, 12)
        ema26 = self._ema(closes, 26)
        macd = ema12 - ema26
        macd_values = []
        for i in range(max(26, len(closes))):
            if i >= 26:
                e12 = self._ema(closes[:i+1], 12)
                e26 = self._ema(closes[:i+1], 26)
                macd_values.append(e12 - e26)
        signal = self._ema(macd_values, 9) if len(macd_values) >= 9 else 0
        histogram = macd - signal

        std20 = math.sqrt(sum((c - ma20)**2 for c in closes[-20:]) / 20)
        bb_upper = ma20 + 2 * std20
        bb_lower = ma20 - 2 * std20

        current = closes[-1]
        recent = closes[-60:] if len(closes) >= 60 else closes
        support = min(recent)
        resistance = max(recent)

        cross = None
        if len(closes) >= 21:
            prev_ma5 = sum(closes[-6:-1]) / 5
            prev_ma20 = sum(closes[-21:-1]) / 20
            if prev_ma5 < prev_ma20 and ma5 > ma20:
                cross = 'golden'
            elif prev_ma5 > prev_ma20 and ma5 < ma20:
                cross = 'dead'

        return {
            'current': current,
            'ma5': round(ma5), 'ma20': round(ma20),
            'ma60': round(ma60) if ma60 else None,
            'ma120': round(ma120) if ma120 else None,
            'rsi': round(rsi, 1),
            'macd': round(macd, 2), 'macd_signal': round(signal, 2),
            'macd_histogram': round(histogram, 2),
            'bb_upper': round(bb_upper), 'bb_lower': round(bb_lower),
            'support': support, 'resistance': resistance, 'cross': cross,
        }

    def _ema(self, data, period):
        if len(data) < period:
            return sum(data) / len(data) if data else 0
        k = 2 / (period + 1)
        ema = sum(data[:period]) / period
        for price in data[period:]:
            ema = price * k + ema * (1 - k)
        return ema


