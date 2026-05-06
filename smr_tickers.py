"""
SMR Bot — Geçerli Ticker Listesi
app.py'deki ASSET_GROUPS ile senkronize tutulmalı.
Kullanıcının yazdığı format: #KCHOL, #AAPL, #BTC, #ALTIN
"""

# ─── BIST (.IS olmadan gösterilir) ───────────────────────────────────────────
BIST = {
    # İndeksler
    "XU100","XU030","XBANK","XTUMY","XUSIN",
    # BIST30
    "EREGL","SISE","TUPRS","AKBNK","ARCLK","ASELS","BIMAS","CCOLA","EKGYO","ENKAI",
    "FROTO","GARAN","GUBRF","HALKB","ISCTR","KCHOL","KOZAA","KRDMD","PETKM","PGSUS",
    "SAHOL","SASA","TCELL","THYAO","TKFEN","TOASO","TTKOM","TTRAK","VAKBN","YKBNK",
    # BIST TÜM (A-Z)
    "A1CAP","ACSEL","ADEL","ADESE","ADGYO","AEFES","AFYON","AGESA","AGHOL","AGROT",
    "AGYO","AHGAZ","AKCNS","AKENR","AKFGY","AKGRT","AKMGY","AKSA","AKSEN","AKSGY",
    "AKSUE","AKYHO","ALARK","ALBRK","ALCAR","ALCTL","ALFAS","ALGYO","ALKA","ALKIM",
    "ALMAD","ALTNY","ALVES","ANELE","ANGEN","ANHYT","ANSGR","ARASE","ARDYZ","ARENA",
    "ARSAN","ARTMS","ARZUM","ASGYO","ASTOR","ASUZU","ATAGY","ATAKP","ATATP","ATEKS",
    "ATLAS","ATSYH","AVGYO","AVHOL","AVOD","AVPGY","AVTUR","AYCES","AYDEM","AYEN",
    "AYES","AYGAZ","AZTEK","BAGFS","BAKAB","BALAT","BANVT","BARMA","BASCM","BASGZ",
    "BAYRK","BEGYO","BERA","BERK","BEYAZ","BFREN","BIENY","BIGCH","BINBN","BINHO",
    "BIOEN","BIZIM","BJKAS","BLCYT","BMSCH","BMSTL","BNTAS","BOBET","BORLS","BOSSA",
    "BRISA","BRKO","BRKSN","BRKVY","BRLSM","BRMEN","BRSAN","BRYAT","BSOKE","BTCIM",
    "BUCIM","BURCE","BURVA","BVSAN","BYDNR","CANTE","CATES","CELHA","CEMAS","CEMTS",
    "CEOEM","CIMSA","CLEBI","CMBTN","CMENT","CONSE","COSMO","CRDFA","CRFSA","CUSAN",
    "CVKMD","CWENE","DAGH","DAGI","DAPGM","DARDL","DENGE","DERHL","DERIM","DESA",
    "DESPC","DEVA","DGATE","DGGYO","DGNMO","DIRIT","DITAS","DMSAS","DNISI","DOAS",
    "DOBUR","DOCO","DOFER","DOGUB","DOHOL","DOKTA","DURDO","DYOBY","DZGYO","EBEBK",
    "ECILC","ECZYT","EDATA","EDIP","EGEEN","EGEPO","EGGUB","EGPRO","EGSER","EKIZ",
    "EKSUN","ELITE","EMKEL","EMNIS","ENJSA","ENSRI","ENTRA","EPLAS","ERBOS","ERCB",
    "ERSU","ESCAR","ESCOM","ESEN","ETILR","ETYAT","EUHOL","EUKYO","EUPWR","EUREN",
    "EUYO","EYGYO","FADE","FENER","FLAP","FMIZP","FONET","FORMT","FORTE","FRIGO",
    "FZLGY","GARFA","GEDIK","GEDZA","GENIL","GENTS","GEREL","GESAN","GLBMD","GLCVY",
    "GLRYH","GLYHO","GMTAS","GOKNR","GOLTS","GOODY","GOZDE","GRNYO","GRSEL","GSDDE",
    "GSDHO","GSRAY","GWIND","GZNMI","HATEK","HATSN","HDFGS","HEDEF","HEKTS","HKTM",
    "HLGYO","HRKET","HTTBT","HUBVC","HUNER","HURGZ","ICBCT","ICUGS","IDGYO","IEYHO",
    "IHAAS","IHEVA","IHGZT","ILVE","IMASM","INDES","INFO","INGRM","INTEM","INVEO",
    "INVES","IPEKE","ISATR","ISBIR","ISBTR","ISDMR","ISFIN","ISGSY","ISGYO","ISKPL",
    "ISKUR","ISMEN","ISSEN","ISYAT","ITTFH","IZENR","IZFAS","IZINV","IZMDC","JANTS",
    "TRALT","ONRYT","EFOR","OZATD","KAPLM","KAREL","KARSN","KARYE","KATMR","KAYSE",
    "KCAER","KENT","KERVN","KERVT","KFEIN","KGYO","KIMMR","KLGYO","KLKIM","KLMSN",
    "KLNMA","KLSER","KLRHO","KMPUR","KNFRT","KOCMT","KONKA","KONTR","KONYA","KOPOL",
    "KORDS","KOTON","KOZAL","KRDMA","KRDMB","KRGYO","KRONT","KRPLS","KRSTL","KRTEK",
    "KRVGD","KSTUR","KTLEV","KTSKR","KUTPO","KUVVA","KUYAS","KZBGY","KZGYO","LIDER",
    "LIDFA","LILAK","LINK","LKMNH","LMKDC","LOGO","LUKSK","MAALT","MACKO","MAGEN",
    "MAKIM","MAKTK","MANAS","MARBL","MARKA","MARTI","MAVI","MEDTR","MEGAP","MEGMT",
    "MEKAG","MEPET","MERCN","MERIT","MERKO","METEM","METRO","METUR","MGROS","MIATK",
    "MIPAZ","MMCAS","MNDRS","MNDTR","MOBTL","MOGAN","MPARK","MRGYO","MRSHL","MSGYO",
    "MTRKS","MTRYO","MZHLD","NATEN","NETAS","NIBAS","NTGAZ","NUGYO","NUHCM","OBASE",
    "OBAMS","ODAS","ODINE","OFSYM","ONCSM","ORCA","ORGE","ORMA","OSMEN","OSTIM",
    "OTKAR","OTTO","OYAKC","OYAYO","OYLUM","OYYAT","OZGYO","OZKGY","OZRDN","OZSUB",
    "PAGYO","PAMEL","PAPIL","PARSN","PASEU","PCILT","PEGYO","PEKGY","PENGD","PENTA",
    "PETUN","PINSU","PKART","PKENT","PLAT","PNLSN","POLHO","POLTK","PRDGS","PRKAB",
    "PRKME","PRZMA","PSDTC","PSGYO","PTEK","QNBFB","QNBFL","QUAGR","PLTUR","PATEK",
    "RALYH","RAYSG","REEDR","RGYAS","RNPOL","RODRG","ROYAL","RTALB","RUBNS","RYGYO",
    "RYSAS","SAFKR","SAMAT","SANEL","SANFM","SANKO","SARKY","SAYAS","SDTTR","SEGYO",
    "SEKFK","SEKUR","SELEC","SELGD","SELVA","SEYKM","SILVR","SKBNK","SKTAS","SKYMD",
    "SMART","SMRTG","SNGYO","SNICA","SNKRN","SNPAM","SODSN","SOKE","SOKM","SONME",
    "SRVGY","SUMAS","SUNTK","SURGY","SUWEN","SYS","TABGD","TARAF","TATGD","TAVHL",
    "TBORG","TDGYO","TEKTU","TERA","TETMT","TEZOL","TGSAS","TKNSA","TLMAN","TMPOL",
    "TMSN","TNZTP","TRCAS","TRGYO","TRILC","TSGYO","TSKB","TSPOR","TUCLK","TUKAS",
    "TUREX","TURGG","TURSG","UFUK","ULAS","ULKER","ULUFA","ULUSE","ULUUN","UMPAS",
    "UNLU","USAK","UZERB","TATEN","VAKFN","VAKKO","VANGD","VBTYZ","VERUS","VESBE",
    "VESTL","VKFYO","VKGYO","VKING","VRGYO","YAPRK","YATAS","YAYLA","YBTAS","YEOTK",
    "YESIL","YGGYO","YGYO","YKSLN","YONGA","YUNSA","YYAPI","YYLGD","ZEDUR","ZOREN",
    "ZRGYO","GIPTA","TEHOL","PAHOL","MARMR","BIGEN","GLRMK","TRHOL","AAGYO",
}

