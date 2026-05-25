import re
import pandas as pd

from glob import glob
from decimal import Decimal, localcontext, InvalidOperation, ROUND_HALF_UP
from dateutil.easter import easter
from os.path import basename, join
from datetime import date, timedelta
from datetime import datetime as _datetime

from .config import ROOT, DATA, VCS, OUTPUTS

with open(join(ROOT, "historical_divisors_values.csv")) as f:
    historical_iv = f.readlines()

with open(join(ROOT, "historical_exchange_rates.csv")) as f:
    historical_fx = f.readlines()


# =========================================================================================================================================
# Util Funcitons
# =========================================================================================================================================
def _batch_rename():
    pass


def _build_fx_map(historical_fx):
    fx_map = {}

    for n, line in enumerate(historical_fx[1:], start=2):
        raw = line.rstrip("\n")
        parts = raw.split(";")

        if len(parts) != 3:
            raise ValueError(f"Line {n}: malformed split -> {raw!r}")

        d, pair, value = parts
        value = value.strip().strip('"').replace(",", ".")

        if value == "":
            # raise ValueError(f"Line {n}: empty decimal -> {raw!r}")
            print(f"Line {n}: empty decimal -> {raw!r}")

        try:
            fx_map[(d, pair)] = Decimal(value)
        except InvalidOperation as e:
            # raise ValueError(
            #     f"Line {n}: invalid decimal -> raw={raw!r}, parsed_value={value!r}"
            # ) from e
            print(f"Line {n}: invalid decimal -> raw={raw!r}, parsed_value={value!r}")

    return fx_map


fx_map = _build_fx_map(historical_fx)


def _yyyymmdd(T: date) -> str:
    return T.strftime("%Y%m%d")


def _min_max_dates(alist: list):
    if not alist:
        raise ValueError("Empty list!")
    return (
        _datetime.strptime(str(min(alist)), "%Y%m%d").date(),
        _datetime.strptime(str(max(alist)), "%Y%m%d").date(),
    )


def _normalize_date_str(x) -> str:
    s = str(x).strip()
    patterns = [
        "%d.%m.%Y",
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%d/%m/%Y",
        "%Y%m%d",
        "%d-%m-%Y",
    ]
    for fmt in patterns:
        try:
            return _datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    raise ValueError(f"Unsupported date format: {s}")


# =========================================================================================================================================
# File Processing
# =========================================================================================================================================
def _get_date_range(idx, folder_path=DATA) -> tuple[date, date, list[date], list[date]]:
    opens = glob(join(folder_path, f"opencomposition_{idx}_*.csv"))
    closes = glob(join(folder_path, f"closecomposition_{idx}_*.csv"))
    open_dates = [
        re.search(rf"opencomposition_{idx}_(\d+)\.csv$", basename(file_name)).group(1)
        for file_name in opens
    ]
    close_dates = [
        re.search(rf"closecomposition_{idx}_(\d+)\.csv$", basename(file_name)).group(1)
        for file_name in closes
    ]
    open_earliest, open_latest = _min_max_dates(open_dates)
    close_earliest, close_latest = _min_max_dates(close_dates)
    if open_earliest == close_earliest and open_latest == close_latest:
        return open_earliest, open_latest, opens, closes
    else:
        raise ValueError(
            f"{idx} open & close comps: earliest & latest do not match! Open Earliest: {min(opens)}; Close Earliest: {min(closes)}; Open Latest: {max(opens)}; Close Latest: {max(closes)}"
        )


def _get_calendar(idx, vcs=VCS) -> str:
    # Hard code for this case
    return "STOXX Americas Calendar"

    # rows = vcs[vcs["Symbol"] == idx.upper()]
    # if len(rows) == 0:
    #     raise ValueError(f"{idx} not found in VCS!")
    # if len(rows) > 1:
    #     raise ValueError(f"{idx} more than 1 match in VCS!")

    # return rows["Dissemination Calendar"].values[0]


def _get_easter_fri_mon(year: int):
    return (easter(year) - timedelta(days=2), easter(year) + timedelta(days=1))


