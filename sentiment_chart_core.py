# -*- coding: utf-8 -*-
"""Screen-aligned technical sentiment chart calculations.

Daily BIST weights were recalibrated on 2026-09-28 against six G-mode target
screenshots; weekly and US200 weights remain unchanged. US200 uses the same
weights as an initial transfer and has not been validated against US target
screens. The score is a technical price/volume sentiment estimate, not news
sentiment or a return forecast. Daily and weekly weights are separate; weekly
uses Friday-anchored OHLCV bars and includes the current partial week. Keep rendering in app.py and unrelated flow-momentum consumers
on indicators.compute_flow_momentum unchanged.
"""
from __future__ import annotations

import logging
import os
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import streamlit as st

from data_layer import get_safe_historical_data, is_last_bar_projected
from indicators import compute_flow_momentum, compute_mfi

_LOG = logging.getLogger(__name__)
_BAR_LAG = 8
_DATA_TICKER_ALIASES = {"KRMD": "KRDMD"}
_MARKET_SETTINGS = {
    "BIST": {
        "benchmark": "XU100",
        "timezone": "Europe/Istanbul",
    },
    "US200": {
        "benchmark": "^GSPC",
        "timezone": "America/New_York",
    },
}


def _market_settings(market_profile=None):
    profile = str(market_profile or os.environ.get("SMR_MARKET_PROFILE", "BIST")).strip().upper()
    if profile not in _MARKET_SETTINGS:
        profile = "BIST"
    return profile, _MARKET_SETTINGS[profile]


