import io
import pandas as pd
import requests

def url_formatter(year, month):
    return f"https://d37ci6vzurychx.cloudfront.net/trip-data/green_tripdata_{year}-{month:02d}.parquet"

def extract_data(year, month):
    url = url_formatter(year, month)
    try:
        response = requests.get(url)
        response.raise_for_status()
        return pd.read_parquet(io.BytesIO(response.content))
    except requests.exceptions.RequestException as e:
        print(f"Error fetching data from {url}: {e}")
        return pd.DataFrame()

if __name__ == "__main__":
    url = url_formatter(2019, 1)
    df = extract_data(url)