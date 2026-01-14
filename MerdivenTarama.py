import yfinance as yf
import pandas as pd
import numpy as np
from ta.trend import EMAIndicator, ADXIndicator

# --- HEDEF: TÜM BIST 100 İÇİN GEÇMİŞ 10 YILIN "ROBOT DOSTU" ANALİZİ ---
# Amaç: Hangi hisseler "Merdiven" (Tip 1), hangileri "Testere" (Tip 2)?

class CharacterAnalyzer:
    def __init__(self, tickers):
        self.tickers = tickers
        self.results = []

    def analyze(self):
        print(f"DEV TARAMA BAŞLIYOR: BIST 100 GENELİ ({len(self.tickers)} Hisse)...")
        print("Kriterler: 10 Yıllık Trend Sadakati + Temiz Mum Yapısı (Düşük Fitil)")
        print("-" * 60)
        
        for i, ticker in enumerate(self.tickers):
            try:
                # İlerleme durumunu göster (Her 10 hissede bir)
                if i % 10 == 0:
                    print(f"İşleniyor... %{int((i / len(self.tickers)) * 100)} tamamlandı.")

                # 10 Yıllık Günlük Veri İndir
                df = yf.download(ticker, period="10y", interval="1d", progress=False, auto_adjust=False)
                
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                
                if len(df) < 500: # En az 2 yıllık veri yoksa atla (Yeni halka arzlar)
                    continue

                # --- GÖSTERGELER ---
                # 1. EMA 200 (Ana Trend Hattı)
                ema_200 = EMAIndicator(close=df["Close"], window=200).ema_indicator()
                
                # 2. ADX (Trend Gücü)
                adx = ADXIndicator(high=df["High"], low=df["Low"], close=df["Close"], window=14).adx()

                # --- KARAKTER ANALİZİ (DNA TESTİ) ---
                
                # A. Trend Sadakati (Time above EMA 200)
                # Boğa piyasasında geçirdiği süre oranı
                days_above_ema = (df["Close"] > ema_200).sum()
                trend_loyalty_score = (days_above_ema / len(df)) * 100
                
                # B. Mum Temizliği (Clean Candle Ratio) - ROBOT İÇİN KRİTİK
                # (Gövde / Toplam Boy). Fitil ne kadar azsa, robot o kadar az stop olur.
                candle_range = df["High"] - df["Low"]
                candle_body = abs(df["Close"] - df["Open"])
                candle_range = candle_range.replace(0, 1) # Sıfıra bölme hatasını önle
                
                cleanliness_ratio = (candle_body / candle_range).mean() * 100
                
                # C. Ortalama Trend Gücü
                avg_adx = adx.mean()

                # --- PUANLAMA SİSTEMİ ---
                # Robot Skoru = (%40 Temizlik) + (%40 Trend Sadakati) + (%20 ADX Gücü)
                robot_score = (cleanliness_ratio * 0.4) + (trend_loyalty_score * 0.4) + (avg_adx * 0.2)

                self.results.append({
                    "Hisse": ticker,
                    "Robot Skoru": round(robot_score, 2),
                    "Trend Sadakati (%)": round(trend_loyalty_score, 2),
                    "Temizlik (Gövde) (%)": round(cleanliness_ratio, 2),
                    "Ort. ADX": round(avg_adx, 2)
                })

            except Exception as e:
                # Sessizce geç, ekrana basıp kirletme
                pass

    def show_results(self):
        # Sonuçları Skor'a göre sırala (En yüksek en iyi)
        sorted_results = sorted(self.results, key=lambda x: x['Robot Skoru'], reverse=True)
        
        print("\n" + "="*85)
        print(f"🏆 BIST 100 GENELİ: EN 'ROBOT DOSTU' İLK 25 HİSSE 🏆")
        print("Açıklama: Bu hisseler son 10 yılda en istikrarlı trendi ve en temiz mumları oluşturmuş.")
        print("="*85)
        
        # Tablo Başlığı
        print(f"{'SIR':<3} | {'HİSSE':<12} | {'SKOR':<8} | {'TEMİZLİK':<10} | {'SADAKAT':<10} | {'ADX':<8}")
        print("-" * 85)
        
        # Sadece ilk 25'i göster
        for idx, row in enumerate(sorted_results[:25]):
            print(f"{idx+1:<3} | {row['Hisse']:<12} | {row['Robot Skoru']:<8} | %{row['Temizlik (Gövde) (%)']:<9} | %{row['Trend Sadakati (%)']:<9} | {row['Ort. ADX']:<8}")

        print("\n" + "="*85)
        print(f"💀 LİSTENİN DİBİ: ROBOTUN EN ÇOK ZORLANDIĞI 5 HİSSE (TESTERE)")
        print("-" * 85)
        # Listenin sonundaki 5 hisseyi göster
        for idx, row in enumerate(sorted_results[-5:]):
            print(f"{len(sorted_results)-4+idx:<3} | {row['Hisse']:<12} | {row['Robot Skoru']:<8} | %{row['Temizlik (Gövde) (%)']:<9} | %{row['Trend Sadakati (%)']:<9} | {row['Ort. ADX']:<8}")