MODEL_PARAMS = {
    "daily": {
        "feature_names": [
            "rsi14",
            "mfi14",
            "position20",
            "cmf5",
            "cmf20",
            "cmf50",
            "return1",
            "relative_return1",
            "return3",
            "relative_return3",
            "return5",
            "relative_return5",
            "return7",
            "relative_return7",
            "return10",
            "relative_return10",
            "return14",
            "relative_return14",
            "return20",
            "relative_return20",
            "return30",
            "relative_return30",
            "return60",
            "relative_return60",
            "ema_distance5",
            "sma_distance5",
            "ema_distance10",
            "sma_distance10",
            "ema_distance20",
            "sma_distance20",
            "ema_distance50",
            "sma_distance50",
            "ema_distance100",
            "sma_distance100",
            "ema_distance200",
            "sma_distance200",
            "ema20_sma50",
            "ema20_sma200",
            "volume_ratio20",
            "volume_z20",
            "signed_volume",
            "money_flow_volume",
            "obv_slope3",
            "ad_slope3",
            "obv_slope5",
            "ad_slope5",
            "obv_slope10",
            "ad_slope10",
            "obv_slope20",
            "ad_slope20",
            "rsi_change_ema3",
            "mfi_change_ema3",
            "position_change_ema3",
            "cmf20_change_ema3",
            "rsi_change_ema5",
            "mfi_change_ema5",
            "position_change_ema5",
            "cmf20_change_ema5",
            "rsi_change_ema10",
            "mfi_change_ema10",
            "position_change_ema10",
            "cmf20_change_ema10",
            "rsi_mfi_position_mean"
        ],
        "means": [
            50.2150834542371,
            58.12963663149068,
            57.13914640808636,
            7.792958121111203,
            0.1847285666272762,
            -2.63299362779219,
            0.9431511725656817,
            0.6963136203210308,
            2.563135095142591,
            1.7969277823127452,
            3.6497070367921975,
            2.610979801344426,
            3.6516696916842566,
            2.619984799438656,
            3.5671668632406988,
            2.41687842747055,
            3.066564070855745,
            1.4567957751469123,
            1.430080588236072,
            -0.23574412625239288,
            -4.926564528693684,
            -5.241290311754022,
            -8.07504174929178,
            -7.906418538248134,
            1.2283182030286357,
            1.5793168504687283,
            1.6057797383795505,
            2.453653531650148,
            0.6568351133359198,
            2.1061549091484575,
            -2.4909527006944603,
            -4.212291420058238,
            -2.836357134946502,
            -6.464695538302979,
            1.6259490787790454,
            4.67113448781746,
            -5.090181598820311,
            4.272956688614366,
            1.1679076677104037,
            0.14862056183630795,
            0.4210820229104604,
            0.11995091037959157,
            1.1697749456096629,
            0.32549600273741675,
            1.8537750564573192,
            0.497227702408501,
            3.0661610391738217,
            0.4663581557767968,
            4.291192751004383,
            0.03694571332545396,
            0.9726321091820157,
            0.9996294841615835,
            2.4512660774291684,
            0.7900770098297705,
            0.8736986795079047,
            0.9189212762452926,
            2.2375389529825327,
            0.7133252623047076,
            0.6589891576643975,
            0.7367929536971068,
            1.7968657627697548,
            0.5277203061178314,
            55.161288831271385
        ],
        "scales": [
            10.841696070592025,
            19.829639340785437,
            35.57411290888574,
            34.53176049616739,
            19.938733141195353,
            8.865452495700778,
            3.6799353959730494,
            3.4695761983817355,
            6.738707259178081,
            6.411407547968429,
            8.422934738059558,
            8.114589992148797,
            9.697771699664854,
            9.489897216155748,
            11.526063592500824,
            11.40241371348943,
            15.230089400084244,
            14.5806877346171,
            20.329762665104354,
            19.033865710538116,
            20.94342783580657,
            19.112281530923173,
            13.861222491168064,
            12.318795940621484,
            3.303719430188605,
            4.102279954489913,
            5.495846120227292,
            6.185790323805021,
            8.407106373309125,
            10.590344661429043,
            10.614229999476075,
            13.06692256074547,
            9.869853674423586,
            10.050808298843117,
            12.20617455642083,
            14.23571030667415,
            8.064429224662668,
            13.424685893458724,
            0.8921182421400394,
            1.2817753469134194,
            1.4038262320382149,
            1.0166183131353141,
            2.207496480772177,
            1.718679742100833,
            2.841333899675206,
            2.2344867024716146,
            4.113022946499979,
            3.4072001106641645,
            5.185275450441483,
            3.9877466282390697,
            2.762987983321651,
            3.740436214903333,
            10.307076884314778,
            3.8036950511768666,
            1.981267139885336,
            2.9258698478451666,
            7.4404351438065985,
            2.944580240366864,
            1.2465422354606646,
            2.1219042502797563,
            4.693071887527113,
            2.1535509349135347,
            20.148010895319832
        ],
        "weights": [
            0.035728964491311306,
            -0.021722927368357424,
            0.06290921743988484,
            0.006580951921330387,
            -0.01935335980250972,
            0.013677681961694953,
            -0.017265038146894184,
            0.0262427070091375,
            0.009917197269101993,
            0.06985649599801302,
            0.04699979380194747,
            0.0993653250380732,
            0.04122746644619274,
            0.09227980928293744,
            -0.032319949531249316,
            0.030676150033726677,
            0.013274452795931408,
            0.08217141579484602,
            -0.0012691879017331154,
            0.048059661704748645,
            -0.008970422063883793,
            0.03456703869173503,
            -0.021309122741610422,
            0.06537502015068326,
            0.016928129904304548,
            0.010077646067788002,
            0.018234720760892185,
            0.023371981913611766,
            0.007195387790809917,
            0.007161162602630862,
            0.0037980872806387994,
            -0.007239682912077601,
            0.015672242008253057,
            0.04254914962137654,
            0.053019673540136515,
            0.02221689521383668,
            -0.016910028410210227,
            0.019703483231295806,
            -0.012798065733466856,
            -0.007333843951127631,
            -0.04271258812342536,
            0.007342033299704683,
            0.02957725571340805,
            -0.004402828758268345,
            0.09759522437073975,
            -0.01502822418783183,
            0.048514723692492386,
            -0.035898878170832586,
            0.05141838079794369,
            -0.01935335980250984,
            0.02057118915446968,
            0.0002596100911143936,
            0.019070683187745983,
            -0.02600946341012582,
            0.038527908606397904,
            0.012556828623955008,
            0.022053484365575995,
            -0.04796048367052801,
            0.04981078475127333,
            0.012874553761733395,
            0.024185923984127507,
            -0.046107930012356795,
            0.03630704745345433
        ],
        "target_mean": 5.947500000000001,
        "target_scale": 2.5232902627323717
    },
    "weekly": {
        "feature_names": [
            "rsi14",
            "mfi14",
            "position20",
            "cmf5",
            "cmf20",
            "cmf50",
            "return1",
            "relative_return1",
            "return3",
            "relative_return3",
            "return5",
            "relative_return5",
            "return7",
            "relative_return7",
            "return10",
            "relative_return10",
            "return14",
            "relative_return14",
            "return20",
            "relative_return20",
            "return30",
            "relative_return30",
            "return60",
            "relative_return60",
            "ema_distance5",
            "sma_distance5",
            "ema_distance10",
            "sma_distance10",
            "ema_distance20",
            "sma_distance20",
            "ema_distance50",
            "sma_distance50",
            "ema_distance100",
            "ema_distance200",
            "ema20_sma50",
            "volume_ratio20",
            "volume_z20",
            "signed_volume",
            "money_flow_volume",
            "obv_slope3",
            "ad_slope3",
            "obv_slope5",
            "ad_slope5",
            "obv_slope10",
            "ad_slope10",
            "obv_slope20",
            "ad_slope20",
            "rsi_change_ema3",
            "mfi_change_ema3",
            "position_change_ema3",
            "cmf20_change_ema3",
            "rsi_change_ema5",
            "mfi_change_ema5",
            "position_change_ema5",
            "cmf20_change_ema5",
            "rsi_change_ema10",
            "mfi_change_ema10",
            "position_change_ema10",
            "cmf20_change_ema10",
            "rsi_mfi_position_mean"
        ],
        "means": [
            49.457803810351194,
            61.37546925392177,
            42.091906254546466,
            0.28895675192720405,
            9.496099003958513,
            5.536182510177707,
            -0.29875114050774976,
            -0.4199986166400489,
            -1.4643230672850966,
            -1.996933431501433,
            -1.9939545859347652,
            -3.3594299718611094,
            -2.226094232602179,
            -4.5374556390058896,
            -1.202739197022672,
            -5.74249246248477,
            1.7373194506669265,
            -6.965480008295777,
            5.205401869557249,
            -9.718004060998304,
            11.947870373413055,
            -11.216011480467191,
            14.005015153824344,
            -25.102448482757406,
            -0.9276255250924405,
            -1.0510943672295538,
            -1.1131943965344688,
            -1.7943342284508492,
            0.1555920143375616,
            -0.4463813484075472,
            4.79131657983121,
            0.9061109150710348,
            9.086713593860866,
            12.725673857819567,
            3.903881833162859,
            1.050882055230898,
            -0.01531608583556715,
            -0.05616785170699033,
            -0.007109626601225216,
            -0.1933715004058433,
            -0.07565791282062401,
            -0.19720486776362722,
            -0.07084439440737403,
            0.3324400162887249,
            0.2974287840533702,
            3.2387010689535414,
            1.8992198007917025,
            -0.5853388446016297,
            -1.1691851563575257,
            -2.0030836376356453,
            -0.5570211543779418,
            -0.5600077129902862,
            -1.067089019292562,
            -2.1472764090286307,
            -0.49082419085299994,
            -0.408371979317623,
            -0.8576863103336443,
            -1.9522818431379751,
            -0.30887070856483156,
            50.97505977293982
        ],
        "scales": [
            7.605053733550199,
            15.028022209009665,
            26.017468023936864,
            24.31103554665584,
            12.260817304924835,
            2.764308822790242,
            5.389700142346776,
            4.01971015101219,
            7.421582187944079,
            5.29655253153266,
            9.360324074988544,
            6.256720723443512,
            10.296721690948974,
            7.061726335780593,
            11.695751327333086,
            7.619565333292148,
            13.728544146241925,
            7.4911954116365305,
            14.206875010637535,
            6.979956849232676,
            16.592049544787873,
            11.431060298243747,
            6.424107466497127,
            6.493418010847175,
            3.7773291769422963,
            4.852281745889002,
            5.008553134487515,
            6.185789974521031,
            6.653324599824871,
            7.760639175684119,
            9.378456421340507,
            5.9273175092257375,
            12.267983365579228,
            15.542527089351978,
            2.4557960917036246,
            0.4508726122921798,
            1.1715933025071883,
            1.1192390914206427,
            0.7084557974009537,
            1.746803885137226,
            0.9189936245740804,
            2.040400860095044,
            1.169509921457603,
            2.8674331068881287,
            1.6689895270731046,
            4.377665979242389,
            2.4521634609849676,
            2.7230492889104787,
            3.434973610871185,
            10.304557769072428,
            2.708139633842146,
            1.8821362458487438,
            2.6212156616003615,
            7.092675535253371,
            2.068779688149516,
            1.0664012493733712,
            1.707139303881499,
            3.9334822800595814,
            1.4947425642399519,
            14.800713155468497
        ],
        "weights": [
            0.05201260852647882,
            -0.00004433224896880103,
            0.011469805977463892,
            -0.05083189628988292,
            0.015837708926859054,
            -0.007910821909904206,
            -0.029581219397695944,
            0.0532151758381353,
            0.01966286548837932,
            0.1961428067823991,
            -0.0030545582908191493,
            0.11330142090523694,
            0.028973574910275336,
            0.09440879259355762,
            0.021452776827848036,
            0.03243532567569709,
            -0.06381385669433123,
            0.08038260344157315,
            0.014648964873248889,
            0.06521724015673229,
            0.038578608494271964,
            0.03304393959000272,
            0.01528259837152903,
            0.017382797837161117,
            0.012164010214877749,
            0.00726324794666355,
            0.012830588577196501,
            0.02599687542266284,
            0.014334303768704277,
            -0.015040670272727713,
            0.044259540689987394,
            0.035566770111375566,
            0.06246683445579699,
            0.06724578600371767,
            0.007010001296355656,
            0.001548308435638736,
            -0.017443211220148473,
            0.058016281255643246,
            -0.004653617169522498,
            0.0103766575975671,
            0.014590310571165345,
            -0.0023050708028652424,
            -0.04907591600681377,
            -0.047257734908379215,
            0.046092964544725994,
            0.029570840077089593,
            0.015837708926859033,
            -0.00920872492563499,
            0.003593286943425759,
            -0.009180860369563074,
            -0.0039746348684534675,
            -0.008497305114231543,
            -0.013768223795268078,
            0.013679678528641711,
            -0.011295381663839813,
            -0.02793530628577367,
            -0.02349086039492914,
            0.03416029102559621,
            -0.024989550169962682,
            0.015614287402251974
        ],
        "target_mean": 4.614166666666667,
        "target_scale": 1.7694540510815446
    },
    "daily_bar_scale": 3.6419538662059336,
    "weekly_bar_scale": 4.275946347193899,
    "feature_count": 63,
    "reference_latest": {
        "daily": {
            "SASA": "2026-09-11 00:00:00",
            "PETKM": "2026-09-11 00:00:00",
            "VAKBN": "2026-09-11 00:00:00",
            "DSTKF": "2026-09-11 00:00:00"
        },
        "weekly": {
            "TTKOM": "2026-09-11 00:00:00",
            "TOASO": "2026-09-11 00:00:00"
        }
    }
}