# ─── ABD HİSSELERİ (S&P 500 + NASDAQ) ───────────────────────────────────────
US = {
    "^GSPC","^DJI","^NDX","^IXIC","^RUT",
    "AAPL","MSFT","NVDA","AMZN","META","TSLA","GOOGL","GOOG","AVGO","COST","NFLX",
    "AMD","QCOM","INTU","AMAT","TXN","AMGN","BKNG","ISRG","CMCSA","SBUX","ADP",
    "REGN","VRTX","LRCX","PANW","MU","KLAC","SNPS","CDNS","CRWD","MELI","MAR",
    "ORLY","CTAS","NXPI","CSX","PCAR","MNST","WDAY","ROP","RKLB","TSPY","ARCC",
    "JEPI","QQQI","SPYI","JEPQ","SOFI","ASTS","A","AAL","ABBV","ABNB","ABT","ACGL",
    "ACN","ADBE","ADI","ADM","AEE","AEP","AES","AFL","AGNC","AIG","AIZ","AJG","AKAM",
    "ALB","ALGN","ALL","ALLE","AMCR","AME","AMP","AMT","AMTM","ANET","ANSS","AON",
    "AOS","APA","APD","APH","APTV","ARE","ATO","AVB","AVY","AWK","AXON","AXP","AZO",
    "BA","BAC","BALL","BAX","BBWI","BBY","BDX","BEN","BG","BIIB","BK","BKR","BLDR",
    "BLK","BMY","BR","BRK-B","BRO","BSX","BWA","BX","BXP","C","CAG","CAH","CARR",
    "CAT","CB","CBOE","CBRE","CCI","CCL","CDW","CE","CEG","CF","CFG","CHD","CHRW",
    "CHTR","CI","CINF","CL","CLX","CME","CMG","CMI","CMS","CNC","CNP","COF","COO",
    "COP","COR","CPAY","CPB","CPRT","CPT","CRL","CRM","CSCO","CSGP","CTSH","CTVA",
    "CVS","CVX","CZR","D","DAL","DAY","DD","DE","DECK","DFS","DG","DGX","DHI","DHR",
    "DIS","DLR","DLTR","DOC","DOV","DOW","DPZ","DRI","DTE","DUK","DVA","DVN","DXCM",
    "EA","EBAY","ECL","ED","EFX","EG","EIX","EL","ELV","EMN","EMR","ENPH","EOG",
    "EQIX","EQR","EQT","ERIE","ES","ESS","ETN","ETR","EVRG","EW","EXC","EXPD","EXPE",
    "EXR","F","FANG","FAST","FCX","FDS","FDX","FE","FFIV","FI","FICO","FIS","FITB",
    "FMC","FOX","FOXA","FRT","FSLR","FTNT","FTV","GD","GE","GEHC","GEN","GEV","GILD",
    "GIS","GL","GLW","GM","GNRC","GPC","GPN","GRMN","GS","GWW","HAL","HAS","HBAN",
    "HCA","HD","HES","HIG","HII","HLT","HOLX","HON","HPE","HPQ","HRL","HSY","HUBB",
    "HUM","HWM","IBM","ICE","IDXX","IEX","IFF","ILMN","INCY","INTC","INVH","IP","IPG",
    "IQV","IR","IRM","IT","ITW","IVZ","J","JBHT","JBL","JCI","JKHY","JNJ","JNPR",
    "JPM","K","KDP","KEY","KEYS","KHC","KIM","KKR","KMB","KMI","KMX","KO","KR","KVUE",
    "L","LDOS","LEN","LH","LHX","LIN","LKQ","LLY","LMT","LNT","LOW","LULU","LUV",
    "LVS","LW","LYB","LYV","MA","MAA","MAS","MCD","MCHP","MCK","MCO","MDLZ","MDT",
    "MET","MGM","MHK","MKC","MKTX","MLM","MMC","MMM","MO","MOH","MOS","MPC","MPWR",
    "MRK","MRNA","MS","MSCI","MSI","MTB","MTCH","MTD","NCLH","NDSN","NEE","NEM","NI",
    "NKE","NOC","NOW","NRG","NSC","NTAP","NTRS","NUE","NVR","NWS","NWSA","O","ODFL",
    "OKE","OMC","ON","ORCL","OTIS","OXY","PARA","PAYC","PAYX","PCG","PEG","PEP","PFE",
    "PFG","PG","PGR","PH","PHM","PKG","PLD","PLTR","PM","PNC","PNR","PNW","POOL",
    "PPG","PPL","PRU","PSA","PSX","PTC","PWR","PYPL","QRVO","RCL","REG","RF","RJF",
    "RL","RMD","ROK","ROL","ROST","RSG","RTX","RVTY","SBAC","SCHW","SHW","SJM","SLB",
    "SMCI","SNA","SO","SOLV","SPG","SPGI","SRCL","SRE","STE","STLD","STT","STX","STZ",
    "SW","SWK","SWKS","SYF","SYK","SYY","T","TAP","TDG","TDY","TECH","TEL","TER",
    "TFC","TFX","TGT","TJX","TMO","TMUS","TPR","TRGP","TRMB","TROW","TRV","TSCO",
    "TSN","TT","TTWO","TTD","TXT","TYL","UAL","UBER","UDR","UHS","ULTA","UNH","UNP",
    "UPS","URI","USB","V","VICI","VLO","VLTO","VMC","VRSK","VRSN","VTR","VTRS","VZ",
    "WAB","WAT","WBA","WBD","WDC","WEC","WELL","WFC","WM","WMB","WMT","WRB","WST",
    "WTW","WY","WYNN","XEL","XOM","XYL","YUM","ZBH","ZBRA","ZTS",
    # NASDAQ ek
    "ROKU","ZS","OKTA","TEAM","DDOG","MDB","SHOP","DOCU","SGEN","MRVL","LULU","VRSK",
    "SIRI","PDD","JD","BIDU","NTES","NXST","SPLK","SWKS","QRVO","AVTR","SEDG","MELI",
}

