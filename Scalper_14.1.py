import yfinance as yf
import pandas as pd
import numpy as np
import time  # <--- YENİ EKLENDİ: Zaman ayarı için
from ta.trend import EMAIndicator, MACD, ADXIndicator
from ta.volatility import AverageTrueRange
from datetime import time as dt_time # İsim çakışmasını önlemek için değiştirdik

class MultiDayBacktester:
    def __init__(self, ticker, capital=20000, risk_per_trade=0.01):
        self.ticker = ticker
        self.capital = capital
        self.initial_capital = capital
        self.risk_pct = risk_per_trade
        
        # --- KOTA AYARLARI (ŞAMPİYON v14.1) ---
        self.profit_target_per_trade = 0.006  # İşlem Başı %0.6
        self.daily_profit_cap = 0.015         # Günlük Kâr Kotası %1.5
        self.daily_loss_limit = 0.015         # Günlük Zarar Limiti %1.5
        
        self.trades = []
        self.equity_curve = [] 
        
    def fetch_data(self):
        print(f"Veri İndiriliyor (Son 7 Gün): {self.ticker}...")
        try:
            # v14.1 orijinal ayarı: 7 Gün, 1 Dakika + Auto Adjust Kapalı
            # Hata yakalama için multi=False ekleyebiliriz ama şimdilik sleep ile çözeceğiz
            df = yf.download(self.ticker, period="7d", interval="1m", progress=False, auto_adjust=False)
        except Exception as e:
            print(f"Hata oluştu: {e}")
            return None
        
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
            
        if df.empty: 
            # Boş gelirse sessizce geç
            return None

        # GÖSTERGELER
        try:
            df["EMA_200"] = EMAIndicator(close=df["Close"], window=200).ema_indicator()
            df["ADX"] = ADXIndicator(high=df["High"], low=df["Low"], close=df["Close"], window=14).adx()
            macd = MACD(close=df["Close"], window_slow=26, window_fast=12, window_sign=9)
            df["MACD"] = macd.macd()
            df["MACD_Signal"] = macd.macd_signal()
            df["ATR"] = AverageTrueRange(high=df["High"], low=df["Low"], close=df["Close"], window=14).average_true_range()
        except Exception as e:
            return None

        return df.dropna()

    def run_backtest(self):
        print("-" * 70)
        
        full_df = self.fetch_data()
        if full_df is None: return

        # Piyasa Performansı Hesabı
        first_price = full_df.iloc[0]['Open']
        last_price = full_df.iloc[-1]['Close']
        market_return = ((last_price - first_price) / first_price) * 100

        print(f"MODEL (v14.1 + 17:58 ÇIKIŞI) TESTİ: {self.ticker}")

        in_position = False
        pos_data = {}
        current_day = None
        daily_start_capital = self.capital
        trading_active_today = True 

        for i in range(1, len(full_df)):
            curr = full_df.iloc[i]
            prev = full_df.iloc[i-1]
            curr_date = curr.name.date()
            curr_time = curr.name.time() 

            # --- GÜN DEĞİŞİMİ (YEDEK SİGORTA) ---
            if current_day != curr_date:
                current_day = curr_date
                daily_start_capital = self.capital 
                trading_active_today = True 
                
                if in_position:
                    pnl = (curr['Open'] - pos_data['entry']) * pos_data['shares']
                    self.capital += pnl
                    self.trades.append({'Exit Time': curr.name, 'Type': 'LONG', 'Entry': pos_data['entry'], 'Exit': curr['Open'], 'PnL': pnl, 'Reason': 'Gün Değişimi (Gap Riski)'})
                    in_position = False

            # --- GÜNLÜK KOTA KONTROLÜ ---
            if trading_active_today:
                day_pnl_pct = (self.capital - daily_start_capital) / daily_start_capital
                
                if day_pnl_pct >= self.daily_profit_cap or day_pnl_pct <= -self.daily_loss_limit:
                    trading_active_today = False 
                    if in_position: 
                        pnl = (curr['Close'] - pos_data['entry']) * pos_data['shares']
                        self.capital += pnl
                        self.trades.append({'Exit Time': curr.name, 'Type': 'LONG', 'Entry': pos_data['entry'], 'Exit': curr['Close'], 'PnL': pnl, 'Reason': 'Kota Doldu'})
                        in_position = False
            
            if not trading_active_today:
                self.equity_curve.append(self.capital)
                continue

            # --- POZİSYON YÖNETİMİ ---
            if in_position:
                exit_signal = False
                reason = ""
                exit_price = 0.0
                
                # 1. SAAT KONTROLÜ (17:58 ÇIKIŞ)
                if curr_time >= dt_time(17, 58):
                    exit_signal = True; reason = "17:58 Kapanış"; exit_price = curr['Close']
                
                # 2. NORMAL HEDEF VE STOP KONTROLLERİ
                elif curr['High'] >= pos_data['tp']:
                    exit_signal = True; reason = "Take Profit"; exit_price = pos_data['tp']
                elif curr['Low'] <= pos_data['sl']:
                    exit_signal = True; reason = "Stop Loss"; exit_price = pos_data['sl']
                
                if exit_signal:
                    pnl = (exit_price - pos_data['entry']) * pos_data['shares']
                    self.capital += pnl
                    self.trades.append({'Exit Time': curr.name, 'Type': 'LONG', 'Entry': pos_data['entry'], 'Exit': exit_price, 'PnL': pnl, 'Reason': reason})
                    in_position = False

            # --- SİNYAL ARAMA ---
            elif trading_active_today:
                if curr_time < dt_time(17, 50): 
                    if curr['Close'] > curr['EMA_200']:
                        if curr['ADX'] > 20:
                            if (curr['MACD'] > curr['MACD_Signal']) and (prev['MACD'] < prev['MACD_Signal']):
                                sl = curr['Low'] - (curr['ATR'] * 1.5)
                                tp = curr['Close'] * (1 + self.profit_target_per_trade) 
                                risk_amt = self.capital * self.risk_pct
                                dist = abs(curr['Close'] - sl)
                                if dist == 0: continue
                                shares = risk_amt / dist
                                pos_data = {'entry': curr['Close'], 'sl': sl, 'tp': tp, 'shares': shares, 'time': curr.name}
                                in_position = True
            
            self.equity_curve.append(self.capital)

        self.print_results(market_return)

    def print_results(self, market_return):
        if not self.trades:
            return

        wins = [t for t in self.trades if t['PnL'] > 0]
        if len(self.trades) > 0:
            win_rate = len(wins) / len(self.trades) * 100
        else:
            win_rate = 0
            
        total_growth = ((self.capital - self.initial_capital) / self.initial_capital) * 100
        fark = total_growth - market_return
        
        if total_growth > 0 and fark > 0:
            durum = "🌟 (ALPHA)"
        elif total_growth > 0:
            durum = "✅ (KÂR)"
        else:
            durum = "❌ (ZARAR)"
            
        print(f"{durum} {self.ticker}")
        print(f"Robot: %{total_growth:.2f} | Piyasa: %{market_return:.2f} | Fark: %{fark:.2f}")
        print(f"Başarı: %{win_rate:.2f} | İşlem: {len(self.trades)}")