# --- BIST 100 GENİŞ LİSTE (Manuel Güncel Liste) ---
bist100_tickers = [
    "AEFES.IS", "AGHOL.IS", "AGROT.IS", "AHGAZ.IS", "AKBNK.IS", "AKCNS.IS", "AKFGY.IS", "AKFYE.IS", "AKSA.IS", "AKSEN.IS", 
    "ALARK.IS", "ALBRK.IS", "ALFAS.IS", "ARCLK.IS", "ASELS.IS", "ASTOR.IS", "ASUZU.IS", "AYDEM.IS", "BAGFS.IS", "BERA.IS", 
    "BFREN.IS", "BIENY.IS", "BIMAS.IS", "BIOEN.IS", "BOBET.IS", "BRSAN.IS", "BRYAT.IS", "BUCIM.IS", "CANTE.IS", "CCOLA.IS", 
    "CEMTS.IS", "CIMSA.IS", "CWENE.IS", "DOAS.IS", "DOHOL.IS", "ECILC.IS", "ECZYT.IS", "EGEEN.IS", "EKGYO.IS", "ENJSA.IS", 
    "ENKAI.IS", "EREGL.IS", "EUPWR.IS", "EUREN.IS", "FROTO.IS", "GARAN.IS", "GENIL.IS", "GESAN.IS", "GLYHO.IS", "GSDHO.IS", 
    "GUBRF.IS", "GWIND.IS", "HALKB.IS", "HEKTS.IS", "IMASM.IS", "IPEKE.IS", "ISCTR.IS", "ISDMR.IS", "ISGYO.IS", "ISMEN.IS", 
    "IZMDC.IS", "KARSN.IS", "KAYSE.IS", "KCAER.IS", "KCHOL.IS", "KONTR.IS", "KONYA.IS", "KORDS.IS", "KOZAA.IS", "KOZAL.IS", 
    "KRDMD.IS", "KZBGY.IS", "LOGO.IS", "MAVI.IS", "MGROS.IS", "MIATK.IS", "ODAS.IS", "OTKAR.IS", "OYAKC.IS", "PENTA.IS", 
    "PETKM.IS", "PGSUS.IS", "PSGYO.IS", "QUAGR.IS", "REEDR.IS", "SAHOL.IS", "SASA.IS", "SMRTG.IS", "SKBNK.IS", "SELEC.IS", 
    "SISE.IS", "SOKM.IS", "TABGD.IS", "TAVHL.IS", "TCELL.IS", "THYAO.IS", "TKFEN.IS", "TOASO.IS", "TSKB.IS", "TTKOM.IS", 
    "TTRAK.IS", "TUKAS.IS", "TUPRS.IS", "TURSG.IS", "ULKER.IS", "VAKBN.IS", "VESBE.IS", "VESTL.IS", "YEOTK.IS", "YKBNK.IS", 
    "YYLGD.IS", "ZOREN.IS"
]

analyzer = CharacterAnalyzer(bist100_tickers)
analyzer.analyze()
analyzer.show_results()