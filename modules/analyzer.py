import re
import json
import os
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY', '')


def call_claude(prompt, system_prompt="", max_tokens=4096):
    if not ANTHROPIC_API_KEY:
        return {"error": "API 키 미설정"}
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        messages = [{"role": "user", "content": prompt}]
        kwargs = {
            "model": "claude-sonnet-4-6",
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system_prompt:
            kwargs["system"] = system_prompt
        response = client.messages.create(**kwargs)
        text = response.content[0].text.strip()
        # 마크다운 코드블록 제거
        fence = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
        if fence:
            text = fence.group(1).strip()
        # JSON 파싱 시도
        try:
            return json.loads(text)
        except:
            pass
        # { } 범위 추출
        start = text.find('{')
        end = text.rfind('}')
        if start != -1 and end > start:
            snippet = text[start:end+1]
            # trailing comma 제거
            snippet = re.sub(r',\s*}', '}', snippet)
            snippet = re.sub(r',\s*]', ']', snippet)
            snippet = re.sub(r'[\x00-\x1f\x7f]', '', snippet)
            try:
                return json.loads(snippet)
            except Exception as e:
                return {"error": f"JSON parse: {e}", "raw_text": text[:2000]}
        return {"error": "No JSON found", "raw_text": text[:2000]}
    except Exception as e:
        return {"error": str(e)}


# ============================================================
# 뉴스 크롤러 클래스
# ============================================================
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


# ============================================================
# AI 분석 프롬프트
# ============================================================
def build_ai_prompt_phase1(articles_summary, theme_list):
    return f"""# 뉴스 플로우 스윙 분석


⚠️ **데이터 정확성 절대 원칙 (위반 금지)**:
1. 제공된 supply_demand.signals 배열에 명시된 표현만 사용. "N일 연속 순매수/순매도", "쌍끌이" 같은 표현은 signals에 없으면 절대 쓰지 말 것.
2. 숫자(순매매량, 거래량, 등락률)는 입력 데이터 값 그대로 인용. 추정·반올림·과장 금지.
3. signals가 비어있으면 "수급 신호 미약/중립"으로만 표현.
4. "외국인 매집", "기관 집중 매수" 같은 표현은 signals에서 명시적으로 확인된 경우만.

## 분석 기준
- A1면 최고 중요도, A2-A3면 핵심, 교차보도 우선
- 뉴스 플로우 연속성 + 재료 지속력

## 오늘의 뉴스:
{articles_summary}

## 테마 목록:
{theme_list}

## 분석일: {datetime.now().strftime('%Y-%m-%d')}

JSON 출력:
{{
  "sectors": [
    {{
      "rank": 1, "name": "섹터명", "score": 92,
      "news_flow": "핵심 뉴스 흐름", "continuity": "연속성 판단",
      "related_themes": ["테마명"], "entry_basis": ["근거1"], "tier": "Tier 1"
    }}
  ],
  "market_risks": ["시장 전체 리스크1", "리스크2"],
  "summary": "전체 요약"
}}"""


def build_ai_prompt_final(stocks_data, articles_summary):
    return f"""# 종합 스윙 매매 리포트 + 악재 분석


⚠️ **데이터 정확성 절대 원칙 (위반 금지)**:
1. 제공된 supply_demand.signals 배열에 명시된 표현만 사용. "N일 연속 순매수/순매도", "쌍끌이" 같은 표현은 signals에 없으면 절대 쓰지 말 것.
2. 숫자(순매매량, 거래량, 등락률)는 입력 데이터 값 그대로 인용. 추정·반올림·과장 금지.
3. signals가 비어있으면 "수급 신호 미약/중립"으로만 표현.
4. "외국인 매집", "기관 집중 매수" 같은 표현은 signals에서 명시적으로 확인된 경우만.

## 통과 종목:
{stocks_data}

## 관련 뉴스:
{articles_summary}

## 분석 요구사항:
1. 각 종목의 악재/리스크 분석 (유상증자, 대주주매도, 재무위험 등)
2. 1~2주 단기 스윙 기준 종합 의견
3. 손익비 1.5:1 이상만 최종 추천
4. 실전 진입 시나리오 구체적 제시

JSON 출력:
{{
  "stocks": [
    {{
      "rank": 1, "name": "종목명", "code": "종목코드",
      "verdict": "적극매수|매수고려|보류|부적합",
      "confidence": 85,
      "risks": [{{"type": "리스크유형", "severity": "low|medium|high", "detail": "설명"}}],
      "positives": ["긍정요소"],
      "entry_scenario": "구체적 진입 시나리오",
      "key_signal": "핵심 시그널",
      "risk_note": "주의사항"
    }}
  ],
  "excluded": [{{"name": "종목명", "reason": "제외 사유"}}],
  "market_view": "시장 전체 판단",
  "overall_strategy": "오늘의 전략"
}}"""


def build_ai_prompt_top3(stocks_data, themes_data, news_summary):
    return f"""# 최종 TOP 3 스윙 추천 종목 선정


⚠️ **데이터 정확성 절대 원칙 (위반 금지)**:
1. 제공된 supply_demand.signals 배열에 명시된 표현만 사용. "N일 연속 순매수/순매도", "쌍끌이" 같은 표현은 signals에 없으면 절대 쓰지 말 것.
2. 숫자(순매매량, 거래량, 등락률)는 입력 데이터 값 그대로 인용. 추정·반올림·과장 금지.
3. signals가 비어있으면 "수급 신호 미약/중립"으로만 표현.
4. "외국인 매집", "기관 집중 매수" 같은 표현은 signals에서 명시적으로 확인된 경우만.

당신은 한국 주식시장 스윙 트레이딩 전문 애널리스트입니다.
아래 기술적 분석을 통과한 종목들 중에서 향후 1~2주 스윙 매매 관점에서
가장 유망한 TOP 3 종목을 선정하고 상세한 근거를 제시하세요.

## 분석 통과 종목:
{stocks_data}

## 오늘의 주요 테마:
{themes_data}

## 관련 뉴스 요약:
{news_summary}

## 선정 기준:
1. 뉴스 모멘텀 + 테마 지속성 (단발성 재료 배제)
2. 기술적 차트 (장대양봉 + 박스권 돌파 가능성)
3. 수급 (외국인/기관 매수세)
4. 리스크 대비 수익비 (최소 1.5:1)
5. 악재/리스크 요소 (유상증자, 대주주 매도, 재무 불안 등)

## 반드시 포함:
- 왜 이 종목인지 구체적 근거 3가지 이상
- 차트 기반 진입 시나리오
- 리스크 요인과 대응 전략
- 예상 수익률 범위

JSON 출력:
{{
  "top3": [
    {{
      "rank": 1,
      "name": "종목명",
      "code": "종목코드",
      "theme": "관련테마",
      "confidence": 85,
      "verdict": "적극매수|매수고려",
      "summary": "한 줄 요약 (왜 이 종목인가)",
      "reasons": [
        "근거1: 구체적 설명",
        "근거2: 구체적 설명",
        "근거3: 구체적 설명"
      ],
      "entry_plan": {{
        "entry_price": "진입가 범위",
        "target_price": "목표가",
        "stop_loss": "손절가",
        "expected_return": "+8~12%",
        "risk_reward": "2.5:1",
        "holding_period": "5~10일"
      }},
      "risks": [
        {{"factor": "리스크 요인", "severity": "low|medium|high", "mitigation": "대응 방법"}}
      ],
      "catalysts": ["향후 예상 촉매제1", "촉매제2"],
      "news_momentum": "관련 뉴스 흐름 분석"
    }}
  ],
  "market_context": "현재 시장 상황 요약",
  "strategy_note": "오늘의 스윙 전략 총평",
  "caution": "전체적 주의사항"
}}"""


def build_ai_prompt_sleepers(sleeper_stocks, themes_data, news_summary):
    return f"""# 숨은 보석 TOP 3 — 핫 테마 속 미슈팅 종목 발굴


⚠️ **데이터 정확성 절대 원칙 (위반 금지)**:
1. 제공된 supply_demand.signals 배열에 명시된 표현만 사용. "N일 연속 순매수/순매도", "쌍끌이" 같은 표현은 signals에 없으면 절대 쓰지 말 것.
2. 숫자(순매매량, 거래량, 등락률)는 입력 데이터 값 그대로 인용. 추정·반올림·과장 금지.
3. signals가 비어있으면 "수급 신호 미약/중립"으로만 표현.
4. "외국인 매집", "기관 집중 매수" 같은 표현은 signals에서 명시적으로 확인된 경우만.

당신은 한국 주식시장 스윙 트레이딩 전문 애널리스트입니다.
아래 종목들은 **뉴스에서 핫한 테마에 속하지만, 아직 주가 급등(슈팅)이 나오지 않은** 종목들입니다.
이 중에서 향후 1~2주 내 슈팅 가능성이 가장 높은 TOP 3를 선정하세요.

## 핵심 판단 기준:
1. **테마 지속성**: 단발성 재료가 아닌, 뉴스 모멘텀이 지속될 테마인가?
2. **수급 전환 조짐**: 거래량 미세 증가, 외국인/기관 매집 시작 등
3. **차트 위치**: 바닥권 or 박스 하단 — 슈팅 직전 위치인가?
4. **테마 내 후행주**: 같은 테마의 다른 종목은 이미 올랐는가? (후행 수혜 가능성)
5. **악재 부재**: 유상증자, 대주주 매도 등 리스크 없는가?

## 미슈팅 후보 종목:
{sleeper_stocks}

## 오늘의 주요 테마:
{themes_data}

## 관련 뉴스:
{news_summary}

JSON 출력:
{{
  "sleepers": [
    {{
      "rank": 1,
      "name": "종목명",
      "code": "종목코드",
      "theme": "관련테마",
      "confidence": 70,
      "summary": "왜 이 종목이 아직 안 올랐고, 왜 곧 오를 수 있는지 한 줄 요약",
      "reasons": [
        "근거1: 테마 내 후행주 — 같은 테마 대장주 이미 급등",
        "근거2: 수급 전환 조짐 설명",
        "근거3: 차트 위치/기술적 근거"
      ],
      "entry_plan": {{
        "entry_price": "진입가 범위",
        "target_price": "목표가",
        "stop_loss": "손절가",
        "expected_return": "+10~15%",
        "risk_reward": "2:1",
        "holding_period": "5~14일"
      }},
      "risks": [
        {{"factor": "리스크 요인", "severity": "low|medium|high", "mitigation": "대응 방법"}}
      ],
      "trigger_signal": "이 시그널이 나오면 진입 — 구체적 트리거",
      "theme_leader": "같은 테마에서 이미 급등한 대장주 이름"
    }}
  ],
  "analysis_note": "숨은 보석 분석 총평",
  "caution": "주의사항"
}}}}"""


# ============================================================
# 크롤링 실행 스레드