def _get_calendar_year_holidays(
    year: int, calendar: str = "STOXX Europe Calendar"
) -> list:
    gf, em = _get_easter_fri_mon(year)
    if calendar == "STOXX Global Calendar":
        return [date(year, 1, 1)]
    if calendar == "STOXX Europe Calendar":
        return [
            date(year, 1, 1),
            gf,
            em,
            date(year, 12, 25),
            date(year, 12, 26),
        ]
    if calendar == "Xetra Calendar":
        return [
            date(year, 1, 1),
            gf,
            em,
            date(year, 5, 1),
            date(year, 12, 24),
            date(year, 12, 25),
            date(year, 12, 26),
            date(year, 12, 31),
        ]
    if calendar == "STOXX Americas Calendar":
        return [
            date(year, 1, 1),
            gf,
            date(year, 12, 25),
        ]

    raise ValueError(f"{calendar} not Supported!")


def _get_all_trading_days(idx: str, start_date: date, end_date: date) -> list:
    calendar = _get_calendar(idx)
    all_dates = [
        start_date + timedelta(days=i) for i in range((end_date - start_date).days + 1)
    ]
    all_years = set(range(start_date.year, end_date.year + 1))
    holidays = []
    for year in all_years:
        holidays_to_append = _get_calendar_year_holidays(year, calendar)
        holidays += holidays_to_append
    all_non_weekend_days = [x for x in all_dates if x.isoweekday() <= 5]
    return [x for x in all_non_weekend_days if x not in holidays]


def _get_Tp1(idx: str, T: date) -> date:
    trading_days = _get_all_trading_days(idx, T, T + timedelta(days=10))
    future_days = [d for d in trading_days if d > T]
    if future_days:
        return future_days[0]
    raise ValueError(f"Cannot find next trading day of {_yyyymmdd(T)}!")


def _get_Tm1(idx: str, T: date) -> date:
    trading_days = _get_all_trading_days(idx, T - timedelta(days=10), T)
    past_days = [d for d in trading_days if d < T]
    if past_days:
        return past_days[-1]
    raise ValueError(f"Cannot find past trading day of {_yyyymmdd(T)}!")


def _to_decimal(x: object) -> Decimal | None:
    if pd.isna(x) or x == "":
        return None
    return Decimal(str(x))


def _read_comp(idx: str, T: date, close_or_open: str) -> pd.DataFrame:
    STR_COLUMNS = {
        "Date",
        "Index_Symbol",
        "Index_Name",
        "Index_ISIN",
        "Index_Type",
        "Index_Currency",
        "Internal_Key",
        "ISIN",
        "Instrument_Name",
        "Currency",
    }

    INT_COLUMNS = {
        "Index_Component_Count",
        "Index_Divisor",
        "Index_Mcap_Units",
        "Shares",
        "Mcap_Units_Index_Currency",
    }

    DECIMAL_COLUMNS = {
        "Index_Float",
        "Free_Float",
        "Capfactor",
        "Weightfactor",
        "Close_unadjusted_local",
        "Close_adjusted_local",
        "FX_local_to_Index_Currency",
        "Weight",
        "Index_Open_Quotation",
        "Index_Settlement_Value",
        "Index_Value_high",
        "Index_Value_low",
        "Index_Close",
        "Index_Close_not_rounded",
    }

    if close_or_open not in {"close", "open"}:
        raise ValueError("Either open or close in Comp Reading!")

    path = join(DATA, f"{close_or_open}composition_{idx.lower()}_{_yyyymmdd(T)}.csv")

    dtype_map = {col: "string" for col in STR_COLUMNS | DECIMAL_COLUMNS}
    dtype_map.update({col: "Int64" for col in INT_COLUMNS})

    df = pd.read_csv(
        path,
        sep=";",
        dtype=dtype_map,
        keep_default_na=True,
        na_values=[""],
    )

    # ICOS Era: replace Currency
    if (
        df["Index_Currency"].iloc[0] == "USD"
        and "ric" in df.columns.str.strip().str.lower()
    ):
        # it is post ICOS data
        df = _usd_df_transfer(df, T, fx_map)

    for col in DECIMAL_COLUMNS:
        if col in df.columns:
            df[col] = df[col].map(_to_decimal)

    return df.rename(columns=str.lower)