# --- BIST 100 DEV LİSTE ---
bist100_full = [
    "AEFES.IS", "AGHOL.IS", "AGROT.IS", "AHGAZ.IS", "AKBNK.IS", "AKCNS.IS", "AKFGY.IS", "AKFYE.IS", "AKSA.IS", "AKSEN.IS", 
    "ALARK.IS", "ALBRK.IS", "ALFAS.IS", "ARCLK.IS", "ASELS.IS", "ASTOR.IS", "ASUZU.IS", "AYDEM.IS", "BAGFS.IS", "BERA.IS", 
    "BFREN.IS", "BIENY.IS", "BIMAS.IS", "BIOEN.IS", "BOBET.IS", "BRSAN.IS", "BRYAT.IS", "BUCIM.IS", "CANTE.IS", "CCOLA.IS", 
    "CEMTS.IS", "CIMSA.IS", "CWENE.IS", "DOAS.IS", "DOHOL.IS", "ECILC.IS", "ECZYT.IS", "EGEEN.IS", "EKGYO.IS", "ENJSA.IS", 
    "ENKAI.IS", "EREGL.IS", "EUPWR.IS", "EUREN.IS", "FROTO.IS", "GARAN.IS", "GENIL.IS", "GESAN.IS", "GLYHO.IS", "GSDHO.IS", 
    "GUBRF.IS", "GWIND.IS", "HALKB.IS", "HEKTS.IS", "IMASM.IS", "IPEKE.IS", "ISCTR.IS", "ISDMR.IS", "ISGYO.IS", "ISMEN.IS", 
    "IZMDC.IS", "KARSN.IS", "KAYSE.IS", "KCAER.IS", "KCHOL.IS", "KONTR.IS", "KONYA.IS", "KORDS.IS", 
    "KRDMD.IS", "KZBGY.IS", "LOGO.IS", "MAVI.IS", "MGROS.IS", "MIATK.IS", "ODAS.IS", "OTKAR.IS", "OYAKC.IS", "PENTA.IS", 
    "PETKM.IS", "PGSUS.IS", "PSGYO.IS", "QUAGR.IS", "REEDR.IS", "SAHOL.IS", "SASA.IS", "SMRTG.IS", "SKBNK.IS", "SELEC.IS", 
    "SISE.IS", "SOKM.IS", "TABGD.IS", "TAVHL.IS", "TCELL.IS", "THYAO.IS", "TKFEN.IS", "TOASO.IS", "TRALT.IS", "TSKB.IS", "TTKOM.IS", 
    "TTRAK.IS", "TUKAS.IS", "TUPRS.IS", "TURSG.IS", "ULKER.IS", "VAKBN.IS", "VESBE.IS", "VESTL.IS", "YEOTK.IS", "YKBNK.IS", 
    "YYLGD.IS", "ZOREN.IS"
]

print(f"v14.1 (SABİT HEDEF) TARAMASI BAŞLIYOR (Güvenli Mod: Beklemeli)...")
print(f"Toplam {len(bist100_full)} hisse taranacak...")

for s in bist100_full:
    tester = MultiDayBacktester(s, capital=20000)
    tester.run_backtest()
    # BURASI ÇOK ÖNEMLİ: Her hisse sonrası 1 saniye bekle ki Yahoo banlamasın
    time.sleep(1)