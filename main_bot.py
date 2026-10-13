import os
import json
import time
import logging
import pandas as pd
import MetaTrader5 as mt5

from mt5_connector import MT5Connector
from telegram_bot import send_telegram_alert
from smart_money_analyzer import SmartMoneyAnalyzer
from rsi_divergence_engine import RSIDivergenceAnalyzer
from news_filter_analyzer import NewsFilterAnalyzer
from ml_model_analyzer import MLTradeFilter

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

class GoldSemiAutoBot:
    def __init__(self):
        self.config_path = "best_config.json"
        self.signal_path = "last_signal.json"
        
        self.load_config()
        self.mt5 = MT5Connector()
        self.smc_analyzer = SmartMoneyAnalyzer()
        self.rsi_analyzer = RSIDivergenceAnalyzer()
        self.news_analyzer = NewsFilterAnalyzer()
        self.ml_filter = MLTradeFilter()
        self.last_signal_time = None

    def load_config(self):
        if os.path.exists(self.config_path):
            with open(self.config_path, 'r', encoding='utf-8') as f:
                self.config = json.load(f)
        else:
            self.config = {"execution_mode": "SIGNAL_ONLY", "max_risk_per_trade": 1.0, "sniper_mode": True}

    def save_signal_to_json(self, signal_data: dict):
        with open(self.signal_path, 'w', encoding='utf-8') as f:
            json.dump(signal_data, f, indent=4, ensure_ascii=False)

    def run(self):
        if not self.mt5.connect():
            logging.error("❌ ไม่สามารถเชื่อมต่อ MetaTrader 5 ได้")
            return

        logging.info("🚀 [Gold-Semi-Auto] ระบบ Cash Flow Cycling Engine เริ่มทำงานแล้ว...")

        while True:
            self.load_config()
            sniper_active = self.config.get("sniper_mode", False)
            
            news_status = self.news_analyzer.is_news_time()
            if news_status.get("is_news_active"):
                logging.warning("⚠️ [NEWS] งดเทรดช่วงมีข่าวสำคัญ")
                time.sleep(60)
                continue

            rates = mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_M5, 0, 200)
            if rates is not None and len(rates) > 0:
                df = pd.DataFrame(rates)
                df['time'] = pd.to_datetime(df['time'], unit='s')
                
                # ตรวจสอบสัญญาณจาก SMC และ RSI Divergence
                signal = self.smc_analyzer.generate_smc_signal(df)
                rsi_check = self.rsi_analyzer.check_divergence(df)
                
                # ถ้า SMC ไม่มีสัญญาณ แต่ RSI Divergence เกิดขึ้น ให้สร้างโครงสร้างสัญญาณทดแทน
                if signal.get("status") != "NEW_SIGNAL" and rsi_check.get("signal") != "NONE":
                    last_close = df['close'].iloc[-1]
                    is_buy = rsi_check.get("type") == "BUY"
                    sl = df['low'].tail(5).min() - 1.0 if is_buy else df['high'].tail(5).max() + 1.0
                    risk = abs(last_close - sl)
                    tp = last_close + (risk * 2.5) if is_buy else last_close - (risk * 2.5)
                    
                    signal = {
                        "status": "NEW_SIGNAL",
                        "type": rsi_check.get("type"),
                        "entry": round(last_close, 2),
                        "sl": round(sl, 2),
                        "tp": round(tp, 2),
                        "module": "RSI_DIVERGENCE",
                        "setup_type": rsi_check.get("signal")
                    }

                if signal.get("status") == "NEW_SIGNAL":
                    candle_time = str(df['time'].iloc[-1])
                    
                    if self.last_signal_time != candle_time:
                        self.last_signal_time = candle_time
                        
                        # ประเมินความน่าจะเป็นด้วย Machine Learning
                        is_approved, win_prob = self.ml_filter.evaluate_signal(signal)
                        signal["ml_probability"] = win_prob
                        
                        required_prob = 80.0 if sniper_active else 70.0
                        is_sniper_approved = is_approved and (win_prob >= required_prob)
                        
                        signal["sniper_active"] = sniper_active
                        self.save_signal_to_json(signal)

                        if not is_sniper_approved:
                            logging.info(f"❌ [FILTER] ปฏิเสธสัญญาณ โอกาสชนะ {win_prob}% (< {required_prob}%)")
                            continue

                        logging.info(f"🎯 [SNIPER HIT] สัญญาณผ่านเกณฑ์ -> {signal['type']} (Win Prob: {win_prob}%)")
                        
                        if self.config.get("execution_mode") == "AUTO_TRADE":
                            account = mt5.account_info()
                            balance = account.balance if account else 10000.0
                            risk_usd = balance * (self.config.get("max_risk_per_trade", 1.0) / 100.0)
                            dist = abs(signal['entry'] - signal['sl'])
                            lot = max(0.01, round(risk_usd / (dist * 100.0), 2))
                            
                            ticket = self.mt5.send_order("XAUUSD", signal['type'], lot, signal['sl'], signal['tp'])
                            if ticket:
                                logging.info(f"✨ เปิดออเดอร์สำเร็จ Ticket #{ticket}")
                        else:
                            send_telegram_alert(signal)

            time.sleep(15)

if __name__ == "__main__":
    bot = GoldSemiAutoBot()
    bot.run()
                      
