import zipfile
import pandas as pd

def extract_data(csv_name):
    zip_path = 'KboOpenData_0540_2026_11_10_Full.zip'
    with zipfile.ZipFile(zip_path) as zf:
        with zf.open(csv_name) as f:
            return pd.read_csv(f, encoding='utf-8', dtype=str)

if __name__ == "__main__":
    df = extract_data('enterprise.csv')
    print(df.head())