def _usd_df_transfer(
    df: pd.DataFrame,
    T: date,
    fx_map: dict = fx_map,
) -> pd.DataFrame:
    T_ddmmyyyy = T.strftime("%d.%m.%Y")
    out = df.copy()
    eurusd = fx_map[(T_ddmmyyyy, "EURUSD")]
    currencies = [x for x in out["Currency"].unique() if x not in ["EUR", "USD"]]
    rate_map = {
        c: round(fx_map[(T_ddmmyyyy, f"{c}EUR")] * eurusd, 7) for c in currencies
    }
    rate_map["EUR"] = round(eurusd, 7)
    rate_map["USD"] = 1

    out["FX_local_to_Index_Currency"] = out["Currency"].map(rate_map)
    return out


def _locate_row(djid: str, idx: str, T: date, close_or_open: str) -> pd.Series:
    df = _read_comp(idx, T, close_or_open)
    mask = df["Internal_Key"] == djid
    mapped_rows = df[mask]

    if len(mapped_rows) != 1:
        raise ValueError(
            f"{len(mapped_rows)} rows found in {close_or_open}comp file for {djid} - {_yyyymmdd(T)}!"
        )

    # mapped_rows = mapped_rows.rename(columns=str.lower)
    return mapped_rows.iloc[0]


def _detect_missing_trade_days(idx: str, test: bool = False) -> bool:
    start_date, end_date, opens, closes = _get_date_range(idx)
    open_dates = [
        re.search(rf"opencomposition_{idx}_(\d+)\.csv$", basename(x)).group(1)
        for x in opens
    ]
    close_dates = [
        re.search(rf"closecomposition_{idx}_(\d+)\.csv$", basename(x)).group(1)
        for x in closes
    ]
    all_trading_days = [
        _datetime.strftime(d, "%Y%m%d")
        for d in _get_all_trading_days(idx, start_date, end_date)
    ]

    opens_missing = [x for x in all_trading_days if x not in open_dates]
    closes_missing = [x for x in all_trading_days if x not in close_dates]

    if len(opens_missing + closes_missing) == 0:
        if test:
            print(f"{idx}:")
            print("No missing trade days")
        return True
    else:
        print(f"{idx}:")
        print(f"Open missing: {opens_missing}")
        print(f"Close missing: {closes_missing}")

        if test:
            # to continue testing which trade days are missing in testing mode
            # test=False in index calculation. Missing trade day will lead to error, cannot continue
            return False

        raise ValueError("Open/Close <= All Trading Days!")


# =========================================================================================================================================
# FFMCap Calculation
# =========================================================================================================================================
def ind_ffmcap(djid: str, idx: str, T: date, close_or_open: str) -> int:
    if close_or_open not in ["close", "open"]:
        raise ValueError("Either open or close in FFMCap calc!")

    row = _locate_row(djid, idx, T, close_or_open)

    shares = row["shares"]
    ff = row["free_float"]
    cf = row["capfactor"]
    close = (
        row["close_unadjusted_local"]
        if close_or_open == "close"
        else row["close_adjusted_local"]
    )
    fx = row["fx_local_to_index_currency"]

    return int(round(round(shares * ff * cf, 0) * round(close * fx, 7), 0))


def idx_ffmcap(df: pd.DataFrame, close_or_open: str) -> int:
    df["close"] = (
        df["close_unadjusted_local"]
        if close_or_open == "close"
        else df["close_adjusted_local"]
    )

    part1 = (df["shares"] * df["free_float"] * df["capfactor"]).round(0)
    part2 = (df["close"] * df["fx_local_to_index_currency"]).round(7)
    return int((part1 * part2).round(0).sum())


