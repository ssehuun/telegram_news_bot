import asyncio
import json
import os
from datetime import datetime, timedelta
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

import FinanceDataReader as fdr
import requests
from dotenv import load_dotenv
from openai import OpenAI
from telegram import Bot
from telegram.ext import Application, CommandHandler, ContextTypes

load_dotenv(dotenv_path=".env")

INTEREST_STOCKS_FILE = "./interest_stocks.json"

# 설정
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "YOUR_CHAT_ID")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "default_user")


class StockNewsBot:
    def __init__(self):
        self.bot = Bot(token=TELEGRAM_BOT_TOKEN)
        self.openai_client = OpenAI(api_key=OPENAI_API_KEY)
        self.today_str = datetime.now().strftime("%Y%m%d")
        self.today_date = datetime.now().date()
        self.yesterday_date = self.today_date - timedelta(days=1)
        self.stock_info_cache: Dict[str, Dict[str, Any]] = {}
        self.interest_stocks = self.load_interest_stocks()
        self.application: Optional[Application] = None

        try:
            listing = fdr.StockListing("NASDAQ")
            print(f"listing: {listing}")
            self.listing = listing.set_index("Symbol") if not listing.empty else None
        except Exception as e:
            print(f"상장 종목 목록 조회 실패 (FinanceDataReader): {e}")
            self.listing = None

    def load_interest_stocks(self):
        try:
            with open(INTEREST_STOCKS_FILE, "r") as f:
                return json.load(f)
        except FileNotFoundError:
            return []

    def save_interest_stocks(self):
        with open(INTEREST_STOCKS_FILE, "w") as f:
            json.dump(self.interest_stocks, f)

    def is_valid_ticker(self, ticker: str) -> bool:
        # 1차: 상장 목록에 있는지 확인
        if self.listing is not None and ticker in self.listing.index:
            return True

        # 2차: 상장 목록 조회 실패했거나 목록에 없을 때, DataReader로 바로 조회해본다.
        try:
            window_start = self.today_date - timedelta(days=5)
            df = fdr.DataReader(ticker, window_start, self.today_date)
            return not df.empty
        except Exception as e:
            print(f"티커 {ticker} DataReader 조회 실패: {e}")
            return False

    # 핸들러들
    async def add_stock(self, update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            return await update.message.reply_text("사용법: /add 005930")
        ticker = context.args[0]
        if not self.is_valid_ticker(ticker):
            return await update.message.reply_text("존재하지 않는 종목입니다.")
        if ticker in self.interest_stocks:
            return await update.message.reply_text("이미 추가된 종목입니다.")
        self.interest_stocks.append(ticker)
        self.save_interest_stocks()
        await update.message.reply_text(f"{ticker} 추가 완료.")

    async def remove_stock(self, update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            return await update.message.reply_text("사용법: /remove 005930")
        ticker = context.args[0]
        if ticker not in self.interest_stocks:
            return await update.message.reply_text("목록에 없는 종목입니다.")
        self.interest_stocks = [code for code in self.interest_stocks if code != ticker]
        self.save_interest_stocks()
        await update.message.reply_text(f"{ticker} 삭제 완료.")

    async def list_stocks(self, update, context):
        await update.message.reply_text(", ".join(self.interest_stocks) or "비어있음")

    async def report_command(self, update, context):
        report = self.create_report()
        print(f"\n생성된 리포트:\n {report}")
        await update.message.reply_text(report)

    def build_application(self) -> Application:
        app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        app.add_handler(CommandHandler("add", self.add_stock))
        app.add_handler(CommandHandler("remove", self.remove_stock))
        app.add_handler(CommandHandler("list", self.list_stocks))
        app.add_handler(CommandHandler("report", self.report_command))
        return app
        
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
                parse_mode=None,  # 사용자 입력/요약에 HTML 태그가 섞일 수 있어 파싱 비활성화
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
        self.stock_info_cache = {}

        # 1. 관심 종목 분석
        report += "🎯 관심 종목\n"
        report += "=" * 30 + "\n"

        if not self.interest_stocks:
            report += "\n등록된 관심 종목이 없습니다. /add <티커>로 추가하세요.\n"

        for ticker in self.interest_stocks:
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

        return report

    def run(self):
        """메인 실행 함수"""
        print("주식 시황 분석 시작 (텔레그램 폴링 모드)...")
        self.application = self.build_application()
        # run_polling은 내부에서 initialize/start/polling/idle/stop/shutdown 순서를 처리합니다.
        # post_init 훅에서 최초 리포트를 전송하도록 설정합니다.

        async def _post_init(app: Application):
            report = self.create_report()
            print("\n생성된 리포트:\n")
            print(report)
            print("\n텔레그램 전송 중...")
            # 애플리케이션이 가진 Bot 인스턴스를 사용해 전송
            await app.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=report,
                parse_mode=None,
                disable_web_page_preview=False,
            )
            print("텔레그램 명령 대기 중 (/add, /remove, /list, /report)...")

        self.application.post_init = _post_init
        # stop_signals=None 을 주면 Windows 등에서 add_signal_handler 없는 경우를 피할 수 있습니다.
        self.application.run_polling(stop_signals=None)


# 실행
def main():
    bot = StockNewsBot()
    bot.run()


if __name__ == "__main__":
    main()
