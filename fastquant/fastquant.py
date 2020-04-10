#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Jun 25 19:48:03 2019

@author: enzoampil
"""

import os
import requests
from datetime import datetime
import pandas as pd
import numpy as np
from string import digits
import lxml.html as LH
from tqdm import tqdm
import tweepy
from pathlib import Path
import yfinance as yf

PSE_TWITTER_ACCOUNTS = [
    "phstockexchange",
    "colfinancial",
    "firstmetrosec",
    "BPItrade",
    "Philstocks_",
    "itradeph",
    "UTradePH",
    "wealthsec",
]

DATA_FORMAT_COLS = {
    "d": "dt",
    "o": "open",
    "h": "high",
    "l": "low",
    "c": "close",
    "v": "volume",
    "i": "openinterest",
}


def get_stock_table(stock_table_fp="stock_table.csv"):
    """
    Returns dataframe containing info about PSE listed stocks while also saving it
    """
    stock_table = pd.DataFrame(
        columns=[
            "Company Name",
            "Stock Symbol",
            "Sector",
            "Subsector",
            "Listing Date",
            "company_id",
            "security_id",
        ]
    )

    data = {
        "pageNo": "1",
        "companyId": "",
        "keyword": "",
        "sortType": "",
        "dateSortType": "DESC",
        "cmpySortType": "ASC",
        "symbolSortType": "ASC",
        "sector": "ALL",
        "subsector": "ALL",
    }

    for p in range(1, 7):
        print(str(p) + " out of " + str(7 - 1) + " pages", end="\r")
        data["pageNo"] = str(p)
        r = requests.post(
            url="https://edge.pse.com.ph/companyDirectory/search.ax", data=data
        )
        table = LH.fromstring(r.text)
        page_df = (
            pd.concat(
                [
                    pd.read_html(r.text)[0],
                    pd.DataFrame(
                        {"attr": table.xpath("//tr/td/a/@onclick")[::2]}
                    ),
                ],
                axis=1,
            )
            .assign(
                company_id=lambda x: x["attr"].apply(
                    lambda s: s[s.index("(") + 2 : s.index(",") - 1]
                )
            )
            .assign(
                security_id=lambda x: x["attr"].apply(
                    lambda s: s[s.index(",") + 2 : s.index(")") - 1]
                )
            )
            .drop(["attr"], axis=1)
        )

        stock_table = stock_table.append(page_df)
    stock_table.to_csv(stock_table_fp, index=False)
    return stock_table


def fill_gaps(df):
    """
    Fills gaps of time series dataframe with NaN rows
    """
    idx = pd.period_range(df.index.min(), df.index.max(), freq="D")
    # idx_forecast = pd.period_range(start_datetime, end_datetime, freq="H")
    ts = pd.DataFrame({"empty": [0 for i in range(idx.shape[0])]}, index=idx)
    ts = ts.to_timestamp()
    df_filled = pd.concat([df, ts], axis=1)
    del df_filled["empty"]
    return df_filled


def date_to_epoch(date):
    return int(datetime.strptime(date, "%Y-%m-%d").timestamp())


def remove_digits(string):
    remove_digits = str.maketrans("", "", digits)
    res = string.translate(remove_digits)
    return res


def get_disclosures_json(symbol, from_date, to_date):
    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Referer": "https://www.investagrams.com/Stock/PSE:JFC",
        "Origin": "https://www.investagrams.com",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_13_4) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/74.0.3729.169 Safari/537.36",
        "Content-Type": "text/plain; charset=utf-8",
    }
    from_date_epoch = date_to_epoch(from_date)
    to_date_epoch = date_to_epoch(to_date)
    params = (
        ("symbol", "PSE:{}".format(symbol)),
        ("from", from_date_epoch),
        ("to", to_date_epoch),
        ("resolution", "D"),  # Setting D (daily) by default
    )

    response = requests.post(
        "https://webapi.investagrams.com/InvestaApi/TradingViewChart/timescale_marks",
        headers=headers,
        params=params,
    )
    results = response.json()
    return results


def disclosures_json_to_df(disclosures):
    disclosure_dfs = {}
    for disc in ["D", "E"]:
        filtered_examples = [ex for ex in disclosures if ex["label"] == disc]
        additional_feats_df = pd.DataFrame(
            [
                dict(
                    [
                        tuple(item.split(":"))
                        for item in ex["tooltip"]
                        if ":" in item
                    ]
                )
                for ex in filtered_examples
            ]
        )
        main_df = pd.DataFrame(filtered_examples)[
            ["id", "time", "color", "label"]
        ]
        combined = pd.concat([main_df, additional_feats_df], axis=1)
        combined["time"] = pd.to_datetime(combined.time, unit="s")
        if "Total Revenue" in combined.columns.values:
            combined["Revenue Unit"] = combined["Total Revenue"].apply(
                lambda x: remove_digits(x).replace(".", "")
            )
            combined["Total Revenue"] = (
                combined["Total Revenue"]
                .str.replace("B", "")
                .str.replace("M", "")
                .astype(float)
            )
            # Net income is followed by a parenthesis which corresponds to that quarter's YoY growth
            combined["NI Unit"] = combined["Net Income"].apply(
                lambda x: remove_digits(x).replace(".", "")
            )
            combined["Net Income Amount"] = (
                combined["Net Income"]
                .str.replace("B", "")
                .str.replace("M", "")
                .apply(lambda x: x.split()[0])
                .astype(float)
            )
            combined["Net Income YoY Growth (%)"] = combined[
                "Net Income"
            ].apply(
                lambda x: str(x)
                .replace("(", "")
                .replace(")", "")
                .replace("%", "")
                .split()[1]
            )
        disclosure_dfs[disc] = combined
    return disclosure_dfs


def get_disclosures_df(symbol, from_date, to_date):
    disclosures = get_disclosures_json(symbol, from_date, to_date)
    disclosures_dfs = disclosures_json_to_df(disclosures)
    return disclosures_dfs


def get_pse_data_old(
    symbol, start_date, end_date, stock_table_fp="stock_table.csv"
):

    """Returns pricing data for a specified stock.

    Parameters
    ----------
    symbol : str
        Symbol of the stock in the PSE. You can refer to this link: https://www.pesobility.com/stock.
    start_date : str
        Starting date (YYYY-MM-DD) of the period that you want to get data on
    end_date : str
        Ending date (YYYY-MM-DD) of the period you want to get data on
    stock_table_fp : str
        File path of an existing stock table or where a newly downloaded table should be saved

    Returns
    -------
    pandas.DataFrame
        Stock data (in OHLCV format) for the specified company and date range
    """

    if os.path.isfile(stock_table_fp):
        print("Stock table exists!")
        print("Reading {} ...".format(stock_table_fp))
        stock_table = pd.read_csv(stock_table_fp)
    else:
        stock_table = get_stock_table(stock_table_fp=stock_table_fp)

    data = {
        "cmpy_id": int(
            stock_table["company_id"][
                stock_table["Stock Symbol"] == symbol
            ].values[0]
        ),
        "security_id": int(
            stock_table["security_id"][
                stock_table["Stock Symbol"] == symbol
            ].values[0]
        ),
        "startDate": datetime.strptime(start_date, "%Y-%m-%d").strftime(
            "%m-%d-%Y"
        ),
        "endDate": datetime.strptime(end_date, "%Y-%m-%d").strftime(
            "%m-%d-%Y"
        ),
    }

    r = requests.post(
        url="https://edge.pse.com.ph/common/DisclosureCht.ax", json=data
    )
    df = pd.DataFrame(r.json()["chartData"])
    rename_dict = {
        "CHART_DATE": "dt",
        "OPEN": "open",
        "HIGH": "high",
        "LOW": "low",
        "CLOSE": "close",
        "VALUE": "value",
    }
    rename_list = ["dt", "open", "high", "low", "close", "value"]
    df = df.rename(columns=rename_dict)[rename_list].drop_duplicates()

    return df


def process_phisix_date_dict(phisix_dict):
    date = datetime.strftime(
        pd.to_datetime(phisix_dict["as_of"]).date(), "%Y-%m-%d"
    )
    stock_dict = phisix_dict["stock"][0]
    stock_price_dict = stock_dict["price"]
    name = stock_dict["name"]
    currency = stock_price_dict["currency"]
    closing_price = stock_price_dict["amount"]
    percent_change = stock_dict["percent_change"]
    volume = stock_dict["volume"]
    symbol = stock_dict["symbol"]
    return {
        "dt": date,
        "name": name,
        "currency": currency,
        "close": closing_price,
        "percent_change": percent_change,
        "volume": volume,
        "symbol": symbol,
    }


def get_pse_data_by_date(symbol, date):
    url = "http://phisix-api2.appspot.com/stocks/{}.{}.json".format(
        symbol, date
    )
    res = requests.get(url)
    if res.status_code == 200:
        unprocessed_dict = res.json()
        processed_dict = process_phisix_date_dict(unprocessed_dict)
        return processed_dict
    return None


def get_pse_data(
    symbol, start_date, end_date, save=True, max_straight_nones=10
):

    """Returns pricing data for a specified stock.

    Parameters
    ----------
    symbol : str
        Symbol of the stock in the PSE. You can refer to this link: https://www.pesobility.com/stock.
    start_date : str
        Starting date (YYYY-MM-DD) of the period that you want to get data on
    end_date : str
        Ending date (YYYY-MM-DD) of the period you want to get data on

    Returns
    -------
    pandas.DataFrame
        Stock data (in CV format if cv = True) for the specified company and date range
    """

    file_name = "{}_{}_{}.csv".format(symbol, start_date, end_date)

    if Path(file_name).exists():
        print("Reading cached file found:", file_name)
        pse_data_df = pd.read_csv(file_name)
        pse_data_df["dt"] = pd.to_datetime(pse_data_df.dt)
        return pse_data_df

    date_range = (
        pd.period_range(start_date, end_date, freq="D")
        .to_series()
        .astype(str)
        .values
    )
    max_straight_nones = min(max_straight_nones, len(date_range))
    pse_data_list = []
    straight_none_count = 0
    for i, date in tqdm(enumerate(date_range)):
        iter_num = i + 1
        pse_data_1day = get_pse_data_by_date(symbol, date)

        # Return None if the first `max_straight_nones` phisix iterations return Nones (status_code != 200)
        if pse_data_1day is None:
            if iter_num < max_straight_nones:
                straight_none_count += 1
            else:
                straight_none_count += 1
                if straight_none_count >= max_straight_nones:
                    print(
                        "Symbol {} not found in phisix after the first {} date iterations!"
                    )
                    return None
            continue
        else:
            # Refresh straight none count when phisix returns
            straight_none_count = 0
        pse_data_list.append(pse_data_1day)
    pse_data_df = pd.DataFrame(pse_data_list)
    pse_data_df = pse_data_df[["dt", "close", "volume"]]

    if save:
        pse_data_df.to_csv(file_name, index=False)

    pse_data_df["dt"] = pd.to_datetime(pse_data_df.dt)
    return pse_data_df


def get_yahoo_data(symbol, start_date, end_date):
    df = yf.download(symbol, start=start_date, end=end_date)
    df = df.reset_index()
    rename_dict = {
        "Date": "dt",
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Adj Close": "adj_close",
        "Volume": "volume",
    }
    rename_list = ["dt", "open", "high", "low", "close", "adj_close", "volume"]
    df = df.rename(columns=rename_dict)[rename_list].drop_duplicates()
    return df if not df.empty else None


def get_stock_data(
    symbol,
    start_date,
    end_date,
    source="phisix",
    format="dcv",
    stock_table_fp="stock_table.csv",
):

    """Returns pricing data for a specified stock.

    Parameters
    ----------
    symbol : str
        Symbol of the stock in the PSE. You can refer to this link: https://www.pesobility.com/stock.
    start_date : str
        Starting date (YYYY-MM-DD) of the period that you want to get data on
    end_date : str
        Ending date (YYYY-MM-DD) of the period you want to get data on
    source : str
        First source to query from ("pse", "yahoo"). If the stock is not found in the first source, the query is run on the other source.
    format : str
        Format of the output data
    stock_table_fp : str
        File path of an existing stock table or where a newly downloaded table will be saved. Only relevant when source='pse'.

    Returns
    -------
    pandas.DataFrame
        Stock data (in the specified `format`) for the specified company and date range
    """

    df_columns = [DATA_FORMAT_COLS[c] for c in format]
    if source == "phisix":
        # The query is run on 'phisix', but if the symbol isn't found, the same query is run on 'yahoo'.
        df = get_pse_data(symbol, start_date, end_date)
        if df is None:
            df = get_yahoo_data(symbol, start_date, end_date)
    elif source == "yahoo":
        # The query is run on 'yahoo', but if the symbol isn't found, the same query is run on 'phisix'.
        df = get_yahoo_data(symbol, start_date, end_date)
        if df is None:
            df = get_pse_data(symbol, start_date, end_date)
    else:
        raise Exception("Source must be either 'phisix' or 'yahoo'")

    missing_columns = [col for col in df_columns if col not in df.columns]

    # Fill missing columns with np.nan
    for missing_column in missing_columns:
        df[missing_column] = np.nan

    if len(missing_columns) > 0:
        print("Missing columns filled w/ NaN:", missing_columns)

    return df[df_columns]


def pse_data_to_csv(
    symbol,
    start_date,
    end_date,
    pse_dir="",
    stock_table_fp="stock_table.csv",
    disclosures=False,
):
    pse = get_pse_data(
        symbol,
        start_date,
        end_date,
        stock_table_fp=stock_table_fp,
        disclosures=disclosures,
    )
    if isinstance(pse, pd.DataFrame):
        pse.to_csv(
            "{}{}_{}_{}_OHLCV.csv".format(
                pse_dir, symbol, start_date, end_date
            )
        )
    else:
        pse[0].to_csv(
            "{}{}_{}_{}_OHLCV.csv".format(
                pse_dir, symbol, start_date, end_date
            )
        )
        performance_dict = pse[1]
        performance_dict["D"].to_csv(
            "{}{}_{}_{}_D.csv".format(pse_dir, symbol, start_date, end_date)
        )
        performance_dict["E"].to_csv(
            "{}{}_{}_{}_E.csv".format(pse_dir, symbol, start_date, end_date)
        )


def tweepy_api(consumer_key, consumer_secret, access_token, access_secret):
    """
    Returns authenticated tweepy.API object

    Sample methods:
        user_timeline: returns recent tweets from a specified twitter user
        - screen_name: username of account of interest
        - count: number of most recent tweets to return
    """
    auth = tweepy.OAuthHandler(consumer_key, consumer_secret)
    auth.set_access_token(access_token, access_secret)
    api = tweepy.API(auth)
    return api
