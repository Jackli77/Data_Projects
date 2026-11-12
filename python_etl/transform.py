import pandas as pd

def transform_data(df, year, month):
    df['filename'] = f"green_tripdata_{year}-{month:02d}.parquet"
    df['unique_id'] = df['filename'] + "_" + df.index.astype(str)
    return df