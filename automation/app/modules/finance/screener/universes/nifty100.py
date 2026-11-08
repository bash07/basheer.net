"""Nifty 100 universe definition with sector groupings for Indian stock screening."""
from __future__ import annotations
from typing import NamedTuple


class StockInfo(NamedTuple):
    symbol: str        # with .NS suffix
    display_name: str
    sector: str


# Sector index symbols on Yahoo Finance
SECTOR_INDEX_MAP = {
    "Banking & Finance": "^NSEBANK",
    "IT": "^CNXIT",
    "Auto": "^CNXAUTO",
    "Pharma & Healthcare": "^CNXPHARMA",
    "FMCG & Retail": "^CNXFMCG",
    "Metal": "^CNXMETAL",
    "Energy & Utilities": "^CNXENERGY",
    "Capital Goods": "^CNXINFRA",
    "Realty": "^CNXREALTY",
}


NIFTY_100_CONSTITUENTS: list[StockInfo] = [
    # Banking & Financial Services
    StockInfo("HDFCBANK.NS", "HDFC Bank", "Banking & Finance"),
    StockInfo("ICICIBANK.NS", "ICICI Bank", "Banking & Finance"),
    StockInfo("SBIN.NS", "State Bank of India", "Banking & Finance"),
    StockInfo("KOTAKBANK.NS", "Kotak Mahindra Bank", "Banking & Finance"),
    StockInfo("AXISBANK.NS", "Axis Bank", "Banking & Finance"),
    StockInfo("BAJFINANCE.NS", "Bajaj Finance", "Banking & Finance"),
    StockInfo("BAJAJFINSV.NS", "Bajaj Finserv", "Banking & Finance"),
    StockInfo("SHRIRAMFIN.NS", "Shriram Finance", "Banking & Finance"),
    StockInfo("CHOLAFIN.NS", "Cholamandalam Investment", "Banking & Finance"),
    StockInfo("JIOFIN.NS", "Jio Financial Services", "Banking & Finance"),
    StockInfo("CANBK.NS", "Canara Bank", "Banking & Finance"),
    StockInfo("BANKBARODA.NS", "Bank of Baroda", "Banking & Finance"),
    StockInfo("PNB.NS", "Punjab National Bank", "Banking & Finance"),
    StockInfo("MUTHOOTFIN.NS", "Muthoot Finance", "Banking & Finance"),
    StockInfo("SBILIFE.NS", "SBI Life Insurance", "Banking & Finance"),
    StockInfo("ICICIPRULI.NS", "ICICI Prudential Life", "Banking & Finance"),

    # Information Technology
    StockInfo("TCS.NS", "Tata Consultancy Services", "IT"),
    StockInfo("INFY.NS", "Infosys", "IT"),
    StockInfo("HCLTECH.NS", "HCL Technologies", "IT"),
    StockInfo("TECHM.NS", "Tech Mahindra", "IT"),
    StockInfo("WIPRO.NS", "Wipro", "IT"),
    StockInfo("LTIM.NS", "LTIMindtree", "IT"),
    StockInfo("PERSISTENT.NS", "Persistent Systems", "IT"),
    StockInfo("COFORGE.NS", "Coforge", "IT"),
    StockInfo("MPHASIS.NS", "Mphasis", "IT"),
    StockInfo("TATAELXSI.NS", "Tata Elxsi", "IT"),

    # Automobile & Auto Components
    StockInfo("TATAMOTORS.NS", "Tata Motors", "Auto"),
    StockInfo("MARUTI.NS", "Maruti Suzuki", "Auto"),
    StockInfo("M&M.NS", "Mahindra & Mahindra", "Auto"),
    StockInfo("BAJAJ-AUTO.NS", "Bajaj Auto", "Auto"),
    StockInfo("EICHERMOT.NS", "Eicher Motors", "Auto"),
    StockInfo("HEROMOTOCO.NS", "Hero MotoCorp", "Auto"),
    StockInfo("TVSMOTOR.NS", "TVS Motor Company", "Auto"),
    StockInfo("BOSCHLTD.NS", "Bosch Limited", "Auto"),
    StockInfo("MOTHERSON.NS", "Samvardhana Motherson", "Auto"),

    # Pharma & Healthcare
    StockInfo("SUNPHARMA.NS", "Sun Pharma", "Pharma & Healthcare"),
    StockInfo("CIPLA.NS", "Cipla", "Pharma & Healthcare"),
    StockInfo("DRREDDY.NS", "Dr. Reddy's Laboratories", "Pharma & Healthcare"),
    StockInfo("DIVISLAB.NS", "Divi's Laboratories", "Pharma & Healthcare"),
    StockInfo("APOLLOHOSP.NS", "Apollo Hospitals", "Pharma & Healthcare"),
    StockInfo("ZYDUSLIFE.NS", "Zydus Lifesciences", "Pharma & Healthcare"),
    StockInfo("LUPIN.NS", "Lupin", "Pharma & Healthcare"),
    StockInfo("AUROPHARMA.NS", "Aurobindo Pharma", "Pharma & Healthcare"),
    StockInfo("TORNTPHARM.NS", "Torrent Pharmaceuticals", "Pharma & Healthcare"),
    StockInfo("MANKIND.NS", "Mankind Pharma", "Pharma & Healthcare"),
    StockInfo("MAXHEALTH.NS", "Max Healthcare Institute", "Pharma & Healthcare"),
    StockInfo("FORTIS.NS", "Fortis Healthcare", "Pharma & Healthcare"),

    # FMCG & Retail
    StockInfo("ITC.NS", "ITC Limited", "FMCG & Retail"),
    StockInfo("HINDUNILVR.NS", "Hindustan Unilever", "FMCG & Retail"),
    StockInfo("NESTLEIND.NS", "Nestle India", "FMCG & Retail"),
    StockInfo("BRITANNIA.NS", "Britannia Industries", "FMCG & Retail"),
    StockInfo("TATACONSUM.NS", "Tata Consumer Products", "FMCG & Retail"),
    StockInfo("VBL.NS", "Varun Beverages", "FMCG & Retail"),
    StockInfo("DABUR.NS", "Dabur India", "FMCG & Retail"),
    StockInfo("GODREJCP.NS", "Godrej Consumer Products", "FMCG & Retail"),
    StockInfo("MARICO.NS", "Marico", "FMCG & Retail"),
    StockInfo("COLPAL.NS", "Colgate-Palmolive India", "FMCG & Retail"),
    StockInfo("TRENT.NS", "Trent Limited", "FMCG & Retail"),
    StockInfo("TITAN.NS", "Titan Company", "FMCG & Retail"),

    # Metals & Mining
    StockInfo("TATASTEEL.NS", "Tata Steel", "Metal"),
    StockInfo("JSWSTEEL.NS", "JSW Steel", "Metal"),
    StockInfo("HINDALCO.NS", "Hindalco Industries", "Metal"),
    StockInfo("VEDL.NS", "Vedanta", "Metal"),
    StockInfo("COALINDIA.NS", "Coal India", "Metal"),

    # Energy & Utilities
    StockInfo("RELIANCE.NS", "Reliance Industries", "Energy & Utilities"),
    StockInfo("NTPC.NS", "NTPC", "Energy & Utilities"),
    StockInfo("ONGC.NS", "Oil & Natural Gas Corporation", "Energy & Utilities"),
    StockInfo("POWERGRID.NS", "Power Grid Corporation", "Energy & Utilities"),
    StockInfo("BPCL.NS", "Bharat Petroleum", "Energy & Utilities"),
    StockInfo("GAIL.NS", "GAIL (India)", "Energy & Utilities"),
    StockInfo("IOC.NS", "Indian Oil Corporation", "Energy & Utilities"),
    StockInfo("PFC.NS", "Power Finance Corporation", "Energy & Utilities"),
    StockInfo("RECLTD.NS", "REC Limited", "Energy & Utilities"),

    # Capital Goods & Industrial
    StockInfo("LT.NS", "Larsen & Toubro", "Capital Goods"),
    StockInfo("BEL.NS", "Bharat Electronics", "Capital Goods"),
    StockInfo("HAL.NS", "Hindustan Aeronautics", "Capital Goods"),
    StockInfo("SIEMENS.NS", "Siemens", "Capital Goods"),
    StockInfo("ABB.NS", "ABB India", "Capital Goods"),
    StockInfo("HAVELLS.NS", "Havells India", "Capital Goods"),
    StockInfo("POLYCAB.NS", "Polycab India", "Capital Goods"),
    StockInfo("CUMMINSIND.NS", "Cummins India", "Capital Goods"),
    StockInfo("PIDILITIND.NS", "Pidilite Industries", "Capital Goods"),
    StockInfo("ULTRACEMCO.NS", "UltraTech Cement", "Capital Goods"),
    StockInfo("GRASIM.NS", "Grasim Industries", "Capital Goods"),
    StockInfo("AMBUJACEM.NS", "Ambuja Cements", "Capital Goods"),

    # Realty
    StockInfo("DLF.NS", "DLF Limited", "Realty"),
    StockInfo("GODREJPROP.NS", "Godrej Properties", "Realty"),
    StockInfo("OBEROIRLTY.NS", "Oberoi Realty", "Realty"),
    StockInfo("PRESTIGE.NS", "Prestige Estates", "Realty"),
    StockInfo("PHOENIXLTD.NS", "The Phoenix Mills", "Realty"),

    # Telecom, Services & Internet
    StockInfo("BHARTIARTL.NS", "Bharti Airtel", "Telecom & Services"),
    StockInfo("ZOMATO.NS", "Zomato", "Telecom & Services"),
    StockInfo("INDIGO.NS", "InterGlobe Aviation", "Telecom & Services"),
    StockInfo("ADANIENT.NS", "Adani Enterprises", "Industrial Conglomerate"),
    StockInfo("ADANIPORTS.NS", "Adani Ports", "Industrial Conglomerate"),
]


def get_nifty100_symbols() -> list[str]:
    """Return all 100 symbols with .NS extension."""
    return [stock.symbol for stock in NIFTY_100_CONSTITUENTS]


def get_stock_info_map() -> dict[str, StockInfo]:
    """Return mapping from symbol to StockInfo."""
    return {stock.symbol: stock for stock in NIFTY_100_CONSTITUENTS}


def get_sector_map() -> dict[str, list[StockInfo]]:
    """Return grouping of stocks by sector."""
    by_sector: dict[str, list[StockInfo]] = {}
    for s in NIFTY_100_CONSTITUENTS:
        by_sector.setdefault(s.sector, []).append(s)
    return by_sector