# ─── KRİPTO (kullanıcı "BTC" veya "BTC-USD" yazabilir) ──────────────────────
CRYPTO_RAW = {
    "BTC-USD","ETH-USD","BNB-USD","SOL-USD","XRP-USD","ADA-USD","DOGE-USD","AVAX-USD",
    "TRX-USD","DOT-USD","MATIC-USD","LINK-USD","TON-USD","SHIB-USD","LTC-USD","BCH-USD",
    "ICP-USD","NEAR-USD","APT-USD","STX-USD","FIL-USD","ATOM-USD","ARB-USD","OP-USD",
    "INJ-USD","KAS-USD","TIA-USD","SEI-USD","SUI-USD","ALGO-USD","HBAR-USD","EGLD-USD",
    "FTM-USD","XLM-USD","VET-USD","ETC-USD","EOS-USD","XTZ-USD","MINA-USD","ASTR-USD",
    "FLOW-USD","KLAY-USD","IOTA-USD","NEO-USD","RNDR-USD","GRT-USD","FET-USD","UNI-USD",
    "LDO-USD","MKR-USD","AAVE-USD","SNX-USD","RUNE-USD","QNT-USD","CRV-USD","CFX-USD",
    "CHZ-USD","AXS-USD","SAND-USD","MANA-USD","THETA-USD","GALA-USD","ENJ-USD",
    "COMP-USD","1INCH-USD","ZIL-USD","BAT-USD","LRC-USD","SUSHI-USD","YFI-USD",
    "ZRX-USD","ANKR-USD","PEPE-USD","BONK-USD","FLOKI-USD","WIF-USD","LUNC-USD",
    "XMR-USD","DASH-USD","ZEC-USD","BTT-USD","RVN-USD","WAVES-USD","OMG-USD",
}
# Kısa yazım eşlemesi: "BTC" → "BTC-USD"
CRYPTO_SHORT = {c.replace("-USD", ""): c for c in CRYPTO_RAW}

