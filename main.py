import asyncio
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import time

from typing import Optional, Dict, Any
from pprint import pprint

import FinanceDataReader as fdr
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from openai import OpenAI
from telegram import Bot

load_dotenv(dotenv_path=".env")


# 설정
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "YOUR_CHAT_ID")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "default_user")
INTEREST_STOCKS = ["005930"]  # 삼성전자, SK하이닉스, NAVER, LG화학


class StockNewsBot:
    def __init__(self):
        self.bot = Bot(token=TELEGRAM_BOT_TOKEN)
        self.openai_client = OpenAI(api_key=OPENAI_API_KEY)
        self.today_str = datetime.now().strftime("%Y%m%d")
        self.today_date = datetime.now().date()
        self.yesterday_date = self.today_date - timedelta(days=1)
        self.stock_info_cache: Dict[str, Dict[str, Any]] = {}

        try:
            listing = fdr.StockListing("KRX-DESC")
            print(f"listing: {listing}")
            self.listing = listing.set_index("Symbol") if not listing.empty else None
        except Exception as e:
            print(f"상장 종목 목록 조회 실패 (FinanceDataReader): {e}")
            self.listing = None

    def get_stock_info(self, ticker: str) -> Optional[Dict[str, Any]]:
        """종목 정보 및 변동률 조회"""
        if ticker in self.stock_info_cache:
            return self.stock_info_cache[ticker]

        try:
            stock_name = self.get_stock_name(ticker)
            print(f"stock_name: {stock_name}")

            # 주말/휴장을 대비해 최근 일주일을 조회 후 마지막 두 영업일 사용
            window_start = self.today_date - timedelta(days=7)
            df = fdr.DataReader(ticker, window_start, self.today_date)

            if df.empty or len(df) < 2:
                return None

            df = df.sort_index()
            today_close = df.iloc[-1]["Close"]
            yesterday_close = df.iloc[-2]["Close"]
            change_rate = ((today_close - yesterday_close) / yesterday_close) * 100

            info = {
                "name": stock_name,
                "ticker": ticker,
                "close": today_close,
                "change_rate": change_rate,
            }
            self.stock_info_cache[ticker] = info
            return info
        except Exception as e:
            print(f"종목 {ticker} 정보 조회 실패: {e}")
            return None

    def get_stock_name(self, ticker: str) -> str:
        """티커에 해당하는 종목명 조회"""
        if self.listing is None:
            return ticker

        try:
            return str(self.listing.loc[ticker]["Name"])
        except Exception:
            return ticker

    def old_get_news_url(self, ticker: str):
        code = "000660"
        api_url = f"https://api.stock.naver.com/stock/{code}/news?count=15&page=1"
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": f"https://stock.naver.com/domestic/stock/{code}/news",
        }

        news_list = []
        resp = requests.get(api_url, headers=headers, timeout=10)
        resp.raise_for_status()
        items = resp.json().get("items", [])
        print(f"items: {items}")

        for item in items[:15]:
            title = item.get("title", "").strip()
            link = item.get("linkUrl", "")
            if not title or not link:
                continue
            news_list.append({"title": title, "url": link})
        print(f"news_list: {news_list}")

    def get_stock_news(self, ticker) -> Optional[list]:
        """네이버 금융 뉴스 크롤링"""
        try:
            url = f"https://stock.naver.com/api/domestic/detail/news?itemCode={ticker}"
            params = {
                "page": 1,
                "pageSize": 1
            }
            headers = {
                "User-Agent": "Mozilla/5.0",
                "Referer": f"https://stock.naver.com/domestic/stock/{ticker}/news"
            }

            resp = requests.get(url, headers=headers, params=params, timeout=10)
            resp.raise_for_status()

            data = resp.json()
            news_list = data['clusters']
            news_info_list = []
            for news in news_list:
                first_news = news["items"][0]
                news_info_list.append({
                    "title": first_news["title"],
                    "officeId": first_news["officeId"],
                    "articleId": first_news["articleId"],
                    "url": f"https://n.news.naver.com/article/{first_news['officeId']}/{first_news['articleId']}"
                })
            # pprint(f"tem_list: {tem_list}")
            return news_info_list
        except Exception as e:
            print(f"뉴스 조회 실패: {e}")
            return None

    def summarize_news_with_openai(self, stock_name, news_url):
        """OpenAI API로 뉴스 요약"""
        try:
            prompt = f"""
            종목명을 뉴스 링크를 바탕으로 투자자 관점에서 핵심 포인트만 짧게 요약해주세요:

            종목명: {stock_name}
            뉴스 링크: {news_url}
            """
            response = self.openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=200,
            )

            return response.choices[0].message.content.strip()
        except Exception as e:
            print(f"OpenAI 요약 실패: {e}")
            return "요약을 생성할 수 없습니다."

    def get_top_movers(self):
        """관심 종목 중 등락률 상위 3개 추출"""
        if not self.stock_info_cache:
            return []

        ranked = sorted(
            self.stock_info_cache.values(),
            key=lambda x: x["change_rate"],
            reverse=True,
        )
        return ranked[:3]

    async def send_telegram_message(self, message):
        """텔레그램 메시지 전송"""
        try:
            await self.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=message,
                parse_mode="HTML",
                disable_web_page_preview=False,
            )
            print("텔레그램 메시지 전송 완료")
        except Exception as e:
            print(f"텔레그램 전송 실패: {e}")

    def create_report(self):
        """시황 리포트 생성"""
        kst = ZoneInfo("Asia/Seoul")
        kst_now = datetime.now(kst)
        report = f"📊 오늘의 주식 시황 ({kst_now.strftime('%Y-%m-%d %H:%M')})\n\n"

        # 1. 관심 종목 분석
        report += "🎯 관심 종목\n"
        report += "=" * 30 + "\n"

        for ticker in INTEREST_STOCKS:
            info = self.get_stock_info(ticker)
            print(f"info: {info}")
            if not info:
                continue

            emoji = "🔴" if info["change_rate"] < 0 else "🟢" if info["change_rate"] > 0 else "⚪"

            report += f"\n{emoji} {info['name']} ({ticker})\n"
            report += f"종가: {info['close']:,}원 ({info['change_rate']:+.2f}%)\n"

            news_list = self.get_stock_news(ticker)

            if news_list:
                for news in news_list:
                    report += f"\n📰 뉴스: {news['title']}\n"
                    report += f"🔗 링크: {news['url']}\n"

                    summary = self.summarize_news_with_openai(
                        info["name"],
                        news["url"],
                    )
                    report += f"💡 요약: {summary}\n"
        
        # 2. 상승 주도 종목
        report += "\n\n📈 관심 종목 기준 강세 TOP 3\n"
        report += "=" * 30 + "\n"

        top_stocks = self.get_top_movers()
        for stock_info in top_stocks:
            report += f"🌟 {stock_info['name']} ({stock_info['ticker']}): "
            report += f"{stock_info['change_rate']:+.2f}%\n"

        # return report

    async def run(self):
        """메인 실행 함수"""
        print("주식 시황 분석 시작...")
        report = self.create_report()
        print("\n생성된 리포트:\n")
        print(report)
        # print("\n텔레그램 전송 중...")
        # await self.send_telegram_message(report)
        print("완료!")


# 실행
async def main():
    bot = StockNewsBot()
    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())