# 2026-10-01 — günlük Sentiment Puanı güçlü deneme katsayıları:
# altı ekran-hedefi + EKGYO yükseliş senaryosu ile yapılan kontrollü testten
# sabitlendi. Fiyat, STP, haftalık model ve bar ölçeği değişmedi.
MODEL_PARAMS["daily"].update({
    "means": [51.2352663565,58.7698290306,57.1993879899,-4.53091524612,-5.65336487893,-3.05577154998,0.0314173111217,0.401311087953,0.190352379871,1.1479307768,0.415623826409,1.6745111284,0.483885949369,1.95639968806,0.422211826087,2.01877036214,1.35561365792,1.97509889966,1.6395717266,1.93374060767,3.66585954504,3.4545205963,8.82346336545,9.79245207114,0.0709833365298,0.0850215862984,0.20084868333,0.210596187142,0.517525191237,0.59345819142,1.71234513288,2.10872057639,4.03480788828,4.1333963977,9.03700860102,10.2314648292,1.29435462934,9.13372105629,0.960587698164,-0.109459014318,0.0785374577772,-0.0549786577243,0.299941434287,-0.150663113585,0.565936165132,-0.225940425987,0.953233347438,-0.582211992759,1.95937566433,-1.13067297579,-0.0681214047995,0.344632536643,0.0408071153154,0.119017785592,-0.0506806456409,0.419753080275,0.116142556278,0.0343295320124,-0.0326264615256,0.427191368117,0.172459160918,-0.0944995769686,55.7348277924],
    "scales": [11.6473526897,12.9598490878,31.2050434206,29.6904217874,13.419208709,9.80579247895,2.3213615361,1.67211075866,3.74489712448,2.84530632137,4.43598310083,3.780827614,4.82850556311,4.83455315369,6.32508328029,6.77261004969,8.65385687257,9.30745935946,11.1918081694,11.7414687941,16.7107418285,16.8714318147,30.6138111593,30.934660266,1.76984782135,2.29208598435,2.7370854334,3.01366060245,4.70390007835,5.19576520373,9.80969200092,12.2676315142,14.7711744587,17.8828994626,20.7730794299,23.7870784609,7.70651561347,19.6898865072,0.36235183133,0.989991283272,1.01770933053,0.662769545021,1.70956338786,1.10537315831,2.05274287166,1.50780474781,2.55582153471,2.07053495645,2.8940688834,2.68384174179,2.54230934629,3.11956924331,9.80784090479,2.77191586252,1.77469433656,2.32298732743,6.76901764667,2.11484777486,1.03667203849,1.47190789306,3.88323883418,1.42716937204,17.0891400654],
    "weights": [0.07116320788587928,-0.26423575155055756,0.1475616834007118,0.3044602452335535,0.1031456584359624,-0.042364380401966176,0.32580067080310804,-0.08325359165405502,0.0,0.5368152655812386,-0.25224775655289544,0.5781542321169411,0.16200361337916522,-0.15366111690314024,-0.11291662565343577,-0.32472606681182403,-0.36002375213561744,0.06429757564237543,0.14524561103740213,0.10149634520155557,-0.16061229534758556,0.21218989272129846,0.014114542205951729,-0.07480320297855664,0.03789672701274542,-0.23273738420985357,0.2594923059403261,0.22939511480604782,0.19705385113819987,0.18822944393099653,0.011388850985580727,0.010425367026825661,-0.013995409781433689,-0.026225306820351117,-0.05624441737600985,0.046081776405735575,-0.009827372870657308,0.087678336083581,-0.01226649528625498,0.003114076309381656,-0.005319134072242694,0.065093822924891,-0.058949096954358,-0.09052431871528537,-0.1258105947219854,-0.26731699681462573,0.07588203349911528,-0.050203201282440185,-0.12174247228784038,0.10314565843587077,-0.2265573465597019,0.02970892010361805,-0.07285013667230428,0.12129735281781284,-0.3440694306045142,0.20993362768119755,-0.1261566083357833,-0.27297418740577073,0.0027007470977528127,0.02117917834475315,0.16003236777275262,0.15093026344711408,0.03918816759539613],
    "target_mean": 5.1033970426357325,
    "target_scale": 2.104338683104475,
})


