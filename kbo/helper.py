import zipfile
import pandas as pd

def csv_preview(zip_path):
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            if name.endswith('.csv'):
                print(f"\n=== {name} ===")
                with zf.open(name) as f:
                    for _ in range(3):
                        line = f.readline()
                        if not line:
                            break
                        print(repr(line.decode('utf-8', errors='replace')))

def inspect_data(df):
    if df.empty:
        print("DataFrame is empty.")
        return
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
    num_df = df.select_dtypes(include="number")
    if num_df.empty:
        print("No numeric columns.")
    else:
        print(num_df.describe().T[["min", "50%", "mean", "max"]])
