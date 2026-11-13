import pandas as pd
import local.extract as extract
import helper as helper
import local.load as load

KBO_CSV_FILES = [
    "enterprise.csv",
    "establishment.csv",
    "denomination.csv",
    "address.csv",
    "contact.csv",
    "activity.csv",
    "branch.csv",
    "meta.csv",
    "code.csv",
]

def main():
    zip_path = 'KboOpenData_0540_2026_11_10_Full.zip'
    helper.csv_preview(zip_path)
    for csv_file in KBO_CSV_FILES:
        print(f"\n=== Inspecting {csv_file} ===")
        extracted_df = extract.extract_data(csv_file)
        helper.inspect_data(extracted_df)
        load.load_data(extracted_df, csv_file)

if __name__ == "__main__":
    main()