def _clean_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    out = frame.copy()
    out.index = pd.to_datetime(out.index)
    if out.index.tz is not None:
        out.index = out.index.tz_localize(None)
    out = out[~out.index.duplicated(keep="last")].sort_index()
    required = ("Open", "High", "Low", "Close", "Volume")
    missing = [column for column in required if column not in out.columns]
    if missing:
        raise ValueError(f"OHLCV columns missing: {missing}")
    out = out.loc[:, list(required)].astype(float)
    return out


def _include_current_bar(frame: pd.DataFrame) -> pd.DataFrame:
    """Keep today's bar while restoring cumulative volume from its projection."""
    projected, progress = is_last_bar_projected(frame)
    if not projected or frame.empty:
        return frame
    out = frame.copy()
    if progress > 0:
        out.loc[out.index[-1], "Volume"] = float(out.iloc[-1]["Volume"]) * progress
    out.attrs["vol_projected"] = False
    out.attrs["vol_progress"] = 1.0
    return out


def _weekly_with_current(frame: pd.DataFrame, now=None, market_profile="BIST") -> pd.DataFrame:
    _profile, _settings = _market_settings(market_profile)
    _tz = ZoneInfo(_settings["timezone"])
    if now is None:
        now = pd.Timestamp.now(tz=_tz).tz_localize(None)
    else:
        now = pd.Timestamp(now)
        if now.tzinfo is not None:
            now = now.tz_convert(_tz).tz_localize(None)
    today = now.normalize()
    source = frame.loc[frame.index < today + pd.Timedelta(days=1)]
    weekly = source.resample("W-FRI").agg({
        "Open": "first", "High": "max", "Low": "min",
        "Close": "last", "Volume": "sum",
    }).dropna(subset=["Close"])
    if now.weekday() <= 4:
        week_start = today - pd.Timedelta(days=now.weekday())
        week_end = week_start + pd.Timedelta(days=4)
        current_dates = frame.index[(frame.index >= week_start) & (frame.index < today + pd.Timedelta(days=1))]
    else:
        week_end = today - pd.Timedelta(days=now.weekday() - 4)
        current_dates = pd.DatetimeIndex([])
    weekly = weekly.loc[weekly.index <= week_end].copy()
    if len(current_dates) and week_end in weekly.index:
        current_week = weekly.loc[week_end].copy()
        weekly = weekly.drop(index=week_end)
        weekly.loc[current_dates.max().normalize()] = current_week
        weekly = weekly.sort_index()
    return weekly


