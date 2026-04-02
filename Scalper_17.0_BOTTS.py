import yfinance as yf
import pandas as pd
import numpy as np
import time
from datetime import time as dt_time

class SmartBOTTSBacktester:
    def __init__(self, ticker, capital=20000, risk_per_trade=0.01):
        self.ticker = ticker
        self.capital = capital
        self.initial_capital = capital
        self.risk_pct = risk_per_trade
        
        # --- v17.0 "BOTTS MANTIKLI" AYARLAR ---
        # Tweet'teki "1A2" ve "Filtre" mantığı:
        self.ott_period = 2        # X1: Çok hızlı (Anlık tepki)
        self.ott_percent = 0.5     # Stop mesafesi %0.5 (Dar ama filtreli)
        self.filter_period = 3     # MOV Filtresi: Fiyatın 3 barlık ortalaması
        
        self.trades = []
        
    def calculate_ott_logic(self, df):
        """
        v17.0 ÖZEL FORMÜL:
        Anıl Özekşi'nin 'MOV > OTT' filtresiyle güçlendirilmiş yapı.
        Fiyat yerine, Fiyatın Ortalaması (Filter_MA) baz alınır.
        """
        # 1. Fiyatın kendisi yerine, yumuşatılmış "Hareketli Ortalamasını" kullan (FİLTRE)
        # Bu, anlık iğne atışlarında (fake) stop olmayı engeller.
        df['Filter_MA'] = df['Close'].ewm(span=self.filter_period, adjust=False).mean()
        
        # 2. OTT Ana Hesabı (Bu Filtreli Fiyat üzerinden hesaplanır)
        df['MA_Base'] = df['Filter_MA'].ewm(span=self.ott_period, adjust=False).mean()
        
        df['Long_Stop'] = df['MA_Base'] * (1 - self.ott_percent / 100)
        df['Short_Stop'] = df['MA_Base'] * (1 + self.ott_percent / 100)
        
        ott = [df['MA_Base'].iloc[0]]
        trend = [1]
        
        long_stop = df['Long_Stop'].values
        short_stop = df['Short_Stop'].values
        # DİKKAT: Kıyaslama Fiyatla değil, Filter_MA ile yapılır!
        src_price = df['Filter_MA'].values 
        
        for i in range(1, len(df)):
            prev_ott = ott[-1]
            prev_trend = trend[-1]
            new_ott = prev_ott
            new_trend = prev_trend
            
            if prev_trend == 1: # Trend YUKARI
                if src_price[i] > prev_ott:
                    new_ott = max(long_stop[i], prev_ott)
                    new_trend = 1
                else:
                    new_ott = short_stop[i]
                    new_trend = -1
            else: # Trend AŞAĞI
                if src_price[i] < prev_ott:
                    new_ott = min(short_stop[i], prev_ott)
                    new_trend = -1
                else:
                    new_ott = long_stop[i]
                    new_trend = 1
            
            ott.append(new_ott)
            trend.append(new_trend)
            
        df['OTT'] = ott
        df['Trend'] = trend
        return df

    def fetch_data(self):
        print(f"Veri İndiriliyor (Son 7 Gün): {self.ticker}...")
        try:
            df = yf.download(self.ticker, period="7d", interval="1m", progress=False, auto_adjust=False)
        except Exception as e:
            print(f"Hata: {e}")
            return None
        
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if df.empty: return None

        df = df.dropna()
        # v17.0 Hesaplamasını Çalıştır
        df = self.calculate_ott_logic(df)
        return df.dropna()

    def run_backtest(self):
        full_df = self.fetch_data()
        if full_df is None: return

        # Piyasa Getirisi
        first_price = full_df.iloc[0]['Open']
        last_price = full_df.iloc[-1]['Close']
        market_return = ((last_price - first_price) / first_price) * 100

        print(f"MODEL v17.0 (BOTTS + MA FİLTRE) TESTİ: {self.ticker}")

        in_position = False
        pos_data = {}
        current_day = None
        daily_start_capital = self.capital
        trading_active_today = True

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
                
                # Gap Riski: Trend yukarıysa bile gün sonu çıkalım
                if in_position:
                    pnl = (curr['Open'] - pos_data['entry']) * pos_data['shares']
                    self.capital += pnl
                    self.trades.append({'Exit Time': curr.name, 'Type': 'LONG', 'Entry': pos_data['entry'], 'Exit': curr['Open'], 'PnL': pnl, 'Reason': 'Gün Sonu'})
                    in_position = False

            # --- GÜNLÜK ZARAR LİMİTİ ---
            if trading_active_today:
                day_pnl_pct = (self.capital - daily_start_capital) / daily_start_capital
                if day_pnl_pct <= -0.02: # %2 Günlük Max Zarar
                    trading_active_today = False
                    if in_position:
                        pnl = (curr['Close'] - pos_data['entry']) * pos_data['shares']
                        self.capital += pnl
                        self.trades.append({'Exit Time': curr.name, 'Type': 'LONG', 'Entry': pos_data['entry'], 'Exit': curr['Close'], 'PnL': pnl, 'Reason': 'Günlük Stop'})
                        in_position = False

            if not trading_active_today: continue

            # --- v17.0 SİNYAL MANTIĞI ---
            
            # AL SİNYALİ
            if not in_position and curr_time < dt_time(17, 50):
                if prev['Trend'] == -1 and curr['Trend'] == 1:
                    risk_amt = self.capital * self.risk_pct
                    # Stop mesafesi dinamik
                    dist = abs(curr['Close'] - curr['OTT'])
                    if dist == 0: dist = curr['Close'] * 0.01
                    shares = risk_amt / dist
                    pos_data = {'entry': curr['Close'], 'shares': shares, 'time': curr.name}
                    in_position = True

            # SAT SİNYALİ (Filtreli Kırılım)
            elif in_position:
                exit_signal = False
                reason = ""
                
                # Trend bozuldu mu? (MA, OTT'nin altına indi mi?)
                if curr['Trend'] == -1: 
                    exit_signal = True; reason = "v17 Filtreli Stop"
                
                elif curr_time >= dt_time(17, 58):
                    exit_signal = True; reason = "17:58 Kapanış"
                
                if exit_signal:
                    pnl = (curr['Close'] - pos_data['entry']) * pos_data['shares']
                    self.capital += pnl
                    self.trades.append({'Exit Time': curr.name, 'Type': 'LONG', 'Entry': pos_data['entry'], 'Exit': curr['Close'], 'PnL': pnl, 'Reason': 'Gün Sonu'})
                    in_position = False

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

# --- BIST 100 TAM LİSTE (Karşılaştırma İçin Standart) ---
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

print(f"v17.0 TURBO BOTTS (TAM LİSTE TARAMASI) BAŞLIYOR...")
print(f"Toplam {len(bist100_full)} hisse taranacak...")

for s in bist100_full:
    tester = SmartBOTTSBacktester(s, capital=20000)
    tester.run_backtest()
    time.sleep(1) # Yahoo banlamasın diye önlem