# =========================================================================================================================================
# Divisor & IV Calculation
# =========================================================================================================================================
def idx_calc(
    idx: str,
    T: date,
    start_date: date,
    last_day_dict: dict | None = None,
    test: bool = False,
) -> dict:

    Tm1 = _get_Tm1(idx, T)
    Tp1 = _get_Tp1(idx, T)

    if test:
        print(f"close T: {_yyyymmdd(T)}")

    close_T = _read_comp(idx, T, "close")
    open_T = _read_comp(idx, T, "open")

    total_ffmc_T = idx_ffmcap(close_T, "close")
    total_ffmc_Tp1 = idx_ffmcap(open_T, "open")

    try:
        iv_pre = Decimal(close_T["index_close_not_rounded"].values[0])
    except Exception:
        # with localcontext() as ctx:
        #     ctx.prec = 40
        #     mcap = Decimal(str(close_T["index_mcap_units"].iloc[0]))
        #     divisor = Decimal(str(close_T["index_divisor"].iloc[0]))
        #     iv_pre = mcap / divisor
        Tp1_ddmmyyyy = _datetime.strftime(Tp1, "%d.%m.%Y")
        that_line = [x for x in historical_iv if f"{Tp1_ddmmyyyy};{idx.upper()}" in x]
        if len(that_line) != 1:
            raise ValueError(f"{idx} - {T} - {Tp1} {len(that_line)} rows")
        that_line = that_line[0]
        iv_pre = Decimal(that_line.split(";")[-1])

    div_pre = int(close_T["index_divisor"].values[0])
    Tp1_pre = _normalize_date_str(open_T["next_trading_day"].values[0])

    if T == start_date:
        # Anchor the chain to the golden divisor once, on the first day only.
        div_opn_T = div_pre
    else:
        if last_day_dict is None:
            raise ValueError(
                f"{_yyyymmdd(T)} is not the first date {_yyyymmdd(start_date)}"
            )

        div_opn_T = int(last_day_dict["Divisor Open T+1"])

    # EOD index value is reported only; it no longer feeds the divisor. Deriving
    # the divisor from a 7dp-rounded iv (and on day 0 from the official iv_pre)
    # injected an error that the never-re-anchored chain carried all year.
    iv_T = round(total_ffmc_T / Decimal(div_opn_T), 7)

    # Next open divisor by pure forward accumulation: D(T+1) = D(T) * open / close.
    # Full-precision Decimal throughout; round only the final integer divisor so
    # our FFMCap rounding bias cancels in the open/close ratio.
    with localcontext() as ctx:
        ctx.prec = 40
        div_opn_Tp1 = int(
            (Decimal(div_opn_T) * total_ffmc_Tp1 / Decimal(total_ffmc_T))
            .to_integral_value(rounding=ROUND_HALF_UP)
        )

    return {
        "T": T,
        "T-1": Tm1,
        "T+1": Tp1,
        "Divisor Open T (from T-1)": div_opn_T,
        "Index Value EOD": round(iv_T, 8),
        "Divisor Open T+1": div_opn_Tp1,
        "IV Pre": iv_pre,
        "Divisor Pre": div_pre,
        "T+1 Pre": Tp1_pre,
    }


# =========================================================================================================================================
# Main
# =========================================================================================================================================
def main(idx: str | None = None, test: bool = False) -> pd.DataFrame:
    if not idx:
        idx = input("Please enter Index ID: ").strip()
    _detect_missing_trade_days(idx, test=False)

    start_date, end_date, _, _ = _get_date_range(idx)
    all_trading_days = _get_all_trading_days(idx, start_date, end_date)

    rows = []

    day0dict = idx_calc(idx, start_date, start_date, None)
    rows.append(day0dict)

    for day in all_trading_days[1:]:
        output_dict = idx_calc(idx, day, start_date, day0dict, test)
        rows.append(output_dict)
        day0dict = output_dict

    df_outputs = pd.DataFrame(rows)

    df_outputs = df_outputs[
        [
            "T",
            "T-1",
            "T+1",
            "Divisor Open T (from T-1)",
            "Index Value EOD",
            "Divisor Open T+1",
            "IV Pre",
            "Divisor Pre",
            "T+1 Pre",
        ]
    ]

    df_outputs.to_csv(
        join(OUTPUTS, f"{idx}_{_yyyymmdd(start_date)}_{_yyyymmdd(end_date)}.csv"),
        sep=";",
        index=False,
    )
    print(f"Finished: {idx} {_yyyymmdd(start_date)} -> {_yyyymmdd(end_date)}")

    return df_outputs


if __name__ == "__main__":
    import sys

    idx = sys.argv[1].replace("-", "") if sys.argv[1] else None
    main(idx)
