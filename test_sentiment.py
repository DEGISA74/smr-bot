import yfinance as yf
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np

# --- AYARLAR ---
HISSELER = ["ASELS.IS", "SAHOL.IS", "TOASO.IS"]
START_DATE = "2025-11-24"
END_DATE = "2026-01-05"

print("EVRENSEL FORMÜL V2.0: DEMA (Gecikmesiz Hibrit Model)...")

# --- DEMA FONKSİYONU ---
def calculate_dema(series, period):
    ema1 = series.ewm(span=period, adjust=False).mean()
    ema2 = ema1.ewm(span=period, adjust=False).mean()
    return (2 * ema1) - ema2

# --- GÖRSELLEŞTİRME ---
plt.style.use('dark_background')
fig, axes = plt.subplots(3, 1, figsize=(14, 15), sharex=False)

for i, sembol in enumerate(HISSELER):
    # Veri Çek
    df = yf.download(sembol, start=START_DATE, end=END_DATE, auto_adjust=True, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    # 1. TİPİK FİYAT (Değişmedi)
    df['Typical_Price'] = (df['High'] + df['Low'] + df['Close']) / 3

    # 2. ORTALAMA: DEMA 6 (EMA 6 Yerine Bunu Kullanıyoruz)
    # DEMA, EMA'nın "Gecikme Hatasını" düzelten versiyonudur.
    df['DEMA6'] = calculate_dema(df['Typical_Price'], period=6)

    # 3. HESAPLAMA (AYNI FORMÜL)
    # (Fiyat - DEMA) / DEMA * 1000
    df['Sentiment'] = (df['Typical_Price'] - df['DEMA6']) / df['DEMA6'] * 1000

    # --- ÇİZİM ---
    ax = axes[i]
    x_pos = np.arange(len(df))
    date_labels = df.index.strftime('%d %b')
    
    # NaN temizliği
    mask = ~np.isnan(df['Sentiment'])
    
    colors = ['purple' if x >= 0 else 'red' for x in df.loc[mask, 'Sentiment']]
    ax.bar(x_pos[mask], df.loc[mask, 'Sentiment'], color=colors, alpha=0.9, width=0.8)
    
    # Başlık
    ax.set_title(f'{sembol} - DEMA 6 (Hızlı Dönüş + Yumuşak Trend)', color='cyan', fontsize=12)
    ax.set_ylim(-30, 30)
    ax.grid(True, alpha=0.2)
    ax.axhline(0, color='white', linewidth=0.5)

    step = max(1, len(df) // 12)
    ax.set_xticks(x_pos[::step])
    ax.set_xticklabels(date_labels[::step], rotation=45, fontsize=8)
    
    if len(df) > 0:
        last_val = df['Sentiment'].iloc[-1]
        ax.text(x_pos[-1], last_val + (2 if last_val > 0 else -4), f"{last_val:.2f}", 
                color='white', fontweight='bold', ha='center')

plt.tight_layout()
plt.show()