def _build_features(stock: pd.DataFrame, benchmark: pd.DataFrame) -> pd.DataFrame:
    """Same causal price/volume inputs used by the six-stock v6 holdout."""
    df = stock
    high, low, close = (df[key].astype(float) for key in ("High", "Low", "Close"))
    volume = df["Volume"].fillna(0.0).astype(float)
    bench_close = benchmark["Close"].reindex(df.index).ffill().astype(float)
    ret = lambda series, n: series.pct_change(n) * 100.0
    ema = lambda series, n: series.ewm(span=n, adjust=False).mean()
    sma = lambda series, n: series.rolling(n).mean()

    delta = close.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rsi = (100 - 100 / (1 + up / down.replace(0, np.nan))).fillna(50.0)
    mfi = compute_mfi(df, period=14)
    mfi = mfi.fillna(50.0) if mfi is not None else pd.Series(50.0, index=df.index)
    roll_range = (close.rolling(20).max() - close.rolling(20).min()).replace(0, np.nan)
    position20 = ((close - close.rolling(20).min()) / roll_range * 100.0).fillna(50.0)
    clv = ((2 * close - high - low) / (high - low).replace(0, np.nan)).fillna(0.0)
    mfv = clv * volume
    obv = (np.sign(delta) * volume).fillna(0.0).cumsum()
    ad_line = mfv.fillna(0.0).cumsum()

    features = pd.DataFrame(index=df.index)
    features["rsi14"] = rsi
    features["mfi14"] = mfi
    features["position20"] = position20
    for n in (5, 20, 50):
        features[f"cmf{n}"] = mfv.rolling(n).sum() / volume.rolling(n).sum().replace(0, np.nan) * 100.0
    for n in (1, 3, 5, 7, 10, 14, 20, 30, 60):
        features[f"return{n}"] = ret(close, n)
        features[f"relative_return{n}"] = ret(close, n) - ret(bench_close, n)
    for n in (5, 10, 20, 50, 100, 200):
        features[f"ema_distance{n}"] = (close / ema(close, n) - 1.0) * 100.0
        features[f"sma_distance{n}"] = (close / sma(close, n) - 1.0) * 100.0
    features["ema20_sma50"] = (ema(close, 20) / sma(close, 50) - 1.0) * 100.0
    features["ema20_sma200"] = (ema(close, 20) / sma(close, 200) - 1.0) * 100.0
    volume_avg = volume.rolling(20).mean().replace(0, np.nan)
    features["volume_ratio20"] = volume / volume_avg
    features["volume_z20"] = (volume - volume.rolling(20).mean()) / volume.rolling(20).std().replace(0, np.nan)
    features["signed_volume"] = np.sign(ret(close, 1)) * features["volume_ratio20"]
    features["money_flow_volume"] = clv * features["volume_ratio20"]
    for n in (3, 5, 10, 20):
        features[f"obv_slope{n}"] = obv.diff(n) / volume_avg
        features[f"ad_slope{n}"] = ad_line.diff(n) / volume_avg
    for n in (3, 5, 10):
        features[f"rsi_change_ema{n}"] = rsi.diff().ewm(span=n, adjust=False).mean()
        features[f"mfi_change_ema{n}"] = mfi.diff().ewm(span=n, adjust=False).mean()
        features[f"position_change_ema{n}"] = position20.diff().ewm(span=n, adjust=False).mean()
        features[f"cmf20_change_ema{n}"] = features["cmf20"].diff().ewm(span=n, adjust=False).mean()
    features["rsi_mfi_position_mean"] = (rsi + mfi + position20) / 3.0
    return features.replace([np.inf, -np.inf], np.nan)