# ─── EMTİALAR ─────────────────────────────────────────────────────────────────
COMMODITY_MAP = {
    "ALTIN":  "GC=F",
    "GUMUS":  "SI=F",
    "GÜMÜŞ":  "SI=F",
    "BAKIR":  "HG=F",
    "PETROL": "CL=F",
    "WTI":    "CL=F",
    "DOGALGAZ":"NG=F",
    "DOĞALGAZ":"NG=F",
    "BRENT":  "BZ=F",
    "GCF":    "GC=F",
    "SIF":    "SI=F",
    "CLF":    "CL=F",
    "NGF":    "NG=F",
    "BZF":    "BZ=F",
}
COMMODITY_RAW = {"GC=F","SI=F","HG=F","CL=F","NG=F","BZ=F"}

# ─── TÜM GEÇERLİ DISPLAY İSİMLER (fuzzy match için) ─────────────────────────
# US hisseleri şimdilik devre dışı — ileride ayrı kanal için aktif edilir
ALL_DISPLAY = BIST | CRYPTO_SHORT.keys() | COMMODITY_MAP.keys()


def resolve_ticker(raw: str) -> tuple[str | None, list[str]]:
    """
    Kullanıcının yazdığı ham ticker'ı çözer.
    Döndürür: (app_ticker_or_None, öneriler_listesi)

    app_ticker: Streamlit selectbox'ta aranacak değer (display name).
    Örnek: "KCHOL" → ("KCHOL", [])
           "KCHOOL" → (None, ["KCHOL"])
           "BTC"   → ("BTC-USD", [])   ← crypto kısa form
           "ALTIN" → ("GC=F", [])
    """
    import difflib

    upper = raw.upper().strip()

    # 1. Doğrudan BIST eşleşmesi
    if upper in BIST:
        return upper, []

    # 2. US hissesi
    if upper in US:
        return upper, []

    # 3. Kripto — kısa form ("BTC") veya uzun form ("BTC-USD")
    if upper in CRYPTO_SHORT:
        return CRYPTO_SHORT[upper], []
    if upper + "-USD" in CRYPTO_RAW:
        return upper + "-USD", []
    if upper in CRYPTO_RAW:
        return upper, []

    # 4. Emtia — Türkçe isim veya kod
    if upper in COMMODITY_MAP:
        return COMMODITY_MAP[upper], []
    if upper in COMMODITY_RAW:
        return upper, []

    # 5. Bulunamadı — fuzzy önerileri bul
    candidates = BIST | US | set(CRYPTO_SHORT.keys()) | set(COMMODITY_MAP.keys())
    suggestions = difflib.get_close_matches(upper, candidates, n=3, cutoff=0.65)
    return None, suggestions
