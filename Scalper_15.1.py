import yfinance as yf
import pandas as pd
import numpy as np
from ta.trend import EMAIndicator, MACD, ADXIndicator
from ta.volatility import AverageTrueRange
from datetime import time, timedelta

class AgileHunterBacktester:
    def __init__(self, ticker, capital=20000, risk_per_trade=0.01):
        self.ticker = ticker
        self.capital = capital
        self.initial_capital = capital
        self.risk_pct = risk_per_trade
        
        # --- v15.1 AYARLARI (VWAP YOK, HIZ VAR) ---
        self.daily_profit_cap = 0.05          # Hedefin ucu açık (Trailing var)
        self.daily_loss_limit = 0.02          # Günlük max zarar limiti %2
        self.cooldown_period = 15             # Stop sonrası 15 dakika bekleme
        self.max_reentries_per_day = 1        # Günde en fazla 1 kere tekrar giriş hakkı
        
        self.trades = []
        
    def fetch_data(self):
        print(f"Veri İndiriliyor (Son 7 Gün): {self.ticker}...")
        try:
            # Hız için son 7 gün, 1 dakikalık veri
            df = yf.download(self.ticker, period="7d", interval="1m", progress=False, auto_adjust=False)
        except Exception as e:
            return None
        
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if df.empty: return None

        # --- GÖSTERGELER (SADECE HIZLI OLANLAR) ---
        # EMA 200: Trendin ana yönü (Bunu kaldırmıyoruz, yoksa düşen bıçağı tutarız)
        df["EMA_200"] = EMAIndicator(close=df["Close"], window=200).ema_indicator()
        
        # MACD: Giriş tetiği
        macd = MACD(close=df["Close"], window_slow=26, window_fast=12, window_sign=9)
        df["MACD"] = macd.macd()
        df["MACD_Signal"] = macd.macd_signal()
        
        # ADX: Trend gücü
        df["ADX"] = ADXIndicator(high=df["High"], low=df["Low"], close=df["Close"], window=14).adx()
        
        # ATR: Stop mesafesi ve Trailing hesabı için
        df["ATR"] = AverageTrueRange(high=df["High"], low=df["Low"], close=df["Close"], window=14).average_true_range()

        return df.dropna()

    def run_backtest(self):
        full_df = self.fetch_data()
        if full_df is None: return

        # Piyasa Getirisi
        first_price = full_df.iloc[0]['Open']
        last_price = full_df.iloc[-1]['Close']
        market_return = ((last_price - first_price) / first_price) * 100

        print(f"MODEL v15.1 (ÇEVİK AVCI) TESTİ: {self.ticker}")

        in_position = False
        pos_data = {}
        
        # Günlük Takip
        current_day = None
        daily_start_capital = self.capital
        trading_active_today = True
        daily_reentry_count = 0
        last_exit_time = None

        for i in range(1, len(full_df)):
            curr = full_df.iloc[i]
            prev = full_df.iloc[i-1]
            curr_time = curr.name.time() 
            curr_date = curr.name.date()

            # --- GÜN DEĞİŞİMİ ---
            if current_day != curr_date:
                current_day = curr_date
                daily_start_capital = self.capital 
                trading_active_today = True 
                daily_reentry_count = 0 
                last_exit_time = None
                
                if in_position: # Geceye mal taşıma
                    pnl = (curr['Open'] - pos_data['entry']) * pos_data['shares']
                    self.capital += pnl
                    self.trades.append({'Exit Time': curr.name, 'Type': 'LONG', 'Entry': pos_data['entry'], 'Exit': curr['Open'], 'PnL': pnl, 'Reason': 'Gün Sonu (Gap)'})
                    in_position = False

            # --- GÜNLÜK ZARAR KONTROLÜ ---
            if trading_active_today:
                day_pnl_pct = (self.capital - daily_start_capital) / daily_start_capital
                if day_pnl_pct <= -self.daily_loss_limit:
                    trading_active_today = False 
                    if in_position: 
                        pnl = (curr['Close'] - pos_data['entry']) * pos_data['shares']
                        self.capital += pnl
                        self.trades.append({'Exit Time': curr.name, 'Type': 'LONG', 'Entry': pos_data['entry'], 'Exit': curr['Close'], 'PnL': pnl, 'Reason': 'Günlük Stop'})
                        in_position = False
            
            if not trading_active_today: continue

            # --- POZİSYON YÖNETİMİ (AKILLI TRAILING) ---
            if in_position:
                current_high = curr['High']
                entry_price = pos_data['entry']
                current_sl = pos_data['sl']

                # 1. AŞAMA: BREAKEVEN (Hemen riski sıfırla - %0.4 kârda)
                if current_high >= entry_price * 1.004:
                    new_sl = entry_price * 1.001
                    if new_sl > current_sl: pos_data['sl'] = new_sl

                # 2. AŞAMA: KÂR KİLİTLEME (%1 kârda yarısını cebine koy)
                if current_high >= entry_price * 1.01:
                    new_sl = entry_price * 1.005
                    if new_sl > current_sl: pos_data['sl'] = new_sl
                
                # 3. AŞAMA: TREND SÖRFÜ (2 ATR geriden takip et)
                atr_stop = current_high - (curr['ATR'] * 2.0)
                if atr_stop > pos_data['sl']: pos_data['sl'] = atr_stop

                # ÇIKIŞ KONTROLÜ
                exit_signal = False; reason = ""; exit_price = 0.0

                if curr_time >= time(17, 58):
                    exit_signal = True; reason = "17:58 Kapanış"; exit_price = curr['Close']
                elif curr['Low'] <= pos_data['sl']: # Stop patladı
                    exit_signal = True; exit_price = pos_data['sl']
                    if exit_price > entry_price * 1.005: reason = "Trailing Profit"
                    elif exit_price > entry_price: reason = "Breakeven"
                    else: reason = "Stop Loss"

                if exit_signal:
                    pnl = (exit_price - pos_data['entry']) * pos_data['shares']
                    self.capital += pnl
                    self.trades.append({'Exit Time': curr.name, 'Type': 'LONG', 'Entry': pos_data['entry'], 'Exit': exit_price, 'PnL': pnl, 'Reason': reason})
                    in_position = False
                    last_exit_time = curr.name # Soğuma süresi için zamanı kaydet

            # --- GİRİŞ SİNYALİ (VWAP YOK, SAF HIZ) ---
            elif trading_active_today:
                if curr_time < time(17, 50): 
                    
                    # SOĞUMA SÜRESİ VE HAK KONTROLÜ
                    is_cooldown_active = False
                    has_entry_right = True
                    
                    if last_exit_time is not None:
                        minutes_passed = (curr.name - last_exit_time).total_seconds() / 60
                        if minutes_passed < self.cooldown_period:
                            is_cooldown_active = True
                        
                        if last_exit_time.date() == current_day:
                             if daily_reentry_count >= self.max_reentries_per_day:
                                 has_entry_right = False

                    if not is_cooldown_active and has_entry_right:
                        # STANDART GİRİŞ ŞARTLARI (v14.1)
                        if curr['Close'] > curr['EMA_200']:
                            if curr['ADX'] > 20:
                                if (curr['MACD'] > curr['MACD_Signal']) and (prev['MACD'] < prev['MACD_Signal']):
                                    
                                    sl = curr['Low'] - (curr['ATR'] * 1.5)
                                    risk_amt = self.capital * self.risk_pct
                                    dist = abs(curr['Close'] - sl)
                                    if dist == 0: continue
                                    shares = risk_amt / dist
                                    
                                    pos_data = {'entry': curr['Close'], 'sl': sl, 'shares': shares, 'time': curr.name}
                                    in_position = True
                                    
                                    if last_exit_time is not None and last_exit_time.date() == current_day:
                                        daily_reentry_count += 1
            
        self.print_results(market_return)

    def print_results(self, market_return):
        if not self.trades: return

        total_growth = ((self.capital - self.initial_capital) / self.initial_capital) * 100
        fark = total_growth - market_return
        
        wins = [t for t in self.trades if t['PnL'] > 0]
        win_rate = (len(wins) / len(self.trades)) * 100 if len(self.trades) > 0 else 0
        
        durum = "🌟 (ALPHA)" if (total_growth > 0 and fark > 0) else ("✅ (KÂR)" if total_growth > 0 else "❌ (ZARAR)")
            
        print(f"{durum} {self.ticker}")
        print(f"Robot: %{total_growth:.2f} | Piyasa: %{market_return:.2f} | Fark: %{fark:.2f}")
        print(f"Başarı: %{win_rate:.0f} | İşlem: {len(self.trades)}")

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

print(f"BIST 100 ÇEVİK AVCI (TRAIL + RE-ENTRY) BAŞLIYOR...")
print(f"Toplam {len(bist100_full)} hisse taranacak...")

for s in bist100_full:
    tester = AgileHunterBacktester(s, capital=20000)
    tester.run_backtest()