def _predict_score(features: pd.DataFrame, timeframe: str) -> pd.Series:
    model = MODEL_PARAMS[timeframe]
    names = model["feature_names"]
    missing = [name for name in names if name not in features.columns]
    if missing:
        raise ValueError(f"Model feature columns missing: {missing}")
    raw = features.loc[:, names].to_numpy(dtype=float)
    means = np.asarray(model["means"], dtype=float)
    scales = np.asarray(model["scales"], dtype=float)
    weights = np.asarray(model["weights"], dtype=float)
    raw = np.where(np.isfinite(raw), raw, means)
    standardized = (raw - means) / scales
    scores = model["target_mean"] + model["target_scale"] * (standardized @ weights)
    return pd.Series(np.clip(scores, 0.0, 10.0), index=features.index, name="Sentiment")


def _signature(frame: pd.DataFrame) -> tuple:
    columns = ("Open", "High", "Low", "Close", "Volume")
    rows = []
    for index, row in frame.tail(3).iterrows():
        values = tuple(None if pd.isna(row[column]) else float(row[column]) for column in columns)
        rows.append((str(index), values))
    return (len(frame), tuple(rows), bool(frame.attrs.get("vol_projected", False)))


@st.cache_data(ttl=600, show_spinner=False)
def _calculate_cached(
    ticker: str, timeframe: str, market_profile: str, benchmark_ticker: str,
    stock_sig: tuple, benchmark_sig: tuple,
):
    stock = _include_current_bar(_clean_frame(get_safe_historical_data(ticker, period="5y")))
    benchmark = _include_current_bar(_clean_frame(get_safe_historical_data(benchmark_ticker, period="5y")))
    if stock.empty or benchmark.empty:
        return None
    if timeframe == "weekly":
        stock = _weekly_with_current(stock, market_profile=market_profile)
        benchmark = _weekly_with_current(benchmark, market_profile=market_profile)
    features = _build_features(stock, benchmark)
    scores = _predict_score(features, timeframe)
    bar_scale = MODEL_PARAMS[f"{timeframe}_bar_scale"]
    bars = (scores - scores.shift(_BAR_LAG)) * bar_scale
    flow, stp = compute_flow_momentum(stock)
    if flow is None or stp is None:
        raise ValueError("Flow/STP series could not be calculated")
    volume_avg = stock["Volume"].rolling(20, min_periods=1).mean()
    result = pd.DataFrame({
        "Date": stock.index,
        "MF_Smooth": bars.reindex(stock.index).to_numpy(dtype=float),
        "Sentiment": scores.reindex(stock.index).to_numpy(dtype=float),
        "STP": stp.reindex(stock.index).to_numpy(dtype=float),
        "Price": stock["Close"].to_numpy(dtype=float),
        "Volume": stock["Volume"].to_numpy(dtype=float),
        "Vol_Avg20": volume_avg.to_numpy(dtype=float),
    }).tail(30).reset_index(drop=True)
    result["Date_Str"] = pd.to_datetime(result["Date"]).dt.strftime("%d %b")
    result["RVOL"] = (result["Volume"] / result["Vol_Avg20"]).replace([np.inf, -np.inf], np.nan)
    return result


