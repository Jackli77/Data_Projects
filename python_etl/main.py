import argparse
import sys
from extract import extract_data
from transform import transform_data


def parse_args():
    parser = argparse.ArgumentParser(description="Run NYC Green Taxi ETL pipeline.")
    parser.add_argument("--year", type=int, required=True, help="Target year (e.g., 2019)")
    parser.add_argument(
        "--month",
        type=int,
        required=True,
        choices=range(1, 13),
        metavar="1-12",
        help="Target month (1-12)",
    )
    return parser.parse_args()

def inspect_data(df):
    if df.empty:
        print("DataFrame is empty.")
        return

    # Prevent terminal output from hiding columns with '...'
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 1000)

    print(f"\n=== SHAPE ===\nRows: {df.shape[0]:,} | Columns: {df.shape[1]}")

    print("\n=== SCHEMA & NULLS ===")
    summary = pd.DataFrame({
        "dtype": df.dtypes,
        "null_count": df.isnull().sum(),
        "null_pct": (df.isnull().mean() * 100).round(2),
        "n_unique": df.nunique()
    })
    print(summary)

    print("\n=== SAMPLE ROWS ===")
    print(df.head(5))

    print("\n=== NUMERIC SUMMARY ===")
    print(df.describe().T[["min", "50%", "mean", "max"]])

    print("\n=== DATETIME RANGE ===")
    for col in df.select_dtypes(include=["datetime64[ns]", "datetime64[us]"]).columns:
        print(f"{col}: {df[col].min()}  -->  {df[col].max()}")



def main():
    args = parse_args()
    print(f"=== Starting pipeline for {args.year}-{args.month:02d} ===")

    raw_df = extract_data(args.year, args.month)
    if raw_df.empty:
        print("Pipeline aborted: extraction returned no data.")
        sys.exit(1)

    clean_df = transform_data(raw_df, args.year, args.month)
    print(clean_df.head())
    inspect_data(clean_df)
    print("=== Pipeline finished ===")


if __name__ == "__main__":
    main()