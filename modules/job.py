import threading
from modules.surge_scanner import run_surge_scan
import time
import json
import math
from datetime import datetime

from modules.crawler import StockNewsCrawler
from modules.analyzer import call_claude, build_ai_prompt_phase1, build_ai_prompt_final, build_ai_prompt_top3, build_ai_prompt_sleepers, ANTHROPIC_API_KEY
from swing_engine import analyze_stock_swing

# 크롤링 상태 관리
crawl_state = {
    'running': False,
    'progress': [],
    'result': None,
    'error': None,
    'phase': '',
    'percent': 0,
    'ai_analysis': None,
}
state_lock = threading.Lock()

def run_crawl_job(mode='all'):
    global crawl_state
    run_swing = mode in ('all', 'swing')
    run_surge = mode in ('all', 'surge')
    crawler = StockNewsCrawler()
    today = datetime.now().strftime("%Y%m%d")

    def log(msg):
        with state_lock:
            crawl_state['progress'].append(msg)

    try:
        with state_lock:
            crawl_state['running'] = True
            crawl_state['progress'] = []
            crawl_state['result'] = None
            crawl_state['error'] = None
            crawl_state['ai_analysis'] = None
            crawl_state['phase'] = '뉴스 수집 중'
            crawl_state['percent'] = 0

        all_articles = []
        company_stats = {}

        # Phase 1: 뉴스 크롤링
        all_sources = list(crawler.news_companies.items()) + list(crawler.general_newspapers.items())
        total = len(all_sources)

        for i, (name, code) in enumerate(all_sources):
            log(f"📰 {name} 기사 수집 중...")
            with state_lock:
                crawl_state['percent'] = int((i / total) * 25)
            articles = crawler.get_news_list(today, name, code, log)
            all_articles.extend(articles)
            company_stats[name] = len(articles)
            log(f"  ✅ {name}: {len(articles)}개 기사 수집")

        log(f"\n📊 총 {len(all_articles)}개 기사 수집 완료")

        # Phase 2: 본문 크롤링
        with state_lock:
            crawl_state['phase'] = '기사 본문 분석 중'
            crawl_state['percent'] = 25

        priority_articles = [a for a in all_articles if a['is_top'] or 'A1' in a.get('page', '') or '1면' in a.get('page', '')]
        log(f"\n📖 주요 기사 {len(priority_articles)}개 본문 수집 중...")

        for i, article in enumerate(priority_articles[:50]):
            body = crawler.get_article_body(article['link'])
            article['body'] = body
            if i % 10 == 0:
                with state_lock:
                    crawl_state['percent'] = 25 + int((i / min(len(priority_articles), 50)) * 15)
            time.sleep(0.3)

        log(f"  ✅ 본문 수집 완료")

        # Phase 3: 테마 매칭
        with state_lock:
            crawl_state['phase'] = '테마 분석 중'
            crawl_state['percent'] = 40

        log(f"\n🔍 테마 매칭 분석 중...")
        theme_analysis = crawler.analyze_sectors(all_articles)

        scored_themes = []
        for name, data in theme_analysis.items():
            score = data['count'] * 2 + data['top_count'] * 5 + len(data['companies']) * 3
            scored_themes.append({
                'name': name, 'code': data['code'], 'score': score,
                'count': data['count'], 'top_count': data['top_count'],
                'companies': data['companies'], 'articles': data['articles'][:5]
            })

        scored_themes.sort(key=lambda x: x['score'], reverse=True)
        top_themes = scored_themes[:15]
        log(f"  ✅ {len(scored_themes)}개 테마 감지, 상위 {len(top_themes)}개 선정")

        # Phase 4: 테마 상세 + 종목 조회
        with state_lock:
            crawl_state['phase'] = '종목 정보 수집 중'
            crawl_state['percent'] = 50

        # 테마 순위 매핑
        theme_rank_map = {t['name']: i + 1 for i, t in enumerate(top_themes)}

        for i, theme in enumerate(top_themes):
            detail = crawler.get_theme_detail(theme['code'])
            if detail:
                stock_items = detail.get('stockItems', [])
                theme['stocks'] = [{'name': s['name'], 'code': s['code']} for s in stock_items[:10]]
                history = detail.get('items', [])[:3]
                theme['history'] = [{'date': h.get('showDate', ''), 'content': h.get('content', '')} for h in history]
            else:
                theme['stocks'] = []
                theme['history'] = []
            with state_lock:
                crawl_state['percent'] = 50 + int((i / len(top_themes)) * 10)
            time.sleep(0.3)

        log(f"  ✅ 종목 상세 정보 수집 완료")

        # Phase 5: 3단계 스윙 분석
        with state_lock:
            crawl_state['phase'] = '스윙 3단계 분석 중'
            crawl_state['percent'] = 60

        log(f"\n📈 3단계 스윙 분석 시작...")
        log(f"  1차: 장대양봉 → 2차: 박스권 → 3차: 수급")
        tech_results = {}
        total_analyzed = 0
        total_passed = 0

        all_stocks_to_analyze = []
        for theme in top_themes[:7]:
            for stock in theme.get('stocks', [])[:7]:
                if stock['code'] not in [s['code'] for s in all_stocks_to_analyze]:
                    stock['theme'] = theme['name']
                    all_stocks_to_analyze.append(stock)

        for i, stock in enumerate(all_stocks_to_analyze):
            total_analyzed += 1
            prices = crawler.get_stock_price_data(stock['code'])

            if prices and len(prices) >= 10:
                # 기술적 지표 (참고용)
                tech = crawler.calculate_technical_indicators(prices) if len(prices) >= 20 else None

                # 현재가 업데이트
                current_price = crawler.get_current_price(stock['code'])
                if current_price and tech:
                    tech['current'] = current_price

                # 시가총액
                market_cap = crawler.get_market_cap(stock['code'])

                # ★ 핵심: prices 리스트를 직접 전달
                swing = analyze_stock_swing(
                    prices=prices,
                    code=stock['code'],
                    stock_name=stock['name'],
                    news_articles=all_articles
                )

                grade = swing['grade']
                show = swing['show']

                tech_results[stock['code']] = {
                    'name': stock['name'],
                    'code': stock['code'],
                    'theme': stock.get('theme', ''),
                    'theme_rank': theme_rank_map.get(stock.get('theme', ''), 99),
                    'current_price': current_price or (prices[-1]['close'] if prices else 0),
                    'market_cap': market_cap,
                    'indicators': tech if tech else {},
                    'swing': swing,
                    'show': show,
                    'show_c': False,
                    'prices': [{'date': p['date'], 'open': p['open'], 'high': p['high'],
                                'low': p['low'], 'close': p['close'], 'volume': p['volume']}
                               for p in prices[-60:]]
                }

                if grade in ['A', 'B']:
                    total_passed += 1
                    log(f"  ✅ {stock['name']} → {grade}등급 ({swing['score']}점) [{swing['stages_passed']}/3단계]")
                elif grade == 'C':
                    log(f"  🟡 {stock['name']} → C등급 관심종목 ({swing['score']}점)")
                else:
                    log(f"  ⬜ {stock['name']} → D등급 제외")

            if i % 3 == 0:
                with state_lock:
                    crawl_state['percent'] = 60 + int((i / max(len(all_stocks_to_analyze), 1)) * 20)
            time.sleep(0.3)

        # C등급 카운트
        total_c = len([s for s in tech_results.values() if s.get('show_c')])
        log(f"\n  📊 {total_analyzed}개 분석 → A/B {total_passed}개 통과, C등급 관심 {total_c}개")

        # Phase 6: AI 분석 (Claude)
        ai_analysis = None
        if ANTHROPIC_API_KEY and run_swing:
            with state_lock:
                crawl_state['phase'] = 'AI 분석 중'
                crawl_state['percent'] = 82

            log(f"\n🧠 Claude AI 분석 시작...")

            # Phase 1: 뉴스플로우
            articles_summary = "\n".join([
                f"[{a['company']}] {'★' if a['is_top'] else ''} {a['page']} - {a['title']}"
                for a in all_articles[:100]
            ])
            theme_list = "\n".join([f"[{t['code']}] {t['name']} (언급:{t['count']})" for t in top_themes])

            log(f"  🔍 뉴스플로우 분석...")
            ai_phase1 = call_claude(build_ai_prompt_phase1(articles_summary, theme_list), max_tokens=12000)
            log(f"  ✅ 뉴스플로우 분석 완료")

            with state_lock:
                crawl_state['percent'] = 88

            # Phase 2: 종합 리포트 + 악재 분석 (A/B/C 등급 모두)
            candidate_stocks = [v for v in tech_results.values()
                               if v.get('show') or v.get('show_c')]
            # AI에게는 D등급 포함 전체 분석 종목 전송 (악재 분석용)
            all_analyzed = list(tech_results.values())

            # AI에게 모든 분석종목 전달 (D등급 포함, 악재/시장 분석용)
            ai_targets = candidate_stocks if candidate_stocks else all_analyzed[:20]
            if ai_targets:
                stocks_data = json.dumps([{
                    'name': s['name'], 'code': s['code'], 'theme': s['theme'],
                    'price': s['current_price'], 'market_cap': s['market_cap'],
                    'grade': s['swing']['grade'], 'score': s['swing']['score'],
                    'signals': s['swing']['signals'],
                    'warnings': s['swing']['warnings'],
                    'trading_guide': s['swing'].get('trading_guide', {}),
                    'stages_passed': s['swing'].get('stages_passed', 0),
                } for s in ai_targets], ensure_ascii=False)

                # 관련 뉴스 요약
                stock_names = [s['name'] for s in candidate_stocks]
                related_articles = [a for a in all_articles
                                   if any(name in a['title'] for name in stock_names)][:30]
                articles_for_ai = "\n".join([
                    f"[{a['company']}] {a['title']}" for a in related_articles
                ]) if related_articles else "관련 뉴스 없음"

                log(f"  🔍 {len(candidate_stocks)}종목 종합분석 + 악재 필터링...")
                ai_final = call_claude(build_ai_prompt_final(stocks_data, articles_for_ai), max_tokens=12000)
                log(f"  ✅ 종합 리포트 완료")

                # AI 결과 반영 (부적합 종목 제외)
                if isinstance(ai_final, dict):
                    for exc in ai_final.get('excluded', []):
                        exc_name = exc.get('name', '')
                        for code, stock_data in tech_results.items():
                            if stock_data['name'] == exc_name:
                                stock_data['show'] = False
                                stock_data['show_c'] = False
                                stock_data['ai_excluded'] = exc.get('reason', '')
                                log(f"  🚫 {exc_name} AI 악재 필터 제외: {exc.get('reason', '')}")
            else:
                ai_final = {"stocks": [], "market_view": "적합 종목 없음", "overall_strategy": "관망"}

            # Phase 3: TOP 3 최종 추천
            ai_top3 = {}
            top3_candidates = [v for v in tech_results.values() if v.get('show')]
            if not top3_candidates:
                top3_candidates = sorted(
                    tech_results.values(),
                    key=lambda s: (
                        {'A': 0, 'B': 1, 'C': 2, 'D': 3}.get(s.get('swing', {}).get('grade', 'D'), 3),
                        s.get('theme_rank', 99),
                        -s.get('swing', {}).get('score', 0)
                    )
                )[:12]

            if top3_candidates:
                log(f"  🏆 TOP 3 최종 선정 분석 중...")
                top3_stocks = json.dumps([{
                    'name': s['name'], 'code': s.get('code',''), 'theme': s.get('theme',''),
                    'theme_rank': s.get('theme_rank', 99),
                    'price': s.get('current_price', 0), 'market_cap': s.get('market_cap', ''),
                    'grade': s.get('swing', {}).get('grade', 'D'), 'score': s.get('swing', {}).get('score', 0),
                    'show': s.get('show', False),
                    'signals': s.get('swing', {}).get('signals', []),
                    'warnings': s.get('swing', {}).get('warnings', []),
                    'stages_passed': s.get('swing', {}).get('stages_passed', 0),
                    'supply_demand': s.get('swing', {}).get('stage3', {}).get('supply_demand', {}).get('signals', []),
                } for s in top3_candidates], ensure_ascii=False)
                top3_themes = json.dumps([{
                    'rank': i+1, 'name': t.get('name',''), 'score': t.get('score',0), 'count': t.get('count',0)
                } for i, t in enumerate(top_themes[:10])], ensure_ascii=False)
                related_news = "\n".join([f"[{a.get('company','')}] {a.get('title','')}" for a in all_articles[:50]])
                ai_top3 = call_claude(build_ai_prompt_top3(top3_stocks, top3_themes, related_news) + "\n\n설명 없이 JSON만 출력하세요.", max_tokens=16000)
                if isinstance(ai_top3, dict) and ai_top3.get('error'):
                    log(f"  ❌ TOP 3 AI 오류: {ai_top3.get('error')}")
                    if ai_top3.get('raw_text'):
                        log(f"  🧾 raw: {ai_top3['raw_text'][:1200]}")
                elif not (isinstance(ai_top3, dict) and ai_top3.get('top3')):
                    log(f"  ⚠️ TOP 3 AI 응답은 왔지만 top3 데이터 없음")
                    log(f"  🧾 응답 타입: {type(ai_top3).__name__}, 키: {list(ai_top3.keys()) if isinstance(ai_top3, dict) else 'N/A'}")
                    if isinstance(ai_top3, dict) and ai_top3.get('raw_text'):
                        log(f"  🧾 raw: {ai_top3['raw_text'][:1200]}")
                else:
                    log(f"  ✅ TOP 3 선정 완료! ({len(ai_top3.get('top3',[]))}종목)")
            else:
                log(f"  ⚠️ 분석 대상 종목이 없어 TOP 3 생략")

            # top3 이중중첩 해제
            if isinstance(ai_top3, dict) and 'top3' in ai_top3:
                ai_top3_flat = ai_top3['top3']
            else:
                ai_top3_flat = ai_top3

            ai_analysis = {
                'news_flow': ai_phase1,
                'final_report': ai_final,
                'top3': ai_top3_flat,
                'market_context': ai_top3.get('market_context', '') if isinstance(ai_top3, dict) else '',
                'strategy_note': ai_top3.get('strategy_note', '') if isinstance(ai_top3, dict) else '',
                'caution': ai_top3.get('caution', '') if isinstance(ai_top3, dict) else '',
            }
            log(f"\n🎉 AI 분석 완료!")
        else:
            log(f"\n⚠️ Claude API 키 미설정 — AI 분석 생략")

        # 최종 결과 정렬
        grade_order = {'A': 0, 'B': 1, 'C': 2, 'D': 3}
        sorted_stocks = sorted(
            tech_results.values(),
            key=lambda s: (
                s.get('theme_rank', 99),
                grade_order.get(s.get('swing', {}).get('grade', 'D'), 3),
                -s.get('swing', {}).get('score', 0)
            )
        )

        # ===== 급등 테마 대장주 스캔 (85→95%) =====
        surge_result = None
        if not run_surge:
            log(f"\n⏭️  급등 테마 스캔 건너뜀 (모드: {mode})")
        try:
            if not run_surge:
                raise RuntimeError('__SKIP_SURGE__')
            with state_lock:
                crawl_state['phase'] = '급등 테마 스캔 중'
                crawl_state['percent'] = 90
            log(f"\n📈 급등 테마 대장주 스캔 시작...")
            # 인포스탁 캐시 공유 위해 동일 crawler 인스턴스 재사용
            surge_result = run_surge_scan(crawler, log)
            with state_lock:
                crawl_state['percent'] = 95
            if surge_result and surge_result.get('leaders'):
                log(f"✅ 급등 대장주 {len(surge_result['leaders'])}개 검출")
            else:
                log(f"  → 급등 대장주 없음 (오늘 시장 잠잠)")
        except Exception as _surge_e:
            if str(_surge_e) != '__SKIP_SURGE__':
                log(f"⚠️ 급등 스캐너 오류 (기존 결과는 정상 출력): {_surge_e}")
            surge_result = None

        with state_lock:
            crawl_state['phase'] = '완료'
            crawl_state['percent'] = 100
            crawl_state['ai_analysis'] = ai_analysis
            crawl_state['result'] = {
                'date': today,
                'total_articles': len(all_articles),
                'company_stats': company_stats,
                'themes': top_themes,
                'all_themes_count': len(scored_themes),
                'tech_analysis': {s['code']: s for s in sorted_stocks},
                'stocks_summary': {
                    'total_scanned': total_analyzed,
                    'total_passed': len([s for s in sorted_stocks if s.get('show')]),
                    'grade_a': len([s for s in sorted_stocks if s.get('swing', {}).get('grade') == 'A']),
                    'grade_b': len([s for s in sorted_stocks if s.get('swing', {}).get('grade') == 'B']),
                    'grade_c': len([s for s in sorted_stocks if s.get('swing', {}).get('grade') == 'C']),
                    'grade_d': len([s for s in sorted_stocks if s.get('swing', {}).get('grade') == 'D']),
                },
                'ai_analysis': ai_analysis,
                'surge_leaders': surge_result,
            }

        a_count = crawl_state['result']['stocks_summary']['grade_a']
        b_count = crawl_state['result']['stocks_summary']['grade_b']
        c_count = crawl_state['result']['stocks_summary']['grade_c']
        log(f"\n🎉 전체 분석 완료! A등급 {a_count}개, B등급 {b_count}개, C등급(관심) {c_count}개")

    except Exception as e:
        import traceback
        with state_lock:
            crawl_state['error'] = str(e)
            crawl_state['progress'].append(f"\n❌ 오류: {str(e)}")
            crawl_state['progress'].append(traceback.format_exc())
    finally:
        with state_lock:
            crawl_state['running'] = False