def calculate_sentiment_chart(ticker: str, timeframe: str = "daily", market_profile=None):
    """Return the selected G/H chart series using the shared frozen v6 fit."""
    try:
        profile, settings = _market_settings(market_profile)
        normalized = str(timeframe).strip().lower()
        if normalized in ("g", "daily", "gunluk", "günlük"):
            normalized = "daily"
        elif normalized in ("h", "weekly", "haftalik", "haftalık"):
            normalized = "weekly"
        else:
            raise ValueError(f"Unknown timeframe: {timeframe}")
        symbol = str(ticker).strip()
        if profile == "BIST":
            base_symbol = symbol.upper().removesuffix(".IS")
            data_symbol = _DATA_TICKER_ALIASES.get(base_symbol, base_symbol)
            if symbol.upper().endswith(".IS"):
                data_symbol += ".IS"
        else:
            data_symbol = symbol.upper()
        benchmark_ticker = settings["benchmark"]
        stock_snapshot = _clean_frame(get_safe_historical_data(data_symbol, period="5y"))
        benchmark_snapshot = _clean_frame(get_safe_historical_data(benchmark_ticker, period="5y"))
        if stock_snapshot.empty or benchmark_snapshot.empty:
            return None
        stock_sig = _signature(stock_snapshot)
        benchmark_sig = _signature(benchmark_snapshot)
        return _calculate_cached(
            data_symbol, normalized, profile, benchmark_ticker, stock_sig, benchmark_sig,
        )
    except Exception:
        _LOG.exception("Sentiment chart calculation failed for %s (%s)", ticker, timeframe)
